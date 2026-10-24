"use strict";

const http = require("http");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const HOST = process.env.HOST || "127.0.0.1";
const PORT = Number(process.env.PORT || 3010);
const ROOT = path.join(__dirname, "public");
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".webp": "image/webp",
  ".json": "application/json; charset=utf-8"
};

const GATE_USER = "rimbaud";
const PASS_SALT = "rimbaud-gate-v1";
const SESSION_MS = 12 * 60 * 60 * 1000;
const FAIL_WINDOW_MS = 10 * 60 * 1000;
const FAIL_MAX = 8;

let cache = { at: 0, body: null };
let inflight = null;
const fails = new Map();

function loadSecret() {
  const file = path.join(__dirname, ".session-secret");
  try {
    const buf = fs.readFileSync(file);
    if (buf.length >= 32) return buf;
  } catch (err) { /* create below */ }
  const buf = crypto.randomBytes(32);
  try {
    fs.writeFileSync(file, buf, { mode: 0o600 });
    fs.chmodSync(file, 0o600);
  } catch (err) { /* keep it in memory for this process */ }
  return buf;
}

function loadPassHash() {
  const env = String(process.env.RIMBAUD_GATE_HASH || "").trim();
  if (/^[0-9a-fA-F]{64}$/.test(env)) return Buffer.from(env, "hex");
  try {
    const hex = fs.readFileSync(path.join(__dirname, ".gate-hash"), "utf8").trim();
    if (/^[0-9a-fA-F]{64}$/.test(hex)) return Buffer.from(hex, "hex");
  } catch (err) { /* the public copy has no gate hash */ }
  return Buffer.alloc(0);
}

const secret = loadSecret();
const PASS_HASH = loadPassHash();

function sameText(a, b) {
  const da = crypto.createHash("sha256").update(String(a)).digest();
  const db = crypto.createHash("sha256").update(String(b)).digest();
  return crypto.timingSafeEqual(da, db);
}

function passwordOk(pass) {
  if (PASS_HASH.length !== 32) return false;
  const got = crypto.scryptSync(String(pass || ""), PASS_SALT, 32);
  return crypto.timingSafeEqual(got, PASS_HASH);
}

function cookies(req) {
  const out = {};
  String(req.headers.cookie || "").split(";").forEach(function (part) {
    const i = part.indexOf("=");
    if (i < 1) return;
    out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  });
  return out;
}

function clientIp(req) {
  const real = req.headers["x-real-ip"] || req.headers["x-forwarded-for"] || "";
  const ip = String(real).split(",")[0].trim();
  return ip || req.socket.remoteAddress || "";
}

function tooMany(ip) {
  const now = Date.now();
  const recent = (fails.get(ip) || []).filter(function (t) { return now - t < FAIL_WINDOW_MS; });
  fails.set(ip, recent);
  return recent.length >= FAIL_MAX;
}

function noteFail(ip) {
  const row = fails.get(ip) || [];
  row.push(Date.now());
  fails.set(ip, row);
}

function signSession() {
  const body = Buffer.from(JSON.stringify({ u: GATE_USER, exp: Date.now() + SESSION_MS })).toString("base64url");
  const mac = crypto.createHmac("sha256", secret).update(body).digest("base64url");
  return body + "." + mac;
}

function readSession(req) {
  const raw = cookies(req).rb_session || "";
  const cut = raw.lastIndexOf(".");
  if (cut < 1) return null;
  const body = raw.slice(0, cut);
  const mac = raw.slice(cut + 1);
  const expect = crypto.createHmac("sha256", secret).update(body).digest("base64url");
  const a = Buffer.from(mac);
  const b = Buffer.from(expect);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;
  try {
    const data = JSON.parse(Buffer.from(body, "base64url").toString("utf8"));
    if (!data || data.u !== GATE_USER || !(data.exp > Date.now())) return null;
    return data;
  } catch (err) {
    return null;
  }
}

function cookieLine(token, req, maxAge) {
  const parts = ["rb_session=" + token, "HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=" + maxAge];
  if (req.headers["x-forwarded-proto"] === "https" || req.socket.encrypted) parts.push("Secure");
  return parts.join("; ");
}

function readBody(req) {
  return new Promise(function (resolve, reject) {
    const chunks = [];
    let n = 0;
    req.on("data", function (chunk) {
      n += chunk.length;
      if (n > 4096) {
        reject(new Error("big"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", function () { resolve(Buffer.concat(chunks).toString("utf8")); });
    req.on("error", reject);
  });
}

const LOGIN_PAGE = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>rimBaud</title>
<style>
  :root { color-scheme: dark; }
  body {
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    background: #020804; color: #9dffc0;
    font: 14px/1.4 "IBM Plex Mono", ui-monospace, monospace;
    background-image:
      linear-gradient(rgba(0,255,106,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,255,106,0.03) 1px, transparent 1px);
    background-size: 24px 24px;
  }
  form {
    width: min(360px, calc(100% - 32px));
    background: #041208; border: 1px solid #0d3b1f; padding: 22px 20px 18px;
    box-shadow: 0 0 24px rgba(0,255,106,0.06);
  }
  h1 { margin: 0 0 6px; font-size: 16px; letter-spacing: 2px; font-weight: 600; color: #2A9FD6; }
  p { margin: 0 0 16px; color: #3d7a55; font-size: 12px; }
  label { display: block; margin: 10px 0; color: #3d7a55; font-size: 11px; letter-spacing: 1px; text-transform: uppercase; }
  input {
    display: block; width: 100%; margin-top: 6px; box-sizing: border-box;
    background: #020804; color: #9dffc0; border: 1px solid #0d3b1f; padding: 8px 10px; font: inherit;
  }
  input:focus { outline: none; border-color: #00ff6a; }
  button {
    margin-top: 8px; width: 100%; background: #03160a; color: #f0b90b;
    border: 1px solid #f0b90b; font: inherit; letter-spacing: 2px; text-transform: uppercase;
    padding: 10px; cursor: pointer;
  }
  #err { min-height: 1.2em; margin: 10px 0 0; color: #ff4d6d; font-size: 12px; }
  a { color: #3d7a55; }
</style>
</head>
<body>
<form id="gate">
  <h1>rimBaud</h1>
  <p>Restricted wire show.</p>
  <label>User <input name="user" autocomplete="username" required></label>
  <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
  <button type="submit">Enter</button>
  <p id="err"></p>
  <p><a href="/">Back to the tools</a></p>
</form>
<script>
document.getElementById("gate").addEventListener("submit", function (ev) {
  ev.preventDefault();
  var fd = new FormData(ev.target);
  var err = document.getElementById("err");
  err.textContent = "";
  fetch("/api/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ user: fd.get("user"), password: fd.get("password") })
  }).then(function (res) {
    if (!res.ok) throw new Error("no");
    location.href = "/desk";
  }).catch(function () {
    err.textContent = "That login does not open the desk.";
  });
});
</script>
</body>
</html>`;

function get(url) {
  return fetch(url, {
    headers: { accept: "application/json", "user-agent": "rimbaud-www/1.0" },
    signal: AbortSignal.timeout(8000)
  }).then(function (res) {
    if (!res.ok) throw new Error(url + " " + res.status);
    return res.json();
  });
}

function fromBinance() {
  return Promise.all([
    get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=60"),
    get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
  ]).then(function (pair) {
    const klines = pair[0];
    const tick = pair[1];
    const candles = klines.map(function (row) {
      return {
        t: row[0],
        o: Number(row[1]),
        h: Number(row[2]),
        l: Number(row[3]),
        c: Number(row[4])
      };
    });
    return { source: "Binance", pair: "BTCUSDT", price: Number(tick.price), candles: candles };
  });
}

function fromKraken() {
  return Promise.all([
    get("https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1"),
    get("https://api.kraken.com/0/public/Ticker?pair=XBTUSD")
  ]).then(function (pair) {
    const ohlc = pair[0];
    const tick = pair[1];
    if (ohlc.error && ohlc.error.length) throw new Error(ohlc.error.join(" "));
    if (tick.error && tick.error.length) throw new Error(tick.error.join(" "));
    const rows = ohlc.result.XXBTZUSD || ohlc.result.XBTUSD;
    const trow = tick.result.XXBTZUSD || tick.result.XBTUSD;
    if (!rows || !trow) throw new Error("kraken shape");
    const candles = rows.slice(-60).map(function (row) {
      return {
        t: row[0] * 1000,
        o: Number(row[1]),
        h: Number(row[2]),
        l: Number(row[3]),
        c: Number(row[4])
      };
    });
    return { source: "Kraken", pair: "XBTUSD", price: Number(trow.c[0]), candles: candles };
  });
}

function loadBtc() {
  const now = Date.now();
  if (cache.body && now - cache.at < 4000) return Promise.resolve(cache.body);
  if (inflight) return inflight;
  inflight = fromBinance().catch(function () { return fromKraken(); }).then(function (book) {
    book.asof = Date.now();
    book.paper = true;
    cache = { at: Date.now(), body: book };
    return book;
  }).finally(function () { inflight = null; });
  return inflight;
}

function send(res, code, body, type, extra) {
  const headers = Object.assign({
    "content-type": type || "text/plain; charset=utf-8",
    "cache-control": "no-cache",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer"
  }, extra || {});
  res.writeHead(code, headers);
  res.end(body);
}

function sendLogin(res) {
  send(res, 200, LOGIN_PAGE, "text/html; charset=utf-8");
}

const PUBLIC_PAGES = {
  "/": "home.html",
  "/index.html": "home.html",
  "/home.html": "home.html",
  "/home.css": "home.css",
  "/dman.js": "dman.js",
  "/easy.js": "easy.js",
  "/oakleaf.svg": "oakleaf.svg",
  "/hummingbot.png": "hummingbot.png",
  "/hummingbot.svg": "hummingbot.svg",
  "/logo.svg": "logo.svg",
  "/nav.js": "nav.js",
  "/dokumentation": "dokumentation.html",
  "/links": "links.html",
  "/impressum": "impressum.html"
};

const DESK_PAGES = {
  "/desk": "index.html",
  "/desk.html": "index.html"
};

function sendFile(res, name) {
  const file = path.join(ROOT, name);
  if (!file.startsWith(ROOT)) {
    send(res, 400, "bad path");
    return;
  }
  fs.readFile(file, function (err, buf) {
    if (err) {
      send(res, 404, "not found");
      return;
    }
    send(res, 200, buf, TYPES[path.extname(file)] || "application/octet-stream");
  });
}

const server = http.createServer(function (req, res) {
  const url = new URL(req.url, "http://127.0.0.1");
  if (url.pathname === "/api/health") {
    send(res, 200, JSON.stringify({ ok: true }), "application/json; charset=utf-8");
    return;
  }
  if (url.pathname === "/api/login" && req.method === "POST") {
    const ip = clientIp(req);
    if (tooMany(ip)) {
      send(res, 429, JSON.stringify({ ok: false }), "application/json; charset=utf-8");
      return;
    }
    readBody(req).then(function (raw) {
      let data = {};
      try { data = JSON.parse(raw || "{}"); } catch (err) { data = {}; }
      const user = data && data.user;
      const pass = data && data.password;
      if (!sameText(user, GATE_USER) || !passwordOk(pass)) {
        noteFail(ip);
        send(res, 401, JSON.stringify({ ok: false }), "application/json; charset=utf-8");
        return;
      }
      fails.delete(ip);
      send(res, 200, JSON.stringify({ ok: true }), "application/json; charset=utf-8", {
        "set-cookie": cookieLine(signSession(), req, Math.floor(SESSION_MS / 1000))
      });
    }).catch(function () {
      send(res, 400, JSON.stringify({ ok: false }), "application/json; charset=utf-8");
    });
    return;
  }
  if (url.pathname === "/api/logout" && req.method === "POST") {
    send(res, 200, JSON.stringify({ ok: true }), "application/json; charset=utf-8", {
      "set-cookie": cookieLine("", req, 0)
    });
    return;
  }
  if (url.pathname === "/api/btc" && req.method === "GET") {
    loadBtc().then(function (book) {
      send(res, 200, JSON.stringify(book), "application/json; charset=utf-8");
    }).catch(function (err) {
      send(res, 502, JSON.stringify({ error: "btc unavailable" }), "application/json; charset=utf-8");
      console.error(err && err.message ? err.message : err);
    });
    return;
  }
  if (PUBLIC_PAGES[url.pathname]) {
    sendFile(res, PUBLIC_PAGES[url.pathname]);
    return;
  }
  if (url.pathname.startsWith("/shots/")) {
    const name = path.basename(url.pathname);
    const rest = url.pathname.slice("/shots/".length);
    if (name === rest && /^[a-z0-9][a-z0-9.-]*\.(jpg|jpeg|png|webp)$/i.test(name)) {
      sendFile(res, path.join("shots", name));
      return;
    }
    send(res, 404, "not found");
    return;
  }
  const authed = !!readSession(req);
  if (!authed) {
    if (url.pathname === "/login" || DESK_PAGES[url.pathname]) {
      sendLogin(res);
      return;
    }
    const api = url.pathname.startsWith("/api/");
    send(res, 401, api ? JSON.stringify({ ok: false }) : "login required", api ? "application/json; charset=utf-8" : "text/plain; charset=utf-8");
    return;
  }
  if (DESK_PAGES[url.pathname]) {
    sendFile(res, DESK_PAGES[url.pathname]);
    return;
  }
  if (url.pathname === "/login") {
    sendLogin(res);
    return;
  }
  let rel = decodeURIComponent(url.pathname);
  if (rel === "/") rel = "/home.html";
  if (rel.includes("\0") || rel.includes("..")) {
    send(res, 400, "bad path");
    return;
  }
  const file = path.join(ROOT, rel);
  if (!file.startsWith(ROOT)) {
    send(res, 400, "bad path");
    return;
  }
  fs.readFile(file, function (err, buf) {
    if (err) {
      send(res, 404, "not found");
      return;
    }
    send(res, 200, buf, TYPES[path.extname(file)] || "application/octet-stream");
  });
});

server.listen(PORT, HOST, function () {
  console.log("rimbaud-www http://" + HOST + ":" + PORT);
});
