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

import { biasId, coinId, COINS, HUNTERS } from "./seed.js";
import { BOARD_CATEGORIES, HEALTH_TOOL_IDS, HOME_STAT_TOOLS, HOME_TOOL_IDS, lsGraphKey } from "./boards.js";
import { V2_MENU } from "./sheets.jsx";

export const CATEGORIES = [
  { id: "hummingbots", label: "Hummingbots" },
  { id: "hummingbots_v2", label: "standard hummingbots_v2" },
  { id: "coins", label: "Coins" },
  { id: "wallets", label: "Wallets" },
  { id: "settings", label: "Settings" },
  { id: "bias", label: "BIAS" },
  { id: "gauges", label: "Gauges" },
  { id: "strategy", label: "Strategy" },
  { id: "condor", label: "Condor" },
  { id: "traders", label: "Traders" },
  { id: "system", label: "System health" },
  { id: "output", label: "Output" },
];

export function emitWire(kind, payload) {
  window.dispatchEvent(new CustomEvent("wire-set", { detail: { kind, ...payload } }));
}

export function isBiasReportBot(n) {
  if (!n) return false;
  if (n.id === "telegram-bot-t84jlc") return true;
  const name = String((n.data && n.data.name) || "").toUpperCase();
  return name.includes("BIAS BOT");
}

function hunterCatalog(hunters) {
  return hunters.map((h) => ({
    toolId: h.id,
    category: "hummingbots",
    unique: true,
    type: "bot",
    label: h.name,
    hint: h.hasBias ? (h.coins || []).join(" / ") : "multi · no BIAS",
    icon: h.icon,
    color: h.color,
    data: { ...h },
  }));
}

export function boardCategories(board) {
  return BOARD_CATEGORIES[board] || CATEGORIES;
}

function v2Tools(rows) {
  const out = [];
  for (const raw of rows || []) {
    const id = raw && raw.id;
    if (!id || String(id).indexOf("hummingbot-v2-") !== 0) continue;
    const hasBias = raw.hasBias !== false;
    const menu = V2_MENU[id] || raw.desc || "Official Hummingbot V2 controller.";
    const color = raw.color || "#7ecbff";
    const name = raw.name || id;
    out.push({
      toolId: id,
      category: "hummingbots_v2",
      unique: true,
      type: "bot",
      label: name,
      hint: menu,
      menu,
      icon: "⧉",
      color,
      data: { ...raw, name, color, hasBias, v2: true },
    });
    out.push({
      toolId: "ctrl-" + id,
      category: "settings",
      unique: true,
      type: "ctrl",
      label: name + " controller",
      hint: "V2 std knobs",
      icon: "⚙",
      color,
      data: { botId: id, name, color, icon: "⚙", v2: true, values: {}, fields: [] },
    });
    out.push({
      toolId: "wallet-" + id,
      category: "wallets",
      unique: true,
      type: "wallet",
      label: name + " wallet",
      hint: "wire before it can trade",
      icon: "▣",
      color,
      data: { botId: id, name: name + " wallet", color, v2: true },
    });
    if (hasBias) {
      out.push({
        toolId: biasId(id),
        category: "bias",
        unique: true,
        type: "bias",
        label: name + " BIAS",
        hint: "LONG / SHORT / FLAT",
        icon: "⊕",
        color,
        data: { botId: id, name, color, coins: ["BTC", "ETH", "LTC", "TAO"], hasBias: true, v2: true, bias: {} },
      });
    }
  }
  return out;
}

export function catalogTools({ hunters, extraWallets = [], extraHunters = [], extraLeaders = [], v2Sleepers = [], board = "desk" } = {}) {
  const huntersList = Array.isArray(hunters) && hunters.length ? hunters : HUNTERS;
  const seen = new Set(huntersList.map((h) => h.id));
  const outHunters = [...huntersList];
  for (const raw of extraHunters) {
    const id = raw && raw.id;
    if (!id || seen.has(id)) continue;
    outHunters.push({
      id,
      name: raw.name || id,
      href: raw.href || "/confiig",
      icon: raw.icon || "⧉",
      color: raw.color || "#a78bfa",
      hasBias: raw.hasBias !== false,
      coins: raw.coins || ["BTC", "ETH", "LTC"],
      controller_fields: raw.controller_fields || [],
    });
    seen.add(id);
  }
  const leaderTools = (Array.isArray(extraLeaders) ? extraLeaders : []).map((t) => ({
    toolId: t.id || ("copy-trader-" + String(t.address || "").slice(0, 10)),
    category: "traders",
    unique: true,
    type: "trader",
    label: (t.num ? `T#${t.num} ` : "") + (t.title || "Leader"),
    hint: t.alumni
      ? "was on Copy · wire does not re-add to yml"
      : "wire into Copy T1 / T2 / T3 to follow",
    icon: "◈",
    color: t.color || "#7ecbff",
    data: { ...t, kind: "trader", name: t.title },
  }));
  const list = [
  ...hunterCatalog(outHunters),
  ...v2Tools(v2Sleepers),
  ...leaderTools,
  ...COINS.map((c) => ({
    toolId: coinId(c.coin),
    category: "coins",
    unique: true,
    type: "coin",
    label: c.coin,
    hint: c.hint,
    icon: "●",
    color: c.color,
    data: { coin: c.coin, color: c.color, name: c.coin },
  })),
  {
    toolId: "sl-pct",
    category: "settings",
    unique: false,
    type: "pct",
    label: "Stop loss %",
    hint: "wire into bot SL · ROE exit %",
    icon: "▼",
    color: "#ff4d6d",
    data: { kind: "sl", pct: 20, name: "Stop loss" },
  },
  {
    toolId: "moon-pct",
    category: "settings",
    unique: false,
    type: "pct",
    label: "Moon %",
    hint: "wire into bot MOON · +% of live coin to exit long",
    icon: "▲",
    color: "#ffd24a",
    data: { kind: "moon", pct: 50, name: "Moon" },
  },
  ...outHunters.map((h) => ({
    toolId: "ctrl-" + h.id,
    category: "settings",
    unique: true,
    type: "ctrl",
    label: h.name + " controller",
    hint: "trail / rebuy / clip for this hunter",
    icon: "⚙",
    color: h.color,
    data: { botId: h.id, name: h.name, color: h.color, icon: h.icon, href: h.href, values: {}, fields: h.controller_fields || [] },
  })),
  {
    toolId: "bias-manual",
    category: "bias",
    unique: false,
    type: "bias",
    label: "Manual BIAS",
    hint: "LONG / SHORT / FLAT · wire into bot BIAS",
    icon: "⊕",
    color: "#ffc857",
    data: { name: "Manual BIAS", coins: ["ALL"], bias: { ALL: "FLAT" } },
  },
  {
    toolId: "desk-bias",
    category: "bias",
    unique: true,
    type: "desk_bias",
    label: "Desk BIAS",
    hint: "LONG / FLAT / SHORT · Telegram OUT → TG · does not close",
    icon: "⊕",
    color: "#f0b90b",
    data: { kind: "desk", name: "Desk BIAS", bias: "", cmd: "" },
  },
  ...outHunters.filter((h) => h.hasBias).map((h) => ({
    toolId: biasId(h.id),
    category: "bias",
    unique: true,
    type: "bias",
    label: h.name + " BIAS",
    hint: (h.coins || []).join(" / ") + " · this hunter",
    icon: "⊕",
    color: h.color,
    data: {
      botId: h.id,
      name: h.name,
      color: h.color,
      icon: h.icon,
      coins: h.coins,
      bias: Object.fromEntries((h.coins || []).map((c) => [c, "FLAT"])),
    },
  })),
  {
    toolId: "gauge-condor",
    category: "gauges",
    unique: true,
    type: "gauge",
    label: "Condor",
    hint: "Grok / heuristic BIAS Messgerät",
    icon: "◎",
    color: "#c084fc",
    data: { kind: "condor", title: "Condor" },
  },
  {
    toolId: "bias-xlate",
    category: "condor",
    unique: false,
    type: "xlate",
    label: "Translator",
    hint: "LONG SHORT FLAT CLOSE OPEN100% · Telegram → hunter BIAS",
    icon: "⇄",
    color: "#34d399",
    data: { kind: "translator", name: "Translator", cmd: "", coins: ["BTC", "ETH", "LTC"] },
  },
  {
    toolId: "close-xlate",
    category: "condor",
    unique: false,
    type: "xlate",
    label: "Close translator",
    hint: "CLOSE only · extra Telegram → hunter E-CLOSE · idle does nothing",
    icon: "⏹",
    color: "#ff4d6d",
    data: { kind: "closer", name: "Close translator", cmd: "", coins: ["BTC", "ETH", "LTC"] },
  },
  {
    toolId: "telegram-bot",
    category: "condor",
    unique: false,
    type: "agent",
    label: "Telegram bot",
    hint: "token + chat ids · LOG→IN = open/close alerts · TG→Condor = commands",
    icon: "✈",
    color: "#34d399",
    data: { kind: "telegram", name: "Telegram bot", token: "", chat_ids: "" },
  },
  {
    toolId: "grok-bot",
    category: "condor",
    unique: false,
    type: "agent",
    label: "Grok bot",
    hint: "xAI key + model · wire into Condor GROK",
    icon: "✦",
    color: "#2A9FD6",
    data: { kind: "grok", name: "Grok bot", api_key: "", model: "grok-4.5" },
  },
  {
    toolId: "gauge-hmm",
    category: "gauges",
    unique: true,
    type: "gauge",
    label: "HMM / vol",
    hint: "regime Messgerät",
    icon: "◎",
    color: "#c084fc",
    data: { kind: "hmm", title: "HMM / vol" },
  },
  {
    toolId: "gauge-vol",
    category: "gauges",
    unique: true,
    type: "gauge",
    label: "Vol meter",
    hint: "realized vol · later",
    icon: "∿",
    color: "#a78bfa",
    data: { kind: "vol", title: "Vol meter" },
  },
  {
    toolId: "gauge-bb",
    category: "gauges",
    unique: true,
    type: "gauge",
    label: "BB width",
    hint: "squeeze / expansion · later",
    icon: "▭",
    color: "#818cf8",
    data: { kind: "bb", title: "BB width" },
  },
  {
    toolId: "gauge-pump",
    category: "strategy",
    unique: true,
    type: "gauge",
    label: "Post pump gauge",
    hint: "quiet / pump / post · recognition only, no orders",
    icon: "▲",
    color: "#f0b90b",
    data: { kind: "pump", title: "Post pump gauge" },
  },
  {
    toolId: "funds",
    category: "wallets",
    unique: true,
    type: "funds",
    label: "FUNDS bus",
    hint: "all HL subs",
    icon: "▣",
    color: "#f0b90b",
    data: { wallets: [] },
  },
  ...outHunters.map((h) => ({
    toolId: "wallet-" + h.id,
    category: "wallets",
    unique: true,
    type: "wallet",
    label: h.name + " wallet",
    hint: "subaccount · Confiig",
    icon: "⬡",
    color: h.color,
    data: { botId: h.id, name: h.name, color: h.color, icon: h.icon },
  })),
  {
    toolId: "risk-wallet",
    category: "wallets",
    unique: false,
    type: "risk_wallet",
    label: "RISK wallet",
    hint: "duplicate · own 0x · % / hours / min $",
    icon: "⛨",
    color: "#ff4d6d",
    data: { kind: "risk", name: "RISK wallet", skim_mode: "win_pct", skim_pct: 20, skim_every_h: 24, skim_min_usd: 10, armed: false, address: "" },
  },
  ...extraWallets.map((w) => ({
    toolId: w.id,
    category: "wallets",
    unique: true,
    type: "wallet",
    label: w.name || "Wallet",
    hint: w.address ? String(w.address).slice(0, 10) + "…" : "extra subaccount",
    icon: "⬡",
    color: w.color || "#f0b90b",
    data: {
      name: w.name,
      address: w.address || "",
      custom: true,
      use_vault: w.use_vault !== false,
      color: w.color || "#f0b90b",
      icon: "⬡",
    },
  })),
  {
    toolId: "health-host",
    category: "system",
    unique: true,
    type: "health",
    label: "Host CPU / RAM / disk",
    hint: "max knobs · LIVE + ALERT into Stream",
    icon: "▣",
    color: "#9dffc0",
    data: { kind: "host", name: "Host", max: { max_cpu: 85, max_mem: 90, max_disk: 92 } },
  },
  {
    toolId: "health-net",
    category: "system",
    unique: true,
    type: "health",
    label: "Internet",
    hint: "1.1.1.1 probe · no HL tokens · LIVE + ALERT",
    icon: "⇄",
    color: "#7ecbff",
    data: { kind: "net", name: "Internet", max: { max_ms: 800 } },
  },
  {
    toolId: "health-hlip",
    category: "system",
    unique: true,
    type: "health",
    label: "HL REST tokens",
    hint: "this IP weight/min · LIVE + ALERT",
    icon: "◈",
    color: "#f0b90b",
    data: { kind: "hlip", name: "HL tokens", max: { max_pct: 85, max_used: 1000 } },
  },
  {
    toolId: "logs",
    category: "output",
    unique: true,
    type: "logs",
    label: "LOGS bus",
    hint: "live tail",
    icon: "≡",
    color: "#7ecbff",
    data: { lines: [] },
  },
  {
    toolId: "out-xml",
    category: "output",
    unique: false,
    type: "output",
    label: "XML file",
    hint: "download / save conf/wire_graph.xml",
    icon: "▤",
    color: "#86efac",
    data: { kind: "xml" },
  },
  {
    toolId: "out-live",
    category: "output",
    unique: false,
    type: "output",
    label: "Live stream",
    hint: "live positions + logs",
    icon: "▶",
    color: "#7ecbff",
    data: { kind: "live" },
  },
  {
    toolId: "out-stream",
    category: "output",
    unique: false,
    type: "output",
    label: "Stream",
    hint: "Telegram / Grok / logs feed",
    icon: "☰",
    color: "#34d399",
    data: { kind: "stream", lines: [] },
  },
  {
    toolId: "email-bot",
    category: "condor",
    unique: false,
    type: "agent",
    label: "Email alerts",
    hint: "SMTP later · Health ALERT → here",
    icon: "✉",
    color: "#ffc857",
    data: { kind: "email", name: "Email alerts", to: "", smtp: "" },
  },
  {
    toolId: "backup-twin",
    category: "system",
    unique: true,
    type: "backup",
    label: "1:1 backup twin",
    hint: "second machine · later",
    icon: "⧉",
    color: "#a78bfa",
    data: { kind: "twin", name: "1:1 backup" },
  },
];
  if (board === "home") {
    const homeBots = hunterCatalog(outHunters).map((t) => ({
      ...t,
      type: "homebot",
      hint: "drop on Home canvas · sets position on /",
      data: { ...(t.data || {}), kind: "homebot", hunter: t.toolId },
    }));
    const homeStats = HOME_STAT_TOOLS.map((t) => ({
      toolId: t.toolId,
      category: "homeinfo",
      unique: true,
      type: "homestat",
      label: t.label,
      hint: t.hint,
      icon: t.icon,
      color: t.color,
      data: { kind: t.kind, name: t.label, color: t.color, icon: t.icon, w: t.w, h: t.h },
    }));
    return [
      ...homeStats,
      ...homeBots,
      ...list.filter((t) => HOME_TOOL_IDS.has(t.toolId)).map((t) => (
        t.toolId === "telegram-bot"
          ? { ...t, hint: "output on Home · paste token · not BIAS" }
          : t
      )),
    ];
  }
  if (board === "health") {
    return list.filter((t) => HEALTH_TOOL_IDS.has(t.toolId)).map((t) => (
      t.toolId === "telegram-bot"
        ? { ...t, hint: "error alerts · not BIAS" }
        : t
    ));
  }
  return list.filter((t) => t.toolId !== "email-bot" && t.toolId !== "backup-twin");
}

export let TOOLS = catalogTools();

export function refreshTools(opts) {
  TOOLS = catalogTools(opts);
  return TOOLS;
}

export function toolById(id) {
  return TOOLS.find((t) => t.toolId === id) || null;
}

export function uid(prefix) {
  return prefix + "-" + Math.random().toString(36).slice(2, 8);
}

export function makeNode(tool, position) {
  const id = tool.unique ? tool.toolId : uid(tool.toolId);
  return {
    id,
    type: tool.type,
    position: position || { x: 80, y: 80 },
    data: { ...(tool.data || {}), toolId: tool.toolId },
  };
}

export function edgeStyle(sourceHandle) {
  if (sourceHandle === "bias") {
    return { stroke: "#ffc857", strokeWidth: 1.4, strokeDasharray: "5 4" };
  }
  if (sourceHandle === "coin") {
    return { stroke: "#94a3b8", strokeWidth: 1.3 };
  }
  if (sourceHandle === "wallet") {
    return { stroke: "#f0b90b", strokeWidth: 1.4 };
  }
  if (sourceHandle === "income" || sourceHandle === "funds") {
    return { stroke: "#86efac", strokeWidth: 1.4 };
  }
  if (sourceHandle === "risk") {
    return { stroke: "#ff4d6d", strokeWidth: 1.5, strokeDasharray: "4 3" };
  }
  if (sourceHandle === "logs") {
    return { stroke: "#7ecbff", strokeWidth: 1.2, opacity: 0.7 };
  }
  if (sourceHandle === "sl") {
    return { stroke: "#ff4d6d", strokeWidth: 1.4 };
  }
  if (sourceHandle === "moon") {
    return { stroke: "#ffd24a", strokeWidth: 1.4 };
  }
  if (sourceHandle === "trader") {
    return { stroke: "#f0b90b", strokeWidth: 1.5 };
  }
  if (sourceHandle === "eclose") {
    return { stroke: "#ff4d6d", strokeWidth: 1.6 };
  }
  if (sourceHandle === "live") {
    return { stroke: "#7ecbff", strokeWidth: 1.3 };
  }
  if (sourceHandle === "alert") {
    return { stroke: "#ff4d6d", strokeWidth: 1.5 };
  }
  if (sourceHandle === "ctrl") {
    return { stroke: "#c084fc", strokeWidth: 1.3, strokeDasharray: "4 4" };
  }
  if (sourceHandle === "tg") {
    return { stroke: "#34d399", strokeWidth: 1.4 };
  }
  if (sourceHandle === "grok") {
    return { stroke: "#2A9FD6", strokeWidth: 1.4 };
  }
  if (sourceHandle === "out") {
    return { stroke: "#34d399", strokeWidth: 1.3 };
  }
  return { stroke: "#c084fc", strokeWidth: 1.3, strokeDasharray: "4 4" };
}

export function makeEdge(id, source, sourceHandle, target, targetHandle) {
  return {
    id,
    source,
    sourceHandle,
    target,
    targetHandle,
    type: "smoothstep",
    style: edgeStyle(sourceHandle),
  };
}

export function autoEdgesFor(node, existing) {
  const has = (id) => existing.some((n) => n.id === id);
  const out = [];
  if (node.type === "bot" || node.type === "homebot") {
    if (node.type === "bot") {
      const bid = biasId(node.id);
      if (node.data.hasBias && has(bid)) {
        out.push(makeEdge("e-bias-" + node.id, bid, "bias", node.id, "bias"));
      }
      const wid = "wallet-" + node.id;
      if (has(wid)) out.push(makeEdge("e-wallet-" + node.id, wid, "wallet", node.id, "wallet"));
      const cid = "ctrl-" + node.id;
      if (has(cid)) out.push(makeEdge("e-ctrl-" + node.id, cid, "ctrl", node.id, "ctrl"));
    }
    if (has("logs")) out.push(makeEdge("e-logs-" + node.id, node.id, "logs", "logs", "in"));
  }
  if (node.type === "logs") {
    for (const n of existing) {
      if (n.type === "homebot") {
        out.push(makeEdge("e-logs-" + n.id, n.id, "logs", node.id, "in"));
      }
    }
  }
  if (node.type === "bias" && node.data.botId && has(node.data.botId)) {
    out.push(makeEdge("e-bias-" + node.data.botId, node.id, "bias", node.data.botId, "bias"));
  }
  if (node.type === "ctrl" && node.data.botId && has(node.data.botId)) {
    out.push(makeEdge("e-ctrl-" + node.data.botId, node.id, "ctrl", node.data.botId, "ctrl"));
  }
  if (node.type === "wallet" && node.data.botId && has(node.data.botId)) {
    out.push(makeEdge("e-wallet-" + node.data.botId, node.id, "wallet", node.data.botId, "wallet"));
  }

  if (node.type === "xlate") {
    const closer = node.data && node.data.kind === "closer";
    if (!closer) {
      for (const n of existing) {
        if (n.type === "agent" && n.data && n.data.kind === "telegram" && !isBiasReportBot(n)) {
          out.push(makeEdge("e-xlate-" + n.id + "-" + node.id, n.id, "out", node.id, "tg"));
        }
      }
    }
  }
  if (node.type === "agent" && node.data && node.data.kind === "telegram" && !isBiasReportBot(node)) {
    for (const n of existing) {
      if (n.type === "xlate" && !(n.data && n.data.kind === "closer")) {
        out.push(makeEdge("e-xlate-" + node.id + "-" + n.id, node.id, "out", n.id, "tg"));
      }
    }
  }
  if (node.type === "homestat" && node.data && node.data.kind === "grok") {
    for (const n of existing) {
      if (isBiasReportBot(n)) {
        out.push(makeEdge("e-grok-bias-" + n.id, node.id, "out", n.id, "in"));
      }
    }
  }
  if (isBiasReportBot(node)) {
    for (const n of existing) {
      if (n.type === "homestat" && n.data && n.data.kind === "grok") {
        out.push(makeEdge("e-grok-bias-" + node.id, n.id, "out", node.id, "in"));
      }
    }
  }
  if (node.type === "agent" && has("gauge-condor") && !isBiasReportBot(node)) {
    const kind = (node.data && node.data.kind) || "telegram";
    if (kind !== "email") {
      const h = kind === "grok" ? "grok" : "tg";
      out.push(makeEdge("e-condor-" + h + "-" + node.id, node.id, h, "gauge-condor", h));
    }
  }
  if (node.type === "agent") {
    const kind = (node.data && node.data.kind) || "telegram";
    for (const n of existing) {
      if (n.type === "output" && n.data && n.data.kind === "stream") {
        out.push(makeEdge("e-stream-" + node.id, node.id, "out", n.id, "in"));
      }
    }
    if ((kind === "telegram" || kind === "email") && !has("gauge-condor")) {
      for (const n of existing) {
        if (n.type === "health") {
          out.push(makeEdge("e-alert-" + n.id + "-" + node.id, n.id, "alert", node.id, "in"));
        }
      }
    }
  }
  if (node.type === "health") {
    for (const n of existing) {
      if (n.type === "output" && n.data && n.data.kind === "stream") {
        out.push(makeEdge("e-live-" + node.id + "-" + n.id, node.id, "live", n.id, "in"));
      }
      const kind = n.data && n.data.kind;
      if (n.type === "agent" && (kind === "telegram" || kind === "email") && !has("gauge-condor")) {
        out.push(makeEdge("e-alert-" + node.id + "-" + n.id, node.id, "alert", n.id, "in"));
      }
    }
  }
  if (node.type === "output" && node.data && node.data.kind === "stream") {
    for (const n of existing) {
      if (n.type === "agent") {
        out.push(makeEdge("e-stream-" + n.id, n.id, "out", node.id, "in"));
      }
      if (n.type === "health") {
        out.push(makeEdge("e-live-" + n.id + "-" + node.id, n.id, "live", node.id, "in"));
      }
    }
  }
  return out;
}

export function isValidConnection(c) {
  if (!c || c.source === c.target) return false;
  const src = c.sourceHandle || "";
  const tgt = c.targetHandle || "";
  if (src === "coin" && tgt === "coin") return true;
  if (src === "wallet" && tgt === "wallet") return true;
  if ((src === "income" || src === "funds") && (tgt === "income" || tgt === "in")) return true;
  if (src === "risk" && tgt === "risk") return true;
  if (src === "logs" && (tgt === "in" || tgt === "logs")) return true;
  if (src === "sl" && tgt === "sl") return true;
  if (src === "moon" && tgt === "moon") return true;
  if (src === "trader" && (tgt === "trader" || /^trader-[123]$/.test(tgt))) return true;
  if (src === "eclose" && tgt === "eclose") return true;
  if (src === "bias" && tgt === "bias") return true;
  if (src === "ctrl" && tgt === "ctrl") return true;
  if ((src === "tg" || src === "out") && tgt === "tg") return true;
  if (src === "tg" && tgt === "tg") return true;
  if (src === "grok" && tgt === "grok") return true;
  if (tgt === "in" && (src === "logs" || src === "income" || src === "funds" || src === "bias" || src === "tg" || src === "grok" || src === "out" || src === "live" || src === "alert")) return true;
  return src === tgt;
}

export function xmlEscape(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function graphToXml(nodes, edges, home) {
  const ts = (home && home.ts) || new Date().toISOString();
  const parts = [`<?xml version="1.0" encoding="UTF-8"?>`, `<wire ts="${xmlEscape(ts)}" v="4">`, `  <nodes>`];
  for (const n of nodes) {
    const d = n.data || {};
    parts.push(
      `    <node id="${xmlEscape(n.id)}" type="${xmlEscape(n.type)}" x="${n.position?.x ?? 0}" y="${n.position?.y ?? 0}">`
    );
    if (d.name) parts.push(`      <name>${xmlEscape(d.name)}</name>`);
    if (d.coin) parts.push(`      <coin>${xmlEscape(d.coin)}</coin>`);
    if (d.kind) parts.push(`      <kind>${xmlEscape(d.kind)}</kind>`);
    if (d.botId) parts.push(`      <bot>${xmlEscape(d.botId)}</bot>`);
    if (d.address) parts.push(`      <address>${xmlEscape(d.address)}</address>`);
    if (d.equity_usd != null) parts.push(`      <equity>${xmlEscape(d.equity_usd)}</equity>`);
    if (d.n_pos != null) parts.push(`      <positions>${xmlEscape(d.n_pos)}</positions>`);
    if (d.bias && typeof d.bias === "object") {
      for (const [coin, mode] of Object.entries(d.bias)) {
        parts.push(`      <bias coin="${xmlEscape(coin)}">${xmlEscape(mode)}</bias>`);
      }
    }
    parts.push(`    </node>`);
  }
  parts.push(`  </nodes>`, `  <edges>`);
  for (const e of edges) {
    parts.push(
      `    <edge id="${xmlEscape(e.id)}" source="${xmlEscape(e.source)}" target="${xmlEscape(e.target)}" from="${xmlEscape(e.sourceHandle || "")}" to="${xmlEscape(e.targetHandle || "")}"/>`
    );
  }
  parts.push(`  </edges>`);
  if (home && Array.isArray(home.positions)) {
    parts.push(`  <live>`);
    for (const p of home.positions) {
      parts.push(
        `    <pos bot="${xmlEscape(p.bot)}" coin="${xmlEscape(p.coin)}" side="${xmlEscape(p.side)}" usd="${xmlEscape(p.usd_size)}" roe="${xmlEscape(p.roe_pct)}"/>`
      );
    }
    parts.push(`  </live>`);
  }
  parts.push(`</wire>`, "");
  return parts.join("\n");
}

export function slimGraph(nodes, edges) {
  const topN = (nodes || []).find((n) => n && n.data && n.data.top_scale != null && n.data.band === "top");
  const topScale = topN && topN.data ? topN.data.top_scale : undefined;
  return {
    v: 5,
    ...(topScale != null ? { top_scale: topScale } : {}),
    nodes: (nodes || []).filter((n) => n && n.type !== "homeband").map((n) => ({
      id: n.id,
      type: n.type,
      position: n.position,
      data: pickStatic(n.data),
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      sourceHandle: e.sourceHandle || null,
      target: e.target,
      targetHandle: e.targetHandle || null,
    })),
  };
}

function pickStatic(d) {
  if (!d || typeof d !== "object") return {};
  const out = {};
  for (const k of [
    "name", "icon", "color", "hasBias", "kind", "botId", "mode", "href",
    "title", "toolId", "coin", "coins", "pct", "values", "bias",
    "address", "custom", "use_vault",
    "skim_mode", "skim_pct", "skim_every_h", "skim_min_usd", "armed",
    "top_scale",
    "chat_ids", "model", "has_token", "has_key",
    "cmd", "last_raw", "last_ts", "max", "to", "smtp", "hunter", "w", "h", "extra", "band",
    "num", "title", "why", "short", "roster", "followed", "alumni",
    "day_pnl", "week_pnl", "month_pnl", "our_wins", "our_losses", "our_net",
    "coins",
  ]) {
    if (d[k] != null) out[k] = d[k];
  }
  return out;
}

export const LS_GRAPH = "matrix-wire-graph-v5";

export function migrateGraph(g) {
  if (!g || !Array.isArray(g.nodes)) return g;
  const edges = (g.edges || []).map((e) => {
    let sh = e.sourceHandle || null;
    let th = e.targetHandle || null;
    if (sh === "funds") sh = "income";
    if (e.target === "hummingbot-hl" && th === "trader") th = "trader-1";
    return { ...e, sourceHandle: sh, targetHandle: th };
  });
  return { ...g, v: 5, edges };
}

export function loadSavedGraph(board = "desk") {
  try {
    const key = lsGraphKey(board);
    const raw = localStorage.getItem(key) || (board === "desk" ? localStorage.getItem("matrix-wire-graph-v4") : null);
    if (!raw) return null;
    const g = JSON.parse(raw);
    if (!g || !Array.isArray(g.nodes)) return null;
    return migrateGraph(g);
  } catch {
    return null;
  }
}

export function saveSavedGraph(nodes, edges, board = "desk") {
  try {
    localStorage.setItem(lsGraphKey(board), JSON.stringify(slimGraph(nodes, edges)));
  } catch {
    /* ignore */
  }
}

export function clearSavedGraph() {
  try {
    localStorage.removeItem(LS_GRAPH);
  } catch {
    /* ignore */
  }
}

export function restoreSaved(saved) {
  const hunterById = Object.fromEntries(HUNTERS.map((h) => [h.id, h]));
  const nodes = (saved.nodes || []).map((n) => {
    let data = { ...(n.data || {}) };
    if (n.type === "bot" || n.type === "homebot") {
      const h = hunterById[n.id] || hunterById[data.hunter] || hunterById[data.botId] || {};
      data = { ...h, ...data, kind: n.type === "homebot" ? "homebot" : data.kind };
      if (n.type === "homebot") data.hunter = data.hunter || n.id;
    }
    return { id: n.id, type: n.type, position: n.position || { x: 0, y: 0 }, data };
  });
  const migrated = migrateGraph(saved);
  const edges = (migrated.edges || []).map((e) => ({
    ...e,
    type: "smoothstep",
    style: edgeStyle(e.sourceHandle),
  }));
  return { nodes, edges };
}
