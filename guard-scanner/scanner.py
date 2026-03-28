#!/usr/bin/env python3
"""
Guard Network Scanner — HA Addon version
Scans local network, discovers devices, reports to Guard IoT server.
Config via environment variables (set by run.sh from HA addon options).
"""

import json
import os
import sys
import time
import signal
import socket
import hashlib
import logging
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# ── Logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("guard-scanner")

# ── Configuration from environment ─────────────────────────
API_KEY = os.environ.get("GUARD_API_KEY", "")
SERVER_URL = os.environ.get("GUARD_SERVER_URL", "https://mcp.jufusi.us")
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL_MINUTES", "30"))
TUYA_SCAN = os.environ.get("TUYA_SCAN_ENABLED", "true").lower() in ("true", "1", "yes")
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8201"))
EXCLUDE_NETWORKS = ["172.", "10."]

# ── Global state ───────────────────────────────────────────
_running = True
_last_scan = None
_last_result = None
_scan_count = 0
_error_count = 0
_start_time = datetime.now()


def signal_handler(sig, frame):
    global _running
    log.info("Shutting down...")
    _running = False

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# ── ARP scan ───────────────────────────────────────────────
def arp_scan():
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
                if any(ip.startswith(net) for net in EXCLUDE_NETWORKS):
                    continue
                hostname = None
                try:
                    hostname = socket.gethostbyaddr(ip)[0]
                except (socket.herror, socket.gaierror, OSError):
                    pass
                dev = {"mac": mac, "ip": ip}
                if hostname:
                    dev["hostname"] = hostname
                devices.append(dev)
    except FileNotFoundError:
        import subprocess
        import re
        try:
            output = subprocess.check_output(["arp", "-a"], timeout=10, text=True)
            for line in output.split("\n"):
                m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-fA-F:]+)", line)
                if m:
                    devices.append({"mac": m.group(2).upper(), "ip": m.group(1)})
        except Exception:
            pass
    return devices


# ── Tuya UDP discovery ─────────────────────────────────────
def tuya_udp_scan(timeout=5):
    found = {}
    udp_key = hashlib.md5(b"yGAdlopoPVldABfn").digest()

    for port in [6666, 6667]:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", port))
            sock.settimeout(timeout)
            end = time.time() + timeout

            while time.time() < end:
                try:
                    data, addr = sock.recvfrom(4096)
                    ip = addr[0]
                    if ip in found:
                        continue

                    device_id = None
                    if port == 6666:
                        try:
                            idx = data.index(b"{")
                            payload = data[idx:data.rindex(b"}") + 1]
                            j = json.loads(payload)
                            device_id = j.get("gwId")
                        except Exception:
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
                        except ImportError:
                            log.warning("pycryptodome not available — Tuya 3.3+ devices won't be identified")
                        except Exception:
                            pass

                    if device_id:
                        found[ip] = device_id
                except socket.timeout:
                    break
            sock.close()
        except Exception as e:
            log.debug("Tuya scan port %d: %s", port, e)

    return found


# ── Guard server communication ─────────────────────────────
def send_to_guard(devices):
    url = f"{SERVER_URL}/api/devices/{API_KEY}"
    payload = json.dumps({"devices": devices}).encode("utf-8")

    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "GuardScanner/1.1"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=15)
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:200]
            log.warning("Guard API HTTP %d (attempt %d): %s", e.code, attempt + 1, body)
            if e.code == 401:
                return {"error": "Invalid API key — check addon configuration"}
            if attempt < 2:
                time.sleep(2 ** attempt)
        except Exception as e:
            log.warning("Guard API error (attempt %d): %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2 ** attempt)

    return {"error": "All retries failed"}


# ── Health endpoint ────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = {
            "status": "running",
            "addon": "guard-scanner",
            "version": "1.0.0",
            "last_scan": _last_scan.isoformat() if _last_scan else None,
            "last_result": _last_result,
            "scan_count": _scan_count,
            "error_count": _error_count,
            "uptime_minutes": round((datetime.now() - _start_time).total_seconds() / 60, 1),
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(status).encode())

    def log_message(self, format, *args):
        pass


def start_health_server():
    try:
        server = HTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
        server.timeout = 1
        t = threading.Thread(target=lambda: _serve(server), daemon=True)
        t.start()
        log.info("Health endpoint on port %d", HEALTH_PORT)
    except Exception as e:
        log.warning("Health server failed: %s", e)

def _serve(server):
    while _running:
        server.handle_request()


# ── Main loop ──────────────────────────────────────────────
def main():
    global _last_scan, _last_result, _scan_count, _error_count

    if not API_KEY:
        log.error("GUARD_API_KEY not set! Configure in addon settings.")
        sys.exit(1)

    log.info("Guard Scanner v1.0.0 — interval %dm, server %s, tuya=%s",
             SCAN_INTERVAL, SERVER_URL, TUYA_SCAN)

    start_health_server()
    time.sleep(10)  # let network settle

    while _running:
        try:
            devices = arp_scan()
            log.info("ARP: %d devices", len(devices))

            if TUYA_SCAN:
                try:
                    tuya = tuya_udp_scan(timeout=5)
                    for dev in devices:
                        if dev["ip"] in tuya:
                            dev["tuya_device_id"] = tuya[dev["ip"]]
                    log.info("Tuya: %d identified", len(tuya))
                except Exception as e:
                    log.warning("Tuya scan: %s", e)

            result = send_to_guard(devices)
            _last_scan = datetime.now()
            _last_result = result
            _scan_count += 1

            if "error" in result and result["error"]:
                _error_count += 1
                log.error("Guard: %s", result["error"])
            else:
                new = result.get("newDevices", 0)
                updated = result.get("updated", 0)
                if new > 0:
                    log.info("Guard: %d new, %d updated", new, updated)

        except Exception as e:
            _error_count += 1
            log.error("Scan failed: %s", e)

        # Sleep with responsive shutdown
        end = time.time() + SCAN_INTERVAL * 60
        while _running and time.time() < end:
            time.sleep(5)

    log.info("Stopped. %d scans, %d errors.", _scan_count, _error_count)


if __name__ == "__main__":
    main()
