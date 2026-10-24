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

import { Handle, useNodeId } from "@xyflow/react";

export default function PortHandle({ tip, ...rest }) {
  const nodeId = useNodeId();
  const full = tip
    ? tip + " Double-click lifts a wire already on this point."
    : "Double-click lifts a wire already on this point.";
  return (
    <Handle
      {...rest}
      data-tip={full}
      aria-label={full}
      onDoubleClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        window.dispatchEvent(new CustomEvent("wire-lift", {
          detail: {
            nodeId,
            handleId: rest.id == null ? null : String(rest.id),
            type: rest.type || "source",
            clientX: e.clientX,
            clientY: e.clientY,
          },
        }));
      }}
    />
  );
}
