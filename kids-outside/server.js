'use strict';
// "Are the kids outside?" status page for the neighbors.
// No dependencies; Node 22+. State lives in memory only.

const http = require('node:http');
const crypto = require('node:crypto');
const zlib = require('node:zlib');

const PORT = Number(process.env.PORT) || 8080;
const PUSH_TOKEN = process.env.PUSH_TOKEN || '';
const TZ = 'America/Chicago';

const MIN = 60 * 1000;
const STALE_MS = 15 * MIN;        // no push for this long -> unknown
const DEFAULT_TTL_MS = 15 * MIN;  // outside with no future expiry
const MAX_TTL_MS = 4 * 60 * MIN;  // expiry cap
const WEATHER_TTL_MS = 10 * MIN;
const MAX_BODY = 512;

function roundCoord(v) {
  const n = Number(v);
  return v !== undefined && v !== '' && Number.isFinite(n) ? Math.round(n * 10) / 10 : null;
}
const LAT = roundCoord(process.env.WEATHER_LAT);
const LON = roundCoord(process.env.WEATHER_LON);

if (PUSH_TOKEN.length < 32) console.warn('PUSH_TOKEN missing or short: POST /api/status is disabled');
if (LAT === null || LON === null) console.warn('WEATHER_LAT/WEATHER_LON not set: weather is hidden');

// ---- state ---------------------------------------------------------------

let last = null; // { outside: bool, expiresAt: ms|null, updatedAt: ms }

function currentState(now = Date.now()) {
  if (!last || now - last.updatedAt > STALE_MS) return 'unknown';
  return last.outside && now < last.expiresAt ? 'outside' : 'inside';
}

function statusJson(now = Date.now()) {
  const state = currentState(now);
  return {
    outside: state === 'unknown' ? null : state === 'outside',
    state,
    updated_at: last ? new Date(last.updatedAt).toISOString() : null,
  };
}

function applyPush(body, now = Date.now()) {
  let expiresAt = null;
  if (body.outside) {
    const sent = typeof body.expires_at === 'number' && Number.isFinite(body.expires_at)
      ? body.expires_at * 1000 : 0;
    expiresAt = sent > now ? sent : now + DEFAULT_TTL_MS;
    expiresAt = Math.min(expiresAt, now + MAX_TTL_MS);
  }
  last = { outside: body.outside, expiresAt, updatedAt: now };
}

// ---- auth ----------------------------------------------------------------

const TOKEN_DIGEST = crypto.createHash('sha256').update(PUSH_TOKEN).digest();

function authorized(header) {
  if (PUSH_TOKEN.length < 32 || typeof header !== 'string') return false;
  const m = /^Bearer (\S+)$/.exec(header);
  if (!m) return false;
  // Hash both sides so the comparison is constant-time regardless of length.
  const got = crypto.createHash('sha256').update(m[1]).digest();
  return crypto.timingSafeEqual(got, TOKEN_DIGEST);
}

// ---- weather (Open-Meteo, no key) ------------------------------------------

const WMO = {
  0: 'Clear', 1: 'Mostly clear', 2: 'Partly cloudy', 3: 'Cloudy',
  45: 'Fog', 48: 'Freezing fog',
  51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle',
  56: 'Freezing drizzle', 57: 'Freezing drizzle',
  61: 'Light rain', 63: 'Rain', 65: 'Heavy rain',
  66: 'Freezing rain', 67: 'Freezing rain',
  71: 'Light snow', 73: 'Snow', 75: 'Heavy snow', 77: 'Snow grains',
  80: 'Light showers', 81: 'Showers', 82: 'Heavy showers',
  85: 'Snow showers', 86: 'Heavy snow showers',
  95: 'Thunderstorms', 96: 'Thunderstorms with hail', 99: 'Thunderstorms with hail',
};

let weatherCache = null; // { at: ms, data }
let weatherInflight = null;

async function fetchWeather() {
  const params = new URLSearchParams({
    latitude: String(LAT),
    longitude: String(LON),
    current: 'temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_gusts_10m,is_day',
    hourly: 'precipitation_probability,uv_index',
    daily: 'temperature_2m_max,temperature_2m_min,sunrise,sunset',
    temperature_unit: 'fahrenheit',
    wind_speed_unit: 'mph',
    timezone: TZ,
    forecast_days: '1',
  });
  const res = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`, {
    signal: AbortSignal.timeout(4000),
  });
  if (!res.ok) throw new Error(`open-meteo HTTP ${res.status}`);
  const j = await res.json();
  const c = j.current;
  // Hourly arrays are local-time ISO strings; find the current hour.
  const hourKey = c.time.slice(0, 13); // "YYYY-MM-DDTHH"
  let i = j.hourly.time.findIndex((t) => t.startsWith(hourKey));
  if (i < 0) i = 0;
  const rain = Math.max(...j.hourly.precipitation_probability.slice(i, i + 3).map((v) => v ?? 0));
  return {
    temp: Math.round(c.temperature_2m),
    feels: Math.round(c.apparent_temperature),
    sky: WMO[c.weather_code] || 'Unknown',
    code: c.weather_code,
    day: c.is_day !== 0,
    wind: Math.round(c.wind_speed_10m),
    gust: Math.round(c.wind_gusts_10m),
    high: Math.round(j.daily.temperature_2m_max[0]),
    low: Math.round(j.daily.temperature_2m_min[0]),
    rain,
    uv: Math.round(j.hourly.uv_index[i] ?? 0),
    sunrise: j.daily.sunrise?.[0], // local "YYYY-MM-DDTHH:MM"
    sunset: j.daily.sunset[0],
  };
}

async function getWeather() {
  if (LAT === null || LON === null) return null;
  const now = Date.now();
  if (weatherCache && now - weatherCache.at < WEATHER_TTL_MS) return weatherCache.data;
  if (!weatherInflight) {
    weatherInflight = fetchWeather()
      .then((data) => { weatherCache = { at: Date.now(), data }; })
      .catch((err) => console.warn(`weather fetch failed: ${err.message}`))
      .finally(() => { weatherInflight = null; });
  }
  await weatherInflight;
  // Fall back to a stale reading for up to an hour rather than showing nothing.
  if (weatherCache && Date.now() - weatherCache.at < 60 * MIN) return weatherCache.data;
  return null;
}

// ---- page ----------------------------------------------------------------

// Each state has its own color, icon shape and tab title, so it reads at a glance without the words
// and without relying on color (green / blue / gray stay distinct with red-green color blindness).
const CSS = `
:root{color-scheme:light dark;--fg:#161616;--mute:#454545;--warn:#8a4600;--card:#fff;--line:#00000024;--bg:#efefec;--ink:#3b3b3b;--acc:#6b6b6b}
.outside{--bg:#e4f3e7;--ink:#0d5524;--acc:#1f8a3e}
.inside{--bg:#e7eef9;--ink:#183c80;--acc:#2f5fb8}
@media (prefers-color-scheme:dark){:root{--fg:#f3f3f3;--mute:#bcbcbc;--warn:#ffc46b;--card:#ffffff10;--line:#ffffff2a;--bg:#161616;--ink:#dadada;--acc:#8c8c8c}
.outside{--bg:#0b1c10;--ink:#8ae8a5;--acc:#3fb866}.inside{--bg:#0c1426;--ink:#b4ccff;--acc:#5d8ae6}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:18px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main,footer{max-width:30rem;margin:0 auto;padding:0 16px}
.hero{text-align:center;padding:1.25rem 0 1.5rem}
h1{font-size:1.25rem;line-height:1.25;font-weight:700;margin:0}
.icon{display:block;width:7.5rem;height:7.5rem;margin:.5rem auto .25rem;overflow:visible}
.fill{fill:var(--acc)}.cut{fill:var(--bg)}.lit{fill:#ffcf40}
.ball circle,.ball path{stroke:#1d1d1d;stroke-width:2.5}.shadow{fill:var(--fg);opacity:.16}
.ball,.shadow{transform-box:fill-box;transform-origin:50% 100%}
.q{fill:var(--bg);font:900 64px system-ui,-apple-system,sans-serif}
.answer{font-size:clamp(2.5rem,14vw,5rem);font-weight:900;line-height:1;letter-spacing:-.02em;margin:0 0 .75rem;color:var(--ink)}
.line{font-size:1.2rem;font-weight:600;margin:0 auto .4rem;max-width:22rem;text-wrap:balance}
.updated{color:var(--mute);margin:0;font-size:1rem}
.weather{background:var(--card);border:1px solid var(--line);border-radius:1rem;padding:1rem}
.weather h2{font-size:.85rem;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0}
.now{display:flex;align-items:center;gap:.75rem;margin:.25rem 0 .75rem}
.sky{width:3rem;height:3rem;flex:none}
.temp{font-size:2.25rem;font-weight:800;line-height:1}
.desc{font-size:1.1rem;font-weight:600}
.weather dl{display:grid;grid-template-columns:1fr 1fr;gap:.6rem 1rem;margin:0}
.weather dl div{border-top:1px solid var(--line);padding-top:.5rem;min-width:0}
.weather dt{font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;font-weight:600;color:var(--mute)}
.weather dd{margin:0;font-weight:600}
.wide{grid-column:1/-1}.wide dd{font-size:1.1rem}.alert dd{color:var(--warn)}
.sun{fill:#f2a900}.ray{fill:none;stroke:#f2a900;stroke-width:3}.moon{fill:#e3c55a}.cloud{fill:#9aa3ad}
.drop{fill:none;stroke:#3f82e4;stroke-width:3;stroke-linecap:round}.flake{fill:#79aee8}
footer{padding-top:1.25rem;padding-bottom:1.5rem;font-size:.9rem;color:var(--mute)}
footer p{margin:0}footer a{color:inherit;display:inline-block;padding:11px 4px;margin:-11px -4px}
@media (prefers-reduced-motion:no-preference){
.ball{animation:hop 1.2s .4s 3}.shadow{animation:squash 1.2s .4s 3}
@keyframes hop{0%,100%{transform:none;animation-timing-function:cubic-bezier(.3,.6,.5,1)}50%{transform:translateY(-14px);animation-timing-function:cubic-bezier(.5,0,.7,.4)}}
@keyframes squash{50%{transform:scaleX(.7);opacity:.08}}}
`.trim();
const CSS_HASH = crypto.createHash('sha256').update(CSS).digest('base64');
const CSP = [
  "default-src 'none'",
  `style-src 'sha256-${CSS_HASH}'`,
  "img-src data: 'self'", // state favicon is an inline data: SVG; home-screen icons are served here
  "manifest-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join('; ');

const SVG_ATTRS = 'viewBox="0 0 120 120" aria-hidden="true" focusable="false"';
const STATES = {
  outside: {
    answer: 'YES.',
    line: 'The kids are out playing. Come on over!',
    title: 'Yes!',
    theme: ['#e4f3e7', '#0b1c10'],
    // A bouncing ball under the sun.
    icon: `<svg class="icon" ${SVG_ATTRS}><circle class="sun" cx="102" cy="18" r="8"/><circle class="ray" cx="102" cy="18" r="14" stroke-dasharray="4 7"/>`
      + '<ellipse class="shadow" cx="60" cy="108" rx="28" ry="5"/><g class="ball"><circle cx="60" cy="64" r="36" fill="#fff"/>'
      + '<path d="M60 28A36 36 0 0 0 60 100C43 82 43 46 60 28Z" fill="#2f6fde"/><path d="M60 28A36 36 0 0 1 60 100C77 82 77 46 60 28Z" fill="#e5463b"/></g></svg>',
    fav: '<circle cx="16" cy="16" r="15" fill="#1f8a3e"/><path d="M9 16.5l4.5 4.5L23 11.5" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>',
  },
  inside: {
    answer: 'NOT RIGHT NOW.',
    line: "They're inside for now. Check back later!",
    title: 'Not right now',
    theme: ['#e7eef9', '#0c1426'],
    // A house with a lit window.
    icon: `<svg class="icon" ${SVG_ATTRS}><path class="fill" d="M60 14 10 56h14v50h72V56h14z"/><rect class="lit" x="36" y="64" width="22" height="20" rx="2"/>`
      + '<path class="cut" d="M46 64h2v20h-2zM36 73h22v2H36z"/><rect class="cut" x="68" y="70" width="18" height="36" rx="2"/></svg>',
    fav: '<circle cx="16" cy="16" r="15" fill="#2f5fb8"/><path d="M16 7 6 16h3v9h14v-9h3z" fill="#fff"/>',
  },
  unknown: {
    answer: 'NOT SURE.',
    line: "Our update is late, so we can't tell. Check back soon!",
    title: 'Not sure',
    theme: ['#efefec', '#161616'],
    icon: `<svg class="icon" ${SVG_ATTRS}><circle class="fill" cx="60" cy="62" r="46"/><text class="q" x="60" y="84" text-anchor="middle">?</text></svg>`,
    fav: '<circle cx="16" cy="16" r="15" fill="#6b6b6b"/><text x="16" y="23" font-family="system-ui,sans-serif" font-size="21" font-weight="900" fill="#fff" text-anchor="middle">?</text>',
  },
};
const FIRST_LINE = "We'll know in a few minutes. Check back soon!";
for (const s of Object.values(STATES)) {
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>${s.fav.replace(/"/g, "'")}</svg>`;
  s.favUri = `data:image/svg+xml,${svg.replace(/[<>#%]/g, encodeURIComponent)}`;
}

// Small weather icons (48x48), picked by WMO code group.
const CLOUD = '<g class="cloud"><circle cx="18" cy="24" r="8"/><circle cx="27" cy="19" r="10"/><circle cx="35" cy="25" r="7"/><rect x="10" y="24" width="32" height="8" rx="4"/></g>';
const SUN = '<circle class="sun" cx="24" cy="24" r="9"/><circle class="ray" cx="24" cy="24" r="16" stroke-dasharray="4 4.38"/>';
const MOON = '<path class="moon" d="M28 8a16 16 0 1 0 12 26A13 13 0 0 1 28 8z"/>';
function skyIcon(code, day) {
  let body;
  if (code <= 1) body = day ? SUN : MOON;
  else if (code === 2) body = `<g transform="translate(-8 -8) scale(.8)">${day ? SUN : MOON}</g><g transform="translate(4 6)">${CLOUD}</g>`;
  else if (code === 3) body = `<g transform="translate(0 4)">${CLOUD}</g>`;
  else if (code < 50) body = `${CLOUD}<path class="drop" d="M12 38h24M16 44h22"/>`;
  else if (code >= 95) body = `${CLOUD}<path class="sun" d="M27 30l-7 9h5l-3 8 9-11h-5l3-6z"/>`;
  else if ((code >= 71 && code <= 77) || code === 85 || code === 86) {
    body = `${CLOUD}<circle class="flake" cx="16" cy="39" r="2.5"/><circle class="flake" cx="25" cy="43" r="2.5"/><circle class="flake" cx="34" cy="39" r="2.5"/>`;
  } else body = `${CLOUD}<path class="drop" d="M17 37l-2 6M26 37l-2 6M35 37l-2 6"/>`;
  return `<svg class="sky" viewBox="0 0 48 48" aria-hidden="true" focusable="false">${body}</svg>`;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

const clockFmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: 'numeric', minute: '2-digit', hour12: true });
function clock(ms) {
  // "4:05 PM" -> "4:05 pm"
  return clockFmt.format(new Date(ms)).replace(/\s?([AP])M$/, (_, p) => ` ${p.toLowerCase()}m`);
}

const partsFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
const weekdayFmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'long' });
function localParts(ms) {
  // -> { date: "YYYY-MM-DD", min: minutes since local midnight }
  const p = {};
  for (const x of partsFmt.formatToParts(new Date(ms))) p[x.type] = x.value;
  return { date: `${p.year}-${p.month}-${p.day}`, min: Number(p.hour) * 60 + Number(p.minute) };
}
function parseLocal(isoLocal) {
  // Open-Meteo local time "YYYY-MM-DDTHH:MM" -> { date, min }
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/.exec(isoLocal || '');
  return m ? { date: m[1], min: Number(m[2]) * 60 + Number(m[3]) } : null;
}

function localClock(isoLocal) {
  // Open-Meteo local time "YYYY-MM-DDTHH:MM" -> "h:mm pm"
  const t = parseLocal(isoLocal);
  if (!t) return null;
  const h = Math.floor(t.min / 60);
  return `${h % 12 || 12}:${String(t.min % 60).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`;
}

function duration(mins) {
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

// How long until dark, which is what a parent deciding whether to head over wants to know.
function daylight(w, now) {
  const today = localParts(now);
  const set = parseLocal(w.sunset);
  if (!set || set.date !== today.date) return null; // stale forecast from yesterday
  const rise = parseLocal(w.sunrise);
  if (rise && rise.date === today.date && today.min < rise.min) {
    return { text: `Dark out, sunrise at ${localClock(w.sunrise)}`, alert: true };
  }
  const left = set.min - today.min;
  const at = localClock(w.sunset);
  if (left > 0) return { text: `Sunset in ${duration(left)} (${at})`, alert: left <= 60 };
  return { text: `${-left < 30 ? 'Getting dark' : 'Dark out'}, sunset was at ${at}`, alert: true };
}

function agoText(ms, now) {
  const mins = Math.max(0, Math.floor((now - ms) / MIN));
  if (mins === 0) return 'Last updated just now';
  if (mins < 60) return `Last updated ${mins} minute${mins === 1 ? '' : 's'} ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `Last updated ${hrs} hour${hrs === 1 ? '' : 's'} ago`;
  const days = Math.floor(hrs / 24);
  return `Last updated ${days} day${days === 1 ? '' : 's'} ago`;
}

function updatedText(now) {
  if (!last) return 'Waiting for the first update';
  const sameDay = localParts(last.updatedAt).date === localParts(now).date;
  const when = sameDay ? `at ${clock(last.updatedAt)}` : `on ${weekdayFmt.format(new Date(last.updatedAt))} at ${clock(last.updatedAt)}`;
  return `${agoText(last.updatedAt, now)}, ${when}`;
}

function weatherHtml(w, now) {
  if (!w) return '';
  const cells = [];
  const cell = (label, value, cls = '') => cells.push(`<div${cls ? ` class="${cls}"` : ''}><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`);
  const day = daylight(w, now);
  if (day) cell('Daylight', day.text, day.alert ? 'wide alert' : 'wide');
  cell('Feels like', `${w.feels}°`);
  cell('High / low', `${w.high}° / ${w.low}°`);
  cell('Wind', `${w.wind} mph${w.gust >= w.wind + 10 ? `, gusts ${w.gust}` : ''}`);
  if (w.rain >= 20) cell('Rain chance', `${w.rain}% soon`, 'alert');
  if (w.uv >= 6) cell('UV index', `${w.uv}, sunscreen`, 'alert');
  return `<section class="weather" aria-labelledby="wx"><h2 id="wx">Weather</h2>
<div class="now">${skyIcon(w.code, w.day)}<span class="temp">${esc(w.temp)}°F</span><span class="desc">${esc(w.sky)}</span></div>
<dl>${cells.join('')}</dl></section>`;
}

async function renderPage() {
  const now = Date.now();
  const state = currentState(now);
  const s = STATES[state];
  const weather = await getWeather();
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta http-equiv="refresh" content="60">
<title>${esc(s.title)} · Are the kids outside?</title>
<meta property="og:title" content="Are the kids outside?">
<meta property="og:description" content="Live answer for the neighbors. Tap to check.">
<meta name="theme-color" media="(prefers-color-scheme: light)" content="${s.theme[0]}">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="${s.theme[1]}">
<link rel="icon" type="image/svg+xml" href="${s.favUri}">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/site.webmanifest">
<meta name="apple-mobile-web-app-title" content="Kids outside?">
<style>${CSS}</style>
</head>
<body class="${state}">
<main>
<section class="hero">
<h1>Are the kids outside?</h1>
${s.icon}
<p class="answer">${esc(s.answer)}</p>
<p class="line">${esc(last ? s.line : FIRST_LINE)}</p>
<p class="updated">${esc(updatedText(now))}</p>
</section>
${weatherHtml(weather, now)}
</main>
<footer><p>This page refreshes every minute.</p><p>For smart‑home tinkerers: <a href="/api/status">/api/status</a></p></footer>
</body>
</html>
`;
}

// ---- home-screen icon ------------------------------------------------------
// A beach ball on the lawn under the sun, drawn per pixel (4x4 supersampled) and PNG-encoded with
// node:zlib at startup, so there are no image files or dependencies. Full-bleed square: iOS rounds the
// corners itself, and the ball and sun sit inside Android's maskable safe zone (centre circle, r = 0.4).

const ICON_SIZES = [180, 192, 512];

function iconColor(x, y) { // x, y in [0, 1)
  const mix = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
  // ball (drawn first: it sits in front of everything)
  const bx = 0.46, by = 0.55, br = 0.25, line = 0.021;
  const dx = x - bx, dy = y - by, d = Math.hypot(dx, dy);
  if (d <= br) {
    if (d > br - line) return [29, 29, 29];
    // Point on the sphere (y down), tilted so the top pole leans toward the viewer.
    const px = dx / br, py = dy / br, pz = Math.sqrt(Math.max(0, 1 - px * px - py * py));
    const t = -0.45; // tilt, radians
    const qy = py * Math.cos(t) + pz * Math.sin(t);
    const qz = -py * Math.sin(t) + pz * Math.cos(t);
    const lat = Math.asin(Math.max(-1, Math.min(1, -qy))); // +pi/2 = top pole
    const lon = Math.atan2(px, qz) + Math.PI / 2 + 0.2; // turn so red, yellow and blue face front
    const capLat = 1.18;
    const seg = Math.PI / 3; // six panels
    const toSeam = Math.abs(((lon % seg) + seg) % seg - seg / 2); // 0 at a panel's centre
    const seamDist = (seg / 2 - toSeam) * Math.cos(lat) * br; // approx distance to the nearest seam
    const capDist = Math.abs(lat - capLat) * br;
    let c;
    if (lat > capLat) c = capDist < line / 2 ? [29, 29, 29] : [250, 250, 246];
    else if (seamDist < line / 2.4 || capDist < line / 2) c = [29, 29, 29];
    else {
      const panel = ((Math.floor(lon / seg) % 6) + 6) % 6;
      c = [[229, 70, 59], [255, 196, 26], [47, 111, 222], [250, 250, 246], [52, 168, 83], [250, 250, 246]][panel];
    }
    // light from the upper left: gentle shading plus a soft highlight
    const shade = 0.82 + 0.18 * Math.max(0, -0.55 * px - 0.55 * py + 0.63 * pz);
    c = c.map((v) => v * shade);
    const h = Math.max(0, 1 - Math.hypot(px + 0.42, py + 0.42) / 0.38);
    return mix(c, [255, 255, 255], h * 0.35);
  }
  // sun with 8 short rays
  const sx = 0.73, sy = 0.25, sr = 0.075;
  const sd = Math.hypot(x - sx, y - sy);
  if (sd <= sr) return [255, 196, 26];
  if (sd > 0.105 && sd < 0.145) {
    const a = Math.atan2(y - sy, x - sx) + Math.PI;
    const step = Math.PI / 4;
    if (Math.abs((a % step) - step / 2) < 0.16) return [255, 196, 26];
  }
  // lawn: a gentle hill, with the ball's shadow
  const hill = 0.7 + 0.05 * ((x - 0.5) / 0.5) ** 2;
  if (y >= hill) {
    if (((x - bx) / 0.19) ** 2 + ((y - 0.822) / 0.03) ** 2 <= 1) return [46, 125, 58];
    return mix([86, 178, 92], [62, 150, 72], (y - hill) / (1 - hill));
  }
  // sky
  return mix([110, 190, 240], [190, 228, 250], y / hill);
}

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function pngChunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}

function renderIcon(size) {
  const SS = 4;
  const raw = Buffer.alloc(size * (size * 3 + 1));
  for (let py = 0; py < size; py++) {
    const row = py * (size * 3 + 1);
    raw[row] = 0; // filter: none
    for (let px = 0; px < size; px++) {
      let r = 0, g = 0, b = 0;
      for (let sy = 0; sy < SS; sy++) {
        for (let sx = 0; sx < SS; sx++) {
          const c = iconColor((px + (sx + 0.5) / SS) / size, (py + (sy + 0.5) / SS) / size);
          r += c[0]; g += c[1]; b += c[2];
        }
      }
      const o = row + 1 + px * 3;
      raw[o] = Math.round(r / SS / SS);
      raw[o + 1] = Math.round(g / SS / SS);
      raw[o + 2] = Math.round(b / SS / SS);
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; ihdr[9] = 2; // 8-bit RGB
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

const ICONS = Object.fromEntries(ICON_SIZES.map((n) => [n, renderIcon(n)]));
const MANIFEST = JSON.stringify({
  name: 'Are the kids outside?',
  short_name: 'Kids outside?',
  start_url: '/',
  scope: '/',
  display: 'standalone',
  background_color: '#e4f3e7',
  theme_color: '#6ebef0',
  icons: [
    { src: '/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
    { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
    { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
  ],
});
const ICON_ROUTES = {
  '/apple-touch-icon.png': ICONS[180],
  '/apple-touch-icon-precomposed.png': ICONS[180], // older iOS asks for this name too
  '/icon-192.png': ICONS[192],
  '/icon-512.png': ICONS[512],
};

// ---- http ----------------------------------------------------------------

const COMMON_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'X-Robots-Tag': 'noindex, nofollow',
};

function send(res, code, body, headers = {}) {
  const buf = Buffer.from(body);
  res.writeHead(code, { ...COMMON_HEADERS, 'Content-Length': buf.length, ...headers });
  res.end(res.req.method === 'HEAD' ? undefined : buf);
}

function sendJson(res, code, obj, headers = {}) {
  send(res, code, JSON.stringify(obj), {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    ...headers,
  });
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const declared = Number(req.headers['content-length']);
    if (declared > MAX_BODY) return reject(Object.assign(new Error('too large'), { code: 413 }));
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY) {
        req.pause();
        reject(Object.assign(new Error('too large'), { code: 413 }));
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

async function handlePush(req, res) {
  if (!authorized(req.headers.authorization)) {
    return sendJson(res, 401, { error: 'unauthorized' }, { 'WWW-Authenticate': 'Bearer' });
  }
  let raw;
  try {
    raw = await readBody(req);
  } catch (err) {
    return sendJson(res, err.code === 413 ? 413 : 400, { error: err.code === 413 ? 'body too large' : 'bad request' },
      { Connection: 'close' });
  }
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    return sendJson(res, 400, { error: 'invalid JSON' });
  }
  if (!body || typeof body !== 'object' || typeof body.outside !== 'boolean') {
    return sendJson(res, 400, { error: '"outside" must be a boolean' });
  }
  if (body.expires_at !== undefined && body.expires_at !== null && typeof body.expires_at !== 'number') {
    return sendJson(res, 400, { error: '"expires_at" must be a unix timestamp' });
  }
  applyPush(body);
  return sendJson(res, 200, statusJson());
}

const server = http.createServer(async (req, res) => {
  const path = (req.url || '/').split('?')[0];
  const method = req.method;
  try {
    if (path === '/healthz' && (method === 'GET' || method === 'HEAD')) {
      return send(res, 200, 'ok\n', { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' });
    }
    if (path === '/api/status') {
      const cors = { 'Access-Control-Allow-Origin': '*' };
      if (method === 'GET' || method === 'HEAD') return sendJson(res, 200, statusJson(), cors);
      if (method === 'OPTIONS') {
        return send(res, 204, '', { ...cors, 'Access-Control-Allow-Methods': 'GET', 'Access-Control-Max-Age': '86400' });
      }
      if (method === 'POST') return await handlePush(req, res);
      return sendJson(res, 405, { error: 'method not allowed' }, { Allow: 'GET, HEAD, POST, OPTIONS' });
    }
    if (path === '/') {
      if (method !== 'GET' && method !== 'HEAD') return send(res, 405, 'method not allowed\n', { Allow: 'GET, HEAD' });
      return send(res, 200, await renderPage(), {
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-store',
        'Content-Security-Policy': CSP,
      });
    }
    if (ICON_ROUTES[path] && (method === 'GET' || method === 'HEAD')) {
      return send(res, 200, ICON_ROUTES[path], { 'Content-Type': 'image/png', 'Cache-Control': 'public, max-age=86400' });
    }
    if (path === '/site.webmanifest' && (method === 'GET' || method === 'HEAD')) {
      return send(res, 200, MANIFEST, { 'Content-Type': 'application/manifest+json', 'Cache-Control': 'public, max-age=86400' });
    }
    if (path === '/robots.txt') {
      return send(res, 200, 'User-agent: *\nDisallow: /\n', { 'Content-Type': 'text/plain; charset=utf-8' });
    }
    return send(res, 404, 'not found\n', { 'Content-Type': 'text/plain; charset=utf-8' });
  } catch (err) {
    console.error(err);
    if (!res.headersSent) send(res, 500, 'internal error\n', { 'Content-Type': 'text/plain; charset=utf-8' });
  }
});

server.requestTimeout = 10000;
server.headersTimeout = 10000;

if (require.main === module) {
  server.listen(PORT, '0.0.0.0', () => console.log(`listening on :${PORT}`));
  const shutdown = () => {
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 3000).unref();
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

// Exported for tests only.
module.exports = { applyPush, statusJson, currentState, authorized };
