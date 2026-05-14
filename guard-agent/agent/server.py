#!/usr/bin/env python3
"""
Guard Agent — Remote management REST API for Home Assistant.
Runs as HA addon, provides full remote access via Cloudflare tunnel.

Endpoints:
  /api/health          — status, version, uptime
  /api/scan/full       — ARP + Tuya + ping sweep
  /api/scan/arp        — ARP table only
  /api/scan/tuya       — Tuya UDP broadcast
  /api/scan/ping       — ping specific targets
  /api/files/list      — list files in /homeassistant/
  /api/files/read      — read file content
  /api/files/write     — write file content
  /api/shell/exec      — execute shell command
  /api/supervisor/*    — proxy Supervisor API
  /api/ha/*            — proxy HA Core API
  /api/telemetry/push  — force telemetry push
"""

import os
import sys
import json
import time
import asyncio
import logging
import subprocess
import socket
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

from aiohttp import web, ClientSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("guard-agent")

# ── Config ──
API_KEY = os.environ.get("GUARD_API_KEY", "")
SERVER_URL = os.environ.get("GUARD_SERVER_URL", "https://mcp.jufusi.us")
SCAN_INTERVAL = int(os.environ.get("GUARD_SCAN_INTERVAL", "30"))
TUYA_SCAN = os.environ.get("GUARD_TUYA_SCAN", "true").lower() == "true"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_URL = "http://supervisor"
HA_CONFIG_DIR = "/homeassistant"
VERSION = "1.7.0"
ENROLL_SENTINEL = "/data/enrolled.json"

#CC- v2 API: key in header instead of URL path (prevents key leaking into logs)
def _guard_headers(extra=None):
    h = {"Content-Type": "application/json", "User-Agent": f"GuardAgent/{VERSION}", "X-Agent-Key": API_KEY}
    if extra:
        h.update(extra)
    return h
START_TIME = datetime.now()

#CC- Explicit entity mapping from server KeyEntitiesJson (populated at startup)
#CC- Maps telemetry field → HA entity_id. Takes priority over TELEMETRY_PATTERNS.
KEY_ENTITIES = {}  # e.g. {"fve_production": "sensor.inverter_xxx_vykon", ...}

#CC- KeyEntitiesJson field names → telemetry payload field names
KEY_ENTITY_FIELD_MAP = {
    "fve_production": "fve_production_w",
    "house_consumption": "house_consumption_w",
    "grid_import": "grid_import_w",
    "grid_export": "grid_export_w",
    "battery_soc": "battery_soc_pct",
    "battery_power": "battery_power_w",
}


# ── Auth middleware ──
@web.middleware
async def auth_middleware(request, handler):
    #CC- Health endpoint bez auth
    if request.path == "/api/health":
        return await handler(request)

    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    if not API_KEY or token != API_KEY:
        return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


# ── Health ──
async def handle_health(request):
    return web.json_response({
        "status": "running",
        "version": VERSION,
        "uptime_minutes": round((datetime.now() - START_TIME).total_seconds() / 60, 1),
        "ha_config_dir": HA_CONFIG_DIR,
        "supervisor_available": bool(SUPERVISOR_TOKEN),
        "api_key_configured": bool(API_KEY),
    })


# ── Network scanning ──
async def handle_scan_arp(request):
    devices = await asyncio.to_thread(_arp_scan)
    return web.json_response({"devices": devices, "count": len(devices)})


async def handle_scan_tuya(request):
    devices = await asyncio.to_thread(_tuya_udp_scan)
    return web.json_response({"devices": devices, "count": len(devices)})


async def handle_scan_ping(request):
    body = await request.json() if request.can_read_body else {}
    targets = body.get("targets", [])
    subnet = body.get("subnet")
    if subnet:
        #CC- Ping sweep celého subnetu
        targets = [f"{subnet}.{i}" for i in range(1, 255)]
    results = await asyncio.to_thread(_ping_sweep, targets)
    return web.json_response({"results": results, "alive": sum(1 for r in results if r["alive"])})


async def handle_scan_full(request):
    #CC- Kompletní scan: ping sweep + ARP + Tuya UDP + port probe
    subnet = _detect_subnet()
    log.info("Full scan starting, subnet: %s", subnet)

    # Ping sweep pro naplnění ARP
    if subnet:
        await asyncio.to_thread(_ping_sweep, [f"{subnet}.{i}" for i in range(1, 255)])

    # ARP scan
    arp_devices = await asyncio.to_thread(_arp_scan)

    # Tuya UDP
    tuya_devices = {}
    if TUYA_SCAN:
        tuya_devices = await asyncio.to_thread(_tuya_udp_scan_raw)
        for dev in arp_devices:
            ip = dev.get("ip")
            if ip in tuya_devices:
                dev["tuya_device_id"] = tuya_devices[ip].get("device_id")
                dev["tuya_version"] = tuya_devices[ip].get("version")

    # Tuya TCP probe (port 6668)
    await asyncio.to_thread(_tuya_tcp_probe, arp_devices)

    log.info("Full scan complete: %d devices", len(arp_devices))
    return web.json_response({
        "devices": arp_devices,
        "count": len(arp_devices),
        "tuya_udp": len(tuya_devices),
        "tuya_tcp": sum(1 for d in arp_devices if d.get("tuya_port_open")),
        "subnet": subnet,
    })


# ── File management ──
async def handle_files_list(request):
    rel_path = request.query.get("path", "/")
    full_path = _safe_path(rel_path)
    if not full_path:
        return web.json_response({"error": "invalid path"}, status=400)

    if not full_path.exists():
        return web.json_response({"error": "not found"}, status=404)

    if full_path.is_file():
        stat = full_path.stat()
        return web.json_response({"type": "file", "size": stat.st_size,
                                   "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()})

    files = []
    for item in sorted(full_path.iterdir()):
        stat = item.stat()
        files.append({
            "name": item.name,
            "type": "dir" if item.is_dir() else "file",
            "size": stat.st_size if item.is_file() else None,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return web.json_response({"path": rel_path, "files": files})


async def handle_files_read(request):
    rel_path = request.query.get("path", "")
    full_path = _safe_path(rel_path)
    if not full_path or not full_path.is_file():
        return web.json_response({"error": "file not found"}, status=404)

    try:
        content = full_path.read_text(encoding="utf-8")
        return web.json_response({"path": rel_path, "content": content, "size": len(content)})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_files_write(request):
    body = await request.json()
    rel_path = body.get("path", "")
    content = body.get("content", "")
    full_path = _safe_path(rel_path)
    if not full_path:
        return web.json_response({"error": "invalid path"}, status=400)

    #CC- Automatický backup před přepisem
    if full_path.exists():
        backup = full_path.with_suffix(full_path.suffix + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}")
        backup.write_text(full_path.read_text(encoding="utf-8"), encoding="utf-8")

    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    log.info("File written: %s (%d bytes)", rel_path, len(content))
    return web.json_response({"ok": True, "path": rel_path, "size": len(content)})


# ── Shell execution ──
async def handle_shell_exec(request):
    body = await request.json()
    command = body.get("command", "")
    timeout = min(body.get("timeout", 30), 120)

    if not command:
        return web.json_response({"error": "no command"}, status=400)

    log.info("Shell exec: %s", command[:100])
    try:
        result = await asyncio.to_thread(
            subprocess.run, command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return web.json_response({
            "exit_code": result.returncode,
            "stdout": result.stdout[-10000:],
            "stderr": result.stderr[-5000:],
        })
    except subprocess.TimeoutExpired:
        return web.json_response({"error": "timeout", "timeout": timeout}, status=408)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ── Supervisor API proxy ──
async def handle_supervisor_proxy(request):
    path = request.match_info.get("path", "")
    if not SUPERVISOR_TOKEN:
        return web.json_response({"error": "no supervisor token"}, status=503)

    url = f"{SUPERVISOR_URL}/{path}"
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}

    async with ClientSession() as session:
        try:
            if request.method == "GET":
                async with session.get(url, headers=headers) as resp:
                    data = await resp.json()
                    return web.json_response(data, status=resp.status)
            elif request.method == "POST":
                body = await request.read()
                async with session.post(url, headers=headers, data=body,
                                        headers_={**headers, "Content-Type": "application/json"}) as resp:
                    data = await resp.json()
                    return web.json_response(data, status=resp.status)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=502)


async def handle_supervisor_get(request):
    return await _supervisor_request("GET", request.match_info.get("path", ""))

async def handle_supervisor_post(request):
    body = await request.read() if request.can_read_body else None
    return await _supervisor_request("POST", request.match_info.get("path", ""), body)


async def _supervisor_request(method, path, body=None):
    if not SUPERVISOR_TOKEN:
        return web.json_response({"error": "no supervisor token"}, status=503)

    url = f"{SUPERVISOR_URL}/{path}"
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}",
               "Content-Type": "application/json"}

    async with ClientSession() as session:
        try:
            if method == "GET":
                async with session.get(url, headers=headers) as resp:
                    data = await resp.json()
                    return web.json_response(data, status=resp.status)
            else:
                async with session.post(url, headers=headers, data=body) as resp:
                    data = await resp.json()
                    return web.json_response(data, status=resp.status)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=502)


# ── HA Core API proxy ──
async def handle_ha_get(request):
    path = request.match_info.get("path", "")
    return await _ha_request("GET", path)

async def handle_ha_post(request):
    path = request.match_info.get("path", "")
    body = await request.read() if request.can_read_body else None
    return await _ha_request("POST", path, body)


async def _ha_request(method, path, body=None):
    if not SUPERVISOR_TOKEN:
        return web.json_response({"error": "no supervisor token"}, status=503)

    url = f"{SUPERVISOR_URL}/core/api/{path}"
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}",
               "Content-Type": "application/json"}

    async with ClientSession() as session:
        try:
            if method == "GET":
                async with session.get(url, headers=headers) as resp:
                    data = await resp.json()
                    return web.json_response(data, status=resp.status)
            else:
                async with session.post(url, headers=headers, data=body) as resp:
                    data = await resp.json()
                    return web.json_response(data, status=resp.status)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=502)


# ── Telemetry push ──

#CC- Entity mapping: HA entity_id patterns → telemetry fields
#CC- Agent auto-detects entities by matching these patterns against all HA states
#CC- Order matters! First match wins. Put most specific patterns first.
TELEMETRY_PATTERNS = {
    "fve_production_w": ["homekit_homekit_pv", "_vykon", "_active_power"],
    "house_consumption_w": ["homekit_homekit_load", "_house_consumption", "_home_consumption"],
    #CC- Grid import/export are derived from physics (_resolve_grid_from_balance),
    #CC- NOT from mapped sensors — SEMS homekit_*_grid is unsigned magnitude and
    #CC- homekit_sems_import/export are daily cumulative kWh (not watts).
    "grid_import_w": ["_grid_import_power", "_grid_active_power_import"],
    "grid_export_w": ["_grid_export_power", "_grid_active_power_export"],
    "battery_soc_pct": ["_state_of_charge", "_battery_soc"],
    "battery_power_w": ["_battery_0_power", "_battery_power"],
    "temperature": ["weather."],  #CC- Pouze weather entity — ne invertor teplota
}

#CC- Entity patterns to EXCLUDE (false positives)
TELEMETRY_EXCLUDE = {
    "fve_production_w": ["_pv_string_", "_pv_1_", "_pv_2_"],  #CC- PV string voltage/current, ne celkový výkon
    "house_consumption_w": ["_load_status", "_load_2"],  #CC- duplicitní/status entity
    "battery_soc_pct": ["_state_of_health"],  #CC- SOH != SOC
    "temperature": ["inverter_", "_teplota", "_bms_"],  #CC- invertor/BMS teplota
}


def _resolve_grid_from_balance(telemetry):
    """
    Compute grid_import_w / grid_export_w from energy balance (physics).
    More reliable than mapped sensors — SEMS homekit_*_grid is unsigned magnitude,
    and homekit_sems_import/export are DAILY kWh counters (not instantaneous W).

    Convention: battery_power_w > 0 = DISCHARGING (supplies load) — GoodWe/Sinclair default.
                battery_power_w < 0 = CHARGING (draws from pv/grid).

    grid_balance = load - pv - battery_discharge
      > 0 → importing (need supply from grid)
      < 0 → exporting (surplus to grid)

    Only applies when pv, load AND battery_power_w are all known — otherwise
    leaves the mapped values untouched (backward compatible fallback).
    """
    pv = telemetry.get("fve_production_w")
    load = telemetry.get("house_consumption_w")
    batt = telemetry.get("battery_power_w")
    if pv is None or load is None or batt is None:
        return  # keep mapped values as-is

    load_abs = abs(load)
    balance = load_abs - pv - batt
    if balance >= 0:
        telemetry["grid_import_w"] = round(balance, 1)
        telemetry["grid_export_w"] = 0.0
    else:
        telemetry["grid_import_w"] = 0.0
        telemetry["grid_export_w"] = round(-balance, 1)


def _match_entity(field, states_dict, all_states=None):
    """Find first matching entity for a telemetry field. Uses KEY_ENTITIES first, then pattern fallback."""
    #CC- Priority 1: explicit mapping from server KeyEntitiesJson
    for ke_field, telem_field in KEY_ENTITY_FIELD_MAP.items():
        if telem_field == field and ke_field in KEY_ENTITIES:
            eid = KEY_ENTITIES[ke_field]
            if eid in states_dict and _is_numeric(states_dict[eid]):
                log.debug("KeyEntity match: %s → %s = %s", field, eid, states_dict[eid])
                return float(states_dict[eid])

    #CC- Priority 2: pattern matching fallback
    patterns = TELEMETRY_PATTERNS.get(field, [])
    excludes = TELEMETRY_EXCLUDE.get(field, [])

    #CC- Special: temperature from weather entity attributes (not state)
    if field == "temperature" and all_states:
        for s in all_states:
            eid = s.get("entity_id", "")
            if eid.startswith("weather."):
                temp = s.get("attributes", {}).get("temperature")
                if temp is not None and _is_numeric(str(temp)):
                    return float(temp)

    for eid, state in states_dict.items():
        if any(pattern in eid for pattern in patterns):
            if any(ex in eid for ex in excludes):
                continue
            if _is_numeric(state):
                return float(state)
    return None


async def handle_telemetry_push(request):
    if not API_KEY or not SERVER_URL:
        return web.json_response({"error": "not configured"}, status=400)

    #CC- Collect HA states and map to structured telemetry format
    try:
        states = await _get_ha_states()
        if not states:
            return web.json_response({"error": "no HA states"}, status=502)

        #CC- Build entity_id → numeric_state lookup
        states_lookup = {}
        for s in states:
            eid = s.get("entity_id", "")
            state = s.get("state")
            if state not in ("unavailable", "unknown"):
                states_lookup[eid] = state

        #CC- Map to structured telemetry payload (snake_case fields)
        telemetry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "fve_production_w": _match_entity("fve_production_w", states_lookup),
            "house_consumption_w": _match_entity("house_consumption_w", states_lookup),
            "grid_import_w": _match_entity("grid_import_w", states_lookup),
            "grid_export_w": _match_entity("grid_export_w", states_lookup),
            "battery_soc_pct": _match_entity("battery_soc_pct", states_lookup),
            "battery_power_w": _match_entity("battery_power_w", states_lookup),
            "temperature": _match_entity("temperature", states_lookup, states),
        }

        #CC- House consumption must be unsigned (abs) before any balance calculation
        load = telemetry.get("house_consumption_w")
        if load is not None:
            telemetry["house_consumption_w"] = abs(load)

        #CC- Derive grid_import_w / grid_export_w from physics (pv - load - battery).
        #CC- Overrides mapped sensors which may be: unsigned magnitude (homekit_homekit_grid),
        #CC- daily cumulative kWh (homekit_sems_import/export), or plain missing.
        _resolve_grid_from_balance(telemetry)

        log.info("Telemetry mapped: PV=%.0fW, Load=%.0fW, Grid=%.0f/%.0fW, SOC=%.0f%%",
                 telemetry.get("fve_production_w") or 0,
                 telemetry.get("house_consumption_w") or 0,
                 telemetry.get("grid_import_w") or 0,
                 telemetry.get("grid_export_w") or 0,
                 telemetry.get("battery_soc_pct") or 0)

        payload = json.dumps(telemetry).encode()

        import urllib.request
        req = urllib.request.Request(
            f"{SERVER_URL}/api/v2/telemetry",
            data=payload,
            headers=_guard_headers(),
        )
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
        return web.json_response({"ok": True, "mapped": {k: v for k, v in telemetry.items() if k != "timestamp" and v is not None}, "server_response": result})
    except Exception as e:
        log.error("Telemetry push error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


# ── Helper functions ──

def _safe_path(rel_path):
    """Resolve path within HA config dir, prevent traversal."""
    try:
        base = Path(HA_CONFIG_DIR).resolve()
        target = (base / rel_path.lstrip("/")).resolve()
        if not str(target).startswith(str(base)):
            return None
        return target
    except:
        return None


def _detect_subnet():
    """Detect local subnet from default gateway."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if parts[1] == "00000000":  # default route
                    gw_hex = parts[2]
                    gw_bytes = bytes.fromhex(gw_hex)
                    gw_ip = f"{gw_bytes[3]}.{gw_bytes[2]}.{gw_bytes[1]}.{gw_bytes[0]}"
                    return ".".join(gw_ip.split(".")[:3])
    except:
        pass
    return "192.168.0"


def _arp_scan():
    """Read ARP table."""
    devices = []
    try:
        with open("/proc/net/arp") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) < 4:
                    continue
                ip, flags, mac = parts[0], parts[2], parts[3].upper()
                if flags == "0x0" or mac == "00:00:00:00:00:00":
                    continue
                if ip.startswith("172.") or ip.startswith("10."):
                    continue
                hostname = None
                try:
                    hostname = socket.gethostbyaddr(ip)[0]
                except:
                    pass
                dev = {"mac": mac, "ip": ip}
                if hostname:
                    dev["hostname"] = hostname
                devices.append(dev)
    except Exception as e:
        log.warning("ARP scan error: %s", e)
    return devices


def _ping_sweep(targets):
    """Ping multiple targets in parallel."""
    results = []
    procs = []
    for ip in targets:
        p = subprocess.Popen(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        procs.append((ip, p))
        if len(procs) >= 50:
            for pip, pp in procs:
                rc = pp.wait()
                results.append({"ip": pip, "alive": rc == 0})
            procs = []
    for pip, pp in procs:
        rc = pp.wait()
        results.append({"ip": pip, "alive": rc == 0})
    return results


def _tuya_udp_scan_raw():
    """Tuya UDP broadcast scan."""
    found = {}
    try:
        udp_key = hashlib.md5(b"yGAdlopoPVldABfn").digest()
    except:
        return found

    for port in [6666, 6667]:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", port))
            sock.settimeout(5)
            end = time.time() + 5

            while time.time() < end:
                try:
                    data, addr = sock.recvfrom(4096)
                    ip = addr[0]
                    if ip in found:
                        continue

                    device_id = None
                    version = None

                    if port == 6666:
                        try:
                            idx = data.index(b"{")
                            payload = data[idx:data.rindex(b"}") + 1]
                            j = json.loads(payload)
                            device_id = j.get("gwId")
                            version = j.get("version")
                        except:
                            pass
                    elif port == 6667:
                        try:
                            from Crypto.Cipher import AES
                            payload = data[20:-8]
                            if len(payload) % 16 != 0:
                                padded = bytearray((len(payload) // 16 + 1) * 16)
                                padded[:len(payload)] = payload
                                payload = bytes(padded)
                            cipher = AES.new(udp_key, AES.MODE_ECB)
                            dec = cipher.decrypt(payload)
                            pad = dec[-1]
                            if 0 < pad <= 16:
                                dec = dec[:-pad]
                            j = json.loads(dec)
                            device_id = j.get("gwId")
                            version = j.get("version")
                        except:
                            pass

                    if device_id:
                        found[ip] = {"device_id": device_id, "version": version}
                except socket.timeout:
                    break
            sock.close()
        except:
            pass

    return found


def _tuya_udp_scan():
    """Tuya UDP scan — returns list format."""
    raw = _tuya_udp_scan_raw()
    return [{"ip": ip, **info} for ip, info in raw.items()]


def _tuya_tcp_probe(devices):
    """Probe port 6668 on devices to find Tuya on guest WiFi."""
    for dev in devices:
        if dev.get("tuya_device_id"):
            continue
        ip = dev.get("ip")
        if not ip:
            continue
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            result = s.connect_ex((ip, 6668))
            s.close()
            if result == 0:
                dev["tuya_port_open"] = True
        except:
            pass


async def _get_ha_states():
    """Get all HA entity states via Supervisor proxy."""
    if not SUPERVISOR_TOKEN:
        return None
    url = f"{SUPERVISOR_URL}/core/api/states"
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    async with ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
        except:
            pass
    return None


def _is_numeric(s):
    try:
        float(s)
        return True
    except:
        return False


async def _scan_via_supervisor():
    """Scan network via HA states — find device_tracker and known entities with IP/MAC."""
    states = await _get_ha_states()
    if not states:
        return []

    devices = []
    seen_macs = set()

    for s in states:
        eid = s.get("entity_id", "")
        attrs = s.get("attributes", {})

        # device_tracker entities often have mac, ip
        mac = attrs.get("mac", "").upper()
        ip = attrs.get("ip")

        if not mac and eid.startswith("device_tracker."):
            # Try to get MAC from source attribute
            mac = attrs.get("source", "").upper() if ":" in attrs.get("source", "") else ""

        if mac and mac not in seen_macs and mac != "00:00:00:00:00:00":
            seen_macs.add(mac)
            dev = {"mac": mac}
            if ip:
                dev["ip"] = ip
            hostname = attrs.get("host_name") or attrs.get("friendly_name", "")
            if hostname:
                dev["hostname"] = hostname
            devices.append(dev)

    return devices


# ── Background scanner loop ──
async def scanner_loop():
    """Periodic network scan + telemetry push."""
    await asyncio.sleep(15)  # wait for startup
    while True:
        try:
            log.info("Background scan starting...")
            subnet = _detect_subnet()

            # Ping sweep
            await asyncio.to_thread(_ping_sweep, [f"{subnet}.{i}" for i in range(1, 255)])

            # ARP
            devices = await asyncio.to_thread(_arp_scan)

            # Tuya
            if TUYA_SCAN:
                tuya = await asyncio.to_thread(_tuya_udp_scan_raw)
                for dev in devices:
                    ip = dev.get("ip")
                    if ip in tuya:
                        dev["tuya_device_id"] = tuya[ip].get("device_id")
                        dev["tuya_version"] = tuya[ip].get("version")

            # TCP probe
            await asyncio.to_thread(_tuya_tcp_probe, devices)

            log.info("Background scan (local): %d devices", len(devices))

            #CC- Fallback: scan přes Supervisor API pokud lokální scan nic nenašel (bridged Docker)
            if len(devices) == 0:
                try:
                    sup_devices = await _scan_via_supervisor()
                    if sup_devices:
                        devices = sup_devices
                        log.info("Supervisor scan: %d devices", len(devices))
                except Exception as e:
                    log.warning("Supervisor scan failed: %s", e)

            # Push to Guard server (only if we found something)
            if API_KEY and SERVER_URL and len(devices) > 0:
                try:
                    import urllib.request
                    payload = json.dumps({"devices": devices}).encode()
                    req = urllib.request.Request(
                        f"{SERVER_URL}/api/v2/devices",
                        data=payload,
                        headers=_guard_headers(),
                    )
                    resp = urllib.request.urlopen(req, timeout=15)
                    result = json.loads(resp.read())
                    log.info("Guard server: %s", result)
                except Exception as e:
                    log.warning("Guard push failed: %s", e)
            elif len(devices) == 0:
                log.info("No devices found, skipping push")

        except Exception as e:
            log.error("Scanner loop error: %s", e)

        await asyncio.sleep(SCAN_INTERVAL * 60)


# ── Telemetry push loop ──
async def telemetry_loop():
    """Push structured telemetry to Guard server every 5 minutes."""
    await asyncio.sleep(30)  # wait for startup + first scan
    while True:
        if not API_KEY or not SERVER_URL:
            await asyncio.sleep(300)
            continue

        try:
            states = await _get_ha_states()
            if states:
                states_lookup = {}
                for s in states:
                    eid = s.get("entity_id", "")
                    state = s.get("state")
                    if state not in ("unavailable", "unknown"):
                        states_lookup[eid] = state

                telemetry = {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "fve_production_w": _match_entity("fve_production_w", states_lookup),
                    "house_consumption_w": _match_entity("house_consumption_w", states_lookup),
                    "grid_import_w": _match_entity("grid_import_w", states_lookup),
                    "grid_export_w": _match_entity("grid_export_w", states_lookup),
                    "battery_soc_pct": _match_entity("battery_soc_pct", states_lookup),
                    "battery_power_w": _match_entity("battery_power_w", states_lookup),
                    "temperature": _match_entity("temperature", states_lookup, states),
                }

                #CC- House consumption abs, then derive grid from energy balance (physics).
                load = telemetry.get("house_consumption_w")
                if load is not None:
                    telemetry["house_consumption_w"] = abs(load)
                _resolve_grid_from_balance(telemetry)

                # Only push if we have at least one value
                has_data = any(v is not None for k, v in telemetry.items() if k != "timestamp")
                if has_data:
                    import urllib.request
                    payload = json.dumps(telemetry).encode()
                    req = urllib.request.Request(
                        f"{SERVER_URL}/api/v2/telemetry",
                        data=payload,
                        headers=_guard_headers(),
                    )
                    resp = urllib.request.urlopen(req, timeout=15)
                    result = json.loads(resp.read())
                    log.info("Telemetry push: PV=%.0fW SOC=%.0f%% → %s",
                             telemetry.get("fve_production_w") or 0,
                             telemetry.get("battery_soc_pct") or 0,
                             result.get("success", False))
                else:
                    log.debug("Telemetry: no FVE data yet, skipping push")
        except Exception as e:
            if "1010" not in str(e) and "403" not in str(e):
                log.warning("Telemetry push error: %s", e)

        await asyncio.sleep(300)  #CC- Push every 5 minutes


# ── Command polling loop ──
async def command_poll_loop():
    """Poll MCP server for commands, execute them locally."""
    await asyncio.sleep(20)
    while True:
        if not API_KEY or not SERVER_URL:
            await asyncio.sleep(60)
            continue

        try:
            import urllib.request as urlreq

            # Poll for pending commands
            req = urlreq.Request(
                f"{SERVER_URL}/api/v2/agent/commands",
                headers=_guard_headers(),
            )
            resp = urlreq.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            commands = data.get("commands", [])

            for cmd in commands:
                cmd_id = cmd.get("id")
                command = cmd.get("command", "")
                payload = cmd.get("payload")
                if payload and isinstance(payload, str):
                    try: payload = json.loads(payload)
                    except: pass

                #CC- FULL verbose logging — příkaz, payload, výsledek
                log.info("═══ Command #%s: %s ═══", cmd_id, command)
                log.info("  PAYLOAD: %s", json.dumps(payload, ensure_ascii=False)[:500] if payload else "(none)")

                try:
                    result = await _execute_command(command, payload)
                except Exception as e:
                    result = {"error": str(e)}

                #CC- Log CELÉHO výsledku (zkrácený na 1000 znaků)
                result_str = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
                if len(result_str) > 1000:
                    result_str = result_str[:1000] + "...(truncated)"

                if isinstance(result, dict) and result.get("error"):
                    log.error("  RESULT: FAILED — %s", result["error"])
                elif isinstance(result, dict) and result.get("exit_code") is not None:
                    ec = result["exit_code"]
                    if ec != 0:
                        log.warning("  RESULT: exit_code=%s", ec)
                        log.warning("  STDERR: %s", str(result.get("stderr", ""))[:500])
                        log.warning("  STDOUT: %s", str(result.get("stdout", ""))[:500])
                    else:
                        log.info("  RESULT: OK (exit_code=0)")
                        log.info("  STDOUT: %s", str(result.get("stdout", ""))[:500])
                else:
                    log.info("  RESULT: %s", result_str)

                # Report result back
                try:
                    result_data = json.dumps({"command_id": cmd_id, "result": result}).encode()
                    req2 = urlreq.Request(
                        f"{SERVER_URL}/api/v2/agent/result",
                        data=result_data,
                        headers=_guard_headers(),
                    )
                    urlreq.urlopen(req2, timeout=15)
                    log.info("  → reported to server")
                except Exception as e:
                    log.warning("Failed to report result for #%s: %s", cmd_id, e)

        except Exception as e:
            if "404" not in str(e) and "connection" not in str(e).lower():
                log.warning("Command poll error: %s", e)

        await asyncio.sleep(10)  #CC- v1.8.0: zrychleno z 60s na 10s — onboarding interactivity


async def _execute_command(command, payload):
    """Execute a command from MCP server."""
    payload = payload or {}

    if command == "scan_network":
        devices = await asyncio.to_thread(_arp_scan)
        return {"devices": devices, "count": len(devices)}

    elif command == "read_file":
        path = payload.get("path", "")
        full = _safe_path(path)
        if not full or not full.is_file():
            return {"error": "file not found"}
        return {"content": full.read_text(encoding="utf-8"), "size": full.stat().st_size}

    elif command == "write_file":
        path = payload.get("path", "")
        content = payload.get("content", "")
        full = _safe_path(path)
        if not full:
            return {"error": "invalid path"}
        if full.exists():
            backup = full.with_suffix(full.suffix + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}")
            backup.write_text(full.read_text(encoding="utf-8"), encoding="utf-8")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path, "size": len(content)}

    elif command == "shell_exec":
        cmd = payload.get("command", "")
        timeout = min(payload.get("timeout", 30), 120)
        result = await asyncio.to_thread(
            subprocess.run, cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return {"exit_code": result.returncode, "stdout": result.stdout[-10000:], "stderr": result.stderr[-5000:]}

    elif command == "supervisor_get":
        path = payload.get("path", "supervisor/info")
        return await _supervisor_cmd("GET", path)

    elif command == "supervisor_post":
        path = payload.get("path", "")
        body = payload.get("body")
        return await _supervisor_cmd("POST", path, body)

    elif command == "ha_states":
        states = await _get_ha_states()
        if states:
            summary = {}
            for s in states:
                eid = s.get("entity_id", "")
                if s.get("state") not in ("unavailable", "unknown"):
                    summary[eid] = s.get("state")
            return {"entity_count": len(summary), "states": summary}
        return {"error": "no states"}

    elif command == "ha_call_service":
        domain = payload.get("domain", "")
        service = payload.get("service", "")
        data = payload.get("data", {})
        return await _ha_service_call(domain, service, data)

    elif command == "restart_ha":
        return await _supervisor_cmd("POST", "core/restart")

    elif command == "list_addons":
        return await _supervisor_cmd("GET", "addons")

    elif command == "install_addon":
        slug = payload.get("slug", "")
        return await _supervisor_cmd("POST", f"store/addons/{slug}/install")

    elif command == "update_addon":
        slug = payload.get("slug", "")
        return await _supervisor_cmd("POST", f"addons/{slug}/update")

    elif command == "restart_addon":
        slug = payload.get("slug", "")
        return await _supervisor_cmd("POST", f"addons/{slug}/restart")

    elif command == "refresh_store":
        return await _supervisor_cmd("POST", "store/reload")

    elif command == "get_network_info":
        return await _supervisor_cmd("GET", "network/info")

    elif command == "get_host_info":
        return await _supervisor_cmd("GET", "host/info")

    elif command == "install_cloudflared":
        #CC- Sprint E (2026-04-21): T2 auto-provisioning. McpHomeServer enqueue tento command po ProvisionAsync.
        #CC- Payload: { tunnel_id, tunnel_name, hostname, tunnel_token, [repo_url], [addon_slug] }.
        #CC- Postup: 0) ensure community repo (brenner-tobias/ha-addons) je v Add-on Store,
        #CC-          1) zaloha tokenu do /share/guard/cloudflared-token.json (recovery),
        #CC-          2) supervisor install Cloudflared addonu,
        #CC-          3) addon options {external_hostname, tunnel_token, additional_hosts:[]},
        #CC-          4) start addonu.
        #CC- Idempotentni: pokud addon uz bezi se stejnym tokenem, jen restart.
        token = payload.get("tunnel_token", "")
        hostname = payload.get("hostname", "")
        tunnel_id = payload.get("tunnel_id", "")
        if not token or not hostname:
            return {"error": "missing tunnel_token or hostname in payload"}

        #CC- Recovery backup (token je sensitive — restrictive perms)
        try:
            from pathlib import Path as _P
            backup_dir = _P("/share/guard")
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_file = backup_dir / "cloudflared-token.json"
            backup_file.write_text(json.dumps({
                "tunnel_id": tunnel_id,
                "hostname": hostname,
                "tunnel_token": token,
                "saved_at": datetime.now().isoformat(),
            }), encoding="utf-8")
            try: backup_file.chmod(0o600)
            except: pass
            log.info("  install_cloudflared: backup saved to %s", backup_file)
        except Exception as e:
            log.warning("  install_cloudflared: backup failed: %s", e)

        #CC- Slug pro Cloudflared addon — community repo brenner-tobias/ha-addons.
        addon_slug = payload.get("addon_slug", "a0d7b954_cloudflared")
        repo_url = payload.get("repo_url", "https://github.com/brenner-tobias/ha-addons")

        #CC- Step 0 (1.6.1): ensure community repo přidaný a addon dostupný.
        #CC- Bez tohoto kroku má čerstvá HA instalace addon_slug=404 → install fail.
        #CC- Idempotentni: list repositories, pokud chybí → POST + reload + krátký wait.
        repo_added = False
        try:
            store_resp = await _supervisor_cmd("GET", "store")
            existing_repos = []
            store_data = (store_resp.get("data") or {}) if isinstance(store_resp, dict) else {}
            for r in store_data.get("repositories", []):
                src = (r.get("source") or "").rstrip("/").lower()
                if src:
                    existing_repos.append(src)
            need_add = repo_url.rstrip("/").lower() not in existing_repos
            if need_add:
                log.info("  install_cloudflared: community repo missing, adding %s", repo_url)
                add_resp = await _supervisor_cmd("POST", "store/repositories", {"repository": repo_url})
                log.info("  install_cloudflared: repo add response: %s", json.dumps(add_resp)[:200])
                repo_added = True
                #CC- Reload aby se addon objevil v store
                reload_resp = await _supervisor_cmd("POST", "store/reload")
                log.info("  install_cloudflared: store reload: %s", json.dumps(reload_resp)[:200])
                #CC- Krátký wait — store reload je async v Supervisoru, addon list se aktualizuje s prodlevou
                await asyncio.sleep(8)
            else:
                log.info("  install_cloudflared: community repo already present")
        except Exception as e:
            log.warning("  install_cloudflared: repo check/add failed: %s — pokračuji s install (může selhat)", e)

        #CC- Step 1: install (idempotentni — pokud uz instalovany, vrati 400 ktere ignorujeme)
        install_resp = await _supervisor_cmd("POST", f"store/addons/{addon_slug}/install")
        log.info("  install_cloudflared: install response: %s", json.dumps(install_resp)[:200])

        #CC- Step 2: set options s tokenem + hostname
        options_resp = await _supervisor_cmd("POST", f"addons/{addon_slug}/options", {
            "options": {
                "external_hostname": hostname,
                "tunnel_token": token,
                "additional_hosts": [],
                "nginx_proxy_manager": False,
                "data_folder": "addon_configs/a0d7b954_cloudflared"
            }
        })
        log.info("  install_cloudflared: options response: %s", json.dumps(options_resp)[:200])

        #CC- Step 3: start (nebo restart pokud uz bezel)
        start_resp = await _supervisor_cmd("POST", f"addons/{addon_slug}/restart")
        log.info("  install_cloudflared: restart response: %s", json.dumps(start_resp)[:200])

        return {
            "ok": True,
            "addon_slug": addon_slug,
            "hostname": hostname,
            "tunnel_id": tunnel_id,
            "install": install_resp.get("result", install_resp),
            "options": options_resp.get("result", options_resp),
            "restart": start_resp.get("result", start_resp),
        }

    elif command == "verify_entity_states":
        #CC- Read specific entity states for cross-layer verification (AutomationHealthService)
        entity_ids = payload.get("entity_ids", [])
        states = await _get_ha_states()
        if not states:
            return {"error": "no HA states"}
        lookup = {s["entity_id"]: s.get("state") for s in states
                  if s.get("state") not in ("unavailable", "unknown")}
        result = {}
        for eid in entity_ids:
            result[eid] = lookup.get(eid, "not_found")
        return {"states": result, "entity_count": len(result)}

    else:
        return {"error": f"unknown command: {command}"}


async def _supervisor_cmd(method, path, body=None):
    if not SUPERVISOR_TOKEN:
        log.error("  _supervisor_cmd: NO SUPERVISOR_TOKEN!")
        return {"error": "no supervisor token"}
    url = f"{SUPERVISOR_URL}/{path}"
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"}
    log.info("  _supervisor_cmd: %s %s body=%s", method, url, json.dumps(body)[:300] if body else "(none)")
    async with ClientSession() as session:
        try:
            if method == "GET":
                async with session.get(url, headers=headers) as resp:
                    data = await resp.json()
                    log.info("  _supervisor_cmd: HTTP %s response=%s", resp.status, json.dumps(data, ensure_ascii=False)[:500])
                    return data
            else:
                async with session.post(url, headers=headers, data=json.dumps(body) if body else None) as resp:
                    data = await resp.json()
                    log.info("  _supervisor_cmd: HTTP %s response=%s", resp.status, json.dumps(data, ensure_ascii=False)[:500])
                    return data
        except Exception as e:
            log.error("  _supervisor_cmd: EXCEPTION %s", e)
            return {"error": str(e)}


async def _ha_service_call(domain, service, data):
    if not SUPERVISOR_TOKEN:
        return {"error": "no supervisor token"}
    url = f"{SUPERVISOR_URL}/core/api/services/{domain}/{service}"
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"}
    async with ClientSession() as session:
        try:
            async with session.post(url, headers=headers, data=json.dumps(data)) as resp:
                return {"status": resp.status, "ok": resp.status == 200}
        except Exception as e:
            return {"error": str(e)}


# ── App setup ──
def create_app():
    app = web.Application(middlewares=[auth_middleware])

    # Health
    app.router.add_get("/api/health", handle_health)

    # Network scanning
    app.router.add_get("/api/scan/arp", handle_scan_arp)
    app.router.add_get("/api/scan/tuya", handle_scan_tuya)
    app.router.add_post("/api/scan/ping", handle_scan_ping)
    app.router.add_get("/api/scan/full", handle_scan_full)

    # File management
    app.router.add_get("/api/files/list", handle_files_list)
    app.router.add_get("/api/files/read", handle_files_read)
    app.router.add_post("/api/files/write", handle_files_write)

    # Shell execution
    app.router.add_post("/api/shell/exec", handle_shell_exec)

    # Supervisor API proxy
    app.router.add_get("/api/supervisor/{path:.*}", handle_supervisor_get)
    app.router.add_post("/api/supervisor/{path:.*}", handle_supervisor_post)

    # HA Core API proxy
    app.router.add_get("/api/ha/{path:.*}", handle_ha_get)
    app.router.add_post("/api/ha/{path:.*}", handle_ha_post)

    # Telemetry
    app.router.add_post("/api/telemetry/push", handle_telemetry_push)

    return app


def _fetch_key_entities():
    """Fetch KeyEntitiesJson from Guard server. Sync, runs in thread."""
    global KEY_ENTITIES
    if not API_KEY or not SERVER_URL:
        return
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{SERVER_URL}/api/v2/telemetry/config",
            headers=_guard_headers(),
        )
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        ke = data.get("key_entities") or {}
        if ke:
            KEY_ENTITIES.update(ke)
            log.info("KeyEntities loaded: %s", {k: v.split(".")[-1] for k, v in ke.items()})
        else:
            log.info("No KeyEntities configured on server, using pattern matching")
    except Exception as e:
        log.warning("Failed to fetch KeyEntities: %s (will use pattern matching)", e)


async def _enroll_once():
    """
    M3 (2026-05-01) — one-shot bidirectional enrollment.
    Agent at first start mints HA LLAT via Supervisor proxy and pushes
    {ha_url, ha_token, ha_version, install_type, agent_version, hostname,
     local_ip, timezone} to MCP /api/agent/{apiKey}/enroll.

    Idempotent via /data/enrolled.json sentinel. Fail-soft: any error is logged
    and retried on next addon restart — never blocks startup.
    Hard timeout: every step has a per-call timeout, total bounded ~30s.
    """
    if not API_KEY or not SERVER_URL or not SUPERVISOR_TOKEN:
        log.info("Enroll: skipped (missing API_KEY / SERVER_URL / SUPERVISOR_TOKEN)")
        return

    #CC- Sentinel — skip if enrolled within last 7d (re-enrolls weekly to refresh metadata)
    try:
        if os.path.exists(ENROLL_SENTINEL):
            sent = json.loads(open(ENROLL_SENTINEL, "r", encoding="utf-8").read())
            ts = datetime.fromisoformat(sent.get("enrolled_at", "1970-01-01T00:00:00"))
            if datetime.now() - ts < timedelta(days=7):
                log.info("Enroll: already done at %s, skipping (sentinel)", ts.isoformat())
                return
    except Exception as e:
        log.warning("Enroll: sentinel read failed (%s), proceeding", e)

    headers_sup = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}",
                   "Content-Type": "application/json"}

    try:
        async with ClientSession(timeout=__import__("aiohttp").ClientTimeout(total=20)) as session:
            #CC- 1) HA config — external_url / internal_url / version / time_zone
            ha_url = None
            ha_version = None
            timezone = None
            try:
                async with session.get(f"{SUPERVISOR_URL}/core/api/config", headers=headers_sup) as r:
                    if r.status == 200:
                        cfg = await r.json()
                        ha_url = (cfg.get("external_url") or cfg.get("internal_url") or "").rstrip("/")
                        ha_version = cfg.get("version")
                        timezone = cfg.get("time_zone")
            except Exception as e:
                log.warning("Enroll: /core/api/config failed: %s", e)

            #CC- 2) Host info — hostname, local IP
            hostname = None
            local_ip = None
            try:
                async with session.get(f"{SUPERVISOR_URL}/host/info", headers=headers_sup) as r:
                    if r.status == 200:
                        d = (await r.json()).get("data", {})
                        hostname = d.get("hostname")
            except Exception as e:
                log.warning("Enroll: /host/info failed: %s", e)
            try:
                async with session.get(f"{SUPERVISOR_URL}/network/info", headers=headers_sup) as r:
                    if r.status == 200:
                        d = (await r.json()).get("data", {})
                        for iface in d.get("interfaces", []):
                            if iface.get("primary"):
                                ipv4 = iface.get("ipv4") or {}
                                addrs = ipv4.get("address") or []
                                if addrs:
                                    local_ip = str(addrs[0]).split("/")[0]
                                    break
            except Exception as e:
                log.warning("Enroll: /network/info failed: %s", e)

            #CC- 3) install_type — supervisor /info → "supervisor.host" + "supervisor"."channel"
            install_type = None
            try:
                async with session.get(f"{SUPERVISOR_URL}/info", headers=headers_sup) as r:
                    if r.status == 200:
                        d = (await r.json()).get("data", {})
                        #CC- Možnosti: "Home Assistant OS", "Home Assistant Supervised", "Home Assistant Container", "Home Assistant Core"
                        op = d.get("operating_system") or ""
                        sup = d.get("supervisor") or ""
                        if "Home Assistant OS" in op or sup:
                            install_type = "haos" if "Home Assistant OS" in op else "supervised"
                        else:
                            install_type = "container"
            except Exception as e:
                log.warning("Enroll: /info failed: %s", e)
            if not install_type:
                install_type = "haos"  #CC- safe default for addon context (always has supervisor)

            if not ha_url:
                #CC- Fallback: use Cloudflare hostname if external_url missing — still better than nothing.
                #CC- MCP will reject if it can't be parsed, that's OK (re-enroll next restart).
                log.warning("Enroll: ha_url not detected, MCP enroll will fail — set HA external_url and restart addon")
                return

            #CC- 4) Mint Long-Lived Access Token via Supervisor → Core proxy
            ha_token = None
            try:
                #CC- HA REST API: POST /core/api/auth/long_lived_access_token
                #CC- Lifespan in days, client_name for audit. Proxy uses SUPERVISOR_TOKEN as system user.
                payload = json.dumps({
                    "lifespan": 3650,
                    "client_name": f"Guard Agent {VERSION} ({datetime.now().strftime('%Y-%m-%d')})"
                }).encode()
                async with session.post(
                    f"{SUPERVISOR_URL}/core/api/auth/long_lived_access_token",
                    headers=headers_sup, data=payload
                ) as r:
                    body = await r.text()
                    if r.status == 200:
                        #CC- HA returns either JSON with .token or raw token string — be defensive.
                        try:
                            j = json.loads(body)
                            ha_token = j.get("token") if isinstance(j, dict) else (body if isinstance(j, str) else None)
                            if not ha_token and isinstance(j, str):
                                ha_token = j
                        except Exception:
                            ha_token = body.strip().strip('"')
                    else:
                        log.warning("Enroll: LLAT mint HTTP %s: %s", r.status, body[:300])
            except Exception as e:
                log.warning("Enroll: LLAT mint failed: %s", e)

            if not ha_token:
                log.warning("Enroll: ha_token unavailable, aborting (will retry next start)")
                return

            #CC- 5) POST to MCP /api/agent/{apiKey}/enroll
            enroll_payload = {
                "ha_url": ha_url,
                "ha_token": ha_token,
                "ha_version": ha_version,
                "install_type": install_type,
                "agent_version": VERSION,
                "hostname": hostname,
                "local_ip": local_ip,
                "timezone": timezone,
            }
            try:
                async with session.post(
                    f"{SERVER_URL}/api/v2/agent/enroll",
                    headers=_guard_headers(),
                    data=json.dumps(enroll_payload).encode()
                ) as r:
                    body = await r.text()
                    if r.status == 200:
                        log.info("Enroll: OK — server response: %s", body[:300])
                        try:
                            os.makedirs("/data", exist_ok=True)
                            with open(ENROLL_SENTINEL, "w", encoding="utf-8") as f:
                                json.dump({
                                    "enrolled_at": datetime.now().isoformat(),
                                    "ha_url": ha_url,
                                    "install_type": install_type,
                                    "agent_version": VERSION,
                                }, f)
                        except Exception as e:
                            log.warning("Enroll: sentinel write failed: %s", e)
                    else:
                        log.warning("Enroll: MCP HTTP %s: %s", r.status, body[:300])
            except Exception as e:
                log.warning("Enroll: MCP POST failed: %s", e)
    except Exception as e:
        log.warning("Enroll: outer error %s — agent continues normally", e)


async def main():
    app = create_app()

    #CC- Fetch explicit entity mapping from server before starting telemetry
    await asyncio.to_thread(_fetch_key_entities)

    #CC- M3 (2026-05-01) — one-shot enrollment, hard-bounded, fail-soft
    try:
        await asyncio.wait_for(_enroll_once(), timeout=30)
    except asyncio.TimeoutError:
        log.warning("Enroll: hard timeout 30s — agent continues, will retry next restart")
    except Exception as e:
        log.warning("Enroll: unexpected error %s — agent continues", e)

    # Start background tasks
    asyncio.create_task(scanner_loop())
    asyncio.create_task(command_poll_loop())
    asyncio.create_task(telemetry_loop())

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8300)
    await site.start()

    log.info("Guard Agent v%s running on port 8300", VERSION)
    log.info("API key: %s", "configured" if API_KEY else "NOT SET")
    log.info("Supervisor: %s", "available" if SUPERVISOR_TOKEN else "not available")

    # Keep running
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
