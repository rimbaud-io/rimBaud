// Copyright 2026 rimbaud.io
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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addEdge,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import BotNode from "./nodes/BotNode.jsx";
import BiasNode from "./nodes/BiasNode.jsx";
import DeskBiasNode from "./nodes/DeskBiasNode.jsx";
import GaugeNode from "./nodes/GaugeNode.jsx";
import FundsNode from "./nodes/FundsNode.jsx";
import LogsNode from "./nodes/LogsNode.jsx";
import WalletNode from "./nodes/WalletNode.jsx";
import RiskWalletNode from "./nodes/RiskWalletNode.jsx";
import OutputNode from "./nodes/OutputNode.jsx";
import CoinNode from "./nodes/CoinNode.jsx";
import PctNode from "./nodes/PctNode.jsx";
import CtrlNode from "./nodes/CtrlNode.jsx";
import AgentNode from "./nodes/AgentNode.jsx";
import TranslatorNode from "./nodes/TranslatorNode.jsx";
import TraderNode from "./nodes/TraderNode.jsx";
import HealthNode, { HEALTH_DEFAULTS } from "./nodes/HealthNode.jsx";
import HomeBotNode from "./nodes/HomeBotNode.jsx";
import HomeStatNode from "./nodes/HomeStatNode.jsx";
import HomeBandNode from "./nodes/HomeBandNode.jsx";
import BackupNode from "./nodes/BackupNode.jsx";
import Toolbox from "./Toolbox.jsx";
import ContextMenu from "./ContextMenu.jsx";
import WalletModal from "./WalletModal.jsx";
import { buildGraph, clearPos, CONTROLLER_FIELDS, HUNTERS, LAYOUT } from "./lib/seed.js";
import { captureBaseline, fmtDiff, listChanges, stampDiffs } from "./lib/diff.js";
import { closeHits, liveKeySet, markEdges } from "./lib/impact.js";
import { HISTORY_KEEP, packStep, persistHistory, replayConfig } from "./lib/history.js";
import { clsPnl, fmtSecs, money } from "./lib/format.js";
import {
  autoEdgesFor,
  clearSavedGraph,
  graphToXml,
  isValidConnection,
  loadSavedGraph,
  makeEdge,
  makeNode,
  refreshTools,
  restoreSaved,
  saveSavedGraph,
  slimGraph,
  toolById,
} from "./lib/tools.js";
import { BOARDS, clampTopScale, ensureHomeStats, graphUrl, HOME_STAT_TOOLS, packHomeLayout, stepTopScale } from "./lib/boards.js";
import { ToolSheet } from "./lib/sheets.jsx";

const nodeTypes = {
  bot: BotNode,
  bias: BiasNode,
  desk_bias: DeskBiasNode,
  gauge: GaugeNode,
  funds: FundsNode,
  logs: LogsNode,
  wallet: WalletNode,
  risk_wallet: RiskWalletNode,
  output: OutputNode,
  coin: CoinNode,
  pct: PctNode,
  ctrl: CtrlNode,
  agent: AgentNode,
  xlate: TranslatorNode,
  trader: TraderNode,
  health: HealthNode,
  homebot: HomeBotNode,
  homestat: HomeStatNode,
  homeband: HomeBandNode,
  backup: BackupNode,
};

const hunterById = Object.fromEntries(HUNTERS.map((h) => [h.id, h]));

function asHunterList(hunters) {
  if (Array.isArray(hunters)) return hunters.filter((h) => h && h.id);
  if (hunters && Array.isArray(hunters.hunters)) {
    return hunters.hunters.filter((h) => h && h.id);
  }
  return HUNTERS;
}

function healthFromSnap(kind, maxIn, snap, rate) {
  const max = { ...(HEALTH_DEFAULTS[kind] || {}), ...(maxIn || {}) };
  const alerts = [];
  let live = {};
  let liveLine = null;
  if (kind === "host") {
    const h = (snap && snap.host) || {};
    live = h;
    liveLine = {
      name: "host",
      color: "#9dffc0",
      text: `CPU ${h.cpu_pct ?? "—"}% · RAM ${h.mem_pct ?? "—"}% · disk ${h.disk_pct ?? "—"}%`,
      ts: snap && snap.ts,
      botId: "health-host",
    };
    if (h.cpu_pct != null && h.cpu_pct >= Number(max.max_cpu)) alerts.push({ key: "cpu", text: `CPU ${h.cpu_pct}% ≥ ${max.max_cpu}%` });
    if (h.mem_pct != null && h.mem_pct >= Number(max.max_mem)) alerts.push({ key: "mem", text: `RAM ${h.mem_pct}% ≥ ${max.max_mem}%` });
    if (h.disk_pct != null && h.disk_pct >= Number(max.max_disk)) alerts.push({ key: "disk", text: `disk ${h.disk_pct}% ≥ ${max.max_disk}%` });
  } else if (kind === "net") {
    const net = (snap && snap.net) || {};
    live = net;
    liveLine = {
      name: "net",
      color: net.ok ? "#7ecbff" : "#ff4d6d",
      text: net.ok ? `up ${net.ms}ms · ${net.target || ""}` : `DOWN · ${net.error || "no route"}`,
      ts: snap && snap.ts,
      botId: "health-net",
      bad: !net.ok,
    };
    if (!net.ok) alerts.push({ key: "down", text: "internet DOWN" });
    else if (net.ms != null && net.ms >= Number(max.max_ms)) alerts.push({ key: "ms", text: `${net.ms}ms ≥ ${max.max_ms}ms` });
  } else if (kind === "hlip") {
    const r = (snap && snap.hlip) || rate || {};
    live = r;
    liveLine = {
      name: "hl",
      color: "#f0b90b",
      text: `HL ${r.used ?? 0}/${r.cap ?? 1200} · ${Number(r.pct || 0).toFixed(0)}% · ${r.left ?? "—"} left`,
      ts: snap && snap.ts,
      botId: "health-hlip",
      bad: Number(r.pct || 0) >= Number(max.max_pct),
    };
    if (r.pct != null && Number(r.pct) >= Number(max.max_pct)) alerts.push({ key: "pct", text: `HL ${Number(r.pct).toFixed(0)}% ≥ ${max.max_pct}%` });
    if (r.used != null && Number(r.used) >= Number(max.max_used)) alerts.push({ key: "used", text: `HL ${r.used} ≥ ${max.max_used}` });
    if (r.last_429_s != null && r.last_429_s < 45) alerts.push({ key: "429", text: `HL 429 ${r.last_429_s}s ago` });
  }
  const alertLine = alerts.length
    ? { name: "alert", color: "#ff4d6d", text: alerts.map((a) => a.text).join(" · "), ts: snap && snap.ts, botId: "health", bad: true }
    : null;
  return { live, max, alerts, liveLine, alertLine, ts: snap && snap.ts };
}

function copyDeskNodes(nodes) {
  return (nodes || []).filter(
    (n) =>
      n.id === "hummingbot-hl" ||
      n.type === "trader" ||
      n.id === "wallet-hummingbot-hl" ||
      n.id === "ctrl-hummingbot-hl"
  );
}

function mergeHome(nodes, edges, home, hunters = HUNTERS) {
  home = home || {};
  const hunterMap = Object.fromEntries(asHunterList(hunters).map((h) => [h.id, h]));
  const bots = home.bots || [];
  const byId = Object.fromEntries(bots.map((b) => [b.id, b]));
  const canvasById = Object.fromEntries((nodes || []).map((n) => [n.id, n]));
  const positions = home.positions || [];
  const wallets = bots.map((b) => ({
    id: b.id,
    name: b.name,
    address: b.address,
    equity_usd: b.equity_usd,
    color: (hunterMap[b.id] || hunterById[b.id] || {}).color || b.color,
  }));
  const allLines = [];
  for (const b of bots) {
    const meta = hunterMap[b.id] || hunterById[b.id] || b;
    for (const text of (b.logs || []).slice(-2)) {
      allLines.push({
        name: b.name,
        color: meta.color,
        text,
        botId: b.id,
        bad: /error|not ready|traceback|blocked|429/i.test(text || ""),
      });
    }
  }

  return (nodes || []).map((n) => {
    if (n.type === "bot" || n.type === "homebot") {
      const b = byId[n.id];
      if (!b) return n;
      return {
        ...n,
        data: {
          ...n.data,
          ...b,
          hasBias: n.data.hasBias,
          kind: n.data.kind || b.kind,
          icon: n.data.icon || b.icon,
          color: n.data.color || b.color,
          positions: positions.filter((p) => p.bot_id === n.id),
        },
      };
    }
    if (n.type === "trader") {
      const leaders = home.leaders || [];
      const addr = String((n.data && n.data.address) || "").toLowerCase();
      const t = leaders.find((x) => String(x.address || "").toLowerCase() === addr);
      if (!t) return n;
      return { ...n, data: { ...n.data, ...t, kind: "trader", name: t.title || n.data.name } };
    }
    if (n.type === "bias") {
      const b = byId[n.data.botId];
      if (!b) return n;
      return {
        ...n,
        data: {
          ...n.data,
          name: b.name,
          bias: b.bias || n.data.bias,
          coins: b.coins || n.data.coins,
        },
      };
    }
    if (n.type === "coin") {
      const coin = String(n.data.coin || "").toUpperCase();
      const pos = positions.filter((p) => String(p.coin || "").toUpperCase() === coin);
      const mark = (home.mids || {})[coin];
      return { ...n, data: { ...n.data, mark, n_pos: pos.length, positions: pos } };
    }
    if (n.type === "ctrl") {
      const b = byId[n.data.botId];
      const meta = hunterMap[n.data.botId] || hunterById[n.data.botId] || {};
      const fields = (meta.controller_fields && meta.controller_fields.length)
        ? meta.controller_fields
        : (n.data.fields || []);
      const pos = positions.filter((p) => p.bot_id === n.data.botId);
      const values = {
        ...(n.data.values || {}),
        ...((b && b.controller) || {}),
      };
      const closes = closeHits(n.data.botId, values, pos);
      return {
        ...n,
        data: {
          ...n.data,
          fields: fields.length ? fields : n.data.fields,
          values,
          positions: pos,
          closes,
        },
      };
    }
    if (n.type === "gauge") {
      const isPump = n.id === "gauge-pump" || n.data.kind === "pump";
      if (isPump) {
        const p = home.pump || {};
        return {
          ...n,
          data: {
            ...n.data,
            kind: "pump",
            title: "Post pump gauge",
            regime: p.regime,
            coins: p.coins || {},
            why: p.why,
            ts: p.ts,
            samples: p.samples,
          },
        };
      }
      const g = home.condor || {};
      const isCondor = n.id === "gauge-condor" || n.data.kind === "condor";
      if (!isCondor) return n;
      return {
        ...n,
        data: {
          ...n.data,
          kind: "condor",
          title: "Condor",
          regime: g.regime,
          bias: g.bias || {},
          why: g.why,
          reasons: g.reasons || {},
          source: g.source,
          ts: g.ts,
          running: g.running,
          applied: g.applied || [],
          wired: g.wired || [],
          error: g.error,
          setup: g.setup || {},
          telegram: g.telegram,
        },
      };
    }
    if (n.type === "agent") {
      const bots = ((home.condor || {}).setup || {}).bots || [];
      const row = bots.find((b) => b.id === n.id);
      const feed = ((home.condor || {}).feed) || [];
      const kind = (row && row.kind) || n.data.kind;
      const lines = feed
        .filter((r) => {
          const k = String(r.kind || "");
          if (kind === "grok") return k === "grok" || k === "xlate";
          return k.startsWith("tg") || k === "xlate";
        })
        .slice(-8)
        .map((r) => ({
          name: r.kind || "tg",
          color: String(r.kind || "").startsWith("tg") ? "#34d399" : "#ffc857",
          text: r.text,
          ts: r.ts,
        }));
      return {
        ...n,
        data: {
          ...n.data,
          name: (row && row.name) || n.data.name,
          has_token: !!((row && row.has_token) || n.data.has_token),
          has_key: !!((row && row.has_key) || n.data.has_key),
          chat_ids: row && row.chat_ids != null ? row.chat_ids : n.data.chat_ids,
          model: (row && row.model) || n.data.model,
          lines,
        },
      };
    }
    if (n.type === "xlate") {
      const row = ((home.condor || {}).xlate || {})[n.id] || {};
      return {
        ...n,
        data: {
          ...n.data,
          cmd: row.cmd || n.data.cmd || "",
          last_raw: row.raw || n.data.last_raw || "",
          last_ts: row.ts || n.data.last_ts || "",
          coins: row.coins || n.data.coins,
        },
      };
    }
    if (n.type === "desk_bias") {
      const db = home.desk_bias || {};
      const row = ((home.condor || {}).xlate || {})[n.id] || {};
      return {
        ...n,
        data: {
          ...n.data,
          kind: "desk",
          bias: db.bias || n.data.bias || "",
          hunters: db.hunters || [],
          updated: db.updated,
          cmd: row.cmd || db.bias || n.data.cmd || "",
          last_raw: row.raw || n.data.last_raw || "",
          last_ts: row.ts || db.updated || n.data.last_ts || "",
        },
      };
    }
    if (n.type === "health") {
      const packed = healthFromSnap(n.data.kind || "host", n.data.max, home.health || {}, home.rate);
      return { ...n, data: { ...n.data, ...packed } };
    }
    if (n.type === "homestat") {
      return {
        ...n,
        data: {
          ...n.data,
          totals: home.totals || {},
          rate: home.rate || {},
          condor: home.condor || {},
          desk_bias: home.desk_bias || {},
          positions: home.positions || [],
          ts: home.ts,
        },
      };
    }
    if (n.type === "funds") return { ...n, data: { ...n.data, wallets } };
    if (n.type === "logs") return { ...n, data: { ...n.data, lines: allLines.slice(-12) } };
    if (n.type === "wallet") {
      const b = byId[n.data.botId];
      if (!b) return n;
      return {
        ...n,
        data: {
          ...n.data,
          address: b.address,
          equity_usd: b.equity_usd,
          free_usd: b.free_usd,
          name: b.name,
        },
      };
    }
    if (n.type === "output" && n.data.kind === "stream") {
      const incoming = (edges || []).filter((e) => e.target === n.id);
      const feed = ((home.condor || {}).feed) || [];
      const healthLines = [];
      for (const e of incoming) {
        const src = canvasById[e.source];
        if (!src || src.type !== "health") continue;
        const packed = healthFromSnap(src.data.kind || "host", src.data.max, home.health || {}, home.rate);
        if ((e.sourceHandle || "live") === "live" && packed.liveLine) healthLines.push(packed.liveLine);
        if (e.sourceHandle === "alert" && packed.alertLine) healthLines.push(packed.alertLine);
      }
      const feedLines = feed.slice(-16).map((row) => {
        const k = String(row.kind || "");
        return {
          name: k || "feed",
          color: k.startsWith("tg") ? "#34d399" : k === "grok" ? "#2A9FD6" : k === "xlate" ? "#ffc857" : "#c084fc",
          text: row.text,
          ts: row.ts,
          botId: "condor",
        };
      });
      const lines = healthLines.concat(feedLines).slice(-16);
      return {
        ...n,
        data: {
          ...n.data,
          lines,
          n_src: incoming.length,
          n_feed: feed.length,
        },
      };
    }
    if (n.type === "output" && n.data.kind === "live") {
      const srcs = (edges || []).filter((e) => e.target === n.id).map((e) => e.source);
      const botSrcs = new Set();
      for (const s of srcs) {
        if (byId[s]) botSrcs.add(s);
        if (s.startsWith("wallet-")) botSrcs.add(s.slice(7));
        if (s === "logs") bots.forEach((b) => botSrcs.add(b.id));
      }
      const use = botSrcs.size ? allLines.filter((l) => botSrcs.has(l.botId)) : allLines;
      const pos = botSrcs.size ? positions.filter((p) => botSrcs.has(p.bot_id)) : positions;
      const upnl = pos.reduce((a, p) => a + Number(p.pnl_usd || 0), 0);
      return {
        ...n,
        data: { ...n.data, lines: use.slice(-12), n_pos: pos.length, upnl_usd: upnl },
      };
    }
    return n;
  });
}

function annotateWires(nodes, edges) {
  const list = nodes || [];
  const byId = Object.fromEntries(list.map((n) => [n.id, n]));
  return list.map((n) => {
    if (n.type !== "bot") return n;
    const ins = edges.filter((e) => e.target === n.id);
    const outs = edges.filter((e) => e.source === n.id);
    const coinEdge = ins.find((e) => e.targetHandle === "coin");
    const slEdge = ins.find((e) => e.targetHandle === "sl");
    const moonEdge = ins.find((e) => e.targetHandle === "moon");
    const biasEdge = ins.find((e) => e.targetHandle === "bias");
    const ecloseEdge = ins.find((e) => e.targetHandle === "eclose");
    const walletEdge = ins.find((e) => e.targetHandle === "wallet");
    const incomeEdge = outs.find((e) => e.sourceHandle === "income" || e.sourceHandle === "funds");
    const coinNode = coinEdge ? byId[coinEdge.source] : null;
    const slNode = slEdge ? byId[slEdge.source] : null;
    const moonNode = moonEdge ? byId[moonEdge.source] : null;
    const biasNode = biasEdge ? byId[biasEdge.source] : null;
    const ecloseNode = ecloseEdge ? byId[ecloseEdge.source] : null;
    const walletNode = walletEdge ? byId[walletEdge.source] : null;
    const incomeNode = incomeEdge ? byId[incomeEdge.target] : null;
    const biasMap = (biasNode && biasNode.data && biasNode.data.bias) || {};
    const biasVal = Object.values(biasMap).find((v) => v && v !== "FLAT") || Object.values(biasMap)[0] || null;
    const traderIns = ins.filter((e) => String(e.targetHandle || "").startsWith("trader"));
    const traderLabs = traderIns.map((e) => {
      const src = byId[e.source];
      const d = (src && src.data) || {};
      return d.num ? `T#${d.num}` : (d.title || d.short || "");
    }).filter(Boolean);
    return {
      ...n,
      data: {
        ...n.data,
        wired: {
          coin: (coinNode && (coinNode.data.coin || coinNode.data.name)) || null,
          sl: slNode && slNode.data.pct != null ? slNode.data.pct : null,
          moon: moonNode && moonNode.data.pct != null ? moonNode.data.pct : null,
          bias: biasVal,
          wallet: walletNode && (walletNode.data.address || walletNode.data.name),
          income: incomeNode && (incomeNode.data.address || incomeNode.data.name || incomeNode.id),
          risk: (function () {
            const re = outs.find((e) => e.sourceHandle === "risk");
            const rn = re ? byId[re.target] : null;
            return rn && (rn.data.address || rn.data.name || rn.id);
          })(),
          traders: traderLabs.join(" ") || null,
          eclose: ecloseNode && (ecloseNode.data.name || ecloseNode.data.kind || "armed"),
        },
      },
    };
  });
}

function initialGraph() {
  return { nodes: [], edges: [] };
}

function clampMenu(x, y) {
  const w = 280;
  const h = 420;
  return {
    x: Math.min(x, window.innerWidth - w - 8),
    y: Math.min(y, window.innerHeight - h - 8),
  };
}

function Board() {
  const seed = useMemo(() => initialGraph(), []);
  const [nodes, setNodes, onNodesChange] = useNodesState(seed.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(seed.edges);
  const [home, setHome] = useState(null);
  const [err, setErr] = useState("");
  const [toast, setToast] = useState("");
  const [selected, setSelected] = useState(null);
  const [toolPick, setToolPick] = useState(null);
  const [query, setQuery] = useState("");
  const [menu, setMenu] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [snapshots, setSnapshots] = useState([]);
  const [loadingLive, setLoadingLive] = useState(true);
  const [undoN, setUndoN] = useState(0);
  const [redoN, setRedoN] = useState(0);
  const [undoLabel, setUndoLabel] = useState("");
  const [liveKeys, setLiveKeys] = useState(() => new Set());
  const [compile, setCompile] = useState(null);
  const [compileBusy, setCompileBusy] = useState(false);
  const [dock, setDock] = useState(null);
  const [dockOpen, setDockOpen] = useState(false);
  const [dockBusy, setDockBusy] = useState(false);
  const [walletForm, setWalletForm] = useState(null);
  const [walletBusy, setWalletBusy] = useState(false);
  const [walletErr, setWalletErr] = useState("");
  const [fitOnce, setFitOnce] = useState(null);
  const [board, setBoard] = useState("desk");
  const [topScale, setTopScale] = useState(0.7);
  const topScaleRef = useRef(0.7);
  const [boardOpen, setBoardOpen] = useState(false);
  const [botsOpen, setBotsOpen] = useState(false);
  const [sysOpen, setSysOpen] = useState(false);
  const [fundsOpen, setFundsOpen] = useState(false);
  const [stratOpen, setStratOpen] = useState(false);
  const catalogRef = useRef({ hunters: HUNTERS, wallets: [], traders: [] });
  const boardRef = useRef("desk");
  const boardMenuRef = useRef(null);

  useEffect(() => {
    const open = document.querySelector("header .board-dd.open");
    if (!open) return;
    const menu = open.querySelector(".board-dd-menu");
    if (!menu) return;
    menu.style.left = "";
    menu.style.right = "";
    const dds = open.parentElement
      ? [...open.parentElement.querySelectorAll(":scope > .board-dd")]
      : [];
    if (dds.length && dds[dds.length - 1] === open) {
      menu.style.left = "auto";
      menu.style.right = "0";
    }
    const pad = 8;
    let r = menu.getBoundingClientRect();
    if (r.right > window.innerWidth - pad) {
      menu.style.left = "auto";
      menu.style.right = "0";
    }
    r = menu.getBoundingClientRect();
    if (r.left < pad) {
      const box = open.getBoundingClientRect();
      menu.style.left = Math.max(pad - box.left, 0) + "px";
      menu.style.right = "auto";
    }
  }, [boardOpen, botsOpen, sysOpen, fundsOpen, stratOpen]);

  useEffect(() => {
    fetch("/api/wire/catalog")
      .then((r) => r.json())
      .then((d) => {
        if (!d || d.ok === false || !Array.isArray(d.hunters)) return;
        catalogRef.current = {
          hunters: d.hunters,
          wallets: d.wallets || [],
          traders: d.traders || [],
          v2Sleepers: d.v2_sleepers || [],
        };
        refreshTools({
          hunters: d.hunters,
          extraWallets: d.wallets || [],
          extraLeaders: d.traders || [],
          v2Sleepers: d.v2_sleepers || [],
        });
        window.dispatchEvent(new Event("wire-catalog"));
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!boardOpen) return;
    const onDoc = (e) => {
      if (boardMenuRef.current && !boardMenuRef.current.contains(e.target)) setBoardOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setBoardOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [boardOpen]);

  useEffect(() => {
    boardRef.current = board;
    const cat = catalogRef.current || {};
    refreshTools({
      hunters: cat.hunters || HUNTERS,
      extraWallets: cat.wallets || [],
      extraLeaders: cat.traders || [],
      v2Sleepers: cat.v2Sleepers || [],
      board,
    });
    window.dispatchEvent(new Event("wire-catalog"));
  }, [board]);
  const saveTimer = useRef(null);
  const homeRef = useRef(null);
  const baselineRef = useRef(null);
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  const undoRef = useRef([]);
  const redoRef = useRef([]);
  const dragHist = useRef(false);
  nodesRef.current = nodes;
  edgesRef.current = edges;
  const { fitView, screenToFlowPosition, setCenter, getNode } = useReactFlow();

  const persistGraph = useCallback((ns, es) => {
    const annotated = annotateWires(ns, es);
    const which = boardRef.current || "desk";
    saveSavedGraph(annotated, es, which);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      const body = slimGraph(annotated, es);
      fetch(graphUrl(which), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).catch(() => {});
    }, 600);
  }, []);

  const persistLayout = useCallback((ns, ids) => {
    if (boardRef.current !== "desk") return;
    const want = ids && ids.size ? ns.filter((n) => ids.has(n.id)) : ns;
    const nodes = want.map((n) => ({
      id: n.id,
      position: { x: n.position.x, y: n.position.y },
    }));
    if (!nodes.length) return;
    fetch("/api/wire/layout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nodes }),
    }).catch(() => {});
  }, []);

  const remember = useCallback((label) => {
    const step = packStep(nodesRef.current, edgesRef.current, label);
    undoRef.current = [...undoRef.current.slice(-(HISTORY_KEEP - 1)), step];
    redoRef.current = [];
    setUndoN(undoRef.current.length);
    setRedoN(0);
    setUndoLabel(label || "step");
    persistHistory(step);
  }, []);

  const applyStep = useCallback(
    (step) => {
      if (!step || !step.graph) return;
      const restored = restoreSaved(step.graph);
      const next = stampDiffs(restored.nodes, baselineRef.current);
      setNodes(next);
      setEdges(restored.edges);
      persistLayout(next);
      persistGraph(next, restored.edges);
      replayConfig(step);
    },
    [persistGraph, persistLayout, setEdges, setNodes]
  );

  const undo = useCallback(() => {
    const prev = undoRef.current[undoRef.current.length - 1];
    if (!prev) return;
    const current = packStep(nodesRef.current, edgesRef.current, "redo");
    undoRef.current = undoRef.current.slice(0, -1);
    redoRef.current = [...redoRef.current, current];
    setUndoN(undoRef.current.length);
    setRedoN(redoRef.current.length);
    setUndoLabel((undoRef.current[undoRef.current.length - 1] || {}).label || "");
    applyStep(prev);
    setToast("recovered: " + (prev.label || "step"));
    setTimeout(() => setToast(""), 2800);
  }, [applyStep]);

  const redo = useCallback(() => {
    const next = redoRef.current[redoRef.current.length - 1];
    if (!next) return;
    const current = packStep(nodesRef.current, edgesRef.current, "undo");
    redoRef.current = redoRef.current.slice(0, -1);
    undoRef.current = [...undoRef.current, current];
    setUndoN(undoRef.current.length);
    setRedoN(redoRef.current.length);
    setUndoLabel(next.label || "step");
    applyStep(next);
    setToast("redo: " + (next.label || "step"));
    setTimeout(() => setToast(""), 2800);
  }, [applyStep]);

  const persist = useCallback(
    (changes) => {
      const dragging = changes.some((c) => c.type === "position" && c.dragging === true);
      if (dragging && !dragHist.current) {
        remember("move");
        dragHist.current = true;
      }
      onNodesChange(changes);
      const ended = changes.filter((c) => c.type === "position" && c.dragging !== true);
      if (!ended.length) return;
      dragHist.current = false;
      const ids = new Set(ended.map((c) => c.id));
      setTimeout(() => {
        setNodes((ns) => {
          persistLayout(ns, ids);
          return ns;
        });
      }, 0);
    },
    [onNodesChange, persistLayout, remember, setNodes]
  );

  useEffect(() => {
    homeRef.current = home;
  }, [home]);

  useEffect(() => {
    baselineRef.current = baseline;
  }, [baseline]);

  const applyLive = useCallback(
    (payload, toastMsg) => {
      const restored = restoreSaved(payload.graph || payload);
      const base = payload.baseline || captureBaseline(restored.nodes);
      baselineRef.current = base;
      setBaseline(base);
      setNodes(stampDiffs(restored.nodes, base));
      setEdges(restored.edges);
      setLiveKeys(liveKeySet(restored.edges));
      saveSavedGraph(restored.nodes, restored.edges, "desk");
      if (toastMsg) {
        setToast(toastMsg);
        setTimeout(() => setToast(""), 2800);
      }
    },
    [setNodes, setEdges]
  );

  const switchBoard = useCallback(
    async (next) => {
      if (!next || next === boardRef.current) return;
      persistGraph(nodesRef.current, edgesRef.current);
      boardRef.current = next;
      setBoard(next);
      setCompile(null);
      setSelected(null);
      setDockOpen(false);
      if (next === "desk") {
        setLoadingLive(true);
        try {
          const g = await fetch("/api/wire/live").then((r) => r.json());
          if (g && g.ok && g.graph && g.graph.nodes && g.graph.nodes.length) applyLive(g);
        } catch {
          /* keep current */
        }
        setLoadingLive(false);
        setFitOnce("all");
        return;
      }
      try {
        const d = await fetch(graphUrl(next)).then((r) => r.json());
        let ns = (d && d.graph && d.graph.nodes) || [];
        let es = (d && d.graph && d.graph.edges) || [];
        if (!ns.length) {
          const local = loadSavedGraph(next);
          if (local && local.nodes && local.nodes.length) {
            ns = local.nodes;
            es = local.edges || [];
          }
        }
        if (!ns.length && next === "home") {
          const hunters = (catalogRef.current && catalogRef.current.hunters) || HUNTERS;
          ns = packHomeLayout([
            ...HOME_STAT_TOOLS.map((t) => ({
              id: t.toolId,
              type: "homestat",
              position: { x: 0, y: 0 },
              data: { kind: t.kind, name: t.label, color: t.color, icon: t.icon, w: t.w, h: t.h, toolId: t.toolId },
            })),
            ...hunters.map((h) => ({
              id: h.id,
              type: "homebot",
              position: { x: 0, y: 0 },
              data: { ...h, kind: "homebot", name: h.name, toolId: h.id, hunter: h.id },
            })),
          ]);
          es = [];
        }
        if (next === "home") {
          const sc = clampTopScale((d && d.graph && d.graph.top_scale) || (ns.find((n) => n && n.data && n.data.top_scale) || {}).data?.top_scale);
          topScaleRef.current = sc;
          setTopScale(sc);
          ns = packHomeLayout(ensureHomeStats(ns), sc);
        }
        const restored = restoreSaved({ nodes: ns, edges: es });
        setNodes(restored.nodes);
        setEdges(restored.edges || []);
        if (restored.nodes.length) persistGraph(restored.nodes, restored.edges || []);
        setFitOnce("all");
      } catch {
        setNodes([]);
        setEdges([]);
      }
    },
    [applyLive, persistGraph, setEdges, setNodes]
  );

  const refreshSnapshots = useCallback(() => {
    fetch("/api/wire/snapshots?kind=save&limit=8")
      .then((r) => r.json())
      .then((d) => {
        if (d && Array.isArray(d.snapshots)) setSnapshots(d.snapshots);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoadingLive(true);
    fetch("/api/wire/live")
      .then((r) => r.json())
      .then((g) => {
        if (cancelled) return;
        if (g && g.ok && g.graph && Array.isArray(g.graph.nodes) && g.graph.nodes.length) {
          applyLive(g);
          setLoadingLive(false);
          refreshSnapshots();
          fetch("/api/wire/history?limit=8")
            .then((r) => r.json())
            .then((d) => {
              const steps = (d && d.steps) || [];
              undoRef.current = steps.slice().reverse();
              setUndoN(undoRef.current.length);
              setUndoLabel((undoRef.current[undoRef.current.length - 1] || {}).label || "");
            })
            .catch(() => {});
          setFitOnce("copy");
          return;
        }
        throw new Error((g && g.error) || "empty live graph");
      })
      .catch(() => {
        if (cancelled) return;
        const local = loadSavedGraph();
        if (local && local.nodes && local.nodes.length) {
          const restored = restoreSaved(local);
          const base = captureBaseline(restored.nodes);
          baselineRef.current = base;
          setBaseline(base);
          setNodes(stampDiffs(restored.nodes, base));
          setEdges(restored.edges);
        }
        setLoadingLive(false);
      });
    return () => {
      cancelled = true;
    };
  }, [applyLive, refreshSnapshots, setNodes, setEdges]);

  useEffect(() => {
    if (!fitOnce || !nodes.length) return;
    const kind = fitOnce;
    setFitOnce(null);
    const cluster = kind === "copy" ? copyDeskNodes(nodes) : nodes;
    const id = requestAnimationFrame(() => {
      fitView({
        nodes: cluster.length ? cluster : nodes,
        padding: kind === "copy" ? 0.28 : 0.14,
        duration: 400,
        minZoom: 0.18,
        maxZoom: kind === "copy" ? 1.15 : 1.4,
      });
    });
    return () => cancelAnimationFrame(id);
  }, [fitOnce, nodes, fitView]);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await fetch("/api/home");
        if (!r.ok) throw new Error("home " + r.status);
        const d = await r.json();
        if (!alive) return;
        setHome(d);
        setErr("");
        setNodes((cur) => {
          try {
            return stampDiffs(
              annotateWires(
                mergeHome(cur, edgesRef.current, d, asHunterList(catalogRef.current)),
                edgesRef.current
              ),
              baselineRef.current
            );
          } catch (e) {
            console.error("wire mergeHome", e);
            return cur;
          }
        });
        setEdges((cur) =>
          cur.map((e) => {
            const bot = (d.bots || []).find((b) => b.id === e.source || b.id === e.target);
            const hot = !!(bot && bot.running && bot.n_pos > 0);
            if (e.sourceHandle === "funds" || e.id.startsWith("e-funds-")) {
              return { ...e, animated: hot };
            }
            return e;
          })
        );
      } catch (e) {
        if (alive) setErr(String(e.message || e));
      }
    };
    tick();
    const id = setInterval(tick, 6000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [setNodes, setEdges]);

  const ping = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2800);
  };

  useEffect(() => {
    const onKey = (e) => {
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA")) return;
      const z = e.key === "z" || e.key === "Z";
      const y = e.key === "y" || e.key === "Y";
      if ((e.ctrlKey || e.metaKey) && z && e.shiftKey) {
        e.preventDefault();
        redo();
      } else if ((e.ctrlKey || e.metaKey) && z) {
        e.preventDefault();
        undo();
      } else if ((e.ctrlKey || e.metaKey) && y) {
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

  const existingIds = useMemo(() => new Set(nodes.map((n) => n.id)), [nodes]);

  const postSleeper = useCallback((id, op) => {
    fetch("/api/wire/sleeper", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, op }),
    })
      .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
      .then(({ d }) => ping((d && (d.message || d.error)) || (op === "start" ? "V2 started" : "V2 asleep")))
      .catch(() => ping("sleeper " + op + " failed"));
  }, []);

  const pickTool = useCallback(
    (tool) => {
      setMenu(null);
      setToolPick(tool);
      setSelected(null);
      if (tool.unique && existingIds.has(tool.toolId)) {
        const n = getNode(tool.toolId);
        if (n) {
          setCenter(n.position.x + 140, n.position.y + 80, { zoom: 0.95, duration: 280 });
          ping(tool.label + " already on canvas");
        }
      }
    },
    [existingIds, getNode, setCenter]
  );

  const addTool = useCallback(
    (tool, flowPos) => {
      setMenu(null);
      setToolPick(null);
      if (tool.unique && existingIds.has(tool.toolId)) {
        const n = getNode(tool.toolId);
        if (n) {
          setCenter(n.position.x + 140, n.position.y + 80, { zoom: 0.95, duration: 280 });
          ping(tool.label + " already on canvas");
        }
        return;
      }
      const pos = flowPos || screenToFlowPosition({ x: window.innerWidth * 0.42, y: window.innerHeight * 0.38 });
      remember("add " + tool.label);
      const node = makeNode(tool, pos);
      const v2bot = tool.type === "bot" && String(tool.toolId || "").startsWith("hummingbot-v2-");
      const spawned = [];
      if (v2bot) {
        const slots = [["wallet-", 0], ["ctrl-", 90]];
        if (!tool.data || tool.data.hasBias !== false) slots.push(["bias-", 180]);
        slots.forEach(([pre, dy]) => {
          const sid = pre + tool.toolId;
          if (existingIds.has(sid)) return;
          const t2 = toolById(sid);
          if (!t2) return;
          spawned.push(makeNode(t2, { x: pos.x - 170, y: pos.y + dy }));
        });
      }
      setNodes((cur) => {
        let next = mergeHome(
          [...cur, node, ...spawned],
          edges,
          homeRef.current || { bots: [], positions: [] },
          asHunterList(catalogRef.current)
        );
        if (boardRef.current === "home") next = packHomeLayout(next, topScaleRef.current);
        const have = [...cur, node, ...spawned];
        const extra = [node, ...spawned].flatMap((n) => autoEdgesFor(n, have));
        setEdges((es) => {
          const merged = [...es, ...extra.filter((e) => !es.some((x) => x.id === e.id))];
          persistGraph(next, merged);
          return merged;
        });
        return next;
      });
      if (v2bot) {
        postSleeper(tool.toolId, "start");
      } else {
        ping("added " + tool.label);
      }
    },
    [edges, existingIds, getNode, persistGraph, postSleeper, remember, screenToFlowPosition, setCenter, setEdges, setNodes]
  );

  const removeNode = useCallback(
    (id) => {
      setMenu(null);
      remember("remove");
      const v2 = String(id || "").startsWith("hummingbot-v2-");
      const drop = new Set([id]);
      if (v2) {
        drop.add("wallet-" + id);
        drop.add("ctrl-" + id);
        drop.add("bias-" + id);
        postSleeper(id, "stop");
      }
      setNodes((ns) => {
        const next = ns.filter((n) => !drop.has(n.id));
        setEdges((es) => {
          const ne = es.filter((e) => !drop.has(e.source) && !drop.has(e.target));
          persistGraph(next, ne);
          return ne;
        });
        return next;
      });
      setSelected((s) => (s && drop.has(s.id) ? null : s));
      setToolPick((t) => (t && drop.has(t.toolId) ? null : t));
      if (!v2) ping("removed from canvas");
    },
    [persistGraph, postSleeper, remember, setEdges, setNodes]
  );

  const deleteEdge = useCallback(
    (id) => {
      setMenu(null);
      remember("unwire");
      setEdges((es) => {
        const ne = es.filter((e) => e.id !== id);
        setNodes((ns) => {
          persistGraph(ns, ne);
          return ns;
        });
        return ne;
      });
    },
    [persistGraph, remember, setEdges, setNodes]
  );

  const onConnect = useCallback(
    (c) => {
      remember("wire");
      const e = makeEdge(
        `e-${c.source}-${c.sourceHandle || "x"}-${c.target}-${c.targetHandle || "y"}-${Date.now().toString(36)}`,
        c.source,
        c.sourceHandle,
        c.target,
        c.targetHandle
      );
      setEdges((es) => {
        let base = es;
        if (c.targetHandle === "coin") {
          base = base.filter((x) => !(x.target === c.target && x.targetHandle === "coin"));
        }
        const next = addEdge(e, base);
        setNodes((ns) => {
          persistGraph(ns, next);
          return annotateWires(ns, next);
        });
        return next;
      });
    },
    [persistGraph, remember, setEdges, setNodes]
  );

  const onDrop = useCallback(
    (ev) => {
      ev.preventDefault();
      const id = ev.dataTransfer.getData("application/x-wire-tool");
      const tool = toolById(id);
      if (!tool) return;
      addTool(tool, screenToFlowPosition({ x: ev.clientX, y: ev.clientY }));
    },
    [addTool, screenToFlowPosition]
  );

  const openMenu = (ev, extra) => {
    ev.preventDefault();
    const { x, y } = clampMenu(ev.clientX, ev.clientY);
    setMenu({
      x,
      y,
      flow: screenToFlowPosition({ x: ev.clientX, y: ev.clientY }),
      ...extra,
    });
  };

  useEffect(() => {
    const onHdr = (ev) => {
      const d = ev.detail || {};
      const x = Number(d.x) || 0;
      const y = Number(d.y) || 0;
      const pos = clampMenu(x, y);
      setMenu({
        kind: "wallets-header",
        x: pos.x,
        y: pos.y,
        flow: screenToFlowPosition({ x, y }),
      });
    };
    window.addEventListener("wire-wallets-header", onHdr);
    return () => window.removeEventListener("wire-wallets-header", onHdr);
  }, [screenToFlowPosition]);

  const openAddWallet = (flow) => {
    setMenu(null);
    setWalletErr("");
    setWalletForm({
      mode: "add",
      custom: true,
      flow: flow || screenToFlowPosition({ x: window.innerWidth * 0.45, y: window.innerHeight * 0.4 }),
      name: "Subaccount",
      address: "",
      use_vault: true,
      bind_id: "",
    });
  };

  const openConfigureWallet = (node) => {
    setMenu(null);
    setWalletErr("");
    const custom = !!(node.data.custom || String(node.id || "").startsWith("wallet-extra-") || !node.data.botId);
    setWalletForm({
      mode: "edit",
      node,
      custom,
      botId: node.data.botId || null,
      name: node.data.name || "Wallet",
      address: node.data.address || "",
      use_vault: node.data.use_vault !== false,
    });
  };

  const deleteWallet = async (node) => {
    setMenu(null);
    const custom = !!(node.data.custom || String(node.id || "").startsWith("wallet-extra-") || !node.data.botId);
    if (custom) {
      try {
        const r = await fetch("/api/wire/wallets", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: node.id }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.ok === false) throw new Error(d.error || "delete failed");
      } catch (e) {
        ping("Delete failed: " + e.message);
        return;
      }
    }
    removeNode(node.id);
    ping(custom ? "wallet deleted" : "wallet removed from canvas");
  };

  const saveWallet = async (form) => {
    setWalletBusy(true);
    setWalletErr("");
    try {
      if ((form.botId && !form.custom) || form.bind_id) {
        if (!/^0x[a-fA-F0-9]{40}$/.test(form.address || "")) {
          throw new Error("address must be 0x + 40 hex chars");
        }
        const bind = form.bind_id || form.botId;
        const r = await fetch("/api/wire/wallets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: form.name,
            address: form.address,
            use_vault: form.use_vault !== false,
            restart: !!form.restart,
            bind_id: bind,
          }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.ok === false) throw new Error(d.error || "subaccount save failed");
        const w = d.wallet || {
          id: "wallet-" + bind,
          botId: bind,
          name: form.name,
          address: form.address,
        };
        setNodes((ns) => {
          const nid = (form.node && form.node.id) || w.id;
          const has = ns.some((n) => n.id === nid || n.id === w.id);
          let next;
          if (has) {
            next = ns.map((n) =>
              n.id === nid || n.id === w.id
                ? {
                    ...n,
                    data: {
                      ...n.data,
                      ...w,
                      address: form.address,
                      use_vault: form.use_vault !== false,
                      botId: bind,
                      custom: false,
                    },
                  }
                : n
            );
          } else {
            remember("add wallet");
            const pos = form.flow || { x: 260, y: 200 };
            next = [
              ...ns,
              {
                id: w.id,
                type: "wallet",
                position: pos,
                data: {
                  ...w,
                  icon: "⬡",
                  toolId: w.id,
                  custom: false,
                },
              },
            ];
          }
          persistGraph(next, edgesRef.current);
          return next;
        });
        ping(d.message || "subaccount saved");
        window.dispatchEvent(new Event("wire-catalog"));
      } else {
        const r = await fetch("/api/wire/wallets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            id: form.mode === "edit" && form.node ? form.node.id : undefined,
            name: form.name,
            address: form.address || "",
            use_vault: form.use_vault !== false,
          }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.ok === false) throw new Error(d.error || "wallet save failed");
        const w = d.wallet || {};
        if (form.mode === "add") {
          remember("add wallet");
          const pos = form.flow || { x: 1000, y: 200 };
          const node = {
            id: w.id,
            type: "wallet",
            position: pos,
            data: {
              name: w.name,
              address: w.address || "",
              custom: true,
              use_vault: w.use_vault !== false,
              color: w.color || "#f0b90b",
              icon: "⬡",
              toolId: w.id,
            },
          };
          setNodes((cur) => {
            const next = [...cur, node];
            persistGraph(next, edgesRef.current);
            return next;
          });
          ping("added " + (w.name || "wallet"));
          window.dispatchEvent(new Event("wire-catalog"));
        } else {
          setNodes((ns) => {
            const next = ns.map((n) =>
              n.id === (form.node && form.node.id)
                ? { ...n, data: { ...n.data, name: w.name, address: w.address || "", use_vault: w.use_vault !== false, custom: true } }
                : n
            );
            persistGraph(next, edgesRef.current);
            return next;
          });
          ping(d.message || "wallet saved");
          window.dispatchEvent(new Event("wire-catalog"));
        }
      }
      setWalletForm(null);
    } catch (e) {
      setWalletErr(e.message || String(e));
    }
    setWalletBusy(false);
  };

  const resetLayout = () => {
    remember("reset layout");
    setNodes((cur) => {
      const next = cur.map((n) => ({
        ...n,
        position: LAYOUT[n.id] || n.position,
      }));
      persistLayout(next);
      return next;
    });
    requestAnimationFrame(() => fitView({ padding: 0.16, duration: 400 }));
  };

  const restoreDefault = () => {
    remember("before default");
    clearSavedGraph();
    clearPos();
    fetch("/api/wire/layout/clear", { method: "POST" }).catch(() => {});
    const g = buildGraph();
    const base = captureBaseline(g.nodes);
    baselineRef.current = base;
    setBaseline(base);
    setNodes(g.nodes);
    setEdges(g.edges);
    persistGraph(g.nodes, g.edges);
    requestAnimationFrame(() => fitView({ padding: 0.16, duration: 400 }));
    ping("default graph restored");
  };

  const rebuildLive = async () => {
    remember("before reload live");
    try {
      const r = await fetch("/api/wire/rebuild_live", { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.ok === false) throw new Error(d.error || "rebuild failed");
      applyLive(d, d.message || "canvas rebuilt from live");
      refreshSnapshots();
      setFitOnce("copy");
    } catch (e) {
      ping("Rebuild failed: " + e.message);
    }
  };

  const canvasBots = useMemo(
    () => nodes.filter((n) => n.type === "bot").map((n) => n.id),
    [nodes]
  );

  const loadDock = useCallback(async () => {
    try {
      const r = await fetch("/api/wire/containers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bots: canvasBots }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.ok === false) throw new Error(d.error || "containers failed");
      setDock(d);
    } catch {
      /* keep last list */
    }
  }, [canvasBots]);

  useEffect(() => {
    loadDock();
    if (!dockOpen) return undefined;
    const id = setInterval(loadDock, 8000);
    return () => clearInterval(id);
  }, [dockOpen, loadDock]);

  const stopContainer = async (id, confirm = false) => {
    const row = (dock && dock.containers || []).find((c) => c.id === id);
    if (row && row.n_pos > 0 && !confirm) {
      const ok = window.confirm(
        (row.name || id) +
          " has " +
          row.n_pos +
          " open Hyperliquid position(s).\n\nStop does NOT close them — they stay on the exchange unmanaged.\nStop this container anyway?"
      );
      if (!ok) return;
      confirm = true;
    }
    setDockBusy(true);
    try {
      const r = await fetch("/api/wire/containers/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, bots: canvasBots, confirm }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.status === 409 && d.need_confirm) {
        const ok = window.confirm((d.warnings || []).map((w) => w.reason).join("\n") + "\n\nStop anyway?");
        if (ok) {
          setDockBusy(false);
          return stopContainer(id, true);
        }
        setDockBusy(false);
        return;
      }
      if (!r.ok || d.ok === false) throw new Error(d.error || "stop blocked");
      setDock(d);
      ping(d.message || "stopped");
    } catch (e) {
      ping("Stop failed: " + e.message);
    }
    setDockBusy(false);
  };

  const stopAllSpare = async () => {
    const spare = ((dock && dock.containers) || []).filter((c) => c.can_stop);
    if (!spare.length) return;
    const hot = spare.filter((c) => c.n_pos > 0);
    let confirm = false;
    if (hot.length) {
      const ok = window.confirm(
        hot.map((c) => c.name + " · " + c.n_pos + " open HL position(s)").join("\n") +
          "\n\nStop does NOT close them. Stop all unused containers anyway?"
      );
      if (!ok) return;
      confirm = true;
    } else if (!window.confirm("Stop " + spare.length + " container(s) not on this canvas? Positions stay on Hyperliquid.")) {
      return;
    }
    setDockBusy(true);
    try {
      const r = await fetch("/api/wire/containers/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: spare.map((c) => c.id), bots: canvasBots, confirm }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.status === 409 && d.need_confirm) {
        const ok = window.confirm((d.warnings || []).map((w) => w.reason).join("\n") + "\n\nStop anyway?");
        if (ok) {
          const r2 = await fetch("/api/wire/containers/stop", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ids: spare.map((c) => c.id), bots: canvasBots, confirm: true }),
          });
          const d2 = await r2.json().catch(() => ({}));
          if (!r2.ok || d2.ok === false) throw new Error(d2.error || "stop blocked");
          setDock(d2);
          ping(d2.message || "stopped");
          setDockBusy(false);
          return;
        }
        setDockBusy(false);
        return;
      }
      if (!r.ok || d.ok === false) throw new Error(d.error || "stop blocked");
      setDock(d);
      ping(d.message || "stopped");
    } catch (e) {
      ping("Stop failed: " + e.message);
    }
    setDockBusy(false);
  };

  const compilePayload = (workaround) => {
    const body = slimGraph(nodes, edges);
    const controllers = {};
    for (const n of nodes) {
      if (n.type === "ctrl" && n.data && n.data.botId) controllers[n.data.botId] = n.data.values || {};
    }
    return {
      ...body,
      controllers,
      positions: (homeRef.current && homeRef.current.positions) || [],
      workaround: !!workaround,
      board: boardRef.current || "desk",
    };
  };

  const compileCheck = async (workaround) => {
    if (boardRef.current !== "desk" && boardRef.current !== "home") return;
    setCompileBusy(true);
    try {
      const r = await fetch("/api/wire/compile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(compilePayload(workaround)),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.ok === false) throw new Error(d.error || "compile failed");
      setCompile(d);
      ping(d.message || "compiled");
    } catch (e) {
      ping("Compile failed: " + e.message);
    }
    setCompileBusy(false);
  };

  const compileWorkaround = async () => {
    if (!window.confirm(
      "Workaround: park knobs that would close a live clip (keep their live SL/TP/trail/hold).\n\nEverything else (wiring, other knobs, Desk BIAS) still compiles. Does NOT close or open any Hyperliquid position."
    )) return;
    await compileCheck(true);
  };

  const integrateProd = async () => {
    if (boardRef.current !== "desk" && boardRef.current !== "home") return;
    if (!compile || !compile.can_integrate) return;
    setCompileBusy(true);
    try {
      const r = await fetch("/api/wire/integrate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...compilePayload(!!compile.workaround_used),
          compile_id: compile.compile_id,
        }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.ok === false) throw new Error(d.error || "integrate blocked");
      ping(d.message || "integrated");
      setCompile(null);
      const live = await fetch("/api/wire/live").then((x) => x.json());
      if (live && live.ok) applyLive(live, "production matches this canvas");
    } catch (e) {
      ping("Integrate failed: " + e.message);
    }
    setCompileBusy(false);
  };

  const saveSnapshot = async () => {
    try {
      const body = slimGraph(nodes, edges);
      const controllers = {};
      for (const n of nodes) {
        if (n.type === "ctrl" && n.data && n.data.botId) controllers[n.data.botId] = n.data.values || {};
      }
      const r = await fetch("/api/wire/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, controllers, label: "wire save" }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.ok === false) throw new Error(d.error || "save failed");
      const base = d.baseline || captureBaseline(nodes);
      baselineRef.current = base;
      setBaseline(base);
      setNodes((ns) => stampDiffs(ns, base));
      refreshSnapshots();
      ping(d.message || "snapshot saved");
    } catch (e) {
      ping("Save failed: " + e.message);
    }
  };

  const downloadXml = useCallback(() => {
    const xml = graphToXml(nodes, edges, homeRef.current);
    const blob = new Blob([xml], { type: "application/xml" });
    const a = document.createElement("a");
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    a.href = URL.createObjectURL(blob);
    a.download = "matrix-wire-" + stamp + ".xml";
    a.click();
    URL.revokeObjectURL(a.href);
    ping("XML downloaded");
  }, [nodes, edges]);

  const saveXml = useCallback(async () => {
    const xml = graphToXml(nodes, edges, homeRef.current);
    try {
      const r = await fetch("/api/wire/xml", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ xml }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.ok === false) throw new Error(d.error || "save failed");
      ping("saved conf/wire_graph.xml");
      setNodes((ns) =>
        ns.map((n) =>
          n.type === "output" && n.data.kind === "xml"
            ? { ...n, data: { ...n.data, xmlStatus: "wrote conf/wire_graph.xml" } }
            : n
        )
      );
    } catch (e) {
      ping("XML save failed: " + e.message);
    }
  }, [nodes, edges, setNodes]);

  useEffect(() => {
    const fn = (ev) => {
      if (ev.detail === "save") saveXml();
      else downloadXml();
    };
    window.addEventListener("wire-xml", fn);
    return () => window.removeEventListener("wire-xml", fn);
  }, [downloadXml, saveXml]);

  useEffect(() => {
    const onSet = (ev) => {
      const d = ev.detail || {};
      if (d.kind === "pct" && d.nodeId) {
        remember(d.kind === "moon" ? "moon" : "stop loss");
        setNodes((ns) => {
          const next = ns.map((n) =>
            n.id === d.nodeId ? { ...n, data: { ...n.data, pct: d.pct } } : n
          );
          const stamped = stampDiffs(annotateWires(next, edgesRef.current), baselineRef.current);
          setEdges((es) => {
            persistGraph(stamped, es);
            return es;
          });
          return stamped;
        });
        ping((d.kind === "moon" ? "Moon" : "SL") + " set to " + d.pct + "%");
        return;
      }
      if (d.kind === "bias") {
        remember("bias");
        const mode = d.mode;
        const coin = d.coin === "ALL" ? null : d.coin;
        setNodes((ns) =>
          stampDiffs(
            ns.map((n) => {
              if (n.type !== "bias") return n;
              if (d.botId && n.data.botId !== d.botId) return n;
              if (!d.botId && n.data.botId) return n;
              const bias = { ...(n.data.bias || {}) };
              if (coin) bias[coin] = mode;
              else Object.keys(bias).forEach((k) => { bias[k] = mode; });
              return { ...n, data: { ...n.data, bias } };
            }),
            baselineRef.current
          )
        );
        const bot = d.botId;
        const coins = coin ? [coin] : [];
        if (bot && coins.length) {
          coins.forEach((c) => {
            fetch("/api/wire/bias", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ id: bot, coin: c, bias: mode }),
            }).then((r) => r.json()).then((x) => ping(x.message || (c + " " + mode))).catch((e) => ping(String(e)));
          });
        } else {
          ping("BIAS " + mode + " — wire this node into a hunter to apply");
        }
        return;
      }
      if (d.kind === "desk_bias") {
        const mode = String(d.mode || "").toUpperCase();
        if (mode !== "LONG" && mode !== "FLAT" && mode !== "SHORT") return;
        if (!window.confirm(
          "Set desk BIAS to " + mode + "?\n\nDoes NOT close any Hyperliquid position. Hunters keep holding until their own SL/TP/trail. After they go flat they follow " + mode + "."
        )) return;
        remember("desk bias");
        fetch("/api/home/desk_bias", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ bias: mode }),
        })
          .then((r) => r.json())
          .then((x) => {
            if (x.closes) throw new Error("refusing result that reported a close");
            ping(x.message || ("Desk BIAS " + mode));
          })
          .catch((e) => ping("Desk BIAS failed: " + e.message));
        return;
      }
      if (d.kind === "health" && d.nodeId) {
        remember("health max");
        setNodes((ns) => {
          const next = ns.map((n) => {
            if (n.id !== d.nodeId) return n;
            return { ...n, data: { ...n.data, max: { ...(n.data.max || {}), [d.key]: d.value } } };
          });
          const stamped = stampDiffs(annotateWires(next, edgesRef.current), baselineRef.current);
          setEdges((es) => {
            persistGraph(stamped, es);
            return es;
          });
          return stamped;
        });
        ping((d.key || "max") + " " + d.value);
        return;
      }
      if (d.kind === "risk_wallet" && d.id) {
        remember("risk wallet");
        setNodes((ns) => {
          const next = ns.map((n) => {
            if (n.id !== d.id) return n;
            const patch = { ...n.data };
            if ("address" in d) patch.address = d.address;
            if ("skim_pct" in d) patch.skim_pct = d.skim_pct;
            if ("skim_every_h" in d) patch.skim_every_h = d.skim_every_h;
            if ("skim_min_usd" in d) patch.skim_min_usd = d.skim_min_usd;
            if ("armed" in d) patch.armed = d.armed;
            if ("skim_mode" in d) patch.skim_mode = d.skim_mode;
            return { ...n, data: patch };
          });
          persistGraph(next, edgesRef.current);
          return next;
        });
        ping("RISK wallet updated · compile to map · no HL send");
        return;
      }
      if (d.kind === "ctrl" && d.botId) {
        remember("controller");
        setNodes((ns) =>
          stampDiffs(
            ns.map((n) => {
              if (n.type !== "ctrl" || n.data.botId !== d.botId) return n;
              return { ...n, data: { ...n.data, values: { ...(n.data.values || {}), [d.key]: d.value } } };
            }),
            baselineRef.current
          )
        );
        fetch("/api/wire/controller", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: d.botId, values: { [d.key]: d.value } }),
        })
          .then((r) => r.json())
          .then((x) => ping(x.message || (d.key + " saved")))
          .catch((e) => ping(String(e)));
        return;
      }
      if (d.kind === "xlate" && d.nodeId) {
        remember("translator");
        const cmd = String(d.cmd || "").toUpperCase();
        setNodes((ns) => {
          const next = ns.map((n) => {
            if (n.id !== d.nodeId) return n;
            return { ...n, data: { ...n.data, cmd } };
          });
          const stamped = stampDiffs(annotateWires(next, edgesRef.current), baselineRef.current);
          setEdges((es) => {
            persistGraph(stamped, es);
            return es;
          });
          const xnode = nodesRef.current.find((n) => n.id === d.nodeId);
          fetch("/api/condor/xlate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              id: d.nodeId,
              cmd,
              xlate_kind: xnode && xnode.data && xnode.data.kind,
            }),
          })
            .then((r) => r.json())
            .then((x) => ping(x.message || cmd))
            .catch((e) => ping(String(e)));
          return stamped;
        });
        return;
      }
      if (d.kind === "agent" && d.nodeId) {
        const botKind = d.botKind === "grok" ? "grok" : d.botKind === "email" ? "email" : "telegram";
        remember(botKind === "grok" ? "grok bot" : botKind === "email" ? "email" : "telegram bot");
        const patch = { ...d };
        delete patch.kind;
        delete patch.nodeId;
        delete patch.botKind;
        setNodes((ns) => {
          const next = ns.map((n) => {
            if (n.id !== d.nodeId) return n;
            const data = { ...n.data, ...patch, kind: botKind };
            if (patch.token) { data.has_token = true; delete data.token; }
            if (patch.api_key) { data.has_key = true; delete data.api_key; }
            return { ...n, data };
          });
          const stamped = stampDiffs(annotateWires(next, edgesRef.current), baselineRef.current);
          setEdges((es) => {
            persistGraph(stamped, es);
            return es;
          });
          if (boardRef.current === "desk" && botKind !== "email") {
            fetch("/api/condor/setup", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ id: d.nodeId, kind: botKind, ...patch }),
            })
              .then((r) => r.json())
              .then((x) => ping(x.message || "Condor bot saved"))
              .catch((e) => ping(String(e)));
          } else {
            ping("saved on this canvas");
          }
          return stamped;
        });
      }
    };
    window.addEventListener("wire-set", onSet);
    return () => window.removeEventListener("wire-set", onSet);
  }, [persistGraph, remember, setNodes, setEdges]);

  const paintedEdges = useMemo(() => {
    const dirtyNodes = new Set();
    const dirtyBots = new Set();
    const closeBots = new Set();
    for (const n of nodes) {
      if (n.type === "ctrl") {
        if (n.data.nDirty) {
          dirtyNodes.add(n.id);
          if (n.data.botId) dirtyBots.add(n.data.botId);
        }
        const hits = n.data.closes || closeHits(n.data.botId, n.data.values || {}, n.data.positions || []);
        if (hits.some((h) => h.kind === "close")) {
          if (n.data.botId) closeBots.add(n.data.botId);
        }
      }
      if (n.type === "pct" && n.data.dirty) dirtyNodes.add(n.id);
      if (n.type === "bias" && n.data.nDirty) {
        dirtyNodes.add(n.id);
        if (n.data.botId) dirtyBots.add(n.data.botId);
      }
    }
    for (const e of edges) {
      if (dirtyNodes.has(e.source) && String(e.target || "").startsWith("hummingbot")) {
        dirtyBots.add(e.target);
      }
    }
    return markEdges(edges, { liveKeys, dirtyNodes, dirtyBots, closeBots });
  }, [nodes, edges, liveKeys]);

  const totals = (home && home.totals) || {};
  const rate = (home && home.rate) || {};
  const cap = Number(rate.cap || 1200);
  const used = Number(rate.used || 0);
  const left = rate.left != null ? Number(rate.left) : Math.max(0, cap - used);
  const rpct = Math.max(0, Math.min(100, Number(rate.pct != null ? rate.pct : cap ? (used / cap) * 100 : 0)));
  const rhot = rpct >= 90 || (rate.last_429_s != null && rate.last_429_s < 45);
  const rwarn = !rhot && rpct >= 70;
  const nOk = totals.n_ok != null ? totals.n_ok : totals.n_live || 0;
  const selBot = selected && selected.type === "bot" ? selected.data : null;
  const changes = listChanges(nodes);
  const closeNotes = [];
  for (const n of nodes) {
    if (n.type === "ctrl" && n.data.closes && n.data.closes.length) {
      for (const h of n.data.closes) closeNotes.push({ bot: n.data.name, ...h });
    }
  }
  const extraWires = paintedEdges.filter((e) => (e.className || "").includes("wire-extra")).length;

  const navHunters = useMemo(() => {
    const onDesk = new Set((nodes || []).filter((n) => n.type === "bot").map((n) => n.id));
    const fromHome = (home && home.bots) || [];
    const cat = (catalogRef.current && catalogRef.current.hunters) || HUNTERS;
    const pick = (id) => {
      const b = fromHome.find((x) => x.id === id) || {};
      const h = cat.find((x) => x.id === id) || {};
      const row = {
        id,
        name: b.name || h.name || id,
        href: b.href || h.href || "/confiig",
        icon: b.icon || h.icon || "",
        running: !!b.running,
      };
      if (id === "hummingbot-hl" || h.kind === "copy" || b.kind === "copy") {
        row.submenu = [
          { name: "Leaders", href: "/traders" },
          { name: "Audit", href: "/audit" },
          { name: "Trade History", href: "/trades" },
        ];
      }
      return row;
    };
    if (onDesk.size) return [...onDesk].map(pick);
    if (fromHome.length) return fromHome.map((b) => pick(b.id));
    return cat.map((h) => pick(h.id));
  }, [nodes, home]);

  let inspect = (
    <>
      <h3>Canvas</h3>
      <p>
        {loadingLive
          ? "Loading live setup…"
          : "Gold wires = changed vs the live setup. Rose wires = a live position would close on that route. Drag only saves x/y."}
      </p>
      {extraWires ? <p className="health">{extraWires} route(s) not in the live reconstruct.</p> : null}
      {closeNotes.length ? (
        <dl>
          {closeNotes.map((h, i) => (
            <div key={h.bot + h.reason + i}>
              <dt style={{ color: "#ff4d6d" }}>{h.bot}</dt>
              <dd className="closes">{h.reason}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {changes.length ? (
        <dl>
          {changes.map((c) => (
            <div key={c.id}>
              <dt style={{ color: c.color }}>{c.bot}</dt>
              <dd className="changed">
                {c.label} {fmtDiff(c.from, c.kind)} → {fmtDiff(c.to, c.kind)}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="health">No controller edits since live load.</p>
      )}
      {snapshots.length ? (
        <>
          <p className="health">Saved snapshots</p>
          <dl>
            {snapshots.map((s) => (
              <div key={s.id}>
                <dt>#{s.id}</dt>
                <dd>{(s.label || "save") + " · " + (s.created_at || "")}</dd>
              </div>
            ))}
          </dl>
        </>
      ) : null}
    </>
  );
  if (selected && selected.type === "bot" && selBot) {
    inspect = (
      <>
        <h3 style={{ color: selBot.color }}>{selBot.icon} {selBot.name}</h3>
        <p className="health">{selBot.health_reason || (selBot.ok ? "ok" : "issue")}</p>
        <dl>
          <div><dt>Equity</dt><dd className="cyan">{money(selBot.equity_usd)}</dd></div>
          <div><dt>Leftover</dt><dd>{money(selBot.free_usd)}</dd></div>
          <div><dt>uPnL</dt><dd className={clsPnl(selBot.upnl_usd)}>{money(selBot.upnl_usd)}</dd></div>
        </dl>
        <a className="wn-link" href={selBot.href}>Open {selBot.name} →</a>
      </>
    );
  } else if (selected && selected.type === "output") {
    const kind = selected.data.kind;
    inspect = (
      <>
        <h3>{kind === "xml" ? "XML file" : kind === "stream" ? "Stream" : "Live stream"}</h3>
        <p>
          {kind === "xml"
            ? "Download or write conf/wire_graph.xml."
            : kind === "stream"
              ? "Wire Telegram OUT (or Grok OUT, Condor REGIME, hunter LOGS) into IN. Shows the live feed."
              : "Live tail of wired hunters."}
        </p>
      </>
    );
  } else if (selected && selected.type === "trader") {
    const d = selected.data || {};
    inspect = (
      <>
        <h3 style={{ color: d.color }}>{d.num ? `T#${d.num} ` : ""}{d.title || "Leader"}</h3>
        <p>
          {d.alumni
            ? "Used on Copy before, not on the live yml roster. Wiring does not add them back."
            : "Wire LEAD into Copy T1, T2 or T3, then Integrate. Copy follows only wired T#s. Open mirrors stay."}
        </p>
        <p className="health">{d.why}</p>
        <dl>
          <div><dt>Address</dt><dd>{d.short || d.address}</dd></div>
          <div><dt>Week</dt><dd className={clsPnl(d.week_pnl)}>{d.week_pnl == null ? "—" : money(d.week_pnl)}</dd></div>
          <div><dt>Month</dt><dd className={clsPnl(d.month_pnl)}>{d.month_pnl == null ? "—" : money(d.month_pnl)}</dd></div>
          <div><dt>Our +</dt><dd className="pos">{d.our_wins == null ? "—" : money(d.our_wins)}</dd></div>
          <div><dt>Our −</dt><dd className="neg">{d.our_losses == null ? "—" : money(d.our_losses)}</dd></div>
        </dl>
      </>
    );
  } else if (selected && selected.type === "risk_wallet") {
    const d = selected.data || {};
    inspect = (
      <>
        <h3 style={{ color: "#ff4d6d" }}>{d.name || "RISK wallet"}</h3>
        <p>
          Wire hunter right RISK into this node’s left RISK. % of realized win is reserved off the trading book.
          Compile maps it. No automatic Hyperliquid send.
        </p>
        <dl>
          <div><dt>Hunter</dt><dd>{d.botId || "wire RISK out"}</dd></div>
          <div><dt>Address</dt><dd>{d.address || "—"}</dd></div>
          <div><dt>Mode</dt><dd>{d.skim_mode === "two_wins" ? "2 wins in a row → 2nd win only" : "Win%"}</dd></div>
          {d.skim_mode === "two_wins" ? null : (
            <>
          <div><dt>Win %</dt><dd>{d.skim_pct != null ? d.skim_pct + "%" : "—"}</dd></div>
          <div><dt>Every</dt><dd>{d.skim_every_h != null ? d.skim_every_h + "h" : "—"}</dd></div>
          <div><dt>Min</dt><dd>{d.skim_min_usd != null ? "$" + d.skim_min_usd : "—"}</dd></div>
            </>
          )}
          <div><dt>Armed</dt><dd>{d.armed ? "map on" : "map off"}</dd></div>
        </dl>
      </>
    );
  } else if (selected && selected.type === "wallet") {
    inspect = (
      <>
        <h3>{selected.data.name} wallet</h3>
        <p>Unique HL sub. Change it on Confiig, then restart.</p>
        <a className="wn-link" href="/confiig">Open Confiig →</a>
      </>
    );
  } else if (selected && selected.type === "pct") {
    inspect = (
      <>
        <h3>{selected.data.kind === "moon" ? "Moon %" : "Stop loss %"}</h3>
        <p>
          {selected.data.kind === "moon"
            ? "Wire into a hunter MOON port. Exit long when the coin itself is this % above the live price."
            : "Wire into a hunter SL port. Home ROE stop at −this %."}
        </p>
        <dl><div><dt>Value</dt><dd>{selected.data.pct != null ? selected.data.pct + "%" : "—"}</dd></div></dl>
      </>
    );
  } else if (selected && selected.type === "ctrl") {
    const fields = (selected.data.fields && selected.data.fields.length)
      ? selected.data.fields
      : (CONTROLLER_FIELDS[selected.data.botId] || []);
    const vals = selected.data.values || {};
    inspect = (
      <>
        <h3 style={{ color: selected.data.color }}>{selected.data.name} controller</h3>
        <p>Edit on the node. Same knobs as the hunter page. Writes live — no yaml restart.</p>
        <dl>
          {fields.map((f) => (
            <div key={f.key}>
              <dt>{f.label}</dt>
              <dd className={selected.data.dirty && f.key in selected.data.dirty ? "changed" : ""}>
                {vals[f.key] == null || vals[f.key] === "" ? "—" : fmtDiff(vals[f.key], f.kind)}
                {selected.data.dirty && f.key in selected.data.dirty
                  ? "  (was " + fmtDiff(selected.data.dirty[f.key], f.kind) + ")"
                  : ""}
              </dd>
            </div>
          ))}
        </dl>
        {(selected.data.closes || []).length ? (
          <dl>
            {selected.data.closes.map((h, i) => (
              <div key={h.reason + i}>
                <dt style={{ color: "#ff4d6d" }}>{h.kind}</dt>
                <dd className="closes">{h.reason}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        {selected.data.href && <a className="wn-link" href={selected.data.href}>Hunter page →</a>}
      </>
    );
  } else if (selected && selected.type === "gauge") {
    const g = selected.data || {};
    const isPump = selected.id === "gauge-pump" || g.kind === "pump";
    const isCondor = selected.id === "gauge-condor" || g.kind === "condor";
    inspect = isPump ? (
      <>
        <h3>Post pump gauge</h3>
        <p>Sees a spike while it is happening (pump) and after it stalls or fades (post). Recognition only — does not write BIAS or place orders. Uses hunter marks, no extra Hyperliquid REST.</p>
        <dl>
          <div><dt>Tape</dt><dd>{g.regime || "—"}</dd></div>
          <div><dt>Why</dt><dd>{g.why || "—"}</dd></div>
          <div><dt>Samples</dt><dd>{g.samples != null ? g.samples : "—"}</dd></div>
        </dl>
        <dl>
          {Object.entries(g.coins || {}).map(([c, row]) => (
            <div key={c}>
              <dt>{c}</dt>
              <dd>
                {String((row && row.state) || "quiet").toUpperCase()}
                {row && row.why ? " · " + row.why : ""}
              </dd>
            </div>
          ))}
        </dl>
        <p className="health">Wire LIVE into a Stream if you want the reading on the log. Do not wire this into hunter BIAS.</p>
      </>
    ) : isCondor ? (
      <>
        <h3>Condor</h3>
        <p>Predicts per-coin BIAS. Wire REGIME into a hunter BIAS (or that hunter’s BIAS node). Copy is skipped. Unwired hunters stay as they are.</p>
        <dl>
          <div><dt>Regime</dt><dd>{g.regime || "—"}</dd></div>
          <div><dt>Source</dt><dd>{g.source || (g.running ? "waiting" : "down")}</dd></div>
          <div><dt>Tick</dt><dd>{g.ts || "—"}</dd></div>
        </dl>
        <dl>
          {Object.entries(g.bias || {}).map(([c, m]) => (
            <div key={c}><dt>{c}</dt><dd>{m}{g.reasons && g.reasons[c] ? " · " + g.reasons[c] : ""}</dd></div>
          ))}
        </dl>
        <p className="health">{g.why || "Drop this node and wire it. No orders from Condor itself."}</p>
        {g.error ? <p className="health">{g.error}</p> : null}
        <dl>
          <div><dt>Telegram</dt><dd>{((g.setup || {}).wired || {}).telegram?.length
            ? ((g.setup || {}).wired.telegram.length + " wired")
            : (g.telegram && g.telegram.configured ? "env" : "none")}</dd></div>
          <div><dt>Grok</dt><dd>{((g.setup || {}).wired || {}).grok?.length
            ? ((g.setup || {}).wired.grok.length + " wired")
            : (g.source === "grok" ? "env" : "heuristic")}</dd></div>
        </dl>
        <p className="health">Condor toolbox: drop Telegram bot / Grok bot, paste token or xAI key, wire TG / GROK into this node.</p>
        <a className="wn-link" href="/condor">Open Condor →</a>
      </>
    ) : (
      <>
        <h3>{g.title || "Messgerät"}</h3>
        <p>Placeholder gauge. Use Condor for live BIAS.</p>
      </>
    );
  } else if (selected && selected.type === "homestat") {
    inspect = (
      <>
        <h3>{selected.data.name || "Home box"}</h3>
        <p>
          {selected.data.kind === "grok"
            ? "GROK BIAS is report-only: wire TG out into BIAS BOT IN. Never writes hunter BIAS. Desk buttons on the right apply after autoclose only."
            : "Display tile for /. Fit bands snaps it to TOP (stats), MIDDLE (hunters) or BOTTOM (health). Compile then Integrate to push to /. Does not trade."}
        </p>
      </>
    );
  } else if (selected && selected.type === "homebot") {
    inspect = (
      <>
        <h3 style={{ color: selected.data.color }}>{selected.data.name}</h3>
        <p>Home card. Drag it — that position is what you see on /. LOG can feed a Stream on this canvas.</p>
        {selected.data.href && <a className="wn-link" href={selected.data.href}>Hunter page →</a>}
      </>
    );
  } else if (selected && selected.type === "backup") {
    inspect = (
      <>
        <h3>1:1 backup twin</h3>
        <p>Placeholder for a second machine running the same desk. Probe and ALERT come later. Drop it on Health so the canvas has a slot.</p>
      </>
    );
  } else if (selected && selected.type === "health") {
    const kind = selected.data.kind || "host";
    inspect = (
      <>
        <h3>{selected.data.name || "Health"}</h3>
        <p>
          {kind === "hlip"
            ? "Live Hyperliquid REST weight for this public IP (1200/min). Does not place orders."
            : kind === "net"
              ? "Internet probe to 1.1.1.1:443. Does not call Hyperliquid, so it does not spend tokens."
              : "CPU, RAM and disk on this desk. Set max knobs. LIVE always streams; ALERT only when a max is hit."}
        </p>
        <p className="health">Wire LIVE and/or ALERT into a Stream node.</p>
      </>
    );
  } else if (selected && selected.type === "xlate") {
    const closer = selected.data && selected.data.kind === "closer";
    inspect = closer ? (
      <>
        <h3 style={{ color: "#ff4d6d" }}>Close translator</h3>
        <p>
          CLOSE only. Drop a <b>new</b> Telegram bot, paste its token, wire OUT into this TG, then E-CLOSE into each hunter E-CLOSE you want on the kill switch.
          Unwired or idle does not touch positions. CLOSE /flatten /exit from that chat flattens those hunters immediately and writes BIAS FLAT on each (Copy has no BIAS rail).
        </p>
        <dl>
          <div><dt>Last</dt><dd>{selected.data.cmd || "—"}</dd></div>
          <div><dt>Said</dt><dd>{selected.data.last_raw || "—"}</dd></div>
        </dl>
      </>
    ) : (
      <>
        <h3>Translator</h3>
        <p>Knows only LONG, SHORT, FLAT, CLOSE, OPEN100%. Wire Telegram OUT into TG, then BIAS into one hunter BIAS. CLOSE and OPEN100% hit that hunter only.</p>
        <dl>
          <div><dt>Last</dt><dd>{selected.data.cmd || "—"}</dd></div>
          <div><dt>Said</dt><dd>{selected.data.last_raw || "—"}</dd></div>
        </dl>
      </>
    );
  } else if (selected && selected.type === "desk_bias") {
    const holding = (selected.data.hunters || []).filter((h) => (h.held || []).length);
    inspect = (
      <>
        <h3 style={{ color: selected.data.color }}>Desk BIAS</h3>
        <p>
          Wire Telegram OUT into TG. LONG / FLAT / SHORT for every hunter after they autoclose.
          Does not close. CLOSE and OPEN100% are ignored.
        </p>
        <dl>
          <div><dt>Next</dt><dd>{selected.data.bias || selected.data.cmd || "unset"}</dd></div>
          <div><dt>Holding</dt><dd>{holding.length
            ? holding.map((h) => (h.name || h.id) + " " + (h.held || []).join("/")).join(" · ")
            : "none"}</dd></div>
          <div><dt>Last</dt><dd>{selected.data.last_raw || "—"}</dd></div>
        </dl>
      </>
    );
  } else if (selected && selected.type === "bias") {
    const bmap = selected.data.bias || {};
    inspect = (
      <>
        <h3 style={{ color: selected.data.color }}>{selected.data.name} BIAS</h3>
        <p>Own rail for this hunter. Live from its bias file. Buttons do not write yet.</p>
        <dl>
          {Object.entries(bmap).map(([c, m]) => (
            <div key={c}><dt>{c}</dt><dd>{m}</dd></div>
          ))}
        </dl>
      </>
    );
  } else if (selected && selected.type === "agent") {
    const k = selected.data.kind === "grok" ? "Grok" : selected.data.kind === "email" ? "Email" : "Telegram";
    inspect = (
      <>
        <h3 style={{ color: selected.data.color }}>{k}{k === "Email" ? " alerts" : " bot"}</h3>
        <p>
          {k === "Email"
            ? "Health ALERT → IN. SMTP send comes later. Saved on this canvas only — not a Condor BIAS bot."
            : k === "Grok"
              ? "Wire into Condor GROK. Secrets stay in conf/condor_setup.json, not on the canvas file."
              : "On Desk: wire OUT into Condor TG, a Translator, or Desk BIAS. On Home / Health: output and error alerts, not BIAS."}
        </p>
        <dl>
          <div><dt>Name</dt><dd>{selected.data.name || "—"}</dd></div>
          {k === "Email"
            ? <div><dt>To</dt><dd>{selected.data.to || "—"}</dd></div>
            : <div><dt>Secret</dt><dd>{selected.data.has_token || selected.data.has_key ? "saved" : "missing"}</dd></div>}
          {k === "Grok"
            ? <div><dt>Model</dt><dd>{selected.data.model || "grok-4.5"}</dd></div>
            : k === "Telegram"
              ? <div><dt>Chats</dt><dd>{selected.data.chat_ids || "—"}</dd></div>
              : null}
        </dl>
      </>
    );
  } else if (selected && selected.type === "coin") {
    inspect = (
      <>
        <h3 style={{ color: selected.data.color }}>{selected.data.coin}</h3>
        <p>Wire this market into hunters. Copy has no coin list — drop extra tickers on it from the toolbox.</p>
        <dl>
          <div><dt>Mark</dt><dd className="cyan">{money(selected.data.mark)}</dd></div>
          <div><dt>Live pos</dt><dd>{selected.data.n_pos || 0}</dd></div>
        </dl>
      </>
    );
  } else if (selected && selected.type === "funds") {
    inspect = (
      <>
        <h3>FUNDS bus</h3>
        <p>All hunter wallets. Rewire through Confiig.</p>
      </>
    );
  } else if (selected && selected.type === "logs") {
    inspect = (
      <>
        <h3>LOGS bus</h3>
        <p>Home health lines. Wire into a live output to isolate a hunter.</p>
      </>
    );
  }
  if (toolPick) inspect = <ToolSheet tool={toolPick} />;

  return (
    <div className="wire-app">
      <header>
        <div className="brand">
          FREIQUELL <span>// WIRE</span>
        </div>
        <div className="nav">
          <a href="/">Home</a>
          <a href="/wire/" className="active">Wire</a>
          <div className={`board-dd${boardOpen ? " open" : ""}`} ref={boardMenuRef}>
            <button
              type="button"
              className="board-dd-btn"
              aria-haspopup="listbox"
              aria-expanded={boardOpen}
              title={(BOARDS.find((b) => b.id === board) || BOARDS[0]).hint}
              onClick={() => { setBotsOpen(false); setSysOpen(false); setFundsOpen(false); setStratOpen(false); setBoardOpen((v) => !v); }}
            >
              Canvas · {(BOARDS.find((b) => b.id === board) || BOARDS[0]).label}
              <span className="caret" aria-hidden>{boardOpen ? "▴" : "▾"}</span>
            </button>
            {boardOpen ? (
              <div className="board-dd-menu" role="listbox">
                {BOARDS.map((b) => (
                  <button
                    key={b.id}
                    type="button"
                    role="option"
                    aria-selected={board === b.id}
                    className={board === b.id ? "on" : ""}
                    onClick={() => {
                      setBoardOpen(false);
                      switchBoard(b.id);
                    }}
                  >
                    <span className="dd-lab">{b.label}</span>
                    <span className="dd-hint">{b.hint}</span>
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          <a href="/condor">Condor</a>
          <div className={`board-dd${botsOpen ? " open" : ""}`}>
            <button
              type="button"
              className="board-dd-btn"
              onClick={() => { setBoardOpen(false); setSysOpen(false); setFundsOpen(false); setStratOpen(false); setBotsOpen((v) => !v); }}
            >
              Hummingbots ▾
            </button>
            {botsOpen ? (
              <div className="board-dd-menu">
                {navHunters.map((h) => (
                  <div key={h.id} className="nav-sub">
                    <a href={h.href}>
                      <span className={`lamp ${h.running ? "on" : "off"}`} />
                      {(h.icon ? h.icon + " " : "") + h.name}
                      {h.submenu && h.submenu.length ? <span className="sub-caret">▸</span> : null}
                    </a>
                    {h.submenu && h.submenu.length ? (
                      <div className="nav-sub-menu">
                        {h.submenu.map((s) => (
                          <a key={s.href} href={s.href}>{s.name}</a>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ))}
                {!navHunters.length ? <span className="dd-hint">no hunters on desk</span> : null}
              </div>
            ) : null}
          </div>
          <div className={`board-dd${fundsOpen ? " open" : ""}`}>
            <button
              type="button"
              className="board-dd-btn"
              onClick={() => { setBoardOpen(false); setBotsOpen(false); setSysOpen(false); setStratOpen(false); setFundsOpen((v) => !v); }}
            >
              Funds ▾
            </button>
            {fundsOpen ? (
              <div className="board-dd-menu">
                <a href="/confiig">Hyperliquid Wallets</a>
              </div>
            ) : null}
          </div>
          <div className={`board-dd${stratOpen ? " open" : ""}`}>
            <button
              type="button"
              className="board-dd-btn"
              onClick={() => { setBoardOpen(false); setBotsOpen(false); setSysOpen(false); setFundsOpen(false); setStratOpen((v) => !v); }}
            >
              Strategy ▾
            </button>
            {stratOpen ? (
              <div className="board-dd-menu">
                <div className="nav-sub">
                  <a href="/janestreet">JaneStreet<span className="sub-caret">▸</span></a>
                  <div className="nav-sub-menu">
                    <a href="/janestreet">Weekly</a>
                    <a href="/janestreet/daily">Daily</a>
                  </div>
                </div>
              </div>
            ) : null}
          </div>
          <div className={`board-dd${sysOpen ? " open" : ""}`}>
            <button
              type="button"
              className="board-dd-btn"
              onClick={() => { setBoardOpen(false); setBotsOpen(false); setFundsOpen(false); setStratOpen(false); setSysOpen((v) => !v); }}
            >
              System ▾
            </button>
            {sysOpen ? (
              <div className="board-dd-menu">
                <a href="/health">Health</a>
                <a href="/backup">Backup</a>
                <a href="/cpp-usb">Copy C++ app to USB</a>
                <a href="/version">Version</a>
                <a href="/update">Update</a>
              </div>
            ) : null}
          </div>

          <span className={`pill ${board !== "desk" ? "preview" : (changes.length ? "dirty" : "preview")}`}>
            {board === "desk"
              ? (loadingLive ? "loading live…" : changes.length ? changes.length + " vs live load" : "live setup")
              : (board === "home" ? "home layout" : "health setup")}
          </span>
        </div>
      </header>

      <div className="hud">
        <div className="kpi">
          <div className="lbl">Lamps</div>
          <div className={`val ${nOk < (totals.n_bots || 0) ? "rose" : "gold"}`}>
            {nOk} / {totals.n_bots || 0}
          </div>
        </div>
        <div className="kpi">
          <div className="lbl">Positions</div>
          <div className="val">{totals.n_pos ?? "—"}</div>
        </div>
        <div className="kpi">
          <div className="lbl">Equity</div>
          <div className="val cyan">{money(totals.equity_usd)}</div>
        </div>
        <div className="kpi">
          <div className="lbl">uPnL</div>
          <div className={`val ${clsPnl(totals.upnl_usd)}`}>{money(totals.upnl_usd)}</div>
        </div>
        <div className="kpi rate">
          <div className="lbl">HL REST · 60s</div>
          <div className={`val ${rhot ? "rose" : rwarn ? "gold" : ""}`}>
            {used} / {cap}
            <span className="left"> · {left} left</span>
          </div>
          <div className={`rbar${rhot ? " hot" : rwarn ? " warn" : ""}`}>
            <i style={{ width: rpct.toFixed(1) + "%" }} />
          </div>
          <div className="rmeta">
            refill {fmtSecs(rate.refill_in_s)}
            {rate.last_429_s != null ? ` · 429 ${fmtSecs(rate.last_429_s)} ago` : " · no 429"}
          </div>
        </div>
        <div className="hud-actions">
          <button type="button" onClick={undo} disabled={!undoN} title={undoLabel ? "Undo " + undoLabel : "Undo"}>
            Undo{undoN ? " " + undoN : ""}
          </button>
          <button type="button" onClick={redo} disabled={!redoN} title="Redo">
            Redo{redoN ? " " + redoN : ""}
          </button>
          <button type="button" onClick={() => fitView({ padding: 0.16, duration: 300 })}>Fit</button>
          {board === "desk" ? (
            <>
          <button
            type="button"
            title="Frame HL Copy and the live T#1 T#2 T#3 leader cards"
            onClick={() => {
              const cluster = copyDeskNodes(nodes);
              fitView({
                nodes: cluster.length ? cluster : nodes,
                padding: 0.28,
                duration: 400,
                minZoom: 0.18,
                maxZoom: 1.15,
              });
            }}
          >
            Copy desk
          </button>
          <button type="button" onClick={resetLayout}>Reset layout</button>
          <button type="button" onClick={rebuildLive}>Reload live</button>
          <button type="button" onClick={saveSnapshot}>Save snapshot</button>
          <button type="button" onClick={() => compileCheck(false)} disabled={compileBusy}>
            {compileBusy ? "Compile…" : "Compile check"}
          </button>
          <button
            type="button"
            onClick={integrateProd}
            disabled={compileBusy || !compile || !compile.can_integrate}
            title={!compile ? "Run Compile check first" : (compile.can_integrate ? "Apply compiled canvas to production" : "Blocked: would close a live position")}
          >
            Integrate
          </button>
          <button type="button" onClick={restoreDefault}>Default graph</button>
          <button
            type="button"
            className={dockOpen ? "on" : ""}
            onClick={() => setDockOpen((v) => !v)}
            title="Running Docker containers vs this canvas"
          >
            Dockers{dock ? ` ${dock.n_up} up` : ""}{dock && dock.n_spare ? ` · ${dock.n_spare} spare` : ""}
          </button>
            </>
          ) : (
            <>
              {board === "home" ? (
                <>
                <button
                  type="button"
                  title="Smaller TOP tiles on /"
                  onClick={() => {
                    const sc = stepTopScale(topScaleRef.current, -1);
                    topScaleRef.current = sc;
                    setTopScale(sc);
                    remember("top smaller");
                    setNodes((ns) => {
                      const next = packHomeLayout(ns, sc);
                      setEdges((es) => {
                        persistGraph(next, es);
                        return es;
                      });
                      return next;
                    });
                    ping("TOP size " + Math.round(sc * 100) + "%");
                  }}
                >
                  Top −
                </button>
                <span className="meta" title="TOP band scale on /">Top {Math.round(topScale * 100)}%</span>
                <button
                  type="button"
                  title="Taller TOP tiles on /"
                  onClick={() => {
                    const sc = stepTopScale(topScaleRef.current, 1);
                    topScaleRef.current = sc;
                    setTopScale(sc);
                    remember("top larger");
                    setNodes((ns) => {
                      const next = packHomeLayout(ns, sc);
                      setEdges((es) => {
                        persistGraph(next, es);
                        return es;
                      });
                      return next;
                    });
                    ping("TOP size " + Math.round(sc * 100) + "%");
                  }}
                >
                  Top +
                </button>
                <button
                  type="button"
                  title="Snap nodes into TOP / MIDDLE / BOTTOM bands"
                  onClick={() => {
                    remember("fit home");
                    setNodes((ns) => {
                      const next = packHomeLayout(ns, topScaleRef.current);
                      setEdges((es) => {
                        persistGraph(next, es);
                        return es;
                      });
                      return next;
                    });
                    setFitOnce("all");
                    ping("Home packed: TOP stats · MIDDLE hunters · BOTTOM health");
                  }}
                >
                  Fit bands
                </button>
                <button type="button" onClick={() => compileCheck(false)} disabled={compileBusy}>
                  {compileBusy ? "Compile…" : "Compile check"}
                </button>
                <button
                  type="button"
                  onClick={integrateProd}
                  disabled={compileBusy || !compile || !compile.can_integrate || compile.board !== "home"}
                  title={!compile ? "Run Compile check first" : (compile.can_integrate ? "Push this layout to /" : "Fix layout warnings first")}
                >
                  Integrate
                </button>
                <span className="meta">TOP stats · MIDDLE hunters (scroll) · BOTTOM health · compile before / changes</span>
                </>
              ) : (
              <span className="meta">health alerts · twin later</span>
              )}
            </>
          )}
        </div>
      </div>

      {err && <div className="banner">{err}</div>}
      {toast && <div className="toast-ok">{toast}</div>}
      {dockOpen && board === "desk" && (
        <div className="dock-panel">
          <div className="compile-head">
            <strong>DOCKER</strong>
            <span>{dock ? dock.message : "loading…"}</span>
            <button type="button" onClick={loadDock} disabled={dockBusy}>Refresh</button>
            <button
              type="button"
              onClick={stopAllSpare}
              disabled={dockBusy || !dock || !(dock.n_spare > 0)}
              title="Stop every running container that is not on this canvas"
            >
              Stop all not needed
            </button>
            <button type="button" onClick={() => setDockOpen(false)}>close</button>
          </div>
          <p className="health">
            On this canvas = needed. Not on canvas = spare. Stop does not close Hyperliquid positions.
          </p>
          <ul className="dock-list">
            {((dock && dock.containers) || []).map((c) => (
              <li key={c.id} className={"dock-row " + c.mark}>
                <span className={"lamp " + (c.running ? "up" : "down")} />
                <div className="dock-meta">
                  <strong>{c.name}</strong>
                  <em>{c.id}</em>
                </div>
                <span className={"tag " + c.mark}>
                  {c.mark === "not_needed" ? "not needed"
                    : c.mark === "on_wire" ? "on canvas"
                    : c.mark === "missing" ? "on canvas · down"
                    : c.mark === "protected" ? "protected"
                    : c.running ? c.state : (c.status || "stopped")}
                </span>
                {c.n_pos > 0 ? <span className="tag pos">{c.n_pos} pos</span> : null}
                {c.can_stop ? (
                  <button
                    type="button"
                    className="stop"
                    disabled={dockBusy}
                    onClick={() => stopContainer(c.id)}
                    title="docker stop — positions stay on Hyperliquid"
                  >
                    Deactivate
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      )}
      {compile && (board === "desk" || board === "home") && (
        <div className={"compile-panel" + (compile.can_integrate ? " ok" : " blocked")}>
          <div className="compile-head">
            <strong>
              {compile.can_integrate
                ? (compile.workaround_used ? "COMPILE OK · WORKAROUND" : "COMPILE OK")
                : "COMPILE BLOCKED"}
            </strong>
            <span>{compile.message}</span>
            <button type="button" onClick={() => setCompile(null)}>close</button>
          </div>
          {compile.bands ? (
            <div className="compile-sec">
              <h4>Home bands (live / after Integrate)</h4>
              <p>TOP · {(compile.bands.top || []).join(" · ") || "—"}</p>
              <p>MIDDLE · {(compile.bands.mid || []).join(" · ") || "—"}</p>
              <p>BOTTOM · {(compile.bands.bot || []).join(" · ") || "—"}</p>
            </div>
          ) : null}
          <p className="compile-sum">
            {compile.n_pos || 0} open clip(s)
            {" · "}{compile.n_new_routes || 0} new route(s)
            {" · "}{compile.n_removed_routes || 0} removed
            {" · "}{compile.n_knobs || 0} knob change(s)
            {compile.n_blockers ? " · " + compile.n_blockers + " would close" : ""}
            {compile.n_parked ? " · " + compile.n_parked + " parked at live" : ""}
            {" · "}positions untouched
          </p>
          {(compile.blockers || []).length ? (
            <div className="compile-sec">
              <h4>Would close now — blocked</h4>
              <ul className="blockers">
                {(compile.blockers || []).map((b, i) => (
                  <li key={i}>
                    <b>{b.bot || b.bot_id}</b> {b.reason}
                    {b.workaround ? <em> · workaround: {b.workaround}</em> : null}
                  </li>
                ))}
              </ul>
              <p className="health">
                Integrate stays off until those knobs would not flatten a live clip.
                Workaround parks only those knobs at their live values and compiles the rest.
              </p>
              <button type="button" onClick={compileWorkaround} disabled={compileBusy}>
                Workaround: park close knobs, compile the rest
              </button>
            </div>
          ) : null}
          {(compile.parked || []).length ? (
            <div className="compile-sec">
              <h4>Parked at live (workaround)</h4>
              <ul>
                {(compile.parked || []).map((p, i) => (
                  <li key={i} className="parked">
                    {p.bot} {p.key}: canvas {String(p.canvas)} → live {String(p.live)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="compile-sec">
            <h4>Hunters</h4>
            {(compile.hunters || []).length ? (
              <ul className="compile-hunters">
                {(compile.hunters || []).map((h) => (
                  <li key={h.id}>
                    <b>{h.name}</b>
                    {" · "}{(h.positions || []).length
                      ? (h.positions || []).map((p) => (p.coin || "?") + " " + (p.side || "") + (p.roe_pct != null ? " " + (Number(p.roe_pct) >= 0 ? "+" : "") + Number(p.roe_pct).toFixed(1) + "%" : "")).join(", ")
                      : "flat"}
                    {h.n_knobs ? " · " + h.n_knobs + " knob(s)" : ""}
                    {h.n_block ? " · " + h.n_block + " close-risk" : ""}
                    {h.n_warn ? " · " + h.n_warn + " clip-warn" : ""}
                  </li>
                ))}
              </ul>
            ) : <p>No hunter rows in this compile.</p>}
          </div>
          <div className="compile-sec">
            <h4>Wiring vs live</h4>
            {(compile.wiring || []).length ? (
              <ul>
                {(compile.wiring || []).map((w, i) => (
                  <li key={i} className={w.kind === "income" ? "income" : w.status}>
                    {w.status === "removed" ? "− " : "+ "}
                    <span className="kind">{w.kind}</span> {w.label}
                  </li>
                ))}
              </ul>
            ) : <p>No wiring changes vs live reconstruct.</p>}
          </div>
          <div className="compile-sec">
            <h4>Controller knobs</h4>
            {(compile.knobs || []).length ? (
              <ul>
                {(compile.knobs || []).map((k, i) => (
                  <li key={i}>{k.bot} {k.key}: {String(k.from)} → {String(k.to)}</li>
                ))}
              </ul>
            ) : <p>Controller knobs match live.</p>}
          </div>
          {(compile.bias || []).length ? (
            <div className="compile-sec">
              <h4>Hunter BIAS (does not close)</h4>
              <ul>
                {(compile.bias || []).map((b, i) => (
                  <li key={i}>
                    {b.bot}{" "}
                    {(b.diffs || []).map((d) => d.coin + " " + d.from + "→" + d.to).join(" · ")}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {(compile.risk || []).length ? (
            <div className="compile-sec">
              <h4>RISK skim (no auto HL send)</h4>
              <ul>
                {(compile.risk || []).map((r, i) => (
                  <li key={i}>
                    {r.hunter} → {r.wallet}
                    {r.address ? " " + String(r.address).slice(0, 10) + "…" : ""}
                    {r.mode === "two_wins"
                      ? " · 2 wins in a row → 2nd win only"
                      : (" · " + (r.skim_pct != null ? r.skim_pct + "%" : "—")
                        + " / " + (r.every_h != null ? r.every_h + "h" : "—")
                        + " min $" + (r.min_usd != null ? r.min_usd : "—"))}
                    {r.armed ? " · armed" : ""}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {compile.desk_bias && (compile.desk_bias.canvas || compile.desk_bias.live) ? (
            <div className="compile-sec">
              <h4>Desk BIAS (does not close)</h4>
              <p>
                live {compile.desk_bias.live || "unset"}
                {compile.desk_bias.canvas ? " · canvas " + compile.desk_bias.canvas : ""}
                {" · "}{compile.desk_bias.note}
              </p>
            </div>
          ) : null}
          {(compile.warnings || []).map((w, i) => (
            <p key={i} className="health">{typeof w === "string" ? w : (w.reason || w.label)}</p>
          ))}
          {compile.can_integrate ? (
            <button type="button" onClick={integrateProd} disabled={compileBusy}>
              {compile.workaround_used ? "Integrate workaround to production" : "Integrate to production"}
            </button>
          ) : null}
        </div>
      )}

      <div className="board">
        <ReactFlow
          nodes={nodes}
          edges={paintedEdges}
          nodeTypes={nodeTypes}
          onNodesChange={persist}
          onEdgesChange={(c) => {
            onEdgesChange(c);
            const structural = c.some((x) => x.type === "remove");
            if (!structural) return;
            setTimeout(() => {
              setEdges((es) => {
                setNodes((ns) => {
                  persistGraph(ns, es);
                  return ns;
                });
                return es;
              });
            }, 0);
          }}
          onConnect={onConnect}
          isValidConnection={isValidConnection}
          onSelectionChange={(s) => {
            const n = s.nodes && s.nodes[0] ? s.nodes[0] : null;
            if (n) setToolPick(null);
            setSelected(n);
          }}
          onPaneContextMenu={(e) => openMenu(e, { kind: "pane" })}
          onNodeContextMenu={(e, node) => openMenu(e, { kind: "node", node })}
          onEdgeContextMenu={(e, edge) => openMenu(e, { kind: "edge", edge })}
          onDragOver={(e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
          }}
          onDrop={onDrop}
          nodesConnectable
          edgesReconnectable={false}
          elementsSelectable
          nodesDraggable
          panOnDrag
          zoomOnScroll
          deleteKeyCode={["Backspace", "Delete"]}
          minZoom={0.18}
          maxZoom={1.6}
          defaultViewport={{ x: 8, y: 12, zoom: 0.72 }}
          proOptions={{ hideAttribution: true }}
          defaultEdgeOptions={{ type: "smoothstep" }}
        >
          <Background id="grid" variant={BackgroundVariant.Lines} gap={24} color="rgba(0,255,106,0.045)" />
          <Background id="dots" variant={BackgroundVariant.Dots} gap={24} size={1.1} color="rgba(0,255,106,0.16)" />
          <MiniMap
            pannable
            zoomable
            position="bottom-right"
            nodeColor={(n) =>
              n.data?.color ||
              (n.type === "bias" || n.type === "desk_bias" || n.type === "xlate"
                ? (n.type === "xlate" ? (n.data?.kind === "closer" ? "#ff4d6d" : "#34d399") : "#f0b90b")
                : n.type === "agent"
                    ? (n.data?.kind === "grok" ? "#2A9FD6" : "#34d399")
                : n.type === "gauge" || n.type === "ctrl"
                  ? "#c084fc"
                  : n.type === "pct"
                    ? (n.data?.kind === "moon" ? "#ffd24a" : "#ff4d6d")
                  : n.type === "health"
                    ? n.data?.color || "#9dffc0"
                  : n.type === "trader"
                    ? n.data?.color || "#f0b90b"
                  : n.type === "coin"
                    ? n.data?.color || "#94a3b8"
                    : n.type === "risk_wallet"
                      ? "#ff4d6d"
                    : n.type === "funds" || n.type === "wallet"
                      ? "#f0b90b"
                      : n.type === "logs" || n.type === "output"
                        ? "#7ecbff"
                        : "#0d3b1f")}
            maskColor="rgba(2,8,4,0.75)"
          />
          <Controls showInteractive={false} position="bottom-left" />
        </ReactFlow>

        <Toolbox
          query={query}
          onQuery={setQuery}
          existingIds={existingIds}
          onAdd={addTool}
          onPick={pickTool}
          toolPickId={toolPick ? toolPick.toolId : ""}
          selected={selected}
          inspect={inspect}
          onRemoveSelected={() => selected && removeNode(selected.id)}
          onWalletsHeaderMenu={(e) => openMenu(e, { kind: "wallets-header" })}
          board={board}
        />
      </div>

      <ContextMenu
        menu={menu}
        existingIds={existingIds}
        onAdd={addTool}
        onRemove={removeNode}
        onDeleteEdge={deleteEdge}
        onAddWallet={openAddWallet}
        onConfigureWallet={openConfigureWallet}
        onDeleteWallet={deleteWallet}
        onClose={() => setMenu(null)}
      />
      <WalletModal
        form={walletForm}
        busy={walletBusy}
        error={walletErr}
        onClose={() => setWalletForm(null)}
        onSave={saveWallet}
      />
    </div>
  );
}

export default function App() {
  return (
    <ReactFlowProvider>
      <Board />
    </ReactFlowProvider>
  );
}
