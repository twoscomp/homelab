# AGENTS.md — Operational SOP & Infrastructure Context

This is the homelab's operational reference — ops review process, and troubleshooting playbooks for problems that have already been solved once. Read this before making infrastructure changes; it exists so a session doesn't have to re-derive context the hard way a second time.

For architecture, stack inventory, and setup instructions, see [`README.md`](./README.md) — not duplicated here.

## Ops Review Process

Run regular ops reviews on the homelab using an AWS-style format, weekly (Monday mornings). Reference `ops-log.md` in the repo root first to avoid re-covering already-resolved items. `ops-review-0407.md` is a worked example of a past review.

**Why:** proactively surface operational issues (uptime degradation, resource pressure, monitoring gaps) before they become incidents.

### Data Sources to Query

**1. fly.io Uptime Kuma SQLite**
```bash
flyctl ssh console --app dlin-uptime-kuma -C "sqlite3 /app/data/kuma.db \"<query>\""
```
Key queries:
- 24h uptime % per monitor: `SELECT m.name, ROUND(100.0*SUM(CASE WHEN h.status=1 THEN 1 ELSE 0 END)/COUNT(*),2) as uptime_pct, SUM(CASE WHEN h.status=0 THEN 1 ELSE 0 END) as down_count, ROUND(AVG(CASE WHEN h.ping IS NOT NULL THEN h.ping END),0) as avg_ping_ms FROM monitor m JOIN heartbeat h ON m.id=h.monitor_id WHERE h.time > datetime('now','-24 hours') AND m.type!='group' GROUP BY m.id ORDER BY uptime_pct ASC`
- Recent downtime events: `SELECT m.name, h.time, h.msg FROM heartbeat h JOIN monitor m ON m.id=h.monitor_id WHERE h.status=0 AND h.time > datetime('now','-7 days') ORDER BY h.time DESC LIMIT 40`
- 30-day HTTP stats with latency: filter `m.type='http'`, include `AVG(h.ping)`, `MAX(h.ping)` (column is `ping`, not `ping_ms`)
- Simultaneous mass-down: group by minute to identify heartbeat-pusher restarts vs real outages

**2. Docker resource usage**
```bash
ssh nuc8-1 'docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"'
ssh nuc8-2 'docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"'
```

**3. Service logs for error patterns**
```bash
ssh nuc8-1 'docker service logs <service> --since 24h --timestamps 2>&1 | grep -v "fetch\|Installing\|OK:\|Executing"'
```

### Report Format (AWS OpsReview style)

```
## Ops Review — twosclub Homelab Services
**Date:** | **Prepared by:** Ops | **Severity:** SEV2+ items require action

### Executive Summary
[1 paragraph: overall health, any notable artifacts that explain skewed numbers]

### SEV1 — [Critical: active outage or data loss risk]
### SEV2 — [High: degraded service, SLA breach]
### SEV3 — [Medium: performance degradation, reliability risk]
### SEV4 — [Low: efficiency, optimization]
### Observations (No Action Required)
### Prioritized Action List (table: Priority | Item | Effort)
```

### Key Patterns to Watch For

- **Sunday 3 AM Central scrub** — ZFS scrub on newton pool, may cause NFS latency spikes for HTTP monitors; add a Kuma weekly maintenance window (`0 9 * * 7` UTC, 60 min) if alerts fire from this.
- **Mass simultaneous push-monitor down** — heartbeat-pusher restart or fly.io cold start hanging the push `wget` (mitigated with `--timeout=10`, but worth checking if it recurs). See `heartbeat-pusher-investigation.md` for the full history on this.
- **Baseline response time drift** — HTTP monitor `avg_ms` increasing over time = service or infra degradation, not noise.
- **Push monitors showing DOWN** — check the heartbeat-pusher loop isn't hung; verify pushes are actually arriving in the Kuma DB with a `MAX(h.time)` query before assuming a real outage.

### Known False Positives / Artifacts

- heartbeat-pusher redeploy = brief DOWN on all push monitors during container restart (expected, ~15s).
- New monitors always show low uptime for the first 24–48h (deployment/debugging period).
- Honeywell Lyric / Total Connect Comfort integrations log async coordinator warnings — cosmetic, not blocking.

### Post-Deploy Steps Required After Media Stack Redeploy

After every `docker-compose -f media.yaml config | docker stack deploy -c - media`:
- Re-apply the epic-games CPU limit: `docker service update --limit-cpu 1.5 media_epic-games`
  - docker-compose v1 serializes `resources.limits.cpus` as a float; `docker stack deploy` rejects it ("must be a string") — this field can't live in `media.yaml` itself and gets silently reset to unlimited on every stack deploy.

### Infrastructure Notes

- TrueNAS runs in America/Chicago timezone — all cron schedules are Central time.
- Drives: 5× Seagate Exos X16 14TB 7200 RPM (ST14000NM001G-2KJ103) + 1× SSD boot.
- ZFS scrub covers data integrity (supersedes SMART long test — long test removed).
- SMART short test kept (Wednesday 3 AM Central) — 2 min, catches early electrical/mechanical failure.

## Infra Troubleshooting — Standing Instruction

Any time you troubleshoot a homelab/infra problem, do both of the following before ending the session:
1. **Log findings in `ops-log.md`** (repo root) — symptom, investigation steps, root cause, fix applied or still pending. Follow the existing dated-entry format already in that file.
2. **Update this file** with anything a future session would need to *not repeat the investigation* — playbooks, gotchas, "check this first" notes. `ops-log.md` is for what happened; playbook entries here are for what to do next time.

### Playbook: TeslaMate / Tesla Fleet API

**Status as of 2026-07-23: fully migrated to Fleet API and working.** Root cause history and the full incident are in `ops-log.md` ("2026-07-23 — TeslaMate Not Connecting" and the follow-up entry). This section is the condensed "how do I redo/extend this" reference — read this first, only dig into `ops-log.md` if something here doesn't explain what you're seeing.

**Topology gotcha:** `teslamate_*` and `smarthomeserver_tesla-http-proxy` swarm services are pinned to **nuc8-2**, not nuc8-1. `docker ps`/`docker logs` for them only work over `ssh nuc8-2` directly (nuc8-2 is a worker, not a swarm manager — `docker service ps <name>` from nuc8-1 tells you which node to SSH into; `docker service inspect`/other manager-only commands must run from nuc8-1). nuc8-1's shell is **fish**, not bash — always wrap remote commands as `ssh nuc8-1 'bash -lc "..."'` or fish syntax errors will bite you.

**Why owner-api broke (background):** TeslaMate `<4.0.0` calls the legacy `owner-api.teslamotors.com`, which Tesla now blocks (`403 forbidden, see fleet-api`) for third-party apps. We're now on `4.0.1` with Fleet API configured — this shouldn't recur, but if it ever does, check container logs for `owner-api.teslamotors.com` + `403` before assuming a network/proxy/deploy problem.

**Two separate Tesla apps exist — don't confuse them:**
- `tesla-http-proxy` (domain `tesla.whatasave.space`) — used by Home Assistant's `tesla_custom` integration (client_id `f8e71c25f95f-...`, stored in HA's `core.config_entries`). Command-signing proxy only; its `CLIENT_ID`/`CLIENT_SECRET` compose env vars are intentionally dummy placeholders, not a bug.
- `Self-hosted TM` (domain `teslamate.whatasave.space`) — TeslaMate's own dedicated app, client_id in `TESLA_AUTH_CLIENT_ID` in nuc8-1's `.env`, client_secret in `TESLA_AUTH_CLIENT_SECRET` (only used for the one-time curl calls below, TeslaMate itself never reads the secret — there is no `TESLA_AUTH_CLIENT_SECRET` env var TeslaMate consumes).

Each app needs **its own domain** — Tesla's `partner_accounts` registration rejects a second app reusing a domain's already-claimed public key (`"Public key hash has already been taken"`). That's why TeslaMate got its own subdomain + keypair rather than reusing `tesla.whatasave.space`.

**How `teslamate.whatasave.space` is wired (for redoing/extending):**
- Keypair: `~/teslamate-bak/tesla-fleet-key/{private,public}-key.pem` on nuc8-1 (private key unused by TeslaMate, kept for hygiene only).
- Public key served at `.well-known/appspecific/com.tesla.3p.public-key.pem` via an NPM proxy host (id 70) whose `advanced_config` does `root /data/teslamate-proxy;` + a `try_files` location block — **not** a real backend, `forward_host` is the placeholder `see-advanced`/`11111` (same pattern as the existing `tesla.whatasave.space` host — look at that one in NPM's proxy-hosts API as a template if setting up a third domain later). Host directory on nuc8-1: `/mnt/dockerData/nginx-package-manager/data/teslamate-proxy/`.
- Cloudflare Tunnel is in **remotely-managed mode** (`cloudflared tunnel --no-autoupdate run` with a token, no local ingress config file) — new hostnames are added via the Cloudflare dashboard (Zero Trust → Networks → Tunnels → Public Hostnames), pointing at `http://tier1_nginx-proxy-manager:80` like every other `*.whatasave.space` host. There's a `CF_API_TOKEN`/`CF_ACCOUNT_ID`/`CF_ZONE_ID` in nuc8-1's `.env` but the tunnel's own ingress config isn't reachable through the standard `cfd_tunnel` API in a way we successfully used — it was faster to just add it by hand in the dashboard.
- NPM admin credentials: `NPM_ADMIN_EMAIL`/`NPM_ADMIN_PASSWORD` in nuc8-1's `.env`. Get an API token with `POST http://127.0.0.1:81/api/tokens` (run from nuc8-1, NPM only listens on 81 locally) `{"identity": ..., "secret": ...}`, then use `Authorization: Bearer <token>` against `/api/nginx/proxy-hosts` etc. Prefer this over raw sqlite edits for anything involving cert issuance — the NPM backend orchestrates that, direct DB rows don't.

**Redoing the OAuth dance (e.g. token revoked, need to re-auth):**
1. Get a fresh authorization code — open in a browser signed into the Tesla account:
   ```
   https://auth.tesla.com/oauth2/v3/authorize?client_id=$TESLA_AUTH_CLIENT_ID&redirect_uri=https%3A%2F%2Fteslamate.whatasave.space%2Fcallback&response_type=code&scope=openid%20offline_access%20vehicle_device_data%20vehicle_location&state=teslamate
   ```
   The redirect target 404s — expected, it doesn't need to serve anything. Copy the `code=` param from the address bar fast, **it expires in minutes.**
2. **Exchange it at `https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token` — NOT `https://auth.tesla.com/oauth2/v3/token`.** This is the single biggest gotcha in this whole flow: the `auth.tesla.com` token endpoint (used in older community guides, including the one this setup was originally based on) still returns `200` with a valid `access_token` but **silently omits `refresh_token` and `scope`** — no error, just a token that dies in 8 hours with no way to renew. Cost us two burned auth codes and real debugging time to catch. Always use `fleet-auth.prd.vn.cloud.tesla.com` for the code exchange:
   ```bash
   curl -s -X POST https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token \
     --data-urlencode grant_type=authorization_code \
     --data-urlencode "client_id=$CLIENT_ID" \
     --data-urlencode "client_secret=$CLIENT_SECRET" \
     --data-urlencode "code=$CODE" \
     --data-urlencode "redirect_uri=https://teslamate.whatasave.space/callback" \
     --data-urlencode "audience=https://fleet-api.prd.na.vn.cloud.tesla.com"
   ```
   Verify success by checking the response has a `refresh_token` key before doing anything else with it.
3. Paste the resulting `access_token`/`refresh_token` into TeslaMate's own `/sign_in` page (token-based sign-in, not email/password) at `teslamate.swarm.local`.
4. If TeslaMate keeps polling with 401s afterward, flush stale tokens and force a restart:
   ```bash
   docker exec <teslamate_database container> psql -U teslamate teslamate -c "DELETE FROM private.tokens;"
   docker service update --force teslamate_teslamate   # NOT docker restart — it's a swarm service
   ```
5. **Disable the legacy streaming API** — Fleet API has no equivalent to the old realtime websocket, and leaving it on causes an infinite `Stream disconnecting/connecting` + `Tokens expired` reconnect loop (multiple times a second) as soon as real polling starts. Fix once, in the DB (there's no UI toggle exposed cleanly, this is `car_settings.use_streaming_api`, shared across all cars via `cars.settings_id`):
   ```bash
   docker exec <teslamate_database container> psql -U teslamate teslamate -c "UPDATE car_settings SET use_streaming_api = false;"
   docker service update --force teslamate_teslamate
   ```

**Domain registration (one-time per app, e.g. if the app or domain ever needs re-registering):**
```bash
PARTNER_TOKEN=$(curl -s -X POST https://auth.tesla.com/oauth2/v3/token \
  -d grant_type=client_credentials \
  --data-urlencode "client_id=$CLIENT_ID" --data-urlencode "client_secret=$CLIENT_SECRET" \
  --data-urlencode 'scope=openid vehicle_device_data' \
  --data-urlencode "audience=https://fleet-api.prd.na.vn.cloud.tesla.com" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4)
curl -s -X POST "https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/partner_accounts" \
  -H "Authorization: Bearer $PARTNER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"domain": "teslamate.whatasave.space"}'
```
(This one-time `client_credentials` partner-token call *does* work fine against `auth.tesla.com` — the broken-endpoint gotcha above is specific to the `authorization_code` grant used for per-user tokens.)

**Required scopes on the Developer Portal app:** check only **Vehicle Information** + **Vehicle Location**. Skip Vehicle Commands (grants unlock/live-camera/remote-start just to get the optional wake-up feature — not worth it for passive logging) and everything else. There's no `offline_access` checkbox in the portal UI; it's requested via the OAuth `scope` parameter directly and is independent of the portal checkboxes.

**Performance gotcha after any TeslaMate major-version upgrade with a large existing DB:** the `4.0.0`/`4.0.1` migrations replace the old btree indexes on `positions.date` with BRIN indexes, optimized for range scans — but TeslaMate's own hot-path query (`WHERE car_id = $1 ORDER BY date DESC LIMIT 1`, used on every car-process (re)start to restore last-known state) has no efficient index left to use, causing a full scan on every restart. On a 19M-row `positions` table this pegged the DB at 75% CPU and cascaded into connection-pool timeouts / a car-process crash-restart-crash loop the moment real Fleet API polling started. Fix: `docker service scale teslamate_teslamate=0`, then `CREATE INDEX CONCURRENTLY positions_car_id_date_index ON positions (car_id, date DESC);`, then scale back to 1. Check `docker stats` on the DB container before/after to confirm — should drop from ~75% CPU / high block I/O to near-idle.

**Backups:** `~/teslamate-bak/` on nuc8-2 (where the DB container runs) has pre-upgrade `pg_dump` snapshots — take a fresh one (`pg_dump -U teslamate teslamate | gzip > ...`) before any future major-version bump.
