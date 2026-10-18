#!/usr/bin/env python3
"""Push TrueNAS health to Home Assistant (Home Lab dashboard).

Runs on TrueNAS as a cron job (user dlin, every 5 min). Reads the middleware
with `midclt call` (read-only) and POSTs one JSON document to an HA webhook;
HA's trigger-based template sensors in /config/templates/truenas.yaml unpack it.
Push instead of an HA integration: no API key on the NAS for HA, no deprecated
REST API, and the only maintained HACS integration targets 25.04.

The webhook URL is a secret and lives next to the script on TrueNAS, not in git:
    /mnt/newton/appdata/ha-push/webhook_url   (one line, mode 600)
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROBLEM_LEVELS = {"WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"}


def midclt(method, *args):
    out = subprocess.run(["midclt", "call", method, *map(json.dumps, args)],
                         capture_output=True, text=True, timeout=60, check=True).stdout
    return json.loads(out)


def iso(ms_date):
    if not ms_date:
        return None
    return datetime.fromtimestamp(ms_date["$date"] / 1000, timezone.utc).isoformat()


def collect():
    doc = {"reported": datetime.now(timezone.utc).isoformat()}

    pool = next(p for p in midclt("pool.query") if p["name"] == "newton")
    scan = pool.get("scan") or {}
    doc["pool"] = {
        "status": pool["status"],
        "status_code": pool.get("status_code"),
        "used_pct": round(100 * pool["allocated"] / pool["size"], 1),
        "free_tb": round(pool["free"] / 1e12, 1),
        "size_tb": round(pool["size"] / 1e12, 1),
        "scrub_state": scan.get("state"),
        "scrub_end": iso(scan.get("end_time")),
        "scrub_errors": scan.get("errors"),
    }

    alerts = [a for a in midclt("alert.list") if not a["dismissed"]]
    doc["alerts"] = [a["formatted"].split("\n")[0][:200] for a in alerts if a["level"] in PROBLEM_LEVELS]
    doc["notices"] = [a["formatted"].split("\n")[0][:200] for a in alerts if a["level"] not in PROBLEM_LEVELS]

    apps = midclt("app.query", [], {"select": ["name", "state"]})
    doc["apps"] = {
        "running": sum(a["state"] == "RUNNING" for a in apps),
        "total": len(apps),
        "stopped": sorted(a["name"] for a in apps if a["state"] == "STOPPED"),
        "problem": sorted(f'{a["name"]} ({a["state"].lower()})' for a in apps
                          if a["state"] not in ("RUNNING", "STOPPED")),
    }

    try:
        temps = midclt("disk.temperatures")
        doc["disk_temp_max"] = max(v for v in temps.values() if v is not None)
    except Exception:  # noqa: BLE001 — temperatures are optional
        doc["disk_temp_max"] = None

    info = midclt("system.info")
    doc["load_15"] = round(info["loadavg"][2], 1)
    doc["uptime_s"] = int(info["uptime_seconds"])
    return doc


def main():
    url = open(os.path.join(HERE, "webhook_url")).read().strip()
    doc = collect()
    if "-n" in sys.argv:
        print(json.dumps(doc, indent=2))
        return
    req = urllib.request.Request(url, json.dumps(doc).encode(), {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


if __name__ == "__main__":
    main()
