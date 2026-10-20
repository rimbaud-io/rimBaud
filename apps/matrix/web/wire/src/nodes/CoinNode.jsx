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
import { money, pct } from "../lib/format.js";
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";

export default function CoinNode({ data, selected }) {
  const color = data.color || "#94a3b8";
  const pos = (data.positions || []).slice(0, 3);
  const extra = (data.positions || []).length - pos.length;
  return (
    <div className={`wn coin${selected ? " selected" : ""}`} style={{ "--bot": color }}>
      <PortHandle type="source" position={Position.Right} id="coin" className="h-coin" tip={PORT_TIP.coinOut} />
      <span className="port right coin" data-tip={PORT_TIP.coinOut}>COIN</span>
      <div className="wn-kicker">MARKET</div>
      <div className="wn-title">
        <span className="coin-dot" style={{ background: color }} />
        <span className="name" style={{ color }}>{data.coin}</span>
      </div>
      <div className="wn-grid">
        <div>
          <div className="k">Mark</div>
          <div className="v cyan">{money(data.mark, data.mark >= 100 ? 2 : 4)}</div>
        </div>
        <div>
          <div className="k">Live</div>
          <div className="v">{data.n_pos || 0}</div>
        </div>
      </div>
      <div className="chips">
        {pos.length === 0 && <span className="chip more">no pos</span>}
        {pos.map((p) => (
          <span key={p.bot_id + p.side} className={`chip ${String(p.side).toUpperCase() === "SHORT" ? "short" : "long"}`}>
            {(p.bot || "").replace(/^HL /, "")} {String(p.side).slice(0, 1)} {pct(p.roe_pct)}
          </span>
        ))}
        {extra > 0 && <span className="chip more">+{extra}</span>}
      </div>
    </div>
  );
}
