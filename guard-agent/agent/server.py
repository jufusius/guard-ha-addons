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
  /api/files/list      — list files in /homeassistant/ (also command: list_directory)
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
VERSION = "1.9.0"
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

    elif command == "list_directory":
        #CC- v1.8.1: nativní výpis adresáře (dřív jen přes shell_exec ls).
        #CC-   Respektuje _safe_path sandbox (/homeassistant), formát shodný s handle_files_list.
        path = payload.get("path", "/")
        full = _safe_path(path)
        if not full:
            return {"error": "invalid path"}
        if not full.exists():
            return {"error": "not found"}
        if full.is_file():
            st = full.stat()
            return {"type": "file", "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime).isoformat()}
        files = []
        for item in sorted(full.iterdir()):
            st = item.stat()
            files.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": st.st_size if item.is_file() else None,
                "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
            })
        return {"path": path, "files": files, "count": len(files)}

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

    elif command in ("restart_ha", "restart"):
        #CC- v1.8.1: "restart" alias k "restart_ha" — restart HA Core přes Supervisor.
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
        #CC- FIX(a): žádný hardcode default. Slug = hash závislý na repo (reálně 9074a9fa_cloudflared
        #CC-   na HAOS, jinde jiný). Discovery ze store; explicitní payload.addon_slug má přednost (override).
        addon_slug_override = payload.get("addon_slug")  #CC- None pokud nezadán → discovery
        repo_url = payload.get("repo_url", "https://github.com/brenner-tobias/ha-addons")
        auto_restart_ha = bool(payload.get("auto_restart_ha", False))

        #CC- Step 0 (1.6.1): ensure community repo přidaný a addon dostupný.
        #CC- Bez tohoto kroku má čerstvá HA instalace addon slug nedostupný → install fail.
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
            log.warning("  install_cloudflared: repo check/add failed: %s — pokračuji s discovery (může selhat)", e)

        #CC- FIX(a): discovery skutečného slugu ze store/addons (po repo add + reload).
        #CC-   Hledáme addon jehož slug končí na _cloudflared NEBO name/repository obsahuje cloudflared/brenner.
        #CC-   Explicitní payload.addon_slug override má vždy přednost.
        addon_slug = addon_slug_override
        if not addon_slug:
            try:
                store_addons_resp = await _supervisor_cmd("GET", "store/addons")
                addons_list = []
                if isinstance(store_addons_resp, dict):
                    sd = store_addons_resp.get("data")
                    if isinstance(sd, dict):
                        addons_list = sd.get("addons", []) or []
                    elif isinstance(sd, list):
                        addons_list = sd
                    if not addons_list and isinstance(store_addons_resp.get("addons"), list):
                        addons_list = store_addons_resp["addons"]
                for a in addons_list:
                    if not isinstance(a, dict):
                        continue
                    slug = str(a.get("slug", ""))
                    name = str(a.get("name", "")).lower()
                    repo = str(a.get("repository", "")).lower()
                    if slug.endswith("_cloudflared") or "cloudflared" in name \
                            or "cloudflared" in repo or "brenner" in repo or "brenner" in slug.lower():
                        addon_slug = slug
                        log.info("  install_cloudflared: discovered slug=%s (name=%s repo=%s)", slug, name, repo)
                        break
            except Exception as e:
                log.warning("  install_cloudflared: slug discovery failed: %s", e)

        if not addon_slug:
            #CC- FIX(a): nehádej hardcode — bez slugu nemá smysl pokračovat.
            return {"error": "cloudflared addon not found in store after repo add/reload",
                    "ok": False, "repo_added": repo_added}

        #CC- Step 1: install (idempotentni — pokud uz instalovany, vrati 400 ktere ignorujeme)
        install_resp = await _supervisor_cmd("POST", f"store/addons/{addon_slug}/install")
        log.info("  install_cloudflared: install response: %s", json.dumps(install_resp)[:200])

        #CC- Step 2: set options s tokenem + hostname
        #CC- FIX(a): data_folder už NEhardcoduje slug — používá discovered/override slug.
        options_resp = await _supervisor_cmd("POST", f"addons/{addon_slug}/options", {
            "options": {
                "external_hostname": hostname,
                "tunnel_token": token,
                "additional_hosts": [],
                "nginx_proxy_manager": False,
                "data_folder": f"addon_configs/{addon_slug}"
            }
        })
        log.info("  install_cloudflared: options response: %s", json.dumps(options_resp)[:200])

        #CC- Step 3: start (nebo restart pokud uz bezel)
        start_resp = await _supervisor_cmd("POST", f"addons/{addon_slug}/restart")
        log.info("  install_cloudflared: restart response: %s", json.dumps(start_resp)[:200])

        #CC- FIX(a): ověř instalaci — addon MUSÍ být v seznamu nainstalovaných + started/running.
        #CC-   Bez toho hlásíme success naslepo. GET addons = instalované addony.
        installed_verified = False
        addon_state = None
        try:
            installed_resp = await _supervisor_cmd("GET", "addons")
            installed_list = []
            if isinstance(installed_resp, dict):
                idata = installed_resp.get("data")
                if isinstance(idata, dict):
                    installed_list = idata.get("addons", []) or []
                elif isinstance(idata, list):
                    installed_list = idata
            for a in installed_list:
                if isinstance(a, dict) and str(a.get("slug", "")) == addon_slug:
                    addon_state = a.get("state")
                    #CC- installed = v seznamu; state started/running považujeme za běžící
                    installed_verified = str(addon_state).lower() in ("started", "running", "startup") \
                        or a.get("installed") is True or a.get("version") is not None
                    break
        except Exception as e:
            log.warning("  install_cloudflared: install verification failed: %s", e)

        #CC- FIX(b): auto-inject http:/trusted_proxies do configuration.yaml (cloudflared přidává XFF).
        http_config = {"changed": False, "skipped": "not_run"}
        try:
            http_config = await _ensure_http_proxy_config()
        except Exception as e:
            #CC- fail-soft — nikdy neshoď install kvůli config helperu
            http_config = {"changed": False, "error": f"ensure_http_proxy_config raised: {e}"}
        log.info("  install_cloudflared: http_config=%s", json.dumps(http_config, ensure_ascii=False)[:200])

        #CC- FIX(b): HA Core restart NEDĚLÁME automaticky uvnitř (řídí orchestrátor / restart_ha command),
        #CC-   POKUD payload.auto_restart_ha=True — pak restartujeme sami.
        restart_required = bool(http_config.get("restart_required"))
        ha_restart_result = None
        if restart_required and auto_restart_ha:
            log.info("  install_cloudflared: auto_restart_ha=True + restart_required → restarting HA Core")
            ha_restart_result = await _supervisor_cmd("POST", "core/restart")

        result = {
            "ok": bool(installed_verified),
            "addon_slug": addon_slug,
            "slug_source": "override" if addon_slug_override else "discovery",
            "hostname": hostname,
            "tunnel_id": tunnel_id,
            "repo_added": repo_added,
            "installed_verified": installed_verified,
            "addon_state": addon_state,
            "install": install_resp.get("result", install_resp) if isinstance(install_resp, dict) else install_resp,
            "options": options_resp.get("result", options_resp) if isinstance(options_resp, dict) else options_resp,
            "restart": start_resp.get("result", start_resp) if isinstance(start_resp, dict) else start_resp,
            "http_config": http_config,
            "ha_restart_required": restart_required and not auto_restart_ha,
        }
        if ha_restart_result is not None:
            result["ha_restart"] = ha_restart_result
        if not installed_verified:
            #CC- FIX(a): nereportuj success naslepo — addon není mezi nainstalovanými/běžícími.
            result["error"] = "cloudflared addon not found among installed/running addons after install"
        return result

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
                resp_ctx = session.get(url, headers=headers)
            else:
                resp_ctx = session.post(url, headers=headers, data=json.dumps(body) if body else None)
            async with resp_ctx as resp:
                #CC- FIX(d): non-JSON odpovědi (core/check text, prázdné 200) nesmí shodit parse.
                #CC-   content_type=None vypne strict aiohttp check; při selhání fallback na raw text.
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {"raw": await resp.text()}
                #CC- FIX(d): status VŽDY propagovat pod _status (existující callery čtou .get("data")/.get("result") — nerozbíjí se).
                #CC-   Pokud odpověď není dict (list/str/None), zabalit ať caller .get() nespadne.
                if isinstance(data, dict):
                    data["_status"] = resp.status
                else:
                    data = {"data": data, "_status": resp.status}
                log_fn = log.info if 200 <= resp.status < 300 else log.warning
                log_fn("  _supervisor_cmd: HTTP %s response=%s", resp.status, json.dumps(data, ensure_ascii=False)[:500])
                return data
        except Exception as e:
            log.error("  _supervisor_cmd: EXCEPTION %s", e)
            return {"error": str(e)}


#CC- FIX(b): hassio Docker interní síť — cloudflared vždy přidává X-Forwarded-For.
#CC-   Bez use_x_forwarded_for + trusted_proxies vrací HA 400 na každý forwardovaný request.
HASSIO_PROXY_CIDR = "172.30.32.0/23"


async def _ensure_http_proxy_config():
    """
    FIX(b): Zajistí, že /homeassistant/configuration.yaml má
    http.use_x_forwarded_for=true + trusted_proxies s hassio rozsahem (172.30.32.0/23).

    Idempotentní: pokud už nakonfigurováno → {"changed": False}.
    Fail-soft: každá chyba → {"changed": False, "error": ...}, nikdy nevyhodí výjimku ven.
    Bezpečnost: .bak záloha před zápisem, po zápisu core/check; při nevalidním configu ROLLBACK z .bak.

    PyYAML se v addonu NEinstaluje (viz Dockerfile — Alpine bez py3-yaml), proto primárně
    běží konzervativní textová větev: přidá čistý http: blok JEN pokud žádný top-level http:
    neexistuje. Existující http: blok bez proxy klíčů → needitovat naslepo (needs_manual),
    riziko rozbití odsazení. Pokud by PyYAML v budoucnu byl přítomen, použije se bezpečný
    parse→merge→dump.
    """
    cfg_path = _safe_path("configuration.yaml")
    if not cfg_path:
        return {"changed": False, "error": "cannot resolve configuration.yaml path"}
    try:
        if not cfg_path.exists():
            return {"changed": False, "error": "configuration.yaml not found"}
        original = cfg_path.read_text(encoding="utf-8")
    except Exception as e:
        return {"changed": False, "error": f"read failed: {e}"}

    #CC- Idempotence: už nakonfigurováno (proxy flag + hassio rozsah přítomny) → nic nedělej.
    if "use_x_forwarded_for" in original and HASSIO_PROXY_CIDR in original:
        return {"changed": False, "reason": "already configured"}

    #CC- Detekce top-level http: bloku (na začátku řádku, ne odsazený, ne komentář).
    import re
    has_http_block = bool(re.search(r"(?m)^http:\s*(#.*)?$", original))

    #CC- Zkus PyYAML (v addonu default NENÍ — fail-soft na textovou větev).
    yaml_mod = None
    try:
        import yaml as _yaml  #CC- není v Dockerfile → typicky ImportError, spadne do textové větve
        yaml_mod = _yaml
    except Exception:
        yaml_mod = None

    new_content = None
    if yaml_mod is not None:
        #CC- Bezpečný parse→merge→dump (jen pokud PyYAML dostupný).
        try:
            doc = yaml_mod.safe_load(original) or {}
            if not isinstance(doc, dict):
                return {"changed": False, "error": "configuration.yaml root is not a mapping"}
            http_block = doc.get("http")
            if http_block is None or not isinstance(http_block, dict):
                http_block = {}
            http_block["use_x_forwarded_for"] = True
            tp = http_block.get("trusted_proxies")
            if not isinstance(tp, list):
                tp = []
            if HASSIO_PROXY_CIDR not in tp:
                tp.append(HASSIO_PROXY_CIDR)
            http_block["trusted_proxies"] = tp
            doc["http"] = http_block
            new_content = yaml_mod.safe_dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True)
        except Exception as e:
            return {"changed": False, "error": f"yaml merge failed: {e}"}
    else:
        #CC- Konzervativní textová větev (bez PyYAML).
        if has_http_block:
            #CC- http: existuje bez proxy klíčů → needitovat naslepo (riziko odsazení).
            log.warning("  _ensure_http_proxy_config: existing http: block without proxy keys — manual merge required")
            return {"changed": False, "needs_manual": True,
                    "reason": "existing http: block, manual merge required"}
        #CC- Žádný http: blok → připoj čistý blok na konec.
        suffix = "" if original.endswith("\n") or original == "" else "\n"
        block = (
            "\nhttp:\n"
            "  use_x_forwarded_for: true\n"
            "  trusted_proxies:\n"
            f"    - {HASSIO_PROXY_CIDR}\n"
        )
        new_content = original + suffix + block

    if new_content is None or new_content == original:
        return {"changed": False, "reason": "no change produced"}

    #CC- .bak záloha (stejný vzor jako handle_files_write) PŘED zápisem.
    backup_path = None
    try:
        backup_path = cfg_path.with_suffix(cfg_path.suffix + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}")
        backup_path.write_text(original, encoding="utf-8")
    except Exception as e:
        return {"changed": False, "error": f"backup failed: {e}"}

    #CC- Zápis.
    try:
        cfg_path.write_text(new_content, encoding="utf-8")
        log.info("  _ensure_http_proxy_config: http block written, running core/check")
    except Exception as e:
        return {"changed": False, "error": f"write failed: {e}"}

    #CC- Config check přes Supervisor → HA core.
    check_resp = await _supervisor_cmd("POST", "core/check")
    #CC- HA core/check: valid => {"result":"ok"} (nebo _status 200). Neúspěch => rollback.
    check_ok = False
    if isinstance(check_resp, dict):
        status = check_resp.get("_status")
        result = str(check_resp.get("result", "")).lower()
        raw = str(check_resp.get("raw", "")).lower()
        if result == "ok":
            check_ok = True
        elif (status is None or (200 <= status < 300)) and not check_resp.get("error") \
                and "error" not in result and "invalid" not in raw and "error" not in raw:
            #CC- Prázdná/textová 2xx odpověď bez chybových markerů bereme jako valid.
            check_ok = True

    if not check_ok:
        #CC- ROLLBACK — nikdy nenechat nevalidní config.
        try:
            cfg_path.write_text(original, encoding="utf-8")
            log.warning("  _ensure_http_proxy_config: config check FAILED — rolled back from original")
        except Exception as e:
            log.error("  _ensure_http_proxy_config: ROLLBACK FAILED: %s (backup at %s)", e, backup_path)
        return {"changed": False, "error": "config check failed", "check": check_resp}

    return {"changed": True, "restart_required": True}


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


import secrets as _secrets

SERVICE_ACCOUNT_BACKUP = "/share/guard/ha-service-account.json"


async def _ws_recv_json(ws):
    """Receive one JSON message from an aiohttp WS, tolerant of frame types."""
    msg = await ws.receive()
    from aiohttp import WSMsgType
    if msg.type == WSMsgType.TEXT:
        return json.loads(msg.data)
    if msg.type == WSMsgType.BINARY:
        return json.loads(msg.data.decode("utf-8"))
    #CC- CLOSE/ERROR/CLOSED → vrať sentinel, caller pozná podle chybějícího "type"
    return {"type": "_ws_closed", "_frame": str(msg.type)}


async def _ws_auth(ws, access_token):
    """
    HA WebSocket auth handshake: čekej auth_required → pošli auth → čekej auth_ok.
    Vrací True při úspěchu, jinak False. Fail-soft.
    """
    try:
        first = await _ws_recv_json(ws)
        if first.get("type") != "auth_required":
            #CC- Některé verze pošlou rovnou auth_ok/auth_invalid; zkusíme přesto poslat auth
            log.warning("  _ws_auth: unexpected first frame: %s", str(first)[:120])
        await ws.send_str(json.dumps({"type": "auth", "access_token": access_token}))
        resp = await _ws_recv_json(ws)
        if resp.get("type") == "auth_ok":
            return True
        log.warning("  _ws_auth: auth failed: %s", str(resp)[:160])
        return False
    except Exception as e:
        log.warning("  _ws_auth: exception %s", e)
        return False


async def _ws_command(ws, msg_id, payload):
    """Send a WS command with id, wait for the matching result frame. Returns dict or None."""
    try:
        frame = {"id": msg_id, **payload}
        await ws.send_str(json.dumps(frame))
        #CC- Čekej na frame se stejným id (přeskoč event/ping frames), bounded počet iterací.
        for _ in range(20):
            resp = await _ws_recv_json(ws)
            if resp.get("type") == "_ws_closed":
                return None
            if resp.get("id") == msg_id:
                return resp
        return None
    except Exception as e:
        log.warning("  _ws_command(%s): exception %s", payload.get("type"), e)
        return None


async def _mint_llat_via_service_account(session, ha_url):
    """
    FIX(c): Vytvoří LLAT přes dedikovaný servisní účet "Guard" (username `guard`).

    Původní REST mint (POST core/api/auth/long_lived_access_token se SUPERVISOR_TOKEN)
    nefunguje — systémový user "Supervisor" nesmí vlastnit LLAT (404 / rejected).

    Ověřený flow (ručně prošel při onboardingu Libora):
      1. WS auth SUPERVISOR_TOKENem na ws://supervisor/core/websocket (má admin práva).
      2. config/auth/list — idempotence: pokud `guard` existuje, přeskoč create.
      3. config/auth/create name=Guard → user.id; provider/homeassistant/create username+heslo;
         config/auth/update group_ids=[system-admin].
      4. login_flow → login_flow/{flow_id} → auth/token (authorization_code) → user access_token.
      5. NOVÝ WS auth tím user tokenem → auth/long_lived_access_token → LLAT string.
      6. Recovery: heslo+LLAT do /share/guard/ha-service-account.json (0o600).

    Fail-soft: jakákoli chyba → None (caller pak zkusí starý REST mint). Bounded ~stávající timeout.
    Vrací LLAT string nebo None.
    """
    ws_url = f"{SUPERVISOR_URL}/core/websocket"
    password = _secrets.token_urlsafe(32)
    msg_id = 1

    #CC- Krok 1-3: admin WS (supervisor token) — najdi/vytvoř servisní účet.
    user_id = None
    account_existed = False
    try:
        async with session.ws_connect(ws_url, heartbeat=None) as ws:
            if not await _ws_auth(ws, SUPERVISOR_TOKEN):
                log.warning("  _mint_llat_via_service_account: admin WS auth failed")
                return None

            #CC- 2) idempotence — existuje user 'guard'?
            listed = await _ws_command(ws, msg_id, {"type": "config/auth/list"}); msg_id += 1
            if listed and listed.get("success"):
                for u in listed.get("result", []) or []:
                    #CC- provider homeassistant username je v credentials; jméno "Guard" je v u.name
                    if str(u.get("name", "")).lower() == "guard":
                        user_id = u.get("id")
                        account_existed = True
                        log.info("  _mint_llat_via_service_account: existing 'Guard' account id=%s", user_id)
                        break

            #CC- 3) create pokud neexistuje
            if not user_id:
                created = await _ws_command(ws, msg_id, {"type": "config/auth/create", "name": "Guard"}); msg_id += 1
                if not (created and created.get("success")):
                    log.warning("  _mint_llat_via_service_account: auth/create failed: %s", str(created)[:160])
                    return None
                user_id = (created.get("result") or {}).get("user", {}).get("id") \
                    or (created.get("result") or {}).get("id")
                if not user_id:
                    log.warning("  _mint_llat_via_service_account: no user.id in create result")
                    return None
                #CC- provider homeassistant credentials (username+password)
                cred = await _ws_command(ws, msg_id, {
                    "type": "config/auth/provider/homeassistant/create",
                    "user_id": user_id, "username": "guard", "password": password,
                }); msg_id += 1
                if not (cred and cred.get("success")):
                    log.warning("  _mint_llat_via_service_account: provider create failed: %s", str(cred)[:160])
                    return None
                #CC- admin group
                upd = await _ws_command(ws, msg_id, {
                    "type": "config/auth/update", "user_id": user_id, "group_ids": ["system-admin"],
                }); msg_id += 1
                if not (upd and upd.get("success")):
                    log.warning("  _mint_llat_via_service_account: group update non-fatal: %s", str(upd)[:160])
    except Exception as e:
        log.warning("  _mint_llat_via_service_account: admin WS phase failed: %s", e)
        return None

    if account_existed:
        #CC- Účet už existoval → heslo neznáme (uloženo jen při create). Bez hesla nedokážeme login flow.
        #CC-   Zkus recovery soubor; pokud tam heslo je, použij ho. Jinak fail → caller fallback.
        try:
            from pathlib import Path as _P
            bf = _P(SERVICE_ACCOUNT_BACKUP)
            if bf.exists():
                saved = json.loads(bf.read_text(encoding="utf-8"))
                #CC- Pokud už máme uložený platný LLAT, rovnou ho vrať (nejlevnější idempotence).
                if saved.get("llat"):
                    log.info("  _mint_llat_via_service_account: reusing LLAT from recovery backup")
                    return saved["llat"]
                if saved.get("password"):
                    password = saved["password"]
                else:
                    log.warning("  _mint_llat_via_service_account: 'Guard' exists but no stored password/LLAT — cannot login")
                    return None
            else:
                log.warning("  _mint_llat_via_service_account: 'Guard' exists but no recovery backup — cannot login")
                return None
        except Exception as e:
            log.warning("  _mint_llat_via_service_account: recovery read failed: %s", e)
            return None

    #CC- Krok 4: login jako reálný user přes HA core auth (proxováno Supervisorem pod core/...).
    #CC-   client_id MUSÍ být URL s koncovým '/'. Cesty ověřit při deploji (viz report — nejistota).
    client_id = (ha_url.rstrip("/") + "/")
    user_token = None
    try:
        base = f"{SUPERVISOR_URL}/core"
        hdr = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"}
        #CC- 4a) login_flow start
        async with session.post(f"{base}/auth/login_flow", headers=hdr, data=json.dumps({
            "client_id": client_id, "handler": ["homeassistant", None], "redirect_uri": client_id,
        })) as r:
            lf = await r.json(content_type=None) if r.status == 200 else None
        flow_id = (lf or {}).get("flow_id")
        if not flow_id:
            log.warning("  _mint_llat_via_service_account: login_flow start failed (status)")
            return None
        #CC- 4b) submit credentials
        async with session.post(f"{base}/auth/login_flow/{flow_id}", headers=hdr, data=json.dumps({
            "username": "guard", "password": password, "client_id": client_id,
        })) as r:
            step = await r.json(content_type=None) if r.status == 200 else None
        code = (step or {}).get("result")
        if not code or (step or {}).get("type") != "create_entry":
            log.warning("  _mint_llat_via_service_account: login_flow submit no code: %s", str(step)[:160])
            return None
        #CC- 4c) exchange code → token (form-urlencoded)
        form = f"grant_type=authorization_code&code={code}&client_id={client_id}"
        async with session.post(f"{base}/auth/token", headers={
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/x-www-form-urlencoded",
        }, data=form) as r:
            tok = await r.json(content_type=None) if r.status == 200 else None
        user_token = (tok or {}).get("access_token")
        if not user_token:
            log.warning("  _mint_llat_via_service_account: token exchange failed: %s", str(tok)[:120])
            return None
    except Exception as e:
        log.warning("  _mint_llat_via_service_account: login flow failed: %s", e)
        return None

    #CC- Krok 5: NOVÝ WS auth user tokenem → mint LLAT jako ten user.
    llat = None
    try:
        async with session.ws_connect(ws_url, heartbeat=None) as ws2:
            if not await _ws_auth(ws2, user_token):
                log.warning("  _mint_llat_via_service_account: user WS auth failed")
                return None
            res = await _ws_command(ws2, 1, {
                "type": "auth/long_lived_access_token",
                "client_name": f"Guard Agent {VERSION}",
                "lifespan": 3650,
            })
            if res and res.get("success"):
                llat = res.get("result")
            else:
                log.warning("  _mint_llat_via_service_account: LLAT mint failed: %s", str(res)[:160])
                return None
    except Exception as e:
        log.warning("  _mint_llat_via_service_account: user WS phase failed: %s", e)
        return None

    if not llat:
        return None

    #CC- Krok 6: recovery backup (heslo + LLAT), restrictive perms 0o600. NEPÍŠEME do DevSecrets.
    try:
        from pathlib import Path as _P
        bdir = _P("/share/guard")
        bdir.mkdir(parents=True, exist_ok=True)
        bf = bdir / "ha-service-account.json"
        bf.write_text(json.dumps({
            "username": "guard",
            "password": password,
            "llat": llat,
            "user_id": user_id,
            "saved_at": datetime.now().isoformat(),
            "agent_version": VERSION,
        }), encoding="utf-8")
        try: bf.chmod(0o600)
        except Exception: pass
        log.info("  _mint_llat_via_service_account: recovery backup saved to %s", bf)
    except Exception as e:
        log.warning("  _mint_llat_via_service_account: recovery backup failed: %s", e)

    return llat


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

            #CC- 4) Mint Long-Lived Access Token
            ha_token = None

            #CC- FIX(c): PRIMÁRNÍ cesta — servisní účet "Guard" přes WS + login flow.
            #CC-   Ověřeno ručně u Libora; starý REST mint (níže) nechán jako fallback.
            try:
                ha_token = await _mint_llat_via_service_account(session, ha_url)
                if ha_token:
                    log.info("Enroll: LLAT minted via service account 'Guard'")
            except Exception as e:
                log.warning("Enroll: service-account LLAT mint raised: %s", e)

            #CC- Fallback: starý REST mint (POST core/api/auth/long_lived_access_token se SUPERVISOR_TOKEN).
            #CC-   Pravděpodobně nefunguje (systémový user Supervisor nesmí vlastnit LLAT), ale zkusíme.
            if not ha_token:
                try:
                    log.info("Enroll: service-account mint unavailable → trying legacy REST mint")
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
                            log.warning("Enroll: legacy LLAT mint HTTP %s: %s", r.status, body[:300])
                except Exception as e:
                    log.warning("Enroll: legacy LLAT mint failed: %s", e)

            if not ha_token:
                log.warning("Enroll: ha_token unavailable (service account + legacy both failed), aborting (will retry next start)")
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

    #CC- Charge Servo (real-time regulátor, SHADOW only) — drží grid_power na cíli
    #CC- z desired-state pomocí go-e amp + battery_max_charging_current. V shadow jen loguje.
    try:
        from regulators.charge_servo import charge_servo_loop
        asyncio.create_task(charge_servo_loop())
    except Exception as e:
        log.warning("Charge servo not started: %s", e)

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
