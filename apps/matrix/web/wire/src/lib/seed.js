// Copyright 2026 Laurence Sterne www.rimbaud.io
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

export const HUNTERS = [
  { id: "hummingbot-hl", name: "HL Copy", href: "/copy", icon: "⚡", color: "#4f8cff", hasBias: false, coins: [], kind: "copy" },
  { id: "hummingbot-bn-swing", name: "PMM Simple", href: "/bn-swing", icon: "▤", color: "#f0b90b", hasBias: true, coins: ["BTC", "ETH", "LTC"] },
  { id: "hummingbot-helliot", name: "Helliot", href: "/helliot", icon: "∿", color: "#7ecbff", hasBias: true, coins: ["BTC", "ETH", "LTC"] },
  { id: "hummingbot-surfn", name: "SuperTrend", href: "/surfn", icon: "≈", color: "#2ee6c7", hasBias: true, coins: ["BTC", "ETH", "LTC", "TAO"] },
  { id: "hummingbot-shortn", name: "Shorty", href: "/shorty", icon: "▾", color: "#ff5d73", hasBias: true, coins: ["BTC", "ETH", "LTC"] },
  { id: "hummingbot-dman", name: "Dman", href: "/dman", icon: "▦", color: "#c084fc", hasBias: true, coins: ["BTC", "ETH", "LTC"] },
  { id: "hummingbot-onlyup", name: "Only Up", href: "/onlyup", icon: "▲", color: "#ffd166", hasBias: true, coins: ["BTC", "LTC"] },
  { id: "hummingbot-dman-maker", name: "DMan Maker", href: "/dman-maker", icon: "⧉", color: "#a78bfa", hasBias: true, coins: ["BTC", "ETH", "LTC"] },
];

export const COINS = [
  { coin: "BTC", color: "#f7931a", hint: "wired to every bias hunter" },
  { coin: "ETH", color: "#627eea", hint: "Swing / Helliot / Surfn / Shorty / Dman" },
  { coin: "LTC", color: "#345d9d", hint: "wired to every bias hunter" },
  { coin: "SOL", color: "#9945ff", hint: "add and wire to a hunter" },
  { coin: "HYPE", color: "#19e5a0", hint: "add and wire to a hunter" },
  { coin: "DOGE", color: "#c2a633", hint: "add and wire to a hunter" },
  { coin: "CHIP", color: "#7ecbff", hint: "Copy universe" },
  { coin: "KPEPE", color: "#86efac", hint: "Copy universe" },
  { coin: "KAITO", color: "#f0abfc", hint: "Copy universe" },
  { coin: "PROVE", color: "#fbbf24", hint: "Copy universe" },
  { coin: "XLM", color: "#38bdf8", hint: "Copy universe" },
  { coin: "CC", color: "#fb7185", hint: "Copy universe" },
];

export function biasId(botId) {
  return "bias-" + botId;
}

export function coinId(coin) {
  return "coin-" + String(coin || "").toUpperCase();
}

const ROW = 196;
const HUNTER_YS = {
  "hummingbot-hl": 24,
  "hummingbot-bn-swing": 24 + ROW,
  "hummingbot-helliot": 24 + ROW * 2,
  "hummingbot-surfn": 24 + ROW * 3,
  "hummingbot-shortn": 24 + ROW * 4,
  "hummingbot-dman": 24 + ROW * 5,
  "hummingbot-onlyup": 24 + ROW * 6,
  "hummingbot-dman-maker": 24 + ROW * 7,
};

export const LAYOUT = {
  "gauge-condor": { x: 24, y: 24 },
  "gauge-hmm": { x: 24, y: 160 },
  "coin-BTC": { x: 24, y: 340 },
  "coin-ETH": { x: 24, y: 500 },
  "coin-LTC": { x: 24, y: 660 },
  ...Object.fromEntries(
    HUNTERS.filter((h) => h.hasBias).map((h) => [biasId(h.id), { x: 300, y: HUNTER_YS[h.id] }])
  ),
  ...Object.fromEntries(HUNTERS.map((h) => [h.id, { x: 620, y: HUNTER_YS[h.id] }])),
  funds: { x: 1000, y: 48 },
  logs: { x: 1000, y: 460 },
};

export const CONTROLLER_FIELDS = {
  "hummingbot-hl": [
    { key: "copy_shorts", label: "Copy shorts", kind: "bool" },
    { key: "max_leverage", label: "Max lev", kind: "num", step: 1, min: 1, max: 50 },
    { key: "max_positions_per_trader", label: "Pos / trader", kind: "num", step: 1, min: 1, max: 8 },
    { key: "max_position_loss_pct", label: "Max loss % eq", kind: "pct_frac", step: 1 },
    { key: "min_free_margin_pct", label: "Min free %", kind: "pct_frac", step: 1 },
    { key: "max_subscribed_markets", label: "Max books", kind: "num", step: 1, min: 3, max: 12 },
    { key: "poll_interval_seconds", label: "Poll sec", kind: "num", step: 1, min: 2, max: 30 },
    { key: "max_zec_short_usd", label: "Max short ZEC $", kind: "num", step: 10, min: 0, max: 500000 },
  ],
  "hummingbot-bn-swing": [
    { key: "take_profit", label: "Take profit %", kind: "pct_frac", step: 0.1 },
    { key: "trail_activation", label: "Trail arm %", kind: "pct_frac", step: 0.1 },
    { key: "trail_delta", label: "Trail delta %", kind: "pct_frac", step: 0.1 },
    { key: "executor_refresh_time", label: "Refresh sec", kind: "num", step: 1, min: 10, max: 600 },
    { key: "sl_roe_pct", label: "Stop loss ROE %", kind: "num", step: 1 },
    { key: "margin_pct", label: "Margin % leftover", kind: "pct_frac", step: 1 },
  ],
  "hummingbot-helliot": [
    { key: "tp_roe_pct", label: "Take profit ROE %", kind: "num", step: 1 },
    { key: "sl_roe_pct", label: "Stop loss ROE %", kind: "num", step: 1 },
    { key: "wave2_min", label: "Wave2 min", kind: "num", step: 0.001 },
    { key: "wave2_max", label: "Wave2 max", kind: "num", step: 0.001 },
    { key: "wave3_ext", label: "Wave3 ext", kind: "num", step: 0.001 },
    { key: "margin_pct", label: "Margin % leftover", kind: "pct_frac", step: 1 },
  ],
  "hummingbot-surfn": [
    { key: "scale_roe_pct", label: "Clip at ROE %", kind: "num", step: 1 },
    { key: "scale_out_pct", label: "Clip size %", kind: "pct_frac", step: 1 },
    { key: "sl_roe_pct", label: "Stop loss ROE %", kind: "num", step: 1 },
    { key: "bbp_min", label: "BBP min", kind: "num", step: 0.01 },
    { key: "bbp_max", label: "BBP max", kind: "num", step: 0.01 },
    { key: "margin_pct", label: "Margin % leftover", kind: "pct_frac", step: 1 },
  ],
  "hummingbot-shortn": [
    { key: "tp_roe_pct", label: "Take profit ROE %", kind: "num", step: 1 },
    { key: "sl_roe_pct", label: "Stop loss ROE %", kind: "num", step: 1 },
    { key: "recover_roe_pct", label: "Rebuy bounce ROE %", kind: "num", step: 1 },
    { key: "bb_short_threshold", label: "Short BBP ≥", kind: "num", step: 0.01 },
    { key: "margin_pct", label: "Margin % leftover", kind: "pct_frac", step: 1 },
  ],
  "hummingbot-dman": [
    { key: "trail_activation", label: "Trail arm %", kind: "pct_frac", step: 0.1 },
    { key: "trail_delta", label: "Trail delta %", kind: "pct_frac", step: 0.1 },
    { key: "bb_long_threshold", label: "Rebuy BBP ≤", kind: "num", step: 0.01 },
    { key: "bb_short_threshold", label: "Short BBP ≥", kind: "num", step: 0.01 },
    { key: "hold_h", label: "Hold hours", kind: "select", options: ["12", "24", "68"] },
    { key: "sl_roe_pct", label: "Stop loss ROE %", kind: "num", step: 1 },
    { key: "margin_pct", label: "Margin % leftover (forced 100%)", kind: "pct_frac", step: 1 },
  ],
  "hummingbot-onlyup": [
    { key: "scale_roe_pct", label: "Clip at ROE %", kind: "num", step: 1 },
    { key: "scale_out_pct", label: "Clip size %", kind: "pct_frac", step: 1 },
    { key: "sl_roe_pct", label: "Stop loss ROE %", kind: "num", step: 1 },
    { key: "st_max_dist", label: "ST max dist %", kind: "pct_frac", step: 0.1 },
    { key: "margin_pct", label: "Margin % leftover", kind: "pct_frac", step: 1 },
  ],
  "hummingbot-dman-maker": [
    { key: "dontGoCrazy", label: "dontGoCrazy", kind: "bool" },
    { key: "afterLossNoRebuy", label: "afterLossNoRebuy", kind: "bool" },
    { key: "take_profit", label: "Take profit %", kind: "pct_frac", step: 0.1 },
    { key: "trail_activation", label: "Trail arm %", kind: "pct_frac", step: 0.1 },
    { key: "trail_delta", label: "Trail delta %", kind: "pct_frac", step: 0.1 },
    { key: "executor_refresh_time", label: "Refresh sec", kind: "num", step: 1, min: 10, max: 600 },
    { key: "sl_roe_pct", label: "Stop loss ROE %", kind: "num", step: 1 },
    { key: "margin_pct", label: "Margin % leftover", kind: "pct_frac", step: 1 },
  ],
  default: [],
};

export const LS_KEY = "matrix-wire-pos-v2";

export function loadPos() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function savePos(nodes) {
  const pos = {};
  for (const n of nodes) pos[n.id] = n.position;
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(pos));
  } catch {
    /* ignore quota */
  }
}

export function clearPos() {
  try {
    localStorage.removeItem(LS_KEY);
  } catch {
    /* ignore */
  }
}

export function buildGraph() {
  const at = (id) => LAYOUT[id] || { x: 0, y: 0 };
  const coreCoins = COINS.filter((c) => ["BTC", "ETH", "LTC"].includes(c.coin));

  const nodes = [
    {
      id: "gauge-condor",
      type: "gauge",
      position: at("gauge-condor"),
      data: { status: "offline", kind: "condor", title: "Condor" },
    },
    {
      id: "gauge-hmm",
      type: "gauge",
      position: at("gauge-hmm"),
      data: { status: "offline", kind: "hmm", title: "HMM / vol" },
    },
    ...coreCoins.map((c) => ({
      id: coinId(c.coin),
      type: "coin",
      position: at(coinId(c.coin)),
      data: { coin: c.coin, color: c.color, name: c.coin },
    })),
    ...HUNTERS.filter((h) => h.hasBias).map((h) => ({
      id: biasId(h.id),
      type: "bias",
      position: at(biasId(h.id)),
      data: {
        botId: h.id,
        name: h.name,
        color: h.color,
        icon: h.icon,
        coins: h.coins,
        bias: Object.fromEntries(h.coins.map((c) => [c, "FLAT"])),
      },
    })),
    ...HUNTERS.map((h) => ({
      id: h.id,
      type: "bot",
      position: at(h.id),
      data: { ...h, running: false, ok: false, n_pos: 0, positions: [], logs: [] },
    })),
    { id: "funds", type: "funds", position: at("funds"), data: { wallets: [] } },
    { id: "logs", type: "logs", position: at("logs"), data: { lines: [] } },
  ];

  const edges = [];
  for (const h of HUNTERS) {
    if (h.hasBias) {
      edges.push({
        id: "e-bias-" + h.id,
        source: biasId(h.id),
        sourceHandle: "bias",
        target: h.id,
        targetHandle: "bias",
        type: "smoothstep",
        style: { stroke: "#ffc857", strokeWidth: 1.4, strokeDasharray: "5 4" },
      });
    }
    edges.push({
      id: "e-logs-" + h.id,
      source: h.id,
      sourceHandle: "logs",
      target: "logs",
      targetHandle: "in",
      type: "smoothstep",
      style: { stroke: "#7ecbff", strokeWidth: 1.2, opacity: 0.55 },
    });
  }
  return { nodes, edges };
}
