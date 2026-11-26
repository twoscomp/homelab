# Are the kids outside?

A tiny public page for the neighbors that answers one question: are our kids outside right now?
Home Assistant pushes the state; the page shows **YES.**, **NOT RIGHT NOW.** or **NOT SURE.**,
when it was last updated, and the local weather. No names, photos or address appear anywhere.

```
HA input_boolean.kids_outside ──rest_command (Bearer token, every change + every 5 min)──▶ Fly app ──▶ neighbors' browsers
```

## App (`server.js`)

Node 22, no dependencies, state in memory only (a restart shows NOT SURE until the next push, at most 5 minutes).

| Route | |
|---|---|
| `GET /` | The page. Refreshes every 60 s; `noindex`, strict CSP (`img-src data: 'self'`, `manifest-src 'self'`), `no-store`. |
| `GET /api/status` | Public, CORS `*`: `{"outside": true\|false\|null, "state": "outside\|inside\|unknown", "updated_at": ISO\|null}` |
| `POST /api/status` | `Authorization: Bearer $PUSH_TOKEN` (timing-safe), body ≤ 512 B: `{"outside": bool, "expires_at": unix}` |
| `GET /healthz` | Fly health check. |
| `GET /apple-touch-icon.png`, `/icon-192.png`, `/icon-512.png`, `/site.webmanifest` | Home-screen icon and manifest (label "Kids outside?"). |

Rules:
- **unknown** if there has been no push, or the last one is more than 15 minutes old.
- **outside** if the last push said outside and its expiry hasn't passed. Expiry is the sent `expires_at`
  if it's in the future, otherwise now + 15 min, and never more than now + 4 h.
- **inside** otherwise.

Page design: each state has its own color, inline SVG icon and favicon so it reads without the words and
without relying on color (YES = green, ball under a sun, gently bounces 3 times unless reduced motion is on;
NOT RIGHT NOW = blue, house; NOT SURE = gray, question mark). The tab title starts with the answer;
`og:title` stays generic so a link preview in a text thread never freezes an old answer. Light and dark
themes; all text is WCAG AAA contrast.

Home-screen icon: a beach ball on the lawn under the sun, drawn per pixel in `iconColor()` and PNG-encoded with
`node:zlib` at startup (about 0.5 s; 180, 192 and 512 px). No image files. The ball and sun sit inside
Android's maskable safe zone; iOS rounds the corners itself. `apple-mobile-web-app-title` and the manifest's
`short_name` keep the home-screen label fixed, since the tab title changes with the answer.

Weather comes from Open-Meteo (no key), cached 10 minutes, for `WEATHER_LAT`/`WEATHER_LON` rounded to one
decimal (~10 km). Rain chance shows only at 20%+, UV only at 6+ (both in amber). A Daylight row shows
"Sunset in 1 h 20 min (5:21 pm)", then "Getting dark" / "Dark out" after sunset and before sunrise.

Environment (all via `fly secrets`, never in git):

| Variable | |
|---|---|
| `PUSH_TOKEN` | `openssl rand -hex 32`. POST is disabled if it's missing or shorter than 32 chars. |
| `WEATHER_LAT`, `WEATHER_LON` | Home area, rounded to 1 decimal. Weather is hidden if unset. |

## Deploy (Fly.io)

Live at **https://kids-outside.fly.dev** (app `kids-outside`, Daniel's personal org, region dfw, one
always-on shared-cpu-1x 256 MB machine, ~$2/month; shared IPv4 + IPv6). No custom domain yet.

First time (from this folder):

```sh
fly launch --no-deploy --copy-config --name <free-app-name> --region dfw
# secrets via stdin so values don't land in shell history
printf 'PUSH_TOKEN=%s\nWEATHER_LAT=%s\nWEATHER_LON=%s\n' "$TOKEN" "$LAT" "$LON" | fly secrets import --stage
fly deploy --ha=false
# if the first deploy says "Failed to provision IP addresses" (it did on 2026-11-26):
fly ips allocate-v4 --shared && fly ips allocate-v6
```

Updates: `fly deploy --ha=false`. Rotate the token: `fly secrets set PUSH_TOKEN=...` (via `import` as above)
and update `kids_status_auth` in HA's `secrets.yaml`, then reload REST commands.

Local: `PUSH_TOKEN=$(openssl rand -hex 32) node server.js` (port 8080), or `docker compose up --build`
with a `.env` next to `compose.yaml`.

## Home Assistant (`ha/kids_outside.yaml`)

Installed as a package on the HAOS VM: `/config/packages/kids_outside.yaml`, loaded by
`homeassistant: packages: !include_dir_named packages` in `configuration.yaml`. `secrets.yaml` holds
`kids_status_url` (`https://<app>.fly.dev/api/status`) and `kids_status_auth` (`"Bearer <token>"`).

- `input_boolean.kids_outside`: the toggle (tile on the Home dashboard).
- Turning it on starts `timer.kids_outside_auto_off` (1:30, survives restarts). Turning it on between
  sunset + 30 min and sunrise flips it straight back off with a phone notice.
- 10 minutes before the timer ends, the phone gets a reminder with **30 more minutes** (restarts the timer
  at 0:30) and **Turn off now**.
- Timer end turns it off and notifies; it also turns off at sunset + 30 min.
- On HA start it turns off unless the timer is still running.
- `rest_command.kids_status_push` runs on every toggle change, every timer `finishes_at` change, every 5
  minutes and at HA start. The pushed `expires_at` is the timer's end, so the page drops to NOT RIGHT NOW
  on time even if HA goes quiet, and to NOT SURE 15 minutes after the last push.

Notifications go to `notify.mobile_app_pixel_10a`; change it there if the phone changes.
After editing: Developer tools → YAML → check config, then reload Input booleans, Timers, REST commands and
Automations. No restart needed for edits; the first install needed one restart because `rest_command` wasn't
loaded before, so it had no reload service.
