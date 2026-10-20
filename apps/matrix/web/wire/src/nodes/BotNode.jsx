// Copyright 2026 Laurence Sterne www.rimbaud.io
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
import { money, pct, shortAddr, clsPnl } from "../lib/format.js";
import { closeHits } from "../lib/impact.js";
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";

export default function BotNode({ id, data, selected }) {
  const ok = !!data.ok;
  const run = !!data.running;
  const isCopy = id === "hummingbot-hl" || data.id === "hummingbot-hl" || data.kind === "copy";
  const color = data.color || "#7ecbff";
  const positions = (data.positions || []).slice(0, 2);
  const extra = (data.positions || []).length - positions.length;
  const lastLog = (data.logs || []).slice(-1)[0] || "no recent log";
  const w = data.wired || {};
  const hits = closeHits(data.id, data.controller || {}, data.positions || []);
  const wouldClose = hits.some((h) => h.kind === "close");

  return (
    <div
      className={`wn bot${isCopy ? " copy" : ""}${selected ? " selected" : ""}${ok ? "" : " issue"}${wouldClose ? " closes" : ""}`}
      style={{ "--bot": color }}
    >
      {isCopy ? [1, 2, 3].map((n) => (
        <PortHandle
          key={"th-" + n}
          type="target"
          position={Position.Top}
          id={"trader-" + n}
          className="h-trader"
          style={{ left: `${16 + (n - 1) * 34}%` }}
          tip={`Copy slot T#${n} — wire a Traders card here to follow that leader.`}
        />
      )) : null}
      {isCopy ? [1, 2, 3].map((n) => (
        <span
          key={"tl-" + n}
          className="port top tslot"
          style={{ left: `${16 + (n - 1) * 34}%`, color }}
          data-tip={`Copy slot T#${n}`}
        >
          T{n}
        </span>
      )) : null}
      <PortHandle type="target" position={Position.Left} id="wallet" className="h-wallet" style={{ top: "30%" }} tip={PORT_TIP.botWallet} />
      <span className="port left wallet" data-tip={PORT_TIP.botWallet}>WALLET</span>
      <PortHandle type="target" position={Position.Left} id="coin" className="h-coin" style={{ top: "68%" }} tip={PORT_TIP.botCoin} />
      <span className="port left coin" data-tip={PORT_TIP.botCoin}>COIN</span>

      <PortHandle type="source" position={Position.Right} id="income" className="h-income" style={{ top: "22%" }} tip={PORT_TIP.botIncome} />
      <span className="port right income" style={{ top: "18%" }} data-tip={PORT_TIP.botIncome}>INCOME</span>
      <PortHandle type="source" position={Position.Right} id="risk" className="h-risk" style={{ top: "50%" }} tip={PORT_TIP.botRisk} />
      <span className="port right" style={{ top: "46%", color: "#ff4d6d" }} data-tip={PORT_TIP.botRisk}>RISK</span>
      <PortHandle type="source" position={Position.Right} id="logs" className="h-logs" style={{ top: "78%" }} tip={PORT_TIP.botLogs} />
      <span className="port right logs" style={{ top: "74%" }} data-tip={PORT_TIP.botLogs}>LOG</span>

      <PortHandle type="target" position={Position.Bottom} id="sl" className="h-sl" style={{ left: "10%" }} tip={PORT_TIP.botSl} />
      <PortHandle type="target" position={Position.Bottom} id="bias" className="h-bias" style={{ left: "28%" }} tip={PORT_TIP.botBias} />
      <PortHandle type="target" position={Position.Bottom} id="moon" className="h-moon" style={{ left: "46%" }} tip={PORT_TIP.botMoon} />
      <PortHandle type="target" position={Position.Bottom} id="ctrl" className="h-ctrl" style={{ left: "64%" }} tip={PORT_TIP.botCtrl} />
      <PortHandle type="target" position={Position.Bottom} id="eclose" className="h-eclose" style={{ left: "88%" }} tip={PORT_TIP.botEclose} />

      <div className="wn-title">
        <span className={`lamp ${ok && run ? "on" : "off"}`} title={data.health_reason || (ok ? "ok" : "issue")} />
        <span className={`run ${run ? "live" : "down"}`}>{run ? "live" : "down"}</span>
        <span className="icon">{data.icon}</span>
        <a className="name" href={data.href || "#"} onClick={(e) => e.stopPropagation()}>
          {data.name}
        </a>
      </div>
      <div className="wn-addr">{shortAddr(data.address)}</div>
      <div className="wn-grid">
        <div>
          <div className="k">Equity</div>
          <div className="v cyan">{money(data.equity_usd)}</div>
        </div>
        <div>
          <div className="k">Free</div>
          <div className="v">{money(data.free_usd)}</div>
        </div>
        <div>
          <div className="k">Pos</div>
          <div className="v">{data.n_pos || 0}</div>
        </div>
        <div>
          <div className="k">uPnL</div>
          <div className={`v ${clsPnl(data.upnl_usd)}`}>{money(data.upnl_usd)}</div>
        </div>
      </div>
      <div className="chips">
        {positions.length === 0 && <span className="chip more">flat</span>}
        {positions.map((p) => (
          <span key={p.coin + p.side} className={`chip ${String(p.side).toUpperCase() === "SHORT" ? "short" : "long"}`}>
            {p.coin} {String(p.side).slice(0, 1)} {pct(p.roe_pct)}
          </span>
        ))}
        {extra > 0 && <span className="chip more">+{extra}</span>}
        {wouldClose && <span className="chip closes">would close</span>}
        {data.risk && <span className="chip closes" title="RISK wallet connected">RISK connected</span>}
      </div>
      <div className="wn-wire">
        <span>coin {w.coin || "—"}</span>
        <span>sl {w.sl != null ? `−${w.sl}%` : "—"}</span>
        <span>moon {w.moon != null ? `+${w.moon}%` : "—"}</span>
        <span>bias {w.bias || "—"}</span>
        {isCopy ? <span>lead {w.traders || "—"}</span> : null}
        <span>risk {w.risk || "—"}</span>
        <span className={w.eclose ? "kill-armed" : ""}>kill {w.eclose || "—"}</span>
      </div>
      <div className="bot-set">
        <span data-tip={PORT_TIP.botSl}>SL</span>
        <span data-tip={PORT_TIP.botBias}>BIAS</span>
        <span data-tip={PORT_TIP.botMoon}>MOON</span>
        <span data-tip={PORT_TIP.botCtrl}>CTRL</span>
        <span data-tip={PORT_TIP.botEclose} className="kill">E-CLOSE</span>
      </div>
      <div className="wn-log">{lastLog}</div>
    </div>
  );
}
