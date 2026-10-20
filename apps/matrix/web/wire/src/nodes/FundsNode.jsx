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

export default function FundsNode({ data, selected }) {
  const wallets = data.wallets || [];
  return (
    <div className={`wn funds${selected ? " selected" : ""}`}>
      <PortHandle type="target" position={Position.Left} id="in" className="h-funds" tip={PORT_TIP.fundsIn} />
      <span className="port left" data-tip={PORT_TIP.fundsIn}>USD</span>
      <div className="wn-kicker">OUT · FUNDS</div>
      <div
        className="wn-head"
        title="Right-click: add wallet"
        onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
          window.dispatchEvent(new CustomEvent("wire-wallets-header", {
            detail: { x: e.clientX, y: e.clientY },
          }));
        }}
      >
        HL wallets
      </div>
      <p className="wn-note">Rewire through Confiig — not raw 0x drag.</p>
      <div className="fund-list">
        {wallets.length === 0 && <div className="empty">waiting for /api/home…</div>}
        {wallets.map((w) => (
          <div key={w.id} className="fund-row">
            <span className="dot" style={{ background: w.color }} />
            <span className="fn">{w.name}</span>
            <span className="fa">{shortAddr(w.address)}</span>
            <span className="fe">{money(w.equity_usd)}</span>
          </div>
        ))}
      </div>
      <a className="wn-link" href="/confiig" onClick={(e) => e.stopPropagation()}>
        Open Confiig →
      </a>
    </div>
  );
}
