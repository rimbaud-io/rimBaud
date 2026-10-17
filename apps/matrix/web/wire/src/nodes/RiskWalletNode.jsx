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

import { useEffect, useState } from "react";
import { Position } from "@xyflow/react";
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";
import { emitWire } from "../lib/tools.js";
import { shortAddr } from "../lib/format.js";

export default function RiskWalletNode({ id, data, selected }) {
  const color = data.color || "#ff4d6d";
  const mode = data.skim_mode === "two_wins" ? "two_wins" : "win_pct";
  const [addr, setAddr] = useState(data.address || "");
  const [pct, setPct] = useState(data.skim_pct != null ? String(data.skim_pct) : "20");
  const [hours, setHours] = useState(data.skim_every_h != null ? String(data.skim_every_h) : "24");
  const [minUsd, setMinUsd] = useState(data.skim_min_usd != null ? String(data.skim_min_usd) : "10");

  useEffect(() => {
    setAddr(data.address || "");
    setPct(data.skim_pct != null ? String(data.skim_pct) : "20");
    setHours(data.skim_every_h != null ? String(data.skim_every_h) : "24");
    setMinUsd(data.skim_min_usd != null ? String(data.skim_min_usd) : "10");
  }, [data.address, data.skim_pct, data.skim_every_h, data.skim_min_usd]);

  const commit = (patch) => emitWire("risk_wallet", { id, ...patch });

  return (
    <div className={`wn risk-wallet${selected ? " selected" : ""}`} style={{ "--bot": color }}>
      <PortHandle type="target" position={Position.Left} id="risk" className="h-risk" tip={PORT_TIP.riskIn} />
      <span className="port left" style={{ color }} data-tip={PORT_TIP.riskIn}>RISK</span>
      <div className="wn-kicker">WALLET · RISK</div>
      <div className="wn-title">
        <span className="icon">⛨</span>
        <span className="name" style={{ color }}>{data.name || "Risk wallet"}</span>
      </div>
      <div className="wn-addr">{shortAddr(addr) || "no address yet"}</div>
      <p className="wn-note">
        Hunter RISK (right) → here (left). Parks realized wins off the trading book.
        Compile maps this — no auto Hyperliquid send.
      </p>
      <label className="risk-lab">Destination 0x…</label>
      <input
        className="risk-inp nodrag nopan"
        value={addr}
        placeholder="0x…"
        onPointerDown={(e) => e.stopPropagation()}
        onChange={(e) => setAddr(e.target.value)}
        onBlur={() => commit({ address: addr.trim() })}
      />
      <div className="risk-modes nodrag nopan" onPointerDown={(e) => e.stopPropagation()}>
        <button
          type="button"
          className={`risk-mode${mode === "win_pct" ? " on" : ""}`}
          onClick={() => commit({ skim_mode: "win_pct" })}
        >
          Win%
        </button>
        <button
          type="button"
          className={`risk-mode${mode === "two_wins" ? " on" : ""}`}
          onClick={() => commit({ skim_mode: "two_wins" })}
        >
          2 wins in a row
        </button>
      </div>
      {mode === "win_pct" ? (
        <div className="risk-row nodrag nopan" onPointerDown={(e) => e.stopPropagation()}>
          <label>Win %
            <input
              value={pct}
              onChange={(e) => setPct(e.target.value)}
              onBlur={() => {
                const n = Number(pct);
                if (!isNaN(n) && n >= 0 && n <= 100) commit({ skim_pct: n });
              }}
            />
          </label>
          <label>Every h
            <input
              value={hours}
              onChange={(e) => setHours(e.target.value)}
              onBlur={() => {
                const n = Number(hours);
                if (!isNaN(n) && n >= 1 && n <= 168) commit({ skim_every_h: n });
              }}
            />
          </label>
          <label>Min $
            <input
              value={minUsd}
              onChange={(e) => setMinUsd(e.target.value)}
              onBlur={() => {
                const n = Number(minUsd);
                if (!isNaN(n) && n >= 0) commit({ skim_min_usd: n });
              }}
            />
          </label>
        </div>
      ) : (
        <p className="wn-note">
          Two realized wins in a row: park only the <b>second</b> trade’s profit.
          The first win stays on the hunter. A loss resets the streak.
        </p>
      )}
      <label className="risk-arm nodrag nopan" onPointerDown={(e) => e.stopPropagation()}>
        <input
          type="checkbox"
          checked={!!data.armed}
          onChange={(e) => commit({ armed: e.target.checked })}
        />
        Arm map (still no HL send)
      </label>
    </div>
  );
}
