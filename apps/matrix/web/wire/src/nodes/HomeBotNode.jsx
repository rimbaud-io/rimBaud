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
import { money, pct, clsPnl, shortAddr } from "../lib/format.js";
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";

export default function HomeBotNode({ data, selected }) {
  const color = data.color || "#7ecbff";
  const ok = data.ok !== false;
  const run = !!data.running;
  const slRaw = Number(data.sl_roe_pct);
  const slArmed = data.sl_roe_pct != null && !Number.isNaN(slRaw) && slRaw >= 1;
  const slLbl = slArmed ? ("−" + slRaw + "%") : "not set";
  const nxt = data.next || {};
  const logs = (data.logs || []).slice(-3);
  const markets = (data.markets || []).slice(0, 8);
  return (
    <div className={`wn homebot${selected ? " selected" : ""}${ok ? "" : " issue"}`} style={{ "--bot": color }}>
      <PortHandle type="source" position={Position.Right} id="logs" className="h-logs" tip={PORT_TIP.botLogs} />
      <span className="port right logs" data-tip={PORT_TIP.botLogs}>LOG</span>
      <div className="wn-kicker">HOME · CARD</div>
      <div className="wn-title">
        <span className={`lamp ${ok && run ? "on" : "off"}`} />
        <span className="icon">{data.icon}</span>
        <span className="name" style={{ color }}>{data.name}</span>
      </div>
      <div className="hb-addr">{shortAddr(data.address)}</div>
      <div className="wn-grid">
        <div><div className="k">Equity</div><div className="v cyan">{money(data.equity_usd)}</div></div>
        <div><div className="k">Leftover</div><div className="v">{money(data.free_usd)}</div></div>
        <div><div className="k">Pos</div><div className="v">{data.n_pos || 0}</div></div>
        <div><div className="k">uPnL</div><div className={`v ${clsPnl(data.upnl_usd)}`}>{money(data.upnl_usd)}</div></div>
      </div>
      <div className="hb-sl">Stop loss <b>{slLbl}</b> ROE</div>
      <div className="hb-next">
        <div><span>Auto sell</span> {nxt.sell || "—"}</div>
        <div><span>Rebuy</span> {nxt.rebuy || "—"}</div>
      </div>
      {markets.length ? (
        <div className="hb-mk">{markets.map((m) => <span key={m}>{String(m).replace(/-USD$/, "")}</span>)}</div>
      ) : null}
      <div className="hb-logs">
        {logs.length ? logs.map((l, i) => <div key={i}>{l}</div>) : <div>no recent log</div>}
      </div>
    </div>
  );
}
