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
import RotaryKnob from "./RotaryKnob.jsx";
import { emitWire } from "../lib/tools.js";

export const HEALTH_FIELDS = {
  host: [
    { key: "max_cpu", label: "CPU max %", min: 10, max: 100, step: 1 },
    { key: "max_mem", label: "RAM max %", min: 10, max: 100, step: 1 },
    { key: "max_disk", label: "Disk max %", min: 10, max: 100, step: 1 },
  ],
  net: [
    { key: "max_ms", label: "Max ms", min: 50, max: 5000, step: 50 },
  ],
  hlip: [
    { key: "max_pct", label: "Token %", min: 10, max: 100, step: 1 },
    { key: "max_used", label: "Used w", min: 100, max: 1200, step: 20 },
  ],
};

export const HEALTH_DEFAULTS = {
  host: { max_cpu: 85, max_mem: 90, max_disk: 92 },
  net: { max_ms: 800 },
  hlip: { max_pct: 85, max_used: 1000 },
};

function barPct(n) {
  const x = Number(n);
  if (!Number.isFinite(x)) return 0;
  return Math.max(0, Math.min(100, x));
}

function Meter({ label, value, unit, max, hot }) {
  const pct = barPct(value);
  return (
    <div className={`health-meter${hot ? " hot" : ""}`}>
      <div className="hm-row">
        <span>{label}</span>
        <span className="hm-val">{value == null || Number.isNaN(Number(value)) ? "—" : `${value}${unit || ""}`}</span>
        <span className="hm-max">max {max}</span>
      </div>
      <div className="hm-bar"><i style={{ width: pct.toFixed(1) + "%" }} /></div>
    </div>
  );
}

export default function HealthNode({ id, data, selected }) {
  const kind = data.kind || "host";
  const fields = HEALTH_FIELDS[kind] || HEALTH_FIELDS.host;
  const defaults = HEALTH_DEFAULTS[kind] || HEALTH_DEFAULTS.host;
  const max = { ...defaults, ...(data.max || {}) };
  const live = data.live || {};
  const alerts = data.alerts || [];
  const color = data.color || (kind === "hlip" ? "#f0b90b" : kind === "net" ? "#7ecbff" : "#9dffc0");
  const [local, setLocal] = useState(max);

  useEffect(() => {
    setLocal({ ...defaults, ...(data.max || {}) });
  }, [id, JSON.stringify(data.max)]);

  const commit = (key, value) => {
    emitWire("health", { nodeId: id, key, value });
  };

  return (
    <div
      className={`wn health${selected ? " selected" : ""}${alerts.length ? " hot" : ""}`}
      style={{ "--bot": color }}
    >
      <PortHandle type="source" position={Position.Right} id="live" className="h-logs" style={{ top: "30%" }} tip={PORT_TIP.healthLive} />
      <PortHandle type="source" position={Position.Right} id="alert" className="h-eclose" style={{ top: "68%" }} tip={PORT_TIP.healthAlert} />
      <span className="port right income" style={{ color: "#7ecbff" }} data-tip={PORT_TIP.healthLive}>LIVE</span>
      <span className="port right logs" style={{ color: "#ff4d6d" }} data-tip={PORT_TIP.healthAlert}>ALERT</span>
      <div className="wn-kicker">SYS · {kind === "hlip" ? "HL TOKENS" : kind === "net" ? "INTERNET" : "HOST"}</div>
      <div className="wn-title">
        <span className="icon">{data.icon || (kind === "hlip" ? "◈" : kind === "net" ? "⇄" : "▣")}</span>
        <span className="name" style={{ color }}>{data.name || "Health"}</span>
      </div>
      <p className="wn-note">
        {kind === "hlip"
          ? "Live HL REST weight this IP. Alert if used hits the max."
          : kind === "net"
            ? "Probe 1.1.1.1:443. Does not spend HL tokens. Alert if down or slower than max ms."
            : "CPU / RAM / disk on this machine. Alert if a bar hits its max."}
      </p>
      {kind === "host" ? (
        <>
          <Meter label="CPU" value={live.cpu_pct} unit="%" max={max.max_cpu} hot={alerts.some((a) => a.key === "cpu")} />
          <Meter label="RAM" value={live.mem_pct} unit="%" max={max.max_mem} hot={alerts.some((a) => a.key === "mem")} />
          <Meter label="Disk" value={live.disk_pct} unit="%" max={max.max_disk} hot={alerts.some((a) => a.key === "disk")} />
        </>
      ) : null}
      {kind === "net" ? (
        <Meter
          label={live.ok ? "up" : "down"}
          value={live.ok ? live.ms : 0}
          unit={live.ok ? " ms" : ""}
          max={max.max_ms}
          hot={alerts.length > 0}
        />
      ) : null}
      {kind === "hlip" ? (
        <>
          <Meter label="used" value={live.pct} unit="%" max={max.max_pct} hot={alerts.some((a) => a.key === "pct")} />
          <Meter label="weight" value={live.used} unit="" max={max.max_used} hot={alerts.some((a) => a.key === "used")} />
        </>
      ) : null}
      <div className="health-knobs nodrag nopan" onPointerDown={(e) => e.stopPropagation()}>
        {fields.map((f) => (
          <RotaryKnob
            key={f.key}
            field={f}
            value={local[f.key]}
            color={color}
            danger={alerts.some((a) => f.key.includes(a.key))}
            onLive={(v) => setLocal((cur) => ({ ...cur, [f.key]: v }))}
            onCommit={(v) => {
              setLocal((cur) => ({ ...cur, [f.key]: v }));
              commit(f.key, v);
            }}
          />
        ))}
      </div>
      <div className="wn-foot">
        {alerts.length
          ? alerts.map((a) => a.text).join(" · ")
          : (data.ts ? "ok · " + String(data.ts).slice(11, 19) : "waiting first sample")}
      </div>
    </div>
  );
}
