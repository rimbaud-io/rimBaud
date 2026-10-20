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

const MODES = ["LONG", "SHORT", "FLAT"];

export default function BiasNode({ data, selected }) {
  const coins = data.coins && data.coins.length
    ? data.coins
    : Object.keys(data.bias || { ALL: "FLAT" });
  const bias = data.bias || {};
  const color = data.color || "#ffc857";
  const botId = data.botId || null;

  const setMode = (coin, mode) => {
    emitWire("bias", { botId, coin, mode, nodeId: data.toolId });
  };

  return (
    <div className={`wn bias${selected ? " selected" : ""}${data.nDirty ? " dirty" : ""}`} style={{ "--bot": color }}>
      <PortHandle type="target" position={Position.Bottom} id="in" className="h-gauge" tip={PORT_TIP.biasIn} />
      <PortHandle type="source" position={Position.Top} id="bias" className="h-bias" tip={PORT_TIP.biasOut} />
      <span className="port top gauge" style={{ color: "var(--amber)" }} data-tip={PORT_TIP.biasOut}>BIAS</span>
      <div className="wn-kicker">SET · BIAS</div>
      <div className="wn-title">
        <span className="icon">{data.icon || "⊕"}</span>
        <span className="name" style={{ color }}>{data.name || "Manual BIAS"}</span>
      </div>
      <p className="wn-note">Wire into the bot BIAS port. LONG / SHORT / FLAT for the connected coin.</p>
      <div className="bias-coins">
        {coins.map((c) => {
          const mode = bias[c] || "FLAT";
          return (
            <div key={c} className={`bias-coin${data.dirty && data.dirty[c] != null ? " changed" : ""}`}>
              <span className="bc-name">{c}</span>
              <div className="bias-btns tight">
                {MODES.map((m) => (
                  <button
                    key={m}
                    type="button"
                    className={`bias-btn ${m.toLowerCase()}${mode === m ? " on" : ""}`}
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.stopPropagation();
                      setMode(c, m);
                    }}
                  >
                    {m.slice(0, 1)}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
