import subprocess
import json
import os
from datetime import datetime, timedelta

STATE_FILE = "/home/dlin/.openclaw/workspace/twoscomp-homelab/state/homelab-health.json"
OS_LOG_FILE = "/home/dlin/.openclaw/workspace/twoscomp-homelab/ops-log.md"

def query_kuma(last_check):
    # status 1 is OK, anything else is down/pending
    query = f"SELECT m.name, h.status, h.msg, h.time FROM monitor m JOIN heartbeat h ON m.id = h.monitor_id WHERE h.status != 1 AND h.time > '{last_check}' ORDER BY h.time ASC;"
    cmd = ["flyctl", "ssh", "console", "--app", "dlin-uptime-kuma", "-C", f"sqlite3 /app/data/kuma.db \"{query}\""]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        
        lines = result.stdout.strip().split('\n')
        outages = []
        for line in lines:
            if "|" in line:
                parts = line.split('|')
                if len(parts) == 4:
                    outages.append({
                        "name": parts[0],
                        "status": parts[1],
                        "msg": parts[2],
                        "time": parts[3]
                    })
        return outages
    except Exception:
        return []

def get_interesting_note(name, time_str):
    # Convert time_str to datetime (UTC)
    dt = datetime.strptime(time_str.split('.')[0], "%Y-%m-%d %H:%M:%S")
    # Convert to Central (approx -5h or -6h depending on DST)
    # Since the log says "3 AM Central", let's check for both possibilities
    
    hour = dt.hour
    day = dt.weekday() # 0 is Monday, 6 is Sunday
    
    # Check for Sunday Scrub (3 AM Central is roughly 8 AM or 9 AM UTC)
    if day == 6 and (hour == 8 or hour == 9):
        return "Correlates with Sunday 3 AM pool scrub (newton). Likely NFS latency."
    
    # Check for Wednesday SMART (3 AM Central)
    if day == 2 and (hour == 8 or hour == 9):
        return "Correlates with Wednesday 3 AM SMART short test."
        
    # Check for daily snapshots (3 AM Central)
    if (hour == 8 or hour == 9):
        return "Correlates with daily 3 AM snapshots (swarm-sync, google-drive, etc.)."

    if "heartbeat" in name.lower() or "pusher" in name.lower():
        return "Potential issue with heartbeat-pusher process or network congestion to Fly.io."

    return "Unexpected outage. Requires further investigation."

def main():
    if not os.path.exists(os.path.dirname(STATE_FILE)):
        os.makedirs(os.path.dirname(STATE_FILE))

    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
    else:
        state = {"last_check": (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"), "incidents": []}

    new_outages = query_kuma(state["last_check"])
    
    if new_outages:
        # Group outages by approximate time and name to avoid duplicate incidents for one drop
        for out in new_outages:
            incident = {
                "name": out["name"],
                "time_utc": out["time"],
                "msg": out["msg"],
                "note": get_interesting_note(out["name"], out["time"])
            }
            state["incidents"].append(incident)

    state["last_check"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    
    # Keep only last 100 incidents
    state["incidents"] = state["incidents"][-100:]

    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

    if new_outages:
        print(f"Logged {len(new_outages)} new outage events.")
    else:
        print("NO_REPLY")

if __name__ == "__main__":
    main()
