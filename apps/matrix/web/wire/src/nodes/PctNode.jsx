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

import { useEffect, useRef, useState } from "react";
import { Position } from "@xyflow/react";
import { emitWire } from "../lib/tools.js";
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";
import RotaryKnob from "./RotaryKnob.jsx";

export default function PctNode({ id, data, selected }) {
  const kind = data.kind === "moon" ? "moon" : "sl";
  const color = kind === "moon" ? "#ffd24a" : "#ff4d6d";
  const label = kind === "moon" ? "MOON" : "STOP LOSS";
  const hint = kind === "moon"
    ? "% up from live coin price to exit the long"
    : "% ROE to exit (Home SL)";
  const [pct, setPct] = useState(
    data.pct != null && data.pct !== "" ? String(data.pct) : kind === "moon" ? "50" : "20"
  );
  const focusRef = useRef(false);

  useEffect(() => {
    if (focusRef.current) return;
    if (data.pct != null && data.pct !== "") setPct(String(data.pct));
  }, [data.pct]);

  const commit = (raw) => {
    const n = Number(raw);
    if (isNaN(n) || n < 1 || n > 500) return;
    const v = Math.round(n * 100) / 100;
    setPct(String(v));
    emitWire("pct", { nodeId: id, kind, pct: v });
  };

  return (
    <div className={`wn pct ${kind}${selected ? " selected" : ""}${data.dirty ? " dirty" : ""}`} style={{ "--bot": color }}>
      <PortHandle type="source" position={Position.Right} id={kind} className={kind === "moon" ? "h-moon" : "h-sl"} tip={kind === "moon" ? PORT_TIP.moonOut : PORT_TIP.slOut} />
      <span className="port right" style={{ color, top: "46%" }} data-tip={kind === "moon" ? PORT_TIP.moonOut : PORT_TIP.slOut}>{kind.toUpperCase()}</span>
      <div className="wn-kicker">SET · {label}</div>
      <div className="wn-title">
        <span className="name" style={{ color }}>{label}</span>
      </div>
      <p className="wn-note">{hint}</p>
      <div className="pct-row nodrag nopan nowheel" onPointerDown={(e) => e.stopPropagation()}>
        <RotaryKnob
          field={{ key: "pct", label: "%", kind: "num", min: 1, max: 500, step: 1 }}
          value={pct}
          color={color}
          changed={!!data.dirty}
          onLive={(v) => {
            focusRef.current = true;
            setPct(String(v));
          }}
          onCommit={(v) => {
            focusRef.current = false;
            commit(v);
          }}
        />
      </div>
    </div>
  );
}
