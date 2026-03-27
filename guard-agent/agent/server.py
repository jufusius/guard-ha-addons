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
VERSION = "1.0.0"
START_TIME = datetime.now()


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
async def handle_telemetry_push(request):
    if not API_KEY or not SERVER_URL:
        return web.json_response({"error": "not configured"}, status=400)

    #CC- Sbírat všechny HA entity a poslat jako telemetrii
    try:
        states = await _get_ha_states()
        if not states:
            return web.json_response({"error": "no HA states"}, status=502)

        #CC- Extrahovat klíčové senzory pro telemetrii
        telemetry = {}
        for s in states:
            eid = s.get("entity_id", "")
            state = s.get("state")
            if state in ("unavailable", "unknown"):
                continue
            attrs = s.get("attributes", {})
            try:
                telemetry[eid] = {
                    "state": state,
                    "numeric": float(state) if _is_numeric(state) else None,
                    "unit": attrs.get("unit_of_measurement"),
                    "friendly_name": attrs.get("friendly_name"),
                }
            except:
                telemetry[eid] = {"state": state}

        #CC- Push na Guard server
        payload = json.dumps({
            "timestamp": datetime.utcnow().isoformat(),
            "entity_count": len(telemetry),
            "entities": telemetry,
        }).encode()

        import urllib.request
        req = urllib.request.Request(
            f"{SERVER_URL}/api/telemetry/{API_KEY}",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
        return web.json_response({"ok": True, "entities": len(telemetry), "server_response": result})
    except Exception as e:
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

            log.info("Background scan: %d devices", len(devices))

            # Push to Guard server
            if API_KEY and SERVER_URL:
                try:
                    import urllib.request
                    payload = json.dumps({"devices": devices}).encode()
                    req = urllib.request.Request(
                        f"{SERVER_URL}/api/devices/{API_KEY}",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    resp = urllib.request.urlopen(req, timeout=15)
                    result = json.loads(resp.read())
                    log.info("Guard server: %s", result)
                except Exception as e:
                    log.warning("Guard push failed: %s", e)

        except Exception as e:
            log.error("Scanner loop error: %s", e)

        await asyncio.sleep(SCAN_INTERVAL * 60)


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


async def main():
    app = create_app()

    # Start background scanner
    asyncio.create_task(scanner_loop())

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
