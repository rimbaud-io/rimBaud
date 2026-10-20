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
import { money, shortAddr } from "../lib/format.js";
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";

export default function WalletNode({ data, selected }) {
  const color = data.color || "#f0b90b";
  return (
    <div className={`wn wallet${selected ? " selected" : ""}`} style={{ "--bot": color }}>
      <PortHandle type="target" position={Position.Left} id="income" className="h-income" style={{ top: "32%" }} tip={PORT_TIP.walletIncome} />
      <PortHandle type="target" position={Position.Left} id="in" className="h-funds" style={{ top: "72%" }} tip={PORT_TIP.walletFunds} />
      <span className="port left income" style={{ top: "28%" }} data-tip={PORT_TIP.walletIncome}>INCOME</span>
      <PortHandle type="source" position={Position.Right} id="wallet" className="h-wallet" tip={PORT_TIP.walletAddr} />
      <span className="port right wallet" data-tip={PORT_TIP.walletAddr}>ADDR</span>
      <div className="wn-kicker">WALLET · SUB</div>
      <div className="wn-title">
        <span className="icon">{data.icon || "⬡"}</span>
        <span className="name">{data.name || "sub"}</span>
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
      </div>
      <p className="wn-note">Wire ADDR into a bot WALLET. Wire INCOME from a bot to sweep profits here (mapped — no auto HL send yet).</p>
      <a className="wn-link" href="/confiig" onClick={(e) => e.stopPropagation()}>
        Open Confiig →
      </a>
    </div>
  );
}
