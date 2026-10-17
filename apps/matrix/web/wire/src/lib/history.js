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

import { slimGraph } from "./tools.js";

export const HISTORY_KEEP = 8;

export const BIAS_API = {
  "hummingbot-bn-swing": "/api/bn_swing/bias",
  "hummingbot-helliot": "/api/helliot/bias",
  "hummingbot-surfn": "/api/surfn/bias",
  "hummingbot-shortn": "/api/shortn/bias",
  "hummingbot-dman": "/api/dman/bias",
  "hummingbot-onlyup": "/api/onlyup/bias",
  "hummingbot-dman-maker": "/api/dman_maker/bias",
};

export function packStep(nodes, edges, label) {
  const graph = slimGraph(nodes, edges);
  const controllers = {};
  const bias = {};
  const pct = {};
  for (const n of nodes || []) {
    const d = n.data || {};
    if (n.type === "ctrl" && d.botId) controllers[d.botId] = { ...(d.values || {}) };
    if (n.type === "bias" && d.botId) bias[d.botId] = { ...(d.bias || {}) };
    if (n.type === "pct" && d.pct != null) pct[n.id] = { kind: d.kind, pct: d.pct };
  }
  return { label: label || "step", graph, controllers, bias, pct };
}

export function replayConfig(step) {
  const controllers = (step && step.controllers) || {};
  for (const [id, values] of Object.entries(controllers)) {
    if (!values || typeof values !== "object") continue;
    const payload = {};
    for (const [k, v] of Object.entries(values)) {
      if (k === "updated" || k === "source" || v == null) continue;
      payload[k] = v;
    }
    if (!Object.keys(payload).length) continue;
    fetch("/api/wire/controller", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, values: payload }),
    }).catch(() => {});
  }
  const bias = (step && step.bias) || {};
  for (const [botId, map] of Object.entries(bias)) {
    const path = BIAS_API[botId] || "/api/wire/bias";
    if (!map) continue;
    for (const [coin, mode] of Object.entries(map)) {
      if (!coin || !mode) continue;
      fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: botId, coin, bias: mode }),
      }).catch(() => {});
    }
  }
}

export function persistHistory(step) {
  const g = (step && step.graph) || {};
  fetch("/api/wire/history", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      label: step.label,
      nodes: g.nodes || [],
      edges: g.edges || [],
      controllers: step.controllers || {},
      bias: step.bias || {},
      pct: step.pct || {},
    }),
  }).catch(() => {});
}
