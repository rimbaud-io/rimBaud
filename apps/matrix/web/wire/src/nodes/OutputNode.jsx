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
import { money, clsPnl } from "../lib/format.js";
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";

export default function OutputNode({ data, selected }) {
  const xml = data.kind === "xml";
  const stream = data.kind === "stream";
  const tip = xml ? PORT_TIP.xmlIn : (stream ? PORT_TIP.streamIn : PORT_TIP.liveIn);
  const handleClass = xml ? "h-funds" : (stream ? "h-tg" : "h-logs");
  return (
    <div className={`wn output ${xml ? "xml" : stream ? "stream" : "live"}${selected ? " selected" : ""}`}>
      <PortHandle type="target" position={Position.Left} id="in" className={handleClass} tip={tip} />
      <span className="port left" data-tip={tip}>{xml ? "IN" : stream ? "IN" : "LOG"}</span>
      <div className="wn-kicker">OUT · {xml ? "FILE" : stream ? "STREAM" : "LIVE"}</div>
      <div className="wn-head">{xml ? "XML file" : stream ? "Stream" : "Live stream"}</div>
      {xml ? (
        <>
          <p className="wn-note">Dump the board + live snapshot. Download locally or write conf/wire_graph.xml.</p>
          <div className="out-actions">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                window.dispatchEvent(new CustomEvent("wire-xml", { detail: "download" }));
              }}
            >
              Download XML
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                window.dispatchEvent(new CustomEvent("wire-xml", { detail: "save" }));
              }}
            >
              Save to conf
            </button>
          </div>
          {data.xmlStatus && <div className="wn-foot">{data.xmlStatus}</div>}
        </>
      ) : stream ? (
        <>
          <p className="wn-note">Live Telegram / Condor feed. Updates every few seconds.</p>
          <div className="log-list stream-list">
            {(data.lines || []).length === 0 && <div className="empty">no messages yet — send /status to the bot</div>}
            {(data.lines || []).map((l, i) => (
              <div key={i} className={`ll${l.bad ? " bad" : ""}`}>
                <span className="ln" style={{ color: l.color }}>{l.name}</span>
                <span className="lt">{l.text}</span>
              </div>
            ))}
          </div>
          <div className="wn-foot">{(data.lines || []).length} line{(data.lines || []).length === 1 ? "" : "s"}</div>
        </>
      ) : (
        <>
          <p className="wn-note">Wire hunters or LOGS in. Empty = all bots.</p>
          <div className="live-kpis">
            <span>pos {data.n_pos ?? "—"}</span>
            <span className={clsPnl(data.upnl_usd)}>{money(data.upnl_usd)}</span>
          </div>
          <div className="log-list">
            {(data.lines || []).length === 0 && <div className="empty">waiting…</div>}
            {(data.lines || []).map((l, i) => (
              <div key={i} className={`ll${l.bad ? " bad" : ""}`}>
                <span className="ln" style={{ color: l.color }}>{l.name}</span>
                <span className="lt">{l.text}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
