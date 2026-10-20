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

export default function LogsNode({ data, selected }) {
  const lines = data.lines || [];
  return (
    <div className={`wn logs${selected ? " selected" : ""}`}>
      <PortHandle type="target" position={Position.Left} id="in" className="h-logs" tip={PORT_TIP.logsIn} />
      <span className="port left" data-tip={PORT_TIP.logsIn}>LOG</span>
      <div className="wn-kicker">OUT · LOGS</div>
      <div className="wn-head">Live tail</div>
      <div className="log-list">
        {lines.length === 0 && <div className="empty">waiting for logs…</div>}
        {lines.map((l, i) => (
          <div key={i} className={`ll${l.bad ? " bad" : ""}`}>
            <span className="ln" style={{ color: l.color }}>
              {l.name}
            </span>
            <span className="lt">{l.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
