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

import { Position } from "@xyflow/react";
import { emitWire } from "../lib/tools.js";
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";

const MODES = [
  { id: "LONG", cls: "long" },
  { id: "FLAT", cls: "flat" },
  { id: "SHORT", cls: "short" },
];

export default function DeskBiasNode({ data, selected }) {
  const color = data.color || "#f0b90b";
  const want = String(data.bias || data.cmd || "").toUpperCase();
  const hunters = data.hunters || [];
  const holding = hunters.filter((h) => (h.held || []).length);
  const send = (mode) => {
    emitWire("desk_bias", { mode });
  };
  return (
    <div className={`wn desk-bias${selected ? " selected" : ""}`} style={{ "--bot": color }}>
      <PortHandle type="target" position={Position.Bottom} id="tg" className="h-tg" tip={PORT_TIP.deskBiasIn} />
      <span className="port bot" style={{ color: "#34d399" }} data-tip={PORT_TIP.deskBiasIn}>TG</span>
      <div className="wn-kicker">DESK · BIAS</div>
      <div className="wn-title">
        <span className="icon">⊕</span>
        <span className="name" style={{ color }}>{data.name || "Desk BIAS"}</span>
      </div>
      <p className="wn-note">
        Telegram OUT → here sets hunter BIAS after autoclose. Do not wire BIAS BOT
        condor1 here — that bot is GROK report only. Does not close.
      </p>
      <div className="xlate-now">{want || "—"}</div>
      <div className="bias-btns xlate-btns nodrag nopan" onPointerDown={(e) => e.stopPropagation()}>
        {MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            className={`bias-btn ${m.cls}${want === m.id ? " on" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              send(m.id);
            }}
          >
            {m.id}
          </button>
        ))}
      </div>
      <div className="wn-foot">
        {holding.length
          ? ("holding " + holding.map((h) => (h.name || h.id) + " " + (h.held || []).join("/")).join(" · "))
          : (data.last_raw || "wire a Telegram bot · no flatten")}
        {data.last_ts ? " · " + String(data.last_ts).slice(11, 19) : ""}
      </div>
    </div>
  );
}
