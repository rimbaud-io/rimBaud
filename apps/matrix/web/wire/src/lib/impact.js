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

import { edgeStyle } from "./tools.js";

export function edgeKey(e) {
  return [e.source || "", e.sourceHandle || "", e.target || "", e.targetHandle || ""].join(">");
}

export function liveKeySet(edges) {
  return new Set((edges || []).map(edgeKey));
}

function num(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function roeStr(v) {
  const n = num(v);
  if (n == null) return "—";
  return (n >= 0 ? "+" : "") + n.toFixed(1) + "%";
}

/** Would this controller close or clip an open position right now? */
export function closeHits(botId, values, positions) {
  const hits = [];
  const sl = num(values && values.sl_roe_pct);
  const tp = num(values && values.tp_roe_pct);
  const scale = num(values && values.scale_roe_pct);
  const trailD = num(values && values.trail_delta);
  const lossFrac = num(values && values.max_position_loss_pct);
  const holdH = num(values && values.hold_h);
  const now = Date.now() / 1000;

  for (const p of positions || []) {
    if (p.bot_id && p.bot_id !== botId) continue;
    const coin = p.coin || "?";
    const side = String(p.side || "").toUpperCase();
    const roe = num(p.roe_pct);
    const mark = num(p.mark_px);
    const pnl = num(p.pnl_usd);
    const eqMargin = num(p.margin);

    if (sl != null && roe != null && roe <= -sl) {
      hits.push({
        coin, kind: "close", key: "sl_roe_pct",
        reason: `${coin} ${side} would SL now · ROE ${roeStr(roe)} ≤ −${sl}%`,
      });
    }
    if (tp != null && roe != null && roe >= tp) {
      hits.push({
        coin, kind: "close", key: "tp_roe_pct",
        reason: `${coin} ${side} would TP now · ROE ${roeStr(roe)} ≥ +${tp}%`,
      });
    }
    if (scale != null && roe != null && roe >= scale) {
      hits.push({
        coin, kind: "clip", key: "scale_roe_pct",
        reason: `${coin} ${side} would clip now · ROE ${roeStr(roe)} ≥ +${scale}%`,
      });
    }
    if (trailD != null && trailD > 0 && p.trail_active && num(p.trail_extreme) != null && mark != null) {
      const ext = num(p.trail_extreme);
      const d = trailD > 1 ? trailD / 100 : trailD;
      if (side === "LONG" && mark <= ext * (1 - d)) {
        hits.push({
          coin, kind: "close", key: "trail_delta",
          reason: `${coin} LONG would trail-stop now · mark ${mark} ≤ ${(ext * (1 - d)).toFixed(4)}`,
        });
      }
      if (side === "SHORT" && mark >= ext * (1 + d)) {
        hits.push({
          coin, kind: "close", key: "trail_delta",
          reason: `${coin} SHORT would trail-stop now · mark ${mark} ≥ ${(ext * (1 + d)).toFixed(4)}`,
        });
      }
    }
    if (lossFrac != null && pnl != null && eqMargin != null && eqMargin > 0) {
      const frac = lossFrac > 1 ? lossFrac / 100 : lossFrac;
      const cap = eqMargin * frac;
      if (pnl <= -cap) {
        hits.push({
          coin, kind: "close", key: "max_position_loss_pct",
          reason: `${coin} would risk-stop now · uPnL ${pnl.toFixed(2)} ≤ −${cap.toFixed(2)}`,
        });
      }
    }
    if (holdH != null && p.entry_time) {
      let started = null;
      const et = p.entry_time;
      if (typeof et === "number") started = et > 1e12 ? et / 1000 : et;
      else {
        const t = Date.parse(et);
        if (!Number.isNaN(t)) started = t / 1000;
      }
      if (started && now - started >= holdH * 3600) {
        hits.push({
          coin, kind: "close", key: "hold_h",
          reason: `${coin} would TIME-exit now · hold ${holdH}h elapsed`,
        });
      }
    }
  }
  return hits;
}

export function markEdges(edges, { liveKeys, dirtyNodes, dirtyBots, closeBots }) {
  const live = liveKeys || new Set();
  const dNodes = dirtyNodes || new Set();
  const dBots = dirtyBots || new Set();
  const cBots = closeBots || new Set();
  return (edges || []).map((e) => {
    const extra = live.size > 0 && !live.has(edgeKey(e));
    const srcBot = dBots.has(e.source) || cBots.has(e.source);
    const tgtBot = dBots.has(e.target) || cBots.has(e.target);
    const dirty = extra || dNodes.has(e.source) || dNodes.has(e.target) || srcBot || tgtBot;
    const handle = e.sourceHandle || e.targetHandle || "";
    const settingWire = ["ctrl", "sl", "moon", "bias"].includes(handle)
      || ["ctrl", "sl", "moon", "bias"].includes(e.targetHandle || "");
    const closes = settingWire && (cBots.has(e.source) || cBots.has(e.target));
    const classes = [];
    if (closes) classes.push("wire-closes");
    else if (dirty) classes.push("wire-changed");
    if (extra) classes.push("wire-extra");
    const base = edgeStyle(e.sourceHandle);
    let style = base;
    if (closes) style = { ...base, stroke: "#ff4d6d", strokeWidth: 2.5, strokeDasharray: undefined };
    else if (extra) style = { ...base, stroke: "#f0b90b", strokeWidth: 2.2 };
    else if (dirty) style = { ...base, stroke: "#f0b90b", strokeWidth: 2.1 };
    return {
      ...e,
      className: classes.join(" "),
      style,
      animated: extra || closes || e.animated,
    };
  });
}
