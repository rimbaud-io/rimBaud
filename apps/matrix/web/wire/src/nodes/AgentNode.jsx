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

import { useEffect, useRef, useState } from "react";
import { Position } from "@xyflow/react";
import { PORT_TIP } from "../lib/ports.js";
import PortHandle from "./PortHandle.jsx";
import { emitWire } from "../lib/tools.js";

function mask(v) {
  const s = String(v || "");
  if (!s) return "";
  if (s.length < 8) return "••••";
  return s.slice(0, 4) + "••••" + s.slice(-4);
}

export default function AgentNode({ id, data, selected }) {
  const kind = data.kind === "grok" ? "grok" : data.kind === "email" ? "email" : "telegram";
  const color = data.color || (kind === "grok" ? "#2A9FD6" : kind === "email" ? "#ffc857" : "#34d399");
  const handleId = kind === "grok" ? "grok" : kind === "email" ? "out" : "tg";
  const tip = kind === "grok" ? PORT_TIP.grokOut : PORT_TIP.streamOut;
  const [name, setName] = useState(data.name || (kind === "grok" ? "Grok bot" : "Telegram bot"));
  const [secret, setSecret] = useState("");
  const [extra, setExtra] = useState(kind === "grok" ? (data.model || "grok-4.5") : (data.chat_ids || ""));
  const focusRef = useRef(false);
  const hasSecret = !!(data.has_token || data.has_key || data.token || data.api_key);

  useEffect(() => {
    if (focusRef.current) return;
    setName(data.name || (kind === "grok" ? "Grok bot" : kind === "email" ? "Email alerts" : "Telegram bot"));
    if (kind === "email") setExtra(data.to || "");
    else if (kind === "grok") setExtra(data.model || "grok-4.5");
    else setExtra(data.chat_ids || "");
  }, [data.name, data.chat_ids, data.model, data.to, kind]);

  const commit = (patch) => {
    emitWire("agent", { nodeId: id, botKind: kind, ...patch });
  };

  return (
    <div className={`wn agent ${kind}${selected ? " selected" : ""}`} style={{ "--bot": color }}>
      {(kind === "telegram" || kind === "email") ? (
        <>
          <PortHandle type="target" position={Position.Left} id="in" className="h-eclose" tip={PORT_TIP.agentIn} />
          <span className="port left" style={{ color: "#ff4d6d" }} data-tip={PORT_TIP.agentIn}>IN</span>
        </>
      ) : null}
      <PortHandle type="source" position={Position.Top} id={handleId} className={kind === "grok" ? "h-grok" : "h-tg"} tip={tip} />
      <span className="port top gauge" style={{ color }} data-tip={tip}>{kind === "grok" ? "GROK" : kind === "email" ? "MAIL" : "TG"}</span>
      <PortHandle type="source" position={Position.Right} id="out" className="h-tg" tip={PORT_TIP.streamOut} />
      <span className="port right" style={{ color }} data-tip={PORT_TIP.streamOut}>OUT</span>
      <div className="wn-kicker">{kind === "email" ? "MAIL · ALERTS" : kind === "grok" ? "IN · CONDOR" : "TG · OUT"}</div>
      <div className="wn-title">
        <span className="icon">{kind === "grok" ? "✦" : kind === "email" ? "✉" : "✈"}</span>
        <span className="name" style={{ color }}>{name || (kind === "grok" ? "Grok bot" : kind === "email" ? "Email alerts" : "Telegram bot")}</span>
      </div>
      <p className="wn-note">
        {kind === "email"
          ? "Health ALERT → here. SMTP send comes later — saved on this canvas only."
          : kind === "grok"
            ? "Wire into Condor. Drives bias when connected."
            : "Hunter LOG → IN posts OPEN/CLOSE. TG OUT → Condor / Translator / Desk BIAS."}
      </p>
      <div className="ctrl-fields nodrag nopan" onPointerDown={(e) => e.stopPropagation()}>
        <label>
          Name
          <input
            value={name}
            onFocus={() => { focusRef.current = true; }}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => { focusRef.current = false; commit({ name }); }}
          />
        </label>
        {kind !== "email" ? (
        <label>
          {kind === "grok" ? "xAI API key" : "Bot token"}
          <input
            type="password"
            autoComplete="off"
            placeholder={hasSecret ? mask(data.token || data.api_key || "saved") : "paste…"}
            value={secret}
            onFocus={() => { focusRef.current = true; }}
            onChange={(e) => setSecret(e.target.value)}
            onBlur={() => {
              focusRef.current = false;
              if (secret.trim()) {
                commit(kind === "grok" ? { api_key: secret.trim() } : { token: secret.trim() });
                setSecret("");
              }
            }}
          />
        </label>
        ) : null}
        {kind === "grok" ? (
          <label>
            Model
            <select
              value={extra}
              onChange={(e) => {
                setExtra(e.target.value);
                commit({ model: e.target.value });
              }}
            >
              <option value="grok-4.5">grok-4.5</option>
              <option value="grok-4.6">grok-4.6</option>
              <option value="grok-4-fast-non-reasoning">grok-4-fast</option>
            </select>
          </label>
        ) : (
          <label>
            {kind === "email" ? "To" : "Chat ids"}
            <input
              placeholder={kind === "email" ? "ops@…" : "123456789, 987…"}
              value={extra}
              onFocus={() => { focusRef.current = true; }}
              onChange={(e) => setExtra(e.target.value)}
              onBlur={() => { focusRef.current = false; commit(kind === "email" ? { to: extra } : { chat_ids: extra }); }}
            />
          </label>
        )}
      </div>
      {(data.lines || []).length ? (
        <div className="log-list stream-list nodrag nopan">
          {data.lines.slice(-6).map((l, i) => (
            <div key={i} className="ll">
              <span className="ln" style={{ color: l.color }}>{l.name}</span>
              <span className="lt">{l.text}</span>
            </div>
          ))}
        </div>
      ) : null}
      <div className="wn-foot">
        {kind === "email"
          ? (extra ? "to saved · SMTP later" : "set To · SMTP later")
          : hasSecret ? "saved · OUT → Stream / Translator" : "paste key · then wire"}
      </div>
    </div>
  );
}
