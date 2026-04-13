# Ops Review Documentation - tw
**Last Updated:** 2026-04-07  
**Reviewer:** twosclaw (🦞)  
**Focus:** Infrastructure Hardening & Availability Improvement

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    TrueNAS Server (NFS)                      │
│  - Plex, Download Clients, Home Assistant                    │
│  - TrueNAS Apps, NFS: /mnt/newton/media (192.168.0.196)    │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   │ NFS
                   ▼
┌─────────────────────────────────────────────────────────────┐
│                  Docker Swarm Cluster                         │
│  ┌──────────────────┐          ┌──────────────────┐         │
│  │  nuc8-1 (mgr)    │          │  nuc8-2 (wkr)    │         │
│  │  - Security      │          │  - *arr Stack     │         │
│  │  - Light Media   │          │  - TeslaMate      │         │
│  │  - AdGuard VIP1  │          │                   │         │
│  └──────────────────┘          └──────────────────┘         │
│  Shared: AdGuard VIP2, Nginx Proxy Manager, cloudflared      │
└─────────────────────────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│              Fly.io Uptime Kuma (External Monitoring)         │
│  https://dlin-uptime-kuma.fly.dev                            │
│  Region: ord (Chicago)                                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 📊 Monitored Services

### Direct HTTP Monitors (Public URLs)
- Overseerr (whatasave.space)
- Calibre
- Komga
- Plex

### Push Monitors (Heartbeat-pusher)
- Sonarr: `http://media_sonarr:8989`
- Radarr:: `http://media_radarr:7878`
- Readarr: `http://media_readarr:8787`
- Bazarr: `http://media_bazarr:6767`
- Prowlarr: `http://media_prowlarr:9696`
- Mylar3: `http://media_mylar3:8090`
- Maintainerr: `http://media_maintainerr:6246`
- Lidarr: `http://media_lidarr:8686`
- qBittorrent: `http://qbt.swarm.localdomain`
- Sabnzbd: `http://sabnzb.swarm.localdomain`
- TeslaMate: `http://teslamate_teslamate:4000`
- Nginx Proxy Manager: `http://tier1_nginx-proxy-manager:81`
- Epic Games: `http://tesla:3000`

### Critical Monitors (Pushover alerts)
- AdGuard VIP1 (192.168.0.253)
- AdGuard VIP2 (192.168.0.254)
- NPM (tier1_nginx-proxy-manager:81)
- Plex
- Home Assistant

### Secondary Monitors (Discord alerts)
- *arr stack
- qBittorrent, NZBGet
- TeslaMate
- Lidarr
- Tautulli

---

## 🔧 Current Infrastructure

### Docker Swarm
- **Nodes:** 2 (nuc8-1 manager, nuc8-2 worker)
- **Network:** `smarthomeserver` (overlay, attachable)
- **Data:** `/mnt/dockerData` (shared NFS)
- **App configs:** `/servarrData/` (local *arr configs)

### Security Stack
- **CrowdSec:** LAPI with iptables bouncer
- **Cloudflare Tunnel:** External access without open ports
- **Nginx Real IP:** CF-Connecting-IP passthrough

### Monitoring
- **heartbeat-pusher:** Alpine container, checks every 30s
- **Uptime Kuma:** Fly.io, push-based monitoring
- **Notifications:** Discord + Pushover

---

## 🐛 Known Issues & Optimizations

### 2026-04-06 Findings

**Performance Degradation:**
- External HTTP monitors spiking to 32–60s at midnight UTC
- Root cause: TrueNAS snapshot/scrub/SMART tasks at 00:00
- Solution: Reschedule to 3 AM Central time slots

**Service Performance:**
- Home Assistant: 651ms → ~130ms after removing co2signal
- Plex: 357ms baseline (healthy)

**Deploy Issues:**
- heartbeat-pusher restarts causing false DOWN alerts
- Root cause: `apk add curl` adding 30–60s delay
- Solution: Switched to busybox wget (native HTTPS)

**Resource Usage:**
- epic-games using 231MB on nuc8-2
- Solution: Pinned to nuc8-1, memory limit 512m

**Nginx Proxy Manager:**
- ~19s startup due to orphaned certbot attempts
- Solution: Removed proxy hosts and certificates for non-public services
- Startup: ~19s → ~2s

---

## 📝 Changes Implemented

### TrueNAS Maintenance
- Pool scrub: `0 0 * * 7` → `0 3 * * 7` (3 AM Central Sundays)
- SMART short test: `0 0 * * 3` → `0 3 * * 3` (3 AM Central Wednesdays)
- SMART long test: Removed (redundant with ZFS scrub)
- Snapshot tasks: Staggered to 3:00/3:05/3:10/3:15 AM Central

### Uptime Kuma
- Added 5 new monitors (AdGuard VIPs, NPM, Lidarr, Tautulli)
- Two-tier alerting (Pushover + Discord)
- Maintenance window removed (maintenance rescheduled)

### heartbeat-pusher
- Removed curl dependency
- Using busybox wget exclusively
- No more startup delays

### Home Assistant
- Removed co2signal integration
- Disabled 4 humidifier automations

### Nginx Proxy Manager
- Removed proxy hosts for non-public services
- Hard-deleted orphaned certificates

---

## 🎯 Ongoing Investigation Areas

### 1. CrowdSec Iptables Bouncer
- [ ] Verify bouncer running on both nodes
- [ ] Check ban enforcement via iptables
- [ ] Review bouncer config: `/etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml`

### 2. AdGuard Failover
- [ ] Test VIP failover manually
- [ ] Review keepalived logs
- [ ] Check VIP health probes

### 3. TeslaMate
- [ ] Verify PostgreSQL backup strategy
- [ ] Review Grafana dashboard updates
- [ ] Check MQTT broker connectivity

### 4. External Monitors Latency
- [ ] Monitor for recurrence after maintenance changes
- [ ] Consider staggering other maintenance tasks
- [ ] Evaluate if HTTP keepalive needed

### 5. NPM Certbot
- [ ] Prevent renewal attempts for deleted certificates
- [ ] Consider disabling auto-renewal for non-public certs

---

## 📈 Metrics to Track

| Metric | Threshold | Alert |
|--------|-----------|-------|
| Home Assistant response | < 200ms | Discord |
| Plex response | < 500ms | Discord |
| External HTTP monitors | < 10s | Discord |
| heartbeat-pusher uptime | 99.9% | Pushover |
| AdGuard VIP availability | 100% | Pushover |
| NPM startup time | < 5s | Discord |

---

## 🔐 Security Notes

### SSH Access
- Keys added under `dlin` user on all hosts
- SSH access required for troubleshooting

### Secrets
- `.env` files are gitignored
- All credentials in environment variables
- No hardcoded secrets in YAML files

### Certificate Hygiene
- Regular cleanup of orphaned certificates
- Use `docker-compose -f cert-prune/` for cleanup tasks

---

## 📚 Next Steps

1. **Access Verification**
   - Verify SSH access to both nodes
   - Confirm fly.io CLI access with token

2. **Log Analysis**
   - Review CrowdSec ban logs
   - Check heartbeat-pusher logs for anomalies
   - Analyze TrueNAS NFS latency patterns

3. **Hardening**
   - Review security.yaml configuration
   - Verify iptables bouncer on both nodes
   - Check firewall rules

4. **Monitoring Improvements**
   - Add Grafana dashboards for Fleet API
   - Consider adding Prometheus metrics
   - Review alerting thresholds

5. **Documentation**
   - Update this document with findings
   - Document runbooks for common issues
   - Create incident response procedures

---

## 🚨 Incident Response

### If External Monitors Spike
1. Check TrueNAS maintenance schedule
2. Review NFS latency in TrueNAS UI
3. Temporarily disable affected monitors
4. Wait for maintenance window to complete
5. Re-enable monitors

### If AdGuard Down
1. Check keepalived VIP status
2. Verify both AdGuard instances running
3. Test failover by flushing DNS
4. Check Nginx upstream health

### If CrowdSec Not Banning
1. Verify bouncer running on both nodes
2. Check LAPI API key configured
3. Review bouncer config
4. Restart bouncer service

### If Push Monitors Show DOWN
1. Verify heartbeat-pusher running
2. Check KUMA_MONITORS environment variable
3. Validate push tokens in Kuma UI
4. Review push logs for errors

---

**Document maintained by:** twosclaw  
**Review frequency:** Weekly  
**Last heartbeat:** 2026-04-07