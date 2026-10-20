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
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";

const FACE = {
  hmm: { labels: ["calm", "trend", "shock"], deg: -18, foot: "offline · later phase" },
  vol: { labels: ["low", "mid", "high"], deg: 22, foot: "realized vol · later" },
  bb: { labels: ["tight", "mid", "wide"], deg: -38, foot: "squeeze / expansion · later" },
  condor: { labels: ["calm", "trend", "shock"], deg: 0, foot: "wire REGIME → hunter BIAS" },
  pump: { labels: ["quiet", "pump", "post"], deg: -28, foot: "recognition only · no orders" },
};

const REGIME_DEG = { calm: -32, trend: 0, shock: 32, quiet: -32, pump: 8, post: 36 };

export default function GaugeNode({ data, selected }) {
  const kind = data.kind || "hmm";
  const isPump = kind === "pump";
  const face = FACE[kind] || FACE.hmm;
  const regime = String(data.regime || "").toLowerCase();
  const deg = (kind === "condor" || isPump) && regime in REGIME_DEG ? REGIME_DEG[regime] : face.deg;
  const bias = data.bias || {};
  const coins = Object.keys(bias);
  const pumpCoins = data.coins || {};
  const live = (kind === "condor" && (data.ts || data.running)) || (isPump && data.ts);
  const foot = kind === "condor"
    ? (data.why || (data.running ? "waiting first tick" : "container down · heuristic when up"))
    : isPump
      ? (data.why || face.foot)
      : face.foot;
  return (
    <div className={`wn gauge${selected ? " selected" : ""}${kind === "condor" ? " condor" : ""}${isPump ? " pump" : ""}`}>
      {isPump ? (
        <>
          <PortHandle type="source" position={Position.Right} id="live" className="h-live" tip={PORT_TIP.pumpLive} />
          <span className="port right" data-tip={PORT_TIP.pumpLive}>LIVE</span>
        </>
      ) : (
        <>
          <PortHandle type="source" position={Position.Top} id="bias" className="h-gauge" tip={PORT_TIP.gaugeOut} />
          <span className="port top gauge" data-tip={PORT_TIP.gaugeOut}>REGIME</span>
        </>
      )}
      {kind === "condor" ? (
        <>
          <PortHandle type="target" position={Position.Bottom} id="tg" className="h-tg" tip={PORT_TIP.condorTg} />
          <PortHandle type="target" position={Position.Left} id="grok" className="h-grok" tip={PORT_TIP.condorGrok} />
          <span className="port bot" style={{ color: "#34d399" }} data-tip={PORT_TIP.condorTg}>TG</span>
          <span className="port left grok" style={{ color: "#2A9FD6" }} data-tip={PORT_TIP.condorGrok}>GROK</span>
        </>
      ) : null}
      <div className="wn-kicker">{isPump ? "SEE · POST PUMP" : "IN · MESSGERÄT"}</div>
      <div className="wn-head">{data.title || (kind === "condor" ? "Condor" : isPump ? "Post pump gauge" : "HMM / vol")}</div>
      <div className="gauge-face">
        <div className="needle" style={{ transform: `rotate(${deg}deg)` }} />
        <div className="g-lbls">
          {face.labels.map((l) => (
            <span key={l} className={l === regime ? "on" : ""}>{l}</span>
          ))}
        </div>
      </div>
      {kind === "condor" ? (
        <div className="condor-bias">
          {(coins.length ? coins : ["BTC", "ETH", "LTC"]).map((c) => {
            const m = String(bias[c] || "—").toUpperCase();
            return (
              <span key={c} className={`cb ${m.toLowerCase()}`}>
                {c} {m}
              </span>
            );
          })}
        </div>
      ) : null}
      {isPump ? (
        <div className="condor-bias">
          {["BTC", "ETH", "LTC"].map((c) => {
            const row = pumpCoins[c] || {};
            const st = String(row.state || "quiet").toLowerCase();
            const r = row.ret_15m != null ? `${row.ret_15m >= 0 ? "+" : ""}${row.ret_15m}% 15m` : "—";
            return (
              <span key={c} className={`cb ${st}`} title={row.why || ""}>
                {c} {st.toUpperCase()} {r}
              </span>
            );
          })}
        </div>
      ) : null}
      <div className="wn-foot">
        {live && data.source ? `${data.source} · ` : ""}
        {foot}
      </div>
    </div>
  );
}
