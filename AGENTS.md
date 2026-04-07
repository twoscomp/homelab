# twosclub Homelab — Agent Instructions

> **Purpose:** Authoritative context and SOP for any LLM agent operating autonomously on this homelab. Read this before taking any action.
> **Last updated:** 2026-04-07
> **Standard:** [AGENTS.md](https://openai.com/index/introducing-codex/) — readable by Claude Code, Gemini, OpenAI Codex, and compatible agents.

---

## 1. Owner & Access

| Item | Value |
|------|-------|
| Owner | Daniel Lin (dlin) |
| Repo | `github.com:twoscomp/homelab.git` (private) |
| Repo path on nuc8-1 | `~/smarthomeserver/` |
| Repo path on nuc8-2 | `~/smarthomeserver/` |
| Primary shell | fish (use `bash -c '...'` or just SSH and run commands directly) |
| SSH | `ssh nuc8-1`, `ssh nuc8-2` (from this machine, keys pre-configured) |
| TrueNAS | `truenas.localdomain` — web UI + SSH. HA config at `/mnt/newton/appdata/homeassistant/` |
| Uptime Kuma | `https://dlin-uptime-kuma.fly.dev` — hosted on fly.io |
| Kuma DB | `flyctl ssh console --app dlin-uptime-kuma -C "sqlite3 /app/data/kuma.db \"<query>\""` |

---

## 2. Infrastructure Overview

```
Internet
   │
   └─ Cloudflare (DNS + Tunnel: cloudflared container)
          │
          ▼
   NPM (Nginx Proxy Manager) — SSL termination, reverse proxy
   nuc8-1:80/443 | internal admin UI: nuc8-1.swarm.localdomain:81
          │
   ┌──────┴────────────────────────────────────┐
   │         Docker Swarm Cluster              │
   │  nuc8-1 (manager)      nuc8-2 (worker)   │
   │  192.168.0.x           192.168.0.y        │
   └───────────────────────────────────────────┘
          │
   AdGuard Home (x2, host-network, not Swarm)
   VIP1: 192.168.0.253  VIP2: 192.168.0.254
   Managed via keepalived failover between nuc8-1 and nuc8-2
          │
   TrueNAS (newton) — 192.168.0.196
   NFS: /mnt/newton/media → mounted in Swarm containers
   ZFS pool: newton (5× 14TB Seagate Exos X16)
   Timezone: America/Chicago (ALL cron schedules are Central time)
```

### Swarm Network

The overlay network is named `smarthomeserver` (legacy name — do not rename without redeploying all stacks). Service DNS inside the overlay: `<stack>_<service>` e.g. `media_sonarr`, `tier1_nginx-proxy-manager`.

### Node Pinning

Some services are pinned via `node.hostname` constraints:
- `nuc8-1`: NPM, cloudflared, epic-games
- `nuc8-2`: most media/arr services (heavier workload)

---

## 3. Stack Inventory

All stacks deployed from `~/smarthomeserver/` on **nuc8-1** using:
```bash
docker-compose -f <file>.yaml config | docker stack deploy -c - <stackname>
```
(docker-compose v1 required to expand `.env` variables before deploy)

| Stack file | Stack name | Key services |
|------------|-----------|--------------|
| `tier1.yaml` | `tier1` | Nginx Proxy Manager |
| `adguard-standalone.yaml` | *(compose, not swarm)* | AdGuard Home ×2 (run on each node separately) |
| `security.yaml` | `security` | cloudflared (CF Tunnel), crowdsec (IPS) |
| `media.yaml` | `media` | Sonarr, Radarr, Lidarr, Readarr, Mylar3, Prowlarr, Bazarr, Overseerr, Tautulli, Komga, Maintainerr, Recyclarr, cross-seed, Kometa, Epic Games |
| `teslamate.yaml` | `teslamate` | TeslaMate, PostgreSQL, Grafana, Mosquitto |
| `docker-compose.yaml` | `tesla` | Tesla HTTP Proxy |
| `monitoring.yaml` | `monitoring` | heartbeat-pusher |

### Deploying Stacks — Critical Nuance

**Always use docker-compose v1 (`docker-compose`, not `docker compose`) to expand `.env` before deploying.** Docker Swarm's `docker stack deploy` does not read `.env` files natively — it passes the raw `${VAR}` strings unexpanded into the service spec. docker-compose v1 is used as a pre-processor only:

```bash
# Correct — v1 expands .env, pipes expanded YAML to stack deploy
docker-compose -f media.yaml config | docker stack deploy -c - media

# WRONG — stack deploy will not expand .env variables
docker stack deploy -f media.yaml media

# WRONG — docker compose v2 has edge-case differences in variable handling
docker compose -f media.yaml config | docker stack deploy -c - media
```

All stack commands run on **nuc8-1** (Swarm manager) from `~/smarthomeserver/`. After a `git pull`, redeploy only the affected stack — not all stacks.

### Environment Variables

`.env` is gitignored. Key vars:
```
DATADIR=/mnt/dockerData       # shared container data (on TrueNAS NFS or shared storage)
SERVARRDIR=/servarrData/      # local *arr SQLite configs (must be local disk, NOT NFS)
TZ=America/Chicago
PUID=1000 / PGID=1001
PUID_APPS=568                 # linuxserver.io containers
KUMA_MONITORS=<url_pairs>     # heartbeat-pusher monitor pairs (see below)
```

---

## 4. Uptime Monitoring Architecture

### heartbeat-pusher (`monitoring.yaml`)

An Alpine container that runs a shell loop every 30 seconds. For each monitored service it:
1. `wget` checks an internal service URL (using Swarm DNS)
2. `wget` pushes a heartbeat to Uptime Kuma on fly.io (up or down)

**Critical implementation notes:**
- Uses `wget` only — NOT `curl`. Alpine's curl uses c-ares which cannot resolve Docker Swarm DNS (`127.0.0.11`). `wget` uses musl libc resolver which works correctly.
- All 14 monitors run **in parallel** (background subshells + `wait`) to prevent sequential timeouts from exceeding the 60s heartbeat window.
- Push timeout: `--timeout=10`. Check timeout: `--timeout=5`.

```sh
while true; do
  for pair in $(echo "$KUMA_MONITORS" | tr ',' '\n'); do
    (
      check_url="${pair%%|*}"
      push_url="${pair##*|}"
      if wget -qO /dev/null --timeout=5 "$check_url" 2>/dev/null; then
        wget -qO /dev/null --timeout=10 "$push_url?status=up&msg=OK" 2>/dev/null
      else
        wget -qO /dev/null --timeout=10 "$push_url?status=down&msg=offline" 2>/dev/null
      fi
    ) &
  done
  wait
  sleep 30
done
```

### Uptime Kuma Monitor Groups (current state)

| Group | Children |
|-------|---------|
| ARR Apps | Sonarr, Radarr, Readarr, Bazarr, Prowlarr, Mylar, Maintainerr |
| Download Clients | QBT, NZBGet |
| Infrastructure | AdGuard VIP1 (192.168.0.253), AdGuard VIP2 (192.168.0.254), NGINX Proxy Manager |
| Media | Plex, Tautulli, Lidarr, Calibre, Komga, Overseerr |
| *(standalone)* | Home Assistant, Teslamate |

### Kuma DB Manipulation Rules

When adding monitors or groups **directly via SQLite** (Kuma has no traditional REST API):

1. **`user_id` must be non-NULL** (set to `1`). NULL user_id silently hides the monitor from the dashboard even though it appears on status pages.
2. **Clone full row for group monitors** — minimal INSERT produces broken groups. Use:
   ```sql
   INSERT INTO monitor SELECT NULL, 'Group Name', active, user_id, interval, url, type, ...
   FROM monitor WHERE id=<existing_group_id>;
   ```
   Copy all columns, set `parent=NULL`, override `name`.
3. After any direct DB change: `flyctl app restart dlin-uptime-kuma` and wait ~20s.

### Alerting Tiers

- **Pushover** (immediate phone alert): AdGuard VIP1, AdGuard VIP2, NGINX PM, Plex, Home Assistant
- **Discord** (`twosclub Admins` webhook, high-urgency): same as Pushover set
- **Discord** (`twosclub` webhook, informational): *arr stack, QBT, NZBGet, Teslamate, Lidarr, Tautulli

---

## 5. TrueNAS Maintenance Schedule

All times **America/Chicago (Central)**:

| Task | Schedule | Notes |
|------|----------|-------|
| Pool scrub (newton) | Sunday 3 AM | Was midnight UTC — moved to avoid NFS latency during peak |
| SMART short test | Wednesday 3 AM | ~2 min, early electrical/mechanical failure detection |
| SMART long test | **REMOVED** | Redundant with ZFS scrub; 10–16h on 5×14TB caused NFS degradation |
| Snapshot: swarm-sync | Sunday 3:00 AM | |
| Snapshot: google-drive | Sunday 3:05 AM | |
| Snapshot: appdata | Sunday 3:10 AM | |
| Snapshot: backups | Sunday 3:15 AM | |

**Open watch item:** Sunday 3 AM scrub may still cause NFS latency spikes for HTTP monitors. If Sunday alerts fire, add Kuma maintenance window: `0 9 * * 7` UTC (= 3 AM Central), 60 minutes.

---

## 6. Recyclarr / Quality Profiles

Recyclarr config lives at `/mnt/dockerData/recyclarr/config/recyclarr.yml` (on TrueNAS NFS) and is also version-controlled at `config/recyclarr/recyclarr.yml` in this repo.

### Custom Profile: SD / Legacy

Added a non-standard quality profile for pre-HD content (pre-720p era movies/shows that were never broadcast in HD). It accepts: `Bluray-480p → WEB 480p → DVD → SDTV` in addition to HD tiers.

**Intentionally omitted from SD profile** (these custom format penalties are counterproductive for pre-HD content):
- x265/HEVC penalty (fine for SD encodes)
- Scene release penalty (Scene is primary source for SD-era content)
- No-RlsGroup penalty

**Radarr movies on SD/Legacy profile (profile ID 10):**
- The Turbo Charged Prelude for 2 Fast 2 Furious (1245), Nick Fury: Agent of S.H.I.E.L.D. (1282), Generation X (1283), The Star Wars Holiday Special (1333), Star Wars: Droids – Pirates and the Prince (1335), Star Wars: Droids – Treasure of the Hidden Planet (1336), Baribari Densetsu Movie (2040), Halloweentown (1998) (2091)

**Sonarr series on SD/Legacy profile (profile ID 14):**
- Doug 1991 (393), Teenage Mutant Ninja Turtles 2003 (489), Queer Eye for the Straight Guy (507)

---

## 7. Weekly Ops Review SOP

**Cadence:** Weekly, Monday mornings (cron: `0 14 * * 1` UTC = 9 AM Central).

**Before starting:** Read `ops-log.md` in the repo root to skip already-resolved items.

**Data sources to query:**

```bash
# 1. Kuma 24h uptime per monitor
flyctl ssh console --app dlin-uptime-kuma -C "sqlite3 /app/data/kuma.db \"
SELECT m.name,
  ROUND(100.0*SUM(CASE WHEN h.status=1 THEN 1 ELSE 0 END)/COUNT(*),2) as uptime_pct,
  SUM(CASE WHEN h.status=0 THEN 1 ELSE 0 END) as down_count,
  ROUND(AVG(CASE WHEN h.ping IS NOT NULL THEN h.ping END),0) as avg_ping_ms
FROM monitor m JOIN heartbeat h ON m.id=h.monitor_id
WHERE h.time > datetime('now','-24 hours') AND m.type!='group'
GROUP BY m.id ORDER BY uptime_pct ASC\""

# 2. Recent downtime events
flyctl ssh console --app dlin-uptime-kuma -C "sqlite3 /app/data/kuma.db \"
SELECT m.name, h.time, h.msg FROM heartbeat h JOIN monitor m ON m.id=h.monitor_id
WHERE h.status=0 AND h.time > datetime('now','-7 days') ORDER BY h.time DESC LIMIT 40\""

# 3. Docker resource usage
ssh nuc8-1 'docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"'
ssh nuc8-2 'docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"'

# 4. Service error logs (example)
ssh nuc8-1 'docker service logs media_sonarr --since 24h --timestamps 2>&1 | grep -i error | tail -20'
```

**Report format:** AWS OpsReview style — SEV1/SEV2/SEV3/SEV4/Observations, executive summary, prioritized action table.

**After changes:** Update `ops-log.md` with findings and all changes made.

---

## 8. Known Issues / False Positives

| Symptom | Cause | Status |
|---------|-------|--------|
| All push monitors DOWN simultaneously for a few minutes | heartbeat-pusher container restart during redeploy | Expected, ~15s recovery. Fixed longer hangs by parallelizing loop. |
| Periodic mass DOWN overnight (historical) | Sequential loop timing out with 14 monitors × 15s/each > 60s heartbeat window | **Fixed 2026-04-06** — loop now parallel |
| Honeywell Lyric / Total Connect Comfort HA warnings | Async coordinator-based integrations have no `scan_interval` config option | Cosmetic, not blocking |
| Sunday HTTP latency spikes (potential) | ZFS scrub on TrueNAS newton pool causing NFS I/O pressure | Watch; add Kuma maintenance window if needed |

---

## 9. Common Operational Commands

```bash
# Deploy a stack (with env var expansion)
ssh nuc8-1 'cd ~/smarthomeserver && docker-compose -f media.yaml config | docker stack deploy -c - media'

# Check service health
ssh nuc8-1 'docker service ps media_sonarr --no-trunc'

# Force update a service image
ssh nuc8-1 'docker service update --image lscr.io/linuxserver/sonarr:latest --force media_sonarr'

# Scale a service down/up
ssh nuc8-1 'docker service scale media_sonarr=0'
ssh nuc8-1 'docker service scale media_sonarr=1'

# Test internal service connectivity (from within overlay network)
ssh nuc8-1 'docker run --rm --network smarthomeserver curlimages/curl:latest http://media_sonarr:8989/'

# Check heartbeat-pusher loop state
ssh nuc8-1 'docker service ps monitoring_heartbeat-pusher --no-trunc'

# Restart Uptime Kuma (after DB changes)
flyctl app restart dlin-uptime-kuma

# Query Kuma DB directly
flyctl ssh console --app dlin-uptime-kuma -C "sqlite3 /app/data/kuma.db \"SELECT id, name, type, active, user_id, parent FROM monitor ORDER BY id\""

# Git: pull and redeploy after a change
ssh nuc8-1 'cd ~/smarthomeserver && git pull && docker-compose -f monitoring.yaml config | docker stack deploy -c - monitoring'
```

---

## 10. Home Assistant

- Config path (on TrueNAS): `/mnt/newton/appdata/homeassistant/`
- HA is a TrueNAS App (not a Docker Swarm service)
- Access: internal via `homeassistant.local` or `192.168.0.x`
- External: `ha.whatasave.space` via Cloudflare Tunnel + NPM

### Known State (post-2026-04-06 review)
- co2signal integration **removed** — was causing 651ms baseline latency (unhandled async exceptions every poll cycle)
- 4 humidifier automations disabled (humidifier hardware no longer relevant)
- Honeywell Lyric + Total Connect Comfort: async coordinator warnings in logs — cosmetic only, no config fix available

---

## 11. Secrets & Security

- `.env` is **gitignored** — all credentials live there only
- Git history was sanitized (Feb 2026) with `git filter-repo` — no secrets in history
- CrowdSec IPS parses NPM logs; iptables bouncer runs on Docker host (outside containers)
- Cloudflare Tunnel: no open WAN ports; all external traffic enters via `cloudflared` container

---

## 12. Open Items as of 2026-04-06

| Priority | Item | Notes |
|----------|------|-------|
| Watch | Sunday 3 AM scrub → NFS latency | Add Kuma weekly maintenance window `0 9 * * 7` UTC if Sunday alerts fire |
| Observation | Honeywell integration async warnings | Cosmetic; no action needed |
