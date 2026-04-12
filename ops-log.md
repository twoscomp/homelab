# Homelab Ops Log

Running record of ops review findings and changes. Reviewed weekly.
See [memory/feedback_ops_review_format.md] for review process and SQL queries.

---

## 2026-04-06

### Review Findings
- All external HTTP monitors (Plex, HA, Calibre, Overseerr, Komga) spiking to 32–60s response at midnight UTC — traced to TrueNAS snapshot/scrub/SMART tasks all scheduled at 00:00
- Home Assistant averaging 651ms baseline response vs 357ms Plex — traced to co2signal integration throwing unhandled task exceptions every poll cycle
- heartbeat-pusher restarting caused all push monitors to show DOWN for 30–60s while `apk add curl` ran at startup
- epic-games using 231MB on nuc8-2 (constrained 2GB node) with no placement constraint
- NPM logging hourly certbot failures for mylar3/babybuddy/code.ha — those services no longer public-facing
- NPM taking ~19s to start due to certbot attempting renewal of 3 orphaned soft-deleted certificates

### Changes Made

**TrueNAS maintenance scheduling**
- Shifted pool scrub (newton) from `0 0 * * 7` → `0 3 * * 7` (3 AM Central Sundays)
- Shifted SMART short test from `0 0 * * 3` → `0 3 * * 3` (3 AM Central Wednesdays)
- Removed SMART long test cron entirely — redundant with ZFS scrub, 10–16h surface scan on all 5 × 14TB drives caused sustained NFS performance degradation
- Staggered 4 snapshot tasks (swarm-sync, google-drive, appdata, backups) to 3:00/3:05/3:10/3:15 AM Central

**Uptime Kuma**
- Added 5 new monitors: AdGuard VIP1 (192.168.0.253), AdGuard VIP2 (192.168.0.254), NPM (tier1_nginx-proxy-manager:81), Lidarr, Tautulli
- Implemented two-tier alerting: Pushover (critical: AdGuard VIPs, NPM, Plex, HA) + Discord (non-critical: *arr stack, QBT, NZBGet, Teslamate, Lidarr, Tautulli)
- Added daily maintenance window 00:00–00:10 UTC; later removed after TrueNAS rescheduled to 3 AM Central
- Migrated heartbeat-pusher KUMA_MONITORS to Docker Swarm native DNS (`stack_service:port`)

**Home Assistant**
- Removed co2signal config entry from `core.config_entries` — response time dropped 651ms → ~130ms avg
- Disabled 4 humidifier automations via `core.entity_registry` (disabled_by: user)

**heartbeat-pusher**
- Removed `apk add --no-cache curl` entirely; switched all checks and pushes to busybox wget (supports HTTPS natively, resolves Swarm DNS via musl resolver)
- Startup delay eliminated; no more false-DOWN alerts on redeploy

**epic-games**
- Pinned to `node.hostname == nuc8-1`
- Added memory limit: 512m

**NPM**
- Removed proxy hosts and whatasave.space certificates for mylar3, babybuddy, code.ha (services no longer public)
- Hard-deleted orphaned certificate DB records (IDs 11, 32, 35) and letsencrypt files
- Hard-deleted orphaned proxy host record (ID 17, duplicate mylar3.swarm.localdomain)
- NPM startup: ~19s → ~2s

### Open Items
- Monitor whether Sunday 3 AM Central scrub causes NFS-related HTTP latency alerts; add targeted weekly Kuma maintenance window if needed (`0 9 * * 7` UTC, 60 min)
- Honeywell Lyric / Total Connect Comfort integrations log async coordinator warnings — cosmetic, not blocking

## 2026-04-07

### Review Findings
- Discovered 14 internal monitors sporadically reporting DOWN alerts via Uptime Kuma on Fly.io.
- Traced root cause to busybox `wget` hanging on TLS handshakes (`ssl_client` process hang) when connecting to the external Fly.io push endpoint, which prevented the heartbeat script loop from continuing.

### Changes Made
**heartbeat-pusher**
- Modified `monitoring.yaml` to install and use `curl` for the external Uptime Kuma push requests to ensure robust TLS connection handling with explicit timeouts (`--connect-timeout 5`, `--max-time 10`).
- Retained busybox `wget` (wrapped in a `timeout` command as an extra safeguard) strictly for the internal Swarm checks, since Alpine's `curl` (using `c-ares`) fails to resolve internal Docker Swarm DNS (`127.0.0.11`).
- Redeployed the `monitoring` stack to `nuc8-1` to apply the loop fix, with all 14 monitors now consistently checking in without blocking.

### Open Items
- Next operational review scheduled for next week (approx. 2026-04-14) to monitor heartbeat-pusher stability and overall homelab performance metrics.

## 2026-04-11

### Review Findings
- **Sustained Outage (17:12 - 17:23 UTC)**: Massive homelab-wide connectivity drop impacting almost all services (Plex, Home Assistant, Calibre, QBT, etc.).
- **DNS/Internal Resolution Failures**: Monitors reported "timeout exceeded" (48s) and "status code 502" (Bad Gateway), indicating internal Docker Swarm / NGINX Proxy Manager could not reach backends.
- **QBT Isolated Outage**: QBT went "offline" first at 17:12:30, followed by the rest of the stack.
- **Recovery**: Services began recovering around 17:23:18 UTC.
- **Correlation**: No scheduled maintenance window (daily snapshots are at 08:00/09:00 UTC). Outage was during peak Saturday usage.

### Changes Made
- Manually ran `homelab-observer.py` to ingest and analyze the spike of 110 Kuma incidents.

## 2026-04-12

### Review Findings
- **Cascading "No heartbeat" Failure (18:25 - 20:04 UTC)**: Widespread monitoring drop across all push-based services (Sonarr, Radarr, Lidarr, QBT, etc.).
- **DNS Correlation**: `AdGuard (VIP1)` and `AdGuard (VIP2)` reported flapping concurrently with the outage.
- **Recursive Reflection**: The Level 2 fix from 2026-04-07 (using `curl` for external pushes) is currently being bypassed or blocked. Traced the fragility to the `apk add curl` command at container startup; if DNS/AdGuard is down or slow, the pusher cannot even initialize `curl`, causing a deadlock in the monitoring loop.
- **Pre-flight Check**: `homelab-observer.py` logged 304 new outage events. Fly.io Kuma logs show `DOMAIN_EXPIRY` noise for orphaned domains, but confirmed "Pending: No heartbeat" as the primary symptom for homelab services.

### Changes Made
- None (Escalated to Level 3).

### Open Items
- **[Level 3 PROPOSAL] Pusher Hardening**: Update `monitoring.yaml` to use a pre-built image (e.g., `curlimages/curl`) and add an external DNS fallback (`1.1.1.1`). This prevents the pusher from being taken down by the very DNS services it is supposed to monitor.
- **[Level 3 PROPOSAL] NFS Latency Mitigation**: Investigate whether Sunday pool scrubs should have a dedicated maintenance window in Kuma (`0 9 * * 7` UTC) to avoid false-positive noise from transient NFS lag.

## 2026-04-12

### Automated Remediation (Approved)
- **Monitoring Hardening**: Applied Level 3 remediation proposed by Senior SRE agent.
  - Switched `heartbeat-pusher` to `curlimages/curl:8.6.0` to eliminate `apk add` runtime delays.
  - Added `dns: [1.1.1.1, 8.8.8.8]` fallback to pusher to ensure Fly.io connectivity during local DNS (AdGuard) failures.
  - Integrated `ops-log.md` tracking for all future automated actions.

### Open Items
- **Manual Action Required**: Set a weekly maintenance window in Uptime Kuma UI for Sunday 3 AM Central (08:00 UTC) to suppress noise during ZFS pool scrubs.
