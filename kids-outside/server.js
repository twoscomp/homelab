'use strict';
// "Are the kids outside?" status page for the neighbors.
// No dependencies; Node 22+. State lives in memory only.

const http = require('node:http');
const crypto = require('node:crypto');

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
    current: 'temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_gusts_10m',
    hourly: 'precipitation_probability,uv_index',
    daily: 'temperature_2m_max,temperature_2m_min,sunset',
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
    wind: Math.round(c.wind_speed_10m),
    gust: Math.round(c.wind_gusts_10m),
    high: Math.round(j.daily.temperature_2m_max[0]),
    low: Math.round(j.daily.temperature_2m_min[0]),
    rain,
    uv: Math.round(j.hourly.uv_index[i] ?? 0),
    sunset: j.daily.sunset[0], // local "YYYY-MM-DDTHH:MM"
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

const CSS = `
:root{color-scheme:light dark;--bg:#fafaf7;--fg:#1b1b1b;--muted:#5d5d5d;--yes:#1d7a35;--no:#8a3b12;--unk:#5d5d5d;--line:#ddd}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#f2f2f2;--muted:#a8a8a8;--yes:#5fd27c;--no:#f0a070;--unk:#a8a8a8;--line:#333}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:18px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:34rem;margin:0 auto;padding:2.5rem 16px}
h1{font-size:2rem;line-height:1.2;margin:0 0 1rem;font-weight:800}
.answer{font-size:clamp(3rem,16vw,5.5rem);font-weight:900;line-height:1;margin:0 0 1rem;letter-spacing:-.02em}
.outside{color:var(--yes)}.inside{color:var(--no)}.unknown{color:var(--unk)}
.line{margin:0 0 .25rem}.updated{color:var(--muted);margin:0 0 2rem}
.weather{border-top:1px solid var(--line);padding-top:1.25rem}
.weather h2{font-size:1rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 .5rem}
.weather p{margin:.15rem 0}
footer{margin-top:2.5rem;font-size:.85rem;color:var(--muted)}
footer a{color:inherit}
`.trim();
const CSS_HASH = crypto.createHash('sha256').update(CSS).digest('base64');
const CSP = [
  "default-src 'none'",
  `style-src 'sha256-${CSS_HASH}'`,
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join('; ');

const COPY = {
  outside: { answer: 'YES.', line: 'The kids are outside right now. Come on over!' },
  inside: { answer: 'NOT RIGHT NOW.', line: "The kids aren't outside at the moment." },
  unknown: { answer: 'NOT SURE.', line: "We haven't had an update lately." },
};

function esc(s) {
  return String(s).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

const clockFmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: 'numeric', minute: '2-digit', hour12: true });
function clock(ms) {
  // "4:05 PM" -> "4:05 pm"
  return clockFmt.format(new Date(ms)).replace(/\s?([AP])M$/, (_, p) => ` ${p.toLowerCase()}m`);
}

function localClock(isoLocal) {
  // Open-Meteo local time "YYYY-MM-DDTHH:MM" -> "h:mm pm"
  const m = /T(\d{2}):(\d{2})/.exec(isoLocal || '');
  if (!m) return null;
  const h = Number(m[1]);
  return `${h % 12 || 12}:${m[2]} ${h < 12 ? 'am' : 'pm'}`;
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

function weatherHtml(w) {
  if (!w) return '';
  const rows = [
    `${w.temp}°F, ${w.sky.toLowerCase()}`,
    `Feels like ${w.feels}°F`,
    `High ${w.high}°F, low ${w.low}°F`,
    `Wind ${w.wind} mph${w.gust >= w.wind + 10 ? `, gusts ${w.gust} mph` : ''}`,
  ];
  if (w.rain >= 20) rows.push(`Rain chance ${w.rain}% in the next few hours`);
  if (w.uv >= 6) rows.push(`UV index ${w.uv}: sunscreen recommended`);
  const sunset = localClock(w.sunset);
  if (sunset) rows.push(`Sunset at ${sunset}`);
  return `<section class="weather"><h2>Weather</h2>${rows.map((r) => `<p>${esc(r)}</p>`).join('')}</section>`;
}

async function renderPage() {
  const now = Date.now();
  const state = currentState(now);
  const copy = COPY[state];
  const updated = last
    ? `${agoText(last.updatedAt, now)}, at ${clock(last.updatedAt)}`
    : 'No updates yet';
  const weather = await getWeather();
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta http-equiv="refresh" content="60">
<title>Are the kids outside?</title>
<style>${CSS}</style>
</head>
<body>
<main>
<h1>Are the kids outside?</h1>
<p class="answer ${state}">${esc(copy.answer)}</p>
<p class="line">${esc(copy.line)}</p>
<p class="updated">${esc(updated)}</p>
${weatherHtml(weather)}
<footer>This page refreshes every minute. Machine-readable: <a href="/api/status">/api/status</a></footer>
</main>
</body>
</html>
`;
}

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
