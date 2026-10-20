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
import { emitWire } from "../lib/tools.js";
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";

const CMDS = [
  { id: "LONG", cls: "long" },
  { id: "SHORT", cls: "short" },
  { id: "FLAT", cls: "flat" },
  { id: "CLOSE", cls: "close" },
  { id: "OPEN100%", cls: "open" },
];

export default function TranslatorNode({ id, data, selected }) {
  const closer = data.kind === "closer";
  const color = data.color || (closer ? "#ff4d6d" : "#34d399");
  const cmd = String(data.cmd || "").toUpperCase();
  const send = (c) => {
    emitWire("xlate", { nodeId: id, cmd: c });
  };
  const btns = closer ? [{ id: "CLOSE", cls: "close" }] : CMDS;
  return (
    <div className={`wn xlate${closer ? " closer" : ""}${selected ? " selected" : ""}`} style={{ "--bot": color }}>
      <PortHandle type="target" position={Position.Bottom} id="tg" className="h-tg" tip={closer ? PORT_TIP.closerIn : PORT_TIP.xlateIn} />
      {closer ? (
        <>
          <PortHandle type="source" position={Position.Top} id="eclose" className="h-eclose" tip={PORT_TIP.closerOut} />
          <span className="port top gauge" style={{ color: "#ff4d6d" }} data-tip={PORT_TIP.closerOut}>E-CLOSE</span>
        </>
      ) : (
        <>
          <PortHandle type="source" position={Position.Top} id="bias" className="h-bias" tip={PORT_TIP.xlateOut} />
          <span className="port top gauge" style={{ color: "var(--amber)" }} data-tip={PORT_TIP.xlateOut}>BIAS</span>
        </>
      )}
      <span className="port bot" style={{ color: closer ? "#ff4d6d" : "#34d399" }} data-tip={closer ? PORT_TIP.closerIn : PORT_TIP.xlateIn}>TG</span>
      <div className="wn-kicker">{closer ? "IN · CLOSE ONLY" : "IN · TRANSLATOR"}</div>
      <div className="wn-title">
        <span className="icon">{closer ? "⏹" : "⇄"}</span>
        <span className="name" style={{ color }}>{data.name || (closer ? "Close translator" : "Translator")}</span>
      </div>
      <p className="wn-note">
        {closer
          ? "Dedicated Telegram → E-CLOSE. Ignores LONG SHORT FLAT OPEN100%. Idle does nothing. CLOSE flattens wired hunters and sets their BIAS to FLAT."
          : "Telegram words → LONG SHORT FLAT CLOSE OPEN100%. Wire BIAS into one hunter."}
      </p>
      <div className="xlate-now">{cmd || "—"}</div>
      <div className="bias-btns xlate-btns nodrag nopan" onPointerDown={(e) => e.stopPropagation()}>
        {btns.map((c) => (
          <button
            key={c.id}
            type="button"
            className={`bias-btn ${c.cls}${cmd === c.id || (c.id === "OPEN100%" && cmd === "OPEN100") ? " on" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              send(c.id);
            }}
          >
            {c.id}
          </button>
        ))}
      </div>
      <div className="wn-foot">
        {data.last_raw ? data.last_raw : (closer ? "waiting · no flatten until CLOSE" : "waiting for Telegram")}
        {data.last_ts ? " · " + String(data.last_ts).slice(11, 19) : ""}
      </div>
    </div>
  );
}
