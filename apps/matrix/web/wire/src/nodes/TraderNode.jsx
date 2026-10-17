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

import { Position } from "@xyflow/react";
import { money, clsPnl, shortAddr } from "../lib/format.js";
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";

function signed(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  const x = Number(n);
  const s = x > 0 ? "+" : "";
  return s + money(x);
}

export default function TraderNode({ data, selected }) {
  const color = data.color || "#7ecbff";
  const live = data.roster && !data.alumni;
  const coins = (data.coins || []).slice(0, 4).join(" · ");
  const tag = live ? (data.followed ? "ON" : "OFF") : "WAS";
  return (
    <div
      className={`wn trader${selected ? " selected" : ""}${data.followed ? " on" : ""}${data.alumni ? " alumni" : ""}`}
      style={{ "--bot": color }}
    >
      <PortHandle type="source" position={Position.Right} id="trader" className="h-trader" tip={PORT_TIP.traderOut} />
      <span className="port right" style={{ color }} data-tip={PORT_TIP.traderOut}>LEAD</span>
      <div className="wn-kicker">
        {data.num ? `T#${data.num}` : "ALUMNI"} · {tag}
      </div>
      <div className="wn-title">
        <span className="icon">◈</span>
        <span className="name" style={{ color }}>{data.title || "Leader"}</span>
      </div>
      <div className="wn-addr">{shortAddr(data.address || data.short)}</div>
      <p className="wn-note">{data.why || "Copy leader"}</p>
      {coins ? <div className="trader-coins">{coins}</div> : null}
      <div className="wn-grid">
        <div>
          <div className="k">Day</div>
          <div className={`v ${clsPnl(data.day_pnl)}`}>{signed(data.day_pnl)}</div>
        </div>
        <div>
          <div className="k">Week</div>
          <div className={`v ${clsPnl(data.week_pnl)}`}>{signed(data.week_pnl)}</div>
        </div>
        <div>
          <div className="k">Month</div>
          <div className={`v ${clsPnl(data.month_pnl)}`}>{signed(data.month_pnl)}</div>
        </div>
        <div>
          <div className="k">Our copy</div>
          <div className={`v ${clsPnl(data.our_net)}`}>
            {data.our_net == null ? "—" : (
              <>
                <span className="pos">{signed(data.our_wins)}</span>
                {" / "}
                <span className="neg">{signed(data.our_losses)}</span>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
