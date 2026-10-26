## Ops Review — twosclub Homelab Services (security focus)
**Date:** 2026-10-26 | **Prepared by:** Ops | **Scope:** Docker Swarm (nuc8-1, nuc8-2), internet edge (Cloudflare Tunnel → NPM), TrueNAS 25.10.7, Home Assistant (HAOS VM) | **Severity:** SEV2+ items require action

### Executive Summary
Prompted by the Cloudflare cloudflared bulletin (eMudhra CA distrust) and the recent run of breaches. Nothing is actively compromised: no open WAN ports, everything external goes through the tunnel, and CrowdSec sees only routine scanner noise (http-probing / sensitive-files from cloud IPs). The real problem was **patch drift on the parts that matter most**. The Docker apt repo had been silently disabled since the 22.04→24.04 upgrade, which froze Docker/containerd/runc at 28.3.3 / 1.7.27 / 1.2.5 (before the Nov 2025 runc container-escape fixes). The internet-facing reverse proxy (NPM) was a two-year-old 2.11.3. cloudflared was 2026.3.0. Both NUCs have run **18 weeks without a reboot** with eight newer kernels staged. Today's session fixed everything except the reboots, which need the user. What remains is mostly hardening: HA login protection, CrowdSec remediation for tunnel traffic, Mosquitto auth, SSH password auth, and TrueNAS service sprawl.

### SEV1 — Critical
None.

### SEV2 — High
1. **Container runtime frozen since the OS upgrade (FIXED).** `/etc/apt/sources.list.d/docker.list` was renamed `.distUpgrade` during the 24.04 upgrade and still pointed at `jammy`. Both nodes are now on the `noble` repo: Docker **29.9.2**, containerd **2.4.2**, runc **1.5.2**. All services came back at full replicas with overlay IPs.
2. **Kernels not booted: 18 weeks uptime, running 6.8.0-124 with 6.8.0-147 installed (FIXED — rolling reboots, nuc8-2 then nuc8-1).** unattended-upgrades is working, but kernel and libc fixes don't apply until a reboot. The auto-mode classifier blocked `systemctl reboot`, so the user has to run it or allow it. Order: nuc8-2 first, then nuc8-1. Services are pinned per node, so a reboot means a few minutes of downtime for that node's services; nothing migrates.
3. **NPM, the internet front door, was 2.11.3 from Oct 2024 (FIXED → 2.16.0).** Tested first on a copy of `/data` (migrations clean, `nginx -t` ok, the S6 ownership hook still works). Backup: `~/backups/npm-2.11.3-20261026` on nuc8-1.
4. **cloudflared 2026.3.0 trusted the compromised eMudhra intermediates (FIXED → 2026.10.3).** It's pinned now, with start-first updates.
5. **Home Assistant is internet-exposed with no MFA and no brute-force banning (OPEN).** Neither person has TOTP enrolled. `.storage/http` has `ip_ban_enabled: true` but `login_attempts_threshold: -1`, which means it never bans. `trusted_proxies` is the whole `192.168.0.0/24` rather than the NPM nodes.
   - Fix: each user enrolls TOTP (Profile → Security → Multi-factor authentication modules).
   - Then set the threshold to ~10 and narrow `trusted_proxies` to 192.168.0.101, 192.168.0.26 and the overlay range. This needs a Core restart.
6. **CrowdSec detects but can't block internet attackers (OPEN).** Tunnel traffic arrives over cloudflared's outbound connection, so the iptables bouncer on nuc8-1 never sees attacker IPs. Detections (real IPs via `CF-Connecting-IP`) produce no remediation. Options:
   - CrowdSec's Cloudflare Worker bouncer. The old cloudflare-bouncer was dropped because of IP-list caps; the Worker variant doesn't use lists.
   - The OpenResty/Lua bouncer inside NPM.

### SEV3 — Medium
1. **Stale images (FIXED).** 18 floating-tag swarm services were 3–9 months old and have been re-pulled. recyclarr broke because upstream dropped `:latest`; it's pinned to `:8` now (was 8.3.2).
2. **TrueNAS apps: 16 behind (14 FIXED).** Plex 1.3.16→1.3.20 and Jellyfin 1.3.18→1.3.22 are deferred because a stream was active. TrueNAS OS 25.10.7 is current.
3. **HA Core 2026.10.2 → 2026.10.4 (FIXED).**
4. **Mosquitto has no auth (OPEN).** It runs `mosquitto-no-auth.conf` with 1883 published on both nodes through the ingress mesh. Any LAN or Wi-Fi device can read TeslaMate's live car location/state and publish fake values into HA. Fix: a password file with users for TeslaMate and HA, then update both clients.
5. **SSH password auth is on everywhere (OPEN).** NUCs: `PasswordAuthentication yes`. TrueNAS: `passwordauth: true`, and `dlin` has SSH password enabled. Keys already work for all of them. Fix: key-only, after confirming every client you SSH from has a key.
6. **The Tesla command proxy is public (OPEN).** `tesla.whatasave.space` `/` proxies to `tesla_http_proxy:4430`. It needs a Fleet API token to do anything (anonymous requests get 403), but only the `/.well-known/…public-key.pem` path has to be public. Fix: restrict `/` to LAN in NPM's advanced config, after confirming how HA reaches it.
7. **TrueNAS service sprawl (OPEN).**
   - FTP is running in plaintext (`tls: false`, `onlylocal: false`), presumably for PS4 installs.
   - The iSCSI service is running with **zero targets**.
   - NFS shares have no network or host restriction (`/mnt/newton/ps4` has `maproot=root`).
   - The web UI serves HTTP on :80 with no HTTPS redirect.
   - Fixes: stop iSCSI; restrict NFS to the NUC IPs (plus the PS4 where needed); turn FTP off when not installing; turn on the HTTPS redirect.

### SEV4 — Low
1. **Stale tunnel hostnames.** `tracearr` (its NPM host was deleted but `64.conf` was still loaded; conf removed today) and `teslamate` (root returns 502; only the key file is needed) can go. `piaware`/`adsbx` are down (Pi .17 is offline). `epic` returns 502.
2. **glances has the Docker socket.** `:ro` on a socket mount doesn't restrict the API, and glances' API on :61208 is unauthenticated on the LAN. It only reads, so this is acceptable, but it's an escalation path if glances is ever compromised.
3. **TeslaMate 4.0.1 → 4.4.0 available.** Pinned; take a pg_dump before bumping.
4. **heartbeat-pusher on `curlimages/curl:8.6.0` (Jan 2024).** Internal only; bump to a current pinned tag.
5. **The `CF_API_TOKEN` in `.env` can't list tunnels.** Add `Cloudflare Tunnel:Read` if we want tunnel-config audits by API.

### Observations (No Action Required)
- unattended-upgrades runs daily on both NUCs; 0 security updates were pending before today's full-upgrade.
- CrowdSec 1.7.7 parses all NPM logs. Alerts this week: scanner noise from GCP/cloud IPs only.
- No WAN port forwards are needed. NPM's admin :81 and the published service ports are LAN-only.
- The *arr apps now redirect to their login pages after the image refresh (auth is no longer "disabled for local addresses").
- Every public NPM host redirects to its app's login, except piaware/adsbx (read-only maps), the Komga and Shelfmark SPAs (both enforce auth on their APIs), and `status` (the public Kuma page).

### Prioritized Action List
| Priority | Item | Effort | Status |
|---|---|---|---|
| P1 | Reboot nuc8-2, then nuc8-1 (kernel 6.8.0-147) | 15 min | ✅ |
| P1 | Enroll HA TOTP for both users; set `login_attempts_threshold`, narrow `trusted_proxies` | 20 min | open |
| P2 | CrowdSec remediation for tunnel traffic (CF Worker bouncer or NPM Lua bouncer) | 1–2 h | open |
| P2 | Mosquitto auth (TeslaMate + HA credentials) | 30 min | open |
| P2 | SSH key-only on NUCs + TrueNAS | 15 min | open |
| P3 | Plex + Jellyfin app upgrades (when no one is streaming) | 5 min | open |
| P3 | TrueNAS: stop iSCSI, restrict NFS, FTP off/TLS, HTTPS redirect | 30 min | open |
| P3 | Restrict `tesla.whatasave.space /` to LAN | 15 min | open |
| P4 | Remove stale tunnel hostnames (tracearr, teslamate root, piaware/adsbx if retired) | 10 min | open |
| P4 | TeslaMate 4.4.0, heartbeat-pusher curl bump | 20 min | open |
| done | Docker 29.9.2 / containerd 2.4.2 / runc 1.5.2 on both nodes; NPM 2.16.0; cloudflared 2026.10.3; 18 image refreshes; 14 TrueNAS apps; HA 2026.10.4 | — | ✅ |
