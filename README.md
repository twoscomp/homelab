# homelab

Docker Swarm stacks for homelab services running on Intel NUCs, integrated with TrueNAS for storage and media applications. Includes Cloudflare Tunnel for external access without open WAN ports and CrowdSec IPS for intrusion prevention.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      TrueNAS Server                         │
│  Plex, Download clients, Home Assistant, TrueNAS Apps       │
│  NFS: /mnt/newton/media (192.168.0.196)                     │
└──────────────────────────┬──────────────────────────────────┘
                           │ NFS
┌──────────────────────────┴──────────────────────────────────┐
│                    Docker Swarm Cluster                      │
│  ┌───────────────────┐          ┌───────────────────┐       │
│  │   nuc8-1 (mgr)    │          │   nuc8-2 (wkr)    │       │
│  │  Media (light),   │          │  Sonarr, Radarr,  │       │
│  │  Security         │          │  Prowlarr,        │       │
│  │                   │          │  TeslaMate        │       │
│  └───────────────────┘          └───────────────────┘       │
│  Shared: AdGuard Home (x2), Nginx Proxy Manager             │
└─────────────────────────────────────────────────────────────┘
```

## Stacks

### tier1.yaml - Core Infrastructure
| Service | Description |
|---------|-------------|
| Nginx Proxy Manager | Reverse proxy and SSL certificate management (1 replica, pinned to nuc8-1) |

### adguard-standalone.yaml - DNS (standalone, not Swarm)
| Service | Description |
|---------|-------------|
| AdGuard Home (x2) | DNS ad-blocking with host networking; managed via keepalived VIP failover |

### security.yaml - Security
| Service | Description |
|---------|-------------|
| cloudflared | Cloudflare Tunnel — routes external traffic in without open WAN ports |
| crowdsec | Lightweight IPS — parses NPM logs and bans malicious IPs via iptables bouncer |

### media.yaml - Media Management
| Service | Description |
|---------|-------------|
| Overseerr | Request management for Plex |
| Radarr | Movie management |
| Sonarr | TV show management |
| Lidarr | Music management |
| Readarr | Book management |
| Mylar3 | Comic management |
| Prowlarr | Indexer management |
| Bazarr | Subtitle management |
| Tautulli | Plex statistics |
| Komga | Comic/manga server |
| Recyclarr | TRaSH guides sync for Radarr/Sonarr |
| cross-seed | Cross-seeding automation |
| Kometa | Plex metadata management |
| Maintainerr | Plex library maintenance |
| Epic Games | Free games claimer |

### teslamate.yaml - Tesla Vehicle Tracking
| Service | Description |
|---------|-------------|
| TeslaMate | Tesla data logging |
| PostgreSQL | TeslaMate database |
| Grafana | TeslaMate dashboards |
| Mosquitto | MQTT broker |

### docker-compose.yaml - Tesla Integration
| Service | Description |
|---------|-------------|
| Tesla HTTP Proxy | Tesla Fleet API proxy for Home Assistant |

### monitoring.yaml - Uptime Monitoring
| Service | Description |
|---------|-------------|
| heartbeat-pusher | Alpine cron container — checks internal services every minute and forwards heartbeats to the external fly.io Uptime Kuma |

## External Services

### fly/uptime-kuma — Public Status Page

Uptime Kuma hosted on [fly.io](https://fly.io) at `https://dlin-uptime-kuma.fly.dev`, providing a public status page without requiring VPN or LAN access.

- **Direct HTTP monitors**: Overseerr, Calibre, Komga, Plex (public `whatasave.space` URLs)
- **Push monitors**: Sonarr, Radarr, Readarr, Bazarr, Prowlarr, QBT, NZBGet, Mylar, Teslamate, Maintainerr — heartbeats sent by `heartbeat-pusher` in `monitoring.yaml`
- **Notifications**: Discord (`twosclub` and `twosclub Admins` webhooks), Email

Initial deploy (one-time):
```bash
cd fly/uptime-kuma
flyctl volumes create uptime_kuma_data --region ord --size 1
flyctl deploy
```

After deploy: open the Kuma UI, create Push-type monitors for each internal service, copy the push tokens, and fill in `KUMA_MONITORS` in `.env`.

## Setup

### Prerequisites
- Docker Swarm initialized across nodes
- TrueNAS NFS share accessible
- keepalived installed on both nodes (for AdGuard VIP failover)

### Environment
Copy `.env.orig` to `.env` and configure:
```bash
DATADIR=/mnt/dockerData      # Shared Docker data
SERVARRDIR=/servarrData/     # Local *arr app configs
TZ=America/Chicago
PUID=1000
PGID=1001
PUID_APPS=568                # UID for app-specific containers (e.g. linuxserver)
KUMA_MONITORS=               # Internal service check/push URL pairs for fly.io Uptime Kuma (see .env.orig)
```

### Deploy Stacks
All stack commands run on **nuc8-1** (Swarm manager) from `~/homelab/`. Use docker-compose to process `.env` variables before deploying:
```bash
docker-compose -f tier1.yaml config | docker stack deploy -c - tier1
docker-compose -f media.yaml config | docker stack deploy -c - media
docker-compose -f security.yaml config | docker stack deploy -c - security
docker-compose -f teslamate.yaml config | docker stack deploy -c - teslamate
docker-compose -f docker-compose.yaml config | docker stack deploy -c - tesla
docker-compose -f monitoring.yaml config | docker stack deploy -c - monitoring
```

AdGuard Home is managed separately (not a Swarm stack):
```bash
# On each node
docker-compose -f adguard-standalone.yaml up -d
```

## Network Setup

AdGuard Home uses host networking with keepalived Virtual IP (VIP) failover. See `keepalived/README.md` for setup details.

The main Docker Swarm overlay network is named `smarthomeserver` (historical name, kept as-is to avoid disrupting live services — renaming would require redeploying all stacks). Create it before first deploy:
```bash
docker network create --driver overlay --attachable smarthomeserver
```

## Security Bootstrap

CrowdSec (in `security.yaml`) uses an **iptables bouncer** that runs on the Docker host, outside the container. After deploying `security.yaml`, the bouncer must be set up manually on each node that should enforce bans:

```bash
# 1. Add CrowdSec packagecloud apt repo (see https://packagecloud.io/crowdsec/crowdsec)
curl -s https://packagecloud.io/install/repositories/crowdsec/crowdsec/script.deb.sh | sudo bash

# 2. Install the firewall bouncer
sudo apt install crowdsec-firewall-bouncer-iptables

# 3. Register the bouncer with the local CrowdSec LAPI
#    (run from within the crowdsec container to get the API key)
docker exec -it <crowdsec_container> cscli bouncers add firewall-bouncer

# 4. Configure the bouncer
#    Edit /etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml
#    Set api_url: http://localhost:8080  (CrowdSec LAPI port, published in security.yaml)
#    Set api_key: <key from step 3>

# 5. Start and enable
sudo systemctl enable --now crowdsec-firewall-bouncer
```

### NPM Real IP Passthrough

For CrowdSec to see real client IPs (not Cloudflare or internal IPs), nginx must be told to trust upstream proxies and read the `CF-Connecting-IP` header. This is configured **globally** via NPM's custom config mechanism — do **not** add real IP directives per proxy host in the NPM UI.

The file `/mnt/dockerData/nginx-package-manager/data/nginx/custom/server_proxy.conf` is automatically included in every NPM proxy host server block. It must contain:

```nginx
# Trust the Docker overlay network (where cloudflared runs)
set_real_ip_from 10.0.1.0/24;
# Trust Cloudflare public IP ranges
set_real_ip_from 103.21.244.0/22;
# ... (all Cloudflare ranges)
real_ip_header CF-Connecting-IP;
real_ip_recursive on;
```

After editing this file, reload nginx inside the NPM container:
```bash
docker exec $(docker ps -q -f name=tier1_nginx) nginx -s reload
```

> **Note:** NPM's include pattern is `server_proxy[.]conf` (literal filename), so the file must be named exactly `server_proxy.conf`. Files with other names (e.g. `server_proxy_cf_realip.conf`) will not be included.

See inline comments in `security.yaml` for additional context.

## Secrets & Git Hygiene

`.env` is **gitignored** — all credentials and secrets belong there only, referenced in stack yamls as `${VAR_NAME}`. Never commit `.env` or hardcode credentials in yaml files.

Git history was sanitized with `git filter-repo` (Feb 2026) to remove previously committed secrets. If you need to add a new secret, add it to `.env` and reference it via the environment variable pattern already used throughout the stack files.

## Data Paths

| Path | Location | Purpose |
|------|----------|---------|
| `/mnt/dockerData` | Shared | General container data |
| `/servarrData` | Local (both nodes) | *arr app configurations (SQLite — must be local disk) |
| `/docker-binds/teslamate` | Local (nuc8-2) | TeslaMate data |
| NFS mount | TrueNAS | Media files |

## Useful Commands

### Deploy with Environment Variables
Docker stack deploy doesn't read `.env` files. Use docker-compose v1 to process variables:
```bash
docker-compose -f media.yaml config | docker stack deploy -c - media
```

### Force Update Service Images
Swarm pins image digests. Force pull fresh images when upstream updates:
```bash
docker service update --image lscr.io/linuxserver/sonarr:latest --force media_sonarr
```

### Debug Failing Services
Check task history with full error messages:
```bash
docker service ps media_sonarr --no-trunc
```

Check service logs:
```bash
docker service logs media_sonarr --tail 50
```

### Test Internal Service Connectivity
```bash
docker run --rm --network smarthomeserver curlimages/curl:latest http://sonarr:8989/
```

### Inspect Service Configuration
Check pinned image digest:
```bash
docker service inspect media_sonarr --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'
```

Check mount configuration:
```bash
docker service inspect media_sonarr --format '{{json .Spec.TaskTemplate.ContainerSpec.Mounts}}' | jq .
```

### Scale Services
```bash
docker service scale media_sonarr=0  # Stop
docker service scale media_sonarr=1  # Start
```

### Remove Orphaned Services
Services not in YAML but still running from previous deploys:
```bash
docker service rm media_calibre
```
