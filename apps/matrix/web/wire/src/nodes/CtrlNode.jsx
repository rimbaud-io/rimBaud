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

import { useEffect, useRef, useState } from "react";
import { Position } from "@xyflow/react";
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";
import RotaryKnob from "./RotaryKnob.jsx";
import { CONTROLLER_FIELDS } from "../lib/seed.js";
import { emitWire } from "../lib/tools.js";
import { closeHits } from "../lib/impact.js";

function shown(field, stored) {
  if (stored == null || stored === "") return field.kind === "bool" ? false : "";
  if (field.kind === "pct_frac") {
    const n = Number(stored);
    if (isNaN(n)) return "";
    return n <= 1 ? String(Math.round(n * 1000) / 10) : String(n);
  }
  if (field.kind === "bool") return !!stored;
  if (field.kind === "select") return String(stored);
  return stored;
}

function toStore(field, raw) {
  if (field.kind === "bool") return !!raw;
  const n = Number(raw);
  if (isNaN(n)) return null;
  if (field.kind === "pct_frac") return n > 1 ? n / 100 : n;
  return n;
}

export default function CtrlNode({ data, selected }) {
  const botId = data.botId;
  const color = data.color || "#c084fc";
  const fields = (data.fields && data.fields.length)
    ? data.fields
    : (CONTROLLER_FIELDS[botId] || CONTROLLER_FIELDS.default);
  const values = data.values || {};
  const dirty = data.dirty || {};
  const [local, setLocal] = useState({});
  const focusRef = useRef(false);

  useEffect(() => {
    if (focusRef.current) return;
    const next = {};
    for (const f of fields) {
      next[f.key] = shown(f, values[f.key]);
    }
    setLocal(next);
  }, [botId, JSON.stringify(values)]);

  const commit = (field, raw) => {
    const v = toStore(field, raw);
    if (v == null && field.kind !== "bool") return;
    emitWire("ctrl", { botId, key: field.key, value: v, field });
  };

  const trial = { ...values };
  for (const f of fields) {
    if (local[f.key] == null || local[f.key] === "") continue;
    const v = f.kind === "bool" ? !!local[f.key] : toStore(f, local[f.key]);
    if (v != null || f.kind === "bool") trial[f.key] = v;
  }
  const hits = closeHits(botId, trial, data.positions || []);
  const hitKeys = new Set(hits.map((h) => h.key));
  const wouldClose = hits.some((h) => h.kind === "close");

  return (
    <div className={`wn ctrl${selected ? " selected" : ""}${data.nDirty ? " dirty" : ""}${wouldClose ? " closes" : ""}`} style={{ "--bot": color }}>
      <PortHandle type="source" position={Position.Top} id="ctrl" className="h-ctrl" tip={PORT_TIP.ctrlOut} />
      <span className="port top gauge" style={{ color }} data-tip={PORT_TIP.ctrlOut}>CTRL</span>
      <div className="wn-kicker">SET · CONTROLLER</div>
      <div className="wn-title">
        <span className="icon">{data.icon}</span>
        <span className="name" style={{ color }}>{data.name}</span>
      </div>
      <p className="wn-note">
        {wouldClose
          ? "Rose = this setting would close a live position now."
          : data.nDirty
            ? data.nDirty + " knob(s) differ from live load. Gold = changed."
            : "Turn knobs left / right (Shift = fine). Double-click a value to type. Gold = changed. Rose = would close now."}
      </p>
      {hits.length ? (
        <ul className="ctrl-closes">
          {hits.map((h) => (
            <li key={h.reason}>{h.reason}</li>
          ))}
        </ul>
      ) : null}
      <div className="ctrl-fields nodrag nopan" onPointerDown={(e) => e.stopPropagation()}>
        {fields.map((f) => {
          if (f.kind === "bool") {
            return (
              <label key={f.key} className={`ctrl-row${f.key in dirty ? " changed" : ""}${hitKeys.has(f.key) ? " closes" : ""}`}>
                <span>{f.label}</span>
                <input
                  type="checkbox"
                  checked={!!local[f.key]}
                  onChange={(e) => {
                    setLocal((s) => ({ ...s, [f.key]: e.target.checked }));
                    commit(f, e.target.checked);
                  }}
                />
              </label>
            );
          }
          if (f.kind === "select") {
            return (
              <label key={f.key} className={`ctrl-row${f.key in dirty ? " changed" : ""}${hitKeys.has(f.key) ? " closes" : ""}`}>
                <span>{f.label}</span>
                <select
                  value={local[f.key] != null && local[f.key] !== "" ? local[f.key] : (f.options || [])[0]}
                  onChange={(e) => {
                    setLocal((s) => ({ ...s, [f.key]: e.target.value }));
                    commit(f, e.target.value);
                  }}
                >
                  {(f.options || []).map((o) => (
                    <option key={o} value={o}>{o}</option>
                  ))}
                </select>
              </label>
            );
          }
          return (
            <RotaryKnob
              key={f.key}
              field={f}
              value={local[f.key]}
              color={hitKeys.has(f.key) ? "#ff4d6d" : color}
              changed={f.key in dirty}
              danger={hitKeys.has(f.key)}
              onLive={(v) => {
                focusRef.current = true;
                setLocal((s) => ({ ...s, [f.key]: v }));
              }}
              onCommit={(v) => {
                focusRef.current = false;
                setLocal((s) => ({ ...s, [f.key]: v }));
                commit(f, v);
              }}
            />
          );
        })}
      </div>
    </div>
  );
}
