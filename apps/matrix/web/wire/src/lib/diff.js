// Copyright 2026 Laurence Sterne
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

import { CONTROLLER_FIELDS, HUNTERS } from "./seed.js";

const hunterName = Object.fromEntries(HUNTERS.map((h) => [h.id, h.name]));

export function sameVal(a, b) {
  if (a === b) return true;
  if (a == null && b == null) return true;
  if (a == null || b == null) return false;
  if (typeof a === "boolean" || typeof b === "boolean") return !!a === !!b;
  const na = Number(a);
  const nb = Number(b);
  if (!Number.isNaN(na) && !Number.isNaN(nb)) return Math.abs(na - nb) < 1e-9;
  return String(a) === String(b);
}

export function captureBaseline(nodes) {
  const controllers = {};
  const pct = {};
  const bias = {};
  for (const n of nodes || []) {
    if (n.type === "ctrl" && n.data && n.data.botId) {
      controllers[n.data.botId] = { ...(n.data.values || {}) };
    }
    if (n.type === "pct" && n.data && n.data.pct != null) {
      pct[n.id] = n.data.pct;
    }
    if (n.type === "bias" && n.data && n.data.botId) {
      bias[n.data.botId] = { ...(n.data.bias || {}) };
    }
  }
  return { controllers, pct, bias };
}

export function stampDiffs(nodes, baseline) {
  if (!baseline) return nodes;
  const ctrl = baseline.controllers || {};
  const pct = baseline.pct || {};
  const bias = baseline.bias || {};
  return (nodes || []).map((n) => {
    if (n.type === "ctrl") {
      const botId = n.data && n.data.botId;
      const live = ctrl[botId] || {};
      const cur = (n.data && n.data.values) || {};
      const fields = (n.data && n.data.fields && n.data.fields.length)
        ? n.data.fields
        : (CONTROLLER_FIELDS[botId] || []);
      const dirty = {};
      let nDirty = 0;
      for (const f of fields) {
        if (!sameVal(cur[f.key], live[f.key])) {
          dirty[f.key] = live[f.key];
          nDirty += 1;
        }
      }
      return {
        ...n,
        data: { ...n.data, baseline: live, dirty, nDirty },
      };
    }
    if (n.type === "pct") {
      const livePct = pct[n.id];
      const dirty = livePct != null && !sameVal(n.data && n.data.pct, livePct);
      return { ...n, data: { ...n.data, livePct, dirty } };
    }
    if (n.type === "bias") {
      const botId = n.data && n.data.botId;
      const liveBias = bias[botId] || {};
      const cur = (n.data && n.data.bias) || {};
      const dirty = {};
      let nDirty = 0;
      for (const coin of Object.keys({ ...liveBias, ...cur })) {
        if (!sameVal(cur[coin], liveBias[coin])) {
          dirty[coin] = liveBias[coin];
          nDirty += 1;
        }
      }
      return { ...n, data: { ...n.data, liveBias, dirty, nDirty } };
    }
    return n;
  });
}

export function listChanges(nodes) {
  const out = [];
  for (const n of nodes || []) {
    if (n.type === "ctrl" && n.data && n.data.nDirty) {
      const fields = CONTROLLER_FIELDS[n.data.botId] || [];
      const cur = n.data.values || {};
      const live = n.data.baseline || {};
      for (const f of fields) {
        if (!n.data.dirty || !(f.key in n.data.dirty)) continue;
        out.push({
          id: n.data.botId + ":" + f.key,
          bot: n.data.name || hunterName[n.data.botId] || n.data.botId,
          label: f.label,
          from: live[f.key],
          to: cur[f.key],
          kind: f.kind,
          color: n.data.color,
        });
      }
    }
    if (n.type === "pct" && n.data && n.data.dirty) {
      out.push({
        id: n.id,
        bot: n.data.kind === "moon" ? "Moon" : "Stop loss",
        label: (n.data.kind === "moon" ? "Moon" : "SL") + " %",
        from: n.data.livePct,
        to: n.data.pct,
        color: n.data.kind === "moon" ? "#ffd24a" : "#ff4d6d",
      });
    }
    if (n.type === "bias" && n.data && n.data.nDirty) {
      const cur = n.data.bias || {};
      const live = n.data.liveBias || {};
      for (const coin of Object.keys(n.data.dirty || {})) {
        out.push({
          id: (n.data.botId || n.id) + ":" + coin,
          bot: n.data.name || "BIAS",
          label: coin + " bias",
          from: live[coin],
          to: cur[coin],
          color: n.data.color,
        });
      }
    }
  }
  return out;
}

export function fmtDiff(v, kind) {
  if (v == null || v === "") return "—";
  if (typeof v === "boolean") return v ? "on" : "off";
  if (kind === "pct_frac") {
    const n = Number(v);
    if (!Number.isNaN(n)) return (n <= 1 ? Math.round(n * 1000) / 10 : n) + "%";
  }
  return String(v);
}
