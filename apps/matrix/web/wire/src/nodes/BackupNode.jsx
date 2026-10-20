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
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";

export default function BackupNode({ data, selected }) {
  return (
    <div className={`wn backup${selected ? " selected" : ""}`}>
      <PortHandle type="source" position={Position.Right} id="alert" className="h-eclose" tip={PORT_TIP.healthAlert} />
      <span className="port right logs" style={{ color: "#ff4d6d" }} data-tip={PORT_TIP.healthAlert}>ALERT</span>
      <div className="wn-kicker">SYS · TWIN</div>
      <div className="wn-title">
        <span className="icon">⧉</span>
        <span className="name">{data.name || "1:1 backup"}</span>
      </div>
      <p className="wn-note">
        Later: watch a second machine running the same Freiquell desk. Alert if it goes dark or drifts.
        Not wired yet — drop it so the Health canvas has a place for it.
      </p>
      <div className="wn-foot">placeholder · no probe</div>
    </div>
  );
}
