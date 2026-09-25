# Plan: Migrate Home Assistant to a Home Assistant OS VM on TrueNAS (keeping history)

## Problem

Home Assistant runs as the TrueNAS catalog app `home-assistant`, which TrueNAS has
deprecated: *"will be removed in a future release. Run Home Assistant OS in a VM
instead."* Expected removal is around November 2026 (the notice itself gives no date).

The notice also warns: history and long-term statistics live in the app's bundled
**PostgreSQL**, which is **not** included in Home Assistant backups and **is deleted
with the app**. Preserving history is a requirement for this migration.

**Decided:** Home Assistant OS (HAOS) in a VM **on TrueNAS**, history preserved.
(Moving back to a NUC was ruled out: 3.8 GB RAM each, and nuc8-1 lost its Swarm
leadership to resource starvation on 2026-09-01.)

## Current State (verified 2026-09-24)

| | |
|---|---|
| App | `home-assistant`, image `homeassistant/home-assistant:2026.9.2`, chart 1.8.60 |
| Limits | 4 CPU, 4096 MB |
| Config | host path `/mnt/newton/appdata/homeassistant` (297 MB) |
| Recorder | `postgres:17.11-bookworm` in the same app, `ix_volume` `postgres_data`; `purge_keep_days: 30` |
| Size | 256 devices, 2,824 entities, 37 integration domains |
| Custom | HACS + 12 custom components (adaptive_lighting, dreo, ha_hatch, smartthinq_sensors, tesla_custom, watchman, …) |
| Radio | Nabu Casa SkyConnect v1.0 (`10c4:ea60`, CP210x) → `/dev/ttyUSB0`, used by **ZHA** (`ezsp`, 115200) |
| Network | isolated app network, published on `192.168.0.196:20810` — so mDNS/SSDP/HomeKit discovery do not work today |
| Last HA backup | **2024-01-25** (`backups/ce63302d.tar`) |

**TrueNAS host:** 25.10.7, 8 cores, 31 GB RAM (ARC currently 14 GB and shrinkable),
virtualization supported, bridge **`br0`** already exists on `enp0s31f6`
(`192.168.0.196`) — the VM can get its own LAN IP with no host network changes.
A stopped VM `openclaw` has 8 GB assigned; don't run both on a tight day.

### Things that point at HA (and whether they change)

| Reference | Value | Change? |
|---|---|---|
| NPM proxy host **1** (`ha.swarm.localdomain`, `ha.whatasave.space`, `homeassistant.whatasave.space`) | `http://192.168.0.196:20810`, websockets on | **Yes** → `http://<VM IP>:8123` |
| HA `internal_url` / `external_url` | `http://ha.swarm.localdomain` / `http://homeassistant.whatasave.space` | No (both go through NPM) |
| MQTT integration | broker `192.168.0.101:1883` (teslamate mosquitto) | No |
| UniFi / UniFi Protect | `192.168.0.1` | No |
| AdGuard integration | `adguard2.swarm.localdomain` | No — but the VM must resolve `*.swarm.localdomain` |
| tesla_custom | `https://tesla.whatasave.space` (tesla-http-proxy on nuc8-1, over HTTPS, no filesystem coupling) | No |
| Mobile apps (8) | external URL | No |
| Kuma | check for any monitor on `192.168.0.196:20810` directly | Maybe |
| Repo / memory | `AGENTS.md`, memory `reference_ha_config_path` → `/mnt/newton/appdata/homeassistant` | **Yes**, after cutover |

### Two findings that shape the plan

1. **The app rewrites `configuration.yaml` on every start.** Its `init` container
   (`mikefarah/yq`) writes the live `recorder: db_url: postgresql://…@postgres:5432/…`
   line. After migration nothing does — that line must be **removed by hand** or the
   recorder will try to reach a host that no longer exists.
2. **No `http:` reverse-proxy config exists in YAML**, yet HA accepts proxied requests
   and even a forged `X-Forwarded-For` (→ 200). The settings are probably in the root-only
   `.storage/http` (written by the app on Sep 17). On the VM, NPM connects directly, so
   **HAOS needs an explicit `http:` block** or every proxied request will get a 400.

## Decision: How to Carry the History

| | **A. Convert to SQLite** (recommended) | B. Keep PostgreSQL via an HAOS add-on |
|---|---|---|
| Migration mechanism | Cross-engine copy, PG → SQLite, same schema version | `pg_dump` → `pg_restore`, same engine |
| Migration risk | Higher, but rehearsed on a throwaway copy first | Lower |
| Long-term | HAOS default and best-supported; DB **inside HA backups** | Community add-on to maintain; extra RAM |
| Backups | One HA backup covers config + history | Add-on data is in HA backups too, but restore is two-part |

**Decided (2026-09-24): A — SQLite**, with a full rehearsal that has to pass before cutover, and
**B as the fallback** if the rehearsal can't be made to validate. 2,824 entities with a 30-day purge is
well within SQLite's comfort zone, and having the history *inside* the normal HA backup is
the durable fix for the "no backup since 2024" problem.

What must survive: **long-term statistics** (`statistics`, `statistics_meta` — kept forever;
energy dashboard, costs, solar forecast history). Short-term `states` only go back 30 days
anyway.

## Open Questions

- [x] **A vs B** — **A, SQLite** (user, 2026-09-24).
- [x] **Root steps on TrueNAS** — **no sudo needed.** Everything goes through the TrueNAS
      middleware API as `dlin` (`midclt`): `vm.device.convert` (qcow2 → zvol), `vm.create`,
      `vm.device.usb_passthrough_choices`, and throwaway custom apps via `app.create` for the dump
      and rehearsal. Needs an `autoMode.allow` entry in `.claude/settings.local.json` (user adds it;
      Claude can't edit its own permissions) — creates/updates/starts/stops only, deletes excluded.
- [x] **VM IP** — **DHCP-assigned**, no reservation (user). NPM host 1 points at that IP, so if the
      lease ever changes, HA goes unreachable through NPM until host 1 is updated — Kuma will flag it.
- [x] **nuc8-2's LAN IP** — `192.168.0.26` per user (confirm in Phase 0); needed for `trusted_proxies`. nuc8-1 is `192.168.0.101`.
- [ ] **Target cutover date** — before November; ideally mid-October to leave room for fallback B.

## Phases

### Phase 0 — Measure and Back Up (no downtime)

1. In HA: **Settings → System → Backups → create a full backup** (on this install it
   covers the config directory only). Record the **backup encryption key** in the password
   manager — restoring on HAOS needs it. Copy the backup file off to the NAS.
2. Take a **consistent dump while HA is running** (MVCC snapshot) to the NAS, and keep it
   permanently as insurance. No root: a throwaway custom app (`midclt call app.create`) running
   `postgres:17.11-bookworm`, joined to the HA app's network
   (`ix-internal-home-assistant-home-assistant-net`), runs
   `pg_dump -h postgres -U home-assistant -Fc home-assistant` into
   `/mnt/newton/appdata/ha-migration/ha-pg-<date>.dump` (credentials from `midclt call app.config home-assistant`).
   Delete the throwaway app afterwards (by hand — deletes aren't pre-approved).
3. Measure, from the same Postgres: DB size, row count per table, earliest `statistics`
   row, and `SELECT MAX(schema_version) FROM schema_changes`.
4. ~~Read `.storage/http`~~ — root-only and not needed; Phase 3 sets the `http:` block explicitly.
5. Confirm nuc8-2's LAN IP is `192.168.0.26` (`ssh nuc8-2 hostname -I`).
6. **Freeze the HA version at 2026.9.2** until cutover is done — don't update the app.
   Same version on both sides = identical recorder schema.

### Phase 1 — Rehearse the Conversion on TrueNAS (no production impact)

Throwaway custom apps on TrueNAS (`midclt call app.create`), working only on the dump — the live
app is not touched.

1. Start a scratch `postgres:17.11-bookworm` and `pg_restore` the Phase 0 dump into it.
2. Start a scratch `homeassistant/home-assistant:2026.9.2` with an empty config containing
   only a SQLite recorder; let it create the schema, then stop it. Confirm its
   `schema_changes` version equals Postgres's.
3. Copy all recorder tables PG → SQLite in foreign-key order (`event_types`, `event_data`,
   `events`, `states_meta`, `state_attributes`, `states`, `statistics_meta`, `statistics`,
   `statistics_short_term`, `statistics_runs`, `recorder_runs`, `migration_changes`),
   preserving primary keys. Run the copy script inside the HA 2026.9.2 image, which already
   has SQLAlchemy and the Postgres driver.
4. **Validate** — all must pass:
   - row count per table identical;
   - min/max IDs identical; earliest and latest `statistics.start_ts` identical;
   - `PRAGMA integrity_check` = `ok`;
   - boot the scratch HA (recorder + frontend only, **no integrations**) against the SQLite
     file: no recorder migration or errors, and long-term statistics visible in
     Developer Tools → Statistics.
5. **Time it.** The conversion time is the core of the cutover downtime.
6. Tear the scratch containers down.

If validation can't be made to pass → switch to **fallback B** before going further.

### Phase 2 — Build the VM (no downtime)

1. Download the HAOS **KVM (`.qcow2`)** image; convert it onto a new zvol
   (`qemu-img convert` to raw, written to the zvol) or use TrueNAS's disk-image import if
   25.10 offers one.
2. Create the VM: **UEFI**, 2 vCPU, **4 GB RAM**, **64 GB** disk (HAOS minimum is 32;
   headroom for SQLite and local backups), VirtIO NIC on **`br0`**.
3. Add a USB passthrough device for the SkyConnect (`10c4:ea60`) but **don't start the VM
   while the app is running** — both would try to open the stick.
4. IP comes from DHCP; note it on first boot.
5. Don't onboard yet. Onboarding is where the backup gets restored, at cutover.

### Phase 3 — Cutover (downtime window: conversion time + ~30–45 min)

1. Final HA backup (Phase 0 step 1) and final `pg_dump` (step 2), both while HA runs.
2. Convert the final dump with the Phase 1 script; run the Phase 1 validation checks.
3. **Stop** the `home-assistant` app — don't delete it. This releases the SkyConnect.
4. Start the VM → onboarding → **Restore from backup** (needs the encryption key).
   Confirm Core is **2026.9.2** after restore.
5. Install the **SSH** or **Samba** add-on. In `/config`:
   - **remove** the `recorder: db_url: postgresql://…@postgres…` line (keep `purge_keep_days`);
   - place the converted file as `/config/home-assistant_v2.db`;
   - add the reverse-proxy block:
     ```yaml
     http:
       use_x_forwarded_for: true
       trusted_proxies:
         - 192.168.0.101   # nuc8-1
         - 192.168.0.26    # nuc8-2, in case NPM moves
     ```
6. Restart Core. Check the log: recorder on SQLite, no migration or errors.
7. Repoint **NPM proxy host 1** to `http://<VM IP>:8123` (websockets stay on).
8. Verify:
   - **ZHA:** all Zigbee devices online, no re-pairing;
   - **History:** graphs go back to the pre-migration earliest date; energy dashboard
     shows past months;
   - `ha.whatasave.space` / `homeassistant.whatasave.space` → 200 through NPM, and no
     reverse-proxy errors in the log;
   - MQTT, tesla_custom, UniFi Protect, AdGuard integration all connected; the VM resolves
     `adguard2.swarm.localdomain`;
   - mobile app: open it and receive a test notification;
   - HACS and all 12 custom components loaded.

**History gap:** only between the final dump and the VM coming up — the length of the
window, nothing more.

### Phase 4 — After Cutover

1. **Automated backups:** HAOS daily backups to a TrueNAS SMB/NFS share (Settings →
   System → Storage → network storage), with the encryption key saved.
2. Kuma: confirm the HA monitor is green; repoint any monitor that targets `:20810` directly.
3. Let discovery work now that HA is on the LAN — review newly discovered devices.
4. Keep the stopped app for **14 days** as the rollback point.
5. Then delete the `home-assistant` app. This deletes its Postgres; the Phase 3 `pg_dump`
   stays on the NAS permanently.
6. Update the repo and memory: `AGENTS.md`, memory `reference_ha_config_path`,
   the tesla-http-proxy comment in `docker-compose.yaml`, and the `ops-log.md` entry.

## Rollback

Until the app is deleted (Phase 4 step 5): **stop the VM → start the `home-assistant` app.**
The old install comes back exactly as it was, with its Postgres intact, the SkyConnect back
in the app, and NPM host 1 pointed back at `192.168.0.196:20810`. The only loss is history
recorded on the VM during the attempt.

After deletion, the archived `pg_dump` plus the Phase 0 backup can rebuild the old
install on any Postgres.

## Status

- [ ] Phase 0 — Measure and back up
- [ ] Phase 1 — Rehearse conversion
- [ ] Phase 2 — Build VM
- [ ] Phase 3 — Cutover
- [ ] Phase 4 — After cutover
