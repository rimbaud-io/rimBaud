// Copyright 2026 Laurence Sterne
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
import { money, pct, clsPnl } from "../lib/format.js";
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";
import { emitWire } from "../lib/tools.js";

function barCls(rate) {
  const p = Number(rate.pct || 0);
  const hot = p >= 90 || (rate.last_429_s != null && rate.last_429_s < 45);
  const warn = !hot && p >= 70;
  return hot ? " hot" : warn ? " warn" : "";
}

export default function HomeStatNode({ data, selected }) {
  const kind = data.kind || "live";
  const t = data.totals || {};
  const rate = data.rate || {};
  const cg = data.condor || {};
  const nOk = t.n_ok != null ? t.n_ok : (t.n_live || 0);
  const nBots = t.n_bots || 0;
  const rows = (data.positions || []).slice(0, 8);
  const color = data.color || "#9dffc0";
  const compact = data.toolId === "grok-bias-report";
  const wide = !compact && (kind === "tokens" || kind === "book" || kind === "grok");

  let body = null;
  if (kind === "grok") {
    const coins = ["BTC", "ETH", "LTC", "TAO"];
    const src = cg.source === "grok" ? "GROK" : (cg.source || "heuristic");
    const db = data.desk_bias || {};
    const want = String(db.bias || "").toUpperCase();
    body = (
      <>
        <PortHandle type="source" position={Position.Right} id="out" className="h-tg" tip={PORT_TIP.grokBiasOut} />
        <span className="port right" style={{ color: "#34d399" }} data-tip={PORT_TIP.grokBiasOut}>TG</span>
        <div className="wn-kicker">{compact ? "REPORT · GROK BIAS" : "HOME · GROK + DESK BIAS"}</div>
        <div className="wn-title"><span className="name" style={{ color }}>GROK BIAS</span>{compact ? null : <span className="name" style={{ color: "#f0b90b", marginLeft: 16 }}>Desk BIAS</span>}</div>
        <div className="hstat-val" style={{ display: "flex", gap: 8, fontSize: 15, flexWrap: "wrap", alignItems: "center" }}>
          {["LONG", "FLAT", "SHORT"].map((m) => {
            const on = String(cg.grok_cmd || "").toUpperCase() === m;
            const col = m === "LONG" ? "#7ecbff" : m === "SHORT" ? "#ff4d6d" : "#f0b90b";
            return (
              <span
                key={"post-" + m}
                style={{
                  color: col,
                  border: "1px solid " + col,
                  padding: "2px 8px",
                  background: on ? (m === "SHORT" ? "rgba(255,77,109,0.18)" : m === "LONG" ? "rgba(126,203,255,0.18)" : "rgba(240,185,11,0.2)") : "transparent",
                  boxShadow: on ? "0 0 10px " + col : "none",
                  fontWeight: on ? 700 : 500,
                }}
              >
                {on ? "LAST " : ""}{m}
              </span>
            );
          })}
          {coins.map((c) => (
            <span key={c} style={{ color: (cg.bias || {})[c] === "LONG" ? "#7ecbff" : (cg.bias || {})[c] === "SHORT" ? "#ff4d6d" : "#f0b90b", opacity: 0.7, fontSize: 12 }}>
              {c} {(cg.bias || {})[c] || "FLAT"}
            </span>
          ))}
          {compact ? null : (
          <span style={{ marginLeft: "auto", display: "flex", gap: 6 }} className="nodrag nopan" onPointerDown={(e) => e.stopPropagation()}>
            {["LONG", "FLAT", "SHORT"].map((m) => (
              <button
                key={m}
                type="button"
                className={`bias-btn ${m.toLowerCase()}${want === m ? " on" : ""}`}
                onClick={(e) => { e.stopPropagation(); emitWire("desk_bias", { mode: m }); }}
              >
                {m}
              </button>
            ))}
          </span>
          )}
        </div>
        <div className="wn-foot">{src} · {String(cg.regime || "—").toUpperCase()} · TG out → BIAS BOT · never writes hunters{compact ? "" : " · Desk after autoclose"}</div>
      </>
    );
  } else if (kind === "tokens") {
    const cap = rate.cap || 1200;
    const used = rate.used || 0;
    const left = rate.left != null ? rate.left : Math.max(0, cap - used);
    const p = Math.max(0, Math.min(100, Number(rate.pct != null ? rate.pct : 0)));
    body = (
      <>
        <div className="wn-kicker">HOME · BOX</div>
        <div className="wn-title"><span className="name" style={{ color }}>HL Tokens</span></div>
        <div className={`hstat-val${barCls(rate)}`}>{used} / {cap} · {left} left</div>
        <div className={`hstat-bar${barCls(rate)}`}><i style={{ width: p.toFixed(1) + "%" }} /></div>
        <div className="wn-foot">1200 w / rolling 60s · this public IP</div>
      </>
    );
  } else if (kind === "live") {
    body = (
      <>
        <div className="hstat-lbl">Bots live</div>
        <div className={`hstat-val ${nOk < nBots ? "rose" : "gold"}`}>{nOk} / {nBots}</div>
      </>
    );
  } else if (kind === "pos") {
    body = (
      <>
        <div className="hstat-lbl">Open positions</div>
        <div className="hstat-val cyan">{t.n_pos ?? 0}</div>
      </>
    );
  } else if (kind === "equity") {
    body = (
      <>
        <div className="hstat-lbl">Total equity</div>
        <div className="hstat-val">{money(t.equity_usd)}</div>
      </>
    );
  } else if (kind === "upnl") {
    body = (
      <>
        <div className="hstat-lbl">Open uPnL</div>
        <div className={`hstat-val ${clsPnl(t.upnl_usd)}`}>{money(t.upnl_usd)}</div>
      </>
    );
  } else if (kind === "condor") {
    const bits = Object.entries(cg.bias || {}).map(([c, m]) => c + " " + m).join(" · ");
    const regime = String(cg.regime || "—").toUpperCase();
    const tone = cg.regime === "shock" ? "rose" : cg.regime === "trend" ? "gold" : "cyan";
    body = (
      <>
        <div className="hstat-lbl">Condor trend</div>
        <div className={`hstat-val ${tone}`}>{regime}</div>
        <div className="wn-foot">{bits || (cg.running ? (cg.source || "gauge") : "down")}</div>
      </>
    );
  } else if (kind === "book") {
    body = (
      <>
        <div className="wn-kicker">HOME · TABLE</div>
        <div className="wn-title"><span className="name" style={{ color }}>Active positions</span></div>
        <div className="hstat-book">
          {rows.length ? rows.map((p, i) => (
            <div key={(p.bot || "") + (p.coin || "") + i} className="hstat-row">
              <span>{p.bot}</span>
              <span>{p.coin} {p.side}</span>
              <span className={clsPnl(p.pnl_usd)}>{money(p.pnl_usd)} · {pct(p.roe_pct)}</span>
            </div>
          )) : <div className="hstat-row muted">no open positions</div>}
        </div>
        <div className="wn-foot">{t.n_pos || 0} open · live on /</div>
      </>
    );
  }

  return (
    <div
      className={`wn homestat ${kind}${wide ? " wide" : ""}${compact ? " grok-report" : ""}${selected ? " selected" : ""}`}
      style={{ "--bot": color }}
    >
      {body}
    </div>
  );
}
