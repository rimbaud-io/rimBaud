// Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

export const BOARDS = [
  { id: "desk", label: "Desk", hint: "Wire hummingbots, BIAS, Copy, E-CLOSE" },
  { id: "home", label: "Home", hint: "Drag what shows on / and where" },
  { id: "health", label: "Health", hint: "Host / net / HL tokens, alerts, twin later" },
];

export const BOARD_CATEGORIES = {
  desk: null,
  home: [
    { id: "homeinfo", label: "Home boxes" },
    { id: "hummingbots", label: "Hummingbots" },
    { id: "system", label: "System health" },
    { id: "output", label: "Logs / stream" },
    { id: "condor", label: "Telegram" },
  ],
  health: [
    { id: "system", label: "System health" },
    { id: "output", label: "Logs / stream" },
    { id: "condor", label: "Alerts" },
  ],
};

export const HOME_GAP = 16;
export const HOME_ORIGIN = { x: 32, y: 16 };
export const HOME_COLS = 3;
export const HOME_CANVAS_W = 992;
export const HOME_TOP_IDS = [
  "home-stat-pilot",
  "home-stat-tokens",
  "home-stat-live",
  "home-stat-pos",
  "home-stat-equity",
  "home-stat-upnl",
  "home-stat-condor",
];
export const HOME_TOP_SCALES = [0.5, 0.6, 0.7, 0.85, 1, 1.15, 1.3];

export function clampTopScale(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 0.7;
  return Math.min(1.35, Math.max(0.45, n));
}

export function stepTopScale(cur, dir) {
  const c = clampTopScale(cur);
  let best = 0;
  HOME_TOP_SCALES.forEach((s, i) => {
    if (Math.abs(s - c) < Math.abs(HOME_TOP_SCALES[best] - c)) best = i;
  });
  const j = Math.max(0, Math.min(HOME_TOP_SCALES.length - 1, best + (dir < 0 ? -1 : 1)));
  return HOME_TOP_SCALES[j];
}

export const HOME_TILE = {
  homebot: { w: 320, h: 520 },
  pilot: { w: 992, h: 128 },
  tokens: { w: 992, h: 118 },
  live: { w: 186, h: 88 },
  pos: { w: 186, h: 88 },
  equity: { w: 186, h: 88 },
  upnl: { w: 186, h: 88 },
  condor: { w: 186, h: 88 },
  book: { w: 992, h: 380 },
  health: { w: 320, h: 140 },
  logs: { w: 320, h: 200 },
  agent: { w: 320, h: 200 },
  output: { w: 320, h: 200 },
};

export const HOME_STAT_TOOLS = [
  { toolId: "home-stat-pilot", kind: "pilot", label: "PILOT BIAS", hint: "PILOT report + Desk BIAS · Pilot never writes hunters", icon: "✦", color: "#2A9FD6", ...HOME_TILE.pilot },
  { toolId: "home-stat-tokens", kind: "tokens", label: "HL Tokens", hint: "IP weight bar on /", icon: "◈", color: "#f0b90b", ...HOME_TILE.tokens },
  { toolId: "home-stat-live", kind: "live", label: "Bots live", hint: "ok / n lamps on /", icon: "●", color: "#f0b90b", ...HOME_TILE.live },
  { toolId: "home-stat-pos", kind: "pos", label: "Open positions", hint: "count across hunters", icon: "▣", color: "#7ecbff", ...HOME_TILE.pos },
  { toolId: "home-stat-equity", kind: "equity", label: "Total equity", hint: "sum of wallets", icon: "$", color: "#00ff6a", ...HOME_TILE.equity },
  { toolId: "home-stat-upnl", kind: "upnl", label: "Open uPnL", hint: "marked to mid", icon: "Δ", color: "#ffc857", ...HOME_TILE.upnl },
  { toolId: "home-stat-condor", kind: "condor", label: "Condor trend", hint: "regime + bias", icon: "◎", color: "#2A9FD6", ...HOME_TILE.condor },
  { toolId: "home-stat-book", kind: "book", label: "Active positions", hint: "full table on /", icon: "▤", color: "#9dffc0", ...HOME_TILE.book },
];

export const HOME_TOOL_IDS = new Set([
  "health-host", "health-net", "health-hlip",
  "logs", "out-stream", "telegram-bot",
]);

export const HEALTH_TOOL_IDS = new Set([
  "health-host", "health-net", "health-hlip",
  "logs", "out-stream", "telegram-bot",
  "email-bot", "backup-twin",
]);

export function graphUrl(board) {
  if (board === "home" || board === "health") return "/api/wire/board/" + board;
  return "/api/wire/graph";
}

export function homeTileSize(n) {
  const type = (n && n.type) || "";
  const kind = (n && n.data && n.data.kind) || "";
  if (type === "homeband") {
    const w = Number((n.data && n.data.w) || HOME_CANVAS_W + 48);
    const h = Number((n.data && n.data.h) || 120);
    return { w, h };
  }
  if (type === "homebot" || kind === "homebot") return HOME_TILE.homebot;
  if (type === "homestat") {
    const base = HOME_TILE[kind] || HOME_TILE.live;
    const band = (n.data && n.data.band) || homeBandFor({ ...n, data: { ...(n.data || {}), band: undefined } });
    if (band === "top") {
      const sc = clampTopScale(n.data && n.data.top_scale);
      return { w: base.w, h: Math.max(36, Math.round(base.h * sc)) };
    }
    return base;
  }
  if (type === "health") return HOME_TILE.health;
  if (type === "logs") return HOME_TILE.logs;
  if (type === "agent") return HOME_TILE.agent;
  if (type === "output") return HOME_TILE.output;
  return HOME_TILE.homebot;
}

export function homeBandFor(n) {
  if (!n) return "mid";
  const forced = n.data && n.data.band;
  if (forced === "top" || forced === "mid" || forced === "bot") return forced;
  const type = n.type || "";
  const kind = (n.data && n.data.kind) || "";
  if (type === "homeband") return kind || "mid";
  if (type === "homebot" || kind === "homebot") return "mid";
  if (type === "homestat") {
    if (kind === "book") return "mid";
    return "top";
  }
  if (type === "health" || type === "logs" || type === "output" || type === "agent") return "bot";
  return "mid";
}

function homeRect(n) {
  const p = (n && n.position) || {};
  const s = homeTileSize(n);
  return { x: Number(p.x) || 0, y: Number(p.y) || 0, w: s.w, h: s.h };
}

function homeOverlap(a, b, gap) {
  const g = gap == null ? 8 : gap;
  return a.x < b.x + b.w + g && a.x + a.w + g > b.x && a.y < b.y + b.h + g && a.y + a.h + g > b.y;
}

export function homeNeedsPack(nodes) {
  const list = (nodes || []).filter((n) => n && (n.type === "homebot" || n.type === "homestat" || n.type === "health" || n.type === "logs" || n.type === "agent" || n.type === "output"));
  for (let i = 0; i < list.length; i++) {
    const ri = homeRect(list[i]);
    if (ri.w < 100 || ri.h < 70) return true;
    for (let j = i + 1; j < list.length; j++) {
      if (homeOverlap(ri, homeRect(list[j]), HOME_GAP - 4)) return true;
    }
  }
  const bots = list.filter((n) => n.type === "homebot");
  if (bots.length >= 2) {
    const rows = [...new Set(bots.map((n) => Math.round(Number((n.position && n.position.y) || 0))))].sort((a, b) => a - b);
    if (rows.length >= 2) {
      const stride = rows.slice(1).reduce((m, y, i) => Math.min(m, y - rows[i]), 9999);
      if (stride < HOME_TILE.homebot.h) return true;
    }
  }
  return false;
}

function homeBandGuide(id, band, label, x, y, w, h) {
  return {
    id,
    type: "homeband",
    position: { x, y },
    draggable: false,
    selectable: false,
    connectable: false,
    deletable: false,
    width: w,
    height: h,
    zIndex: 1,
    style: { width: w, height: h, zIndex: 1 },
    data: { kind: band, band, label, name: label, w, h, toolId: id },
  };
}

export function withHomeBandGuides(nodes) {
  const list = (nodes || []).filter((n) => n && n.type !== "homeband");
  const x0 = HOME_ORIGIN.x;
  const bandX = x0 - 24;
  const bandW = HOME_CANVAS_W + 48;
  const of = (band) => list.filter((n) => homeBandFor(n) === band);
  const box = (arr, fallbackY, fallbackH) => {
    if (!arr.length) return { y: fallbackY, h: fallbackH };
    let minY = Infinity;
    let maxY = -Infinity;
    arr.forEach((n) => {
      const r = homeRect(n);
      minY = Math.min(minY, r.y);
      maxY = Math.max(maxY, r.y + r.h);
    });
    return { y: minY - 28, h: Math.max(fallbackH, maxY - minY + 48) };
  };
  const top = box(of("top"), 0, 230);
  let mid = box(of("mid"), top.y + top.h + 12, 420);
  if (mid.y < top.y + top.h + 8) mid = { ...mid, y: top.y + top.h + 12 };
  let bot = box(of("bot"), mid.y + mid.h + 12, 160);
  if (bot.y < mid.y + mid.h + 8) bot = { ...bot, y: mid.y + mid.h + 12 };
  const guides = [
    homeBandGuide("band-top", "top", "TOP", bandX, top.y, bandW, top.h),
    homeBandGuide("band-mid", "mid", "MIDDLE", bandX, mid.y, bandW, mid.h),
    homeBandGuide("band-bot", "bot", "BOTTOM", bandX, bot.y, bandW, bot.h),
  ];
  return guides.concat(list);
}

export function packHomeLayout(nodes, topScale) {
  const sc = clampTopScale(
    topScale != null
      ? topScale
      : ((nodes || []).find((n) => n && n.data && n.data.top_scale) || {}).data?.top_scale
  );
  const raw = (nodes || []).filter((n) => n && n.type !== "homeband");
  const list = raw.map((n) => {
    const band = homeBandFor({ ...n, data: { ...(n.data || {}), band: undefined } });
    const stamped = { ...n, data: { ...(n.data || {}), band, top_scale: band === "top" ? sc : (n.data && n.data.top_scale) } };
    const sz = homeTileSize(stamped);
    return {
      ...stamped,
      position: { x: (n.position && n.position.x) || 0, y: (n.position && n.position.y) || 0 },
      data: { ...stamped.data, ...sz, band, top_scale: band === "top" ? sc : stamped.data.top_scale },
    };
  });
  const byId = Object.fromEntries(list.map((n) => [n.id, n]));
  const x0 = HOME_ORIGIN.x;
  const g = HOME_GAP;
  const bot = HOME_TILE.homebot;
  const canvasW = HOME_CANVAS_W;
  const pad = 20;
  const bandX = x0 - 16;
  const bandW = canvasW + 32;

  const place = (id, x, yy) => {
    const n = byId[id];
    if (!n) return;
    n.position = { x, y: yy };
  };

  let y = HOME_ORIGIN.y;
  const topY = y - 12;

  const topHOf = (id) => {
    const n = byId[id];
    return n ? homeTileSize(n).h : 0;
  };
  if (byId["home-stat-pilot"]) {
    place("home-stat-pilot", x0, y);
    y += topHOf("home-stat-pilot") + g;
  }
  if (byId["home-stat-tokens"]) {
    place("home-stat-tokens", x0, y);
    y += topHOf("home-stat-tokens") + g;
  }
  const kpis = ["home-stat-live", "home-stat-pos", "home-stat-equity", "home-stat-upnl", "home-stat-condor"];
  const presentK = kpis.filter((id) => byId[id]);
  if (presentK.length) {
    presentK.forEach((id, i) => place(id, x0 + i * (HOME_TILE.live.w + g), y));
    y += topHOf(presentK[0]) + g;
  }
  const otherTop = list.filter((n) => n.data.band === "top" && !HOME_TOP_IDS.includes(n.id));
  otherTop.forEach((n, i) => {
    const s = homeTileSize(n);
    n.position = { x: x0 + (i % HOME_COLS) * (bot.w + g), y };
    if ((i + 1) % HOME_COLS === 0 || i === otherTop.length - 1) y += s.h + g;
  });
  const topH = Math.max(120, y - topY + 8);

  const midY = y + pad;
  y = midY + 28;
  const bots = list.filter((n) => n.type === "homebot");
  bots.sort((a, b) => ((a.position.y - b.position.y) || (a.position.x - b.position.x)));
  bots.forEach((n, i) => {
    n.position = {
      x: x0 + (i % HOME_COLS) * (bot.w + g),
      y: y + Math.floor(i / HOME_COLS) * (bot.h + g),
    };
  });
  if (bots.length) y += Math.ceil(bots.length / HOME_COLS) * (bot.h + g);
  const midExtras = list.filter((n) => n.data.band === "mid" && n.type !== "homebot");
  midExtras.forEach((n) => {
    const s = homeTileSize(n);
    n.position = { x: x0, y };
    y += s.h + g;
  });
  if (!bots.length && !midExtras.length) y += 180;
  const midH = Math.max(200, y - midY + 8);

  const botY = y + pad;
  y = botY + 28;
  const bottom = list.filter((n) => n.data.band === "bot");
  let rowH = 140;
  bottom.forEach((n, i) => {
    const s = homeTileSize(n);
    rowH = Math.max(rowH, s.h);
    n.position = { x: x0 + (i % HOME_COLS) * (bot.w + g), y };
  });
  if (bottom.length) y += rowH + g;
  else y += 120;
  const botH = Math.max(140, y - botY + 8);

  return withHomeBandGuides(list);
}

export function ensureHomeStats(nodes) {
  const ns = Array.isArray(nodes) ? nodes.slice() : [];
  const hasStats = ns.some((n) => n && n.type === "homestat");
  // Do not re-insert tiles the operator deleted (e.g. Condor).
  if (hasStats) return withHomeBandGuides(ns);
  const extras = HOME_STAT_TOOLS.filter((t) => t.kind !== "condor").map((t) => ({
    id: t.toolId,
    type: "homestat",
    position: { x: 0, y: 0 },
    data: { kind: t.kind, name: t.label, color: t.color, icon: t.icon, w: t.w, h: t.h, toolId: t.toolId },
  }));
  return withHomeBandGuides(packHomeLayout(extras.concat(ns)));
}

export function lsGraphKey(board) {
  if (board === "home") return "matrix-wire-home-v1";
  if (board === "health") return "matrix-wire-health-v1";
  return "matrix-wire-graph-v5";
}
