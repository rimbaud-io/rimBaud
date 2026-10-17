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

export default function HomeBandNode({ data }) {
  const band = data.band || data.kind || "mid";
  const title = band === "top" ? "TOP" : band === "bot" ? "BOTTOM" : "MIDDLE";
  const w = Math.max(120, Number(data.w) || 1040);
  const h = Math.max(80, Number(data.h) || 160);
  return (
    <div className={`home-band-node ${band}`} style={{ width: w, height: h, minWidth: w, minHeight: h }}>
      <i className="hb-line hb-t" />
      <i className="hb-line hb-b" />
      <i className="hb-line hb-l" />
      <i className="hb-line hb-r" />
      <span>{title}</span>
    </div>
  );
}
