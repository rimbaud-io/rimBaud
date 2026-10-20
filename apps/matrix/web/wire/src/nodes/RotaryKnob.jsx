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

const START_DEG = -135;
const SWEEP_DEG = 270;

function displayRange(field) {
  if (field.min != null && field.max != null) return [Number(field.min), Number(field.max)];
  const k = String(field.key || "");
  if (field.kind === "pct_frac") return [0, 100];
  if (/bbp|bb_/.test(k)) return [0, 1.2];
  if (/wave/.test(k)) return [0, 2];
  if (/roe|sl_/.test(k)) return [1, 200];
  if (/lev/.test(k)) return [1, 50];
  if (/poll/.test(k)) return [2, 30];
  if (/refresh/.test(k)) return [10, 600];
  if (/books|positions/.test(k)) return [1, 12];
  return [0, 100];
}

function decimalsFor(step) {
  const s = String(step ?? 1);
  const i = s.indexOf(".");
  return i < 0 ? 0 : s.length - i - 1;
}

function snap(n, min, max, step) {
  const st = step > 0 ? step : 1;
  const v = Math.round(n / st) * st;
  const d = decimalsFor(st);
  const clipped = Math.min(max, Math.max(min, v));
  return Number(clipped.toFixed(d));
}

function formatVal(n, step) {
  if (n == null || n === "" || Number.isNaN(Number(n))) return "—";
  const d = decimalsFor(step);
  return String(Number(Number(n).toFixed(d)));
}

export default function RotaryKnob({
  field,
  value,
  color = "#9dffc0",
  changed = false,
  danger = false,
  disabled = false,
  onLive,
  onCommit,
}) {
  const [min, max] = displayRange(field || {});
  const step = Number(field?.step) > 0 ? Number(field.step) : 1;
  const num = value === "" || value == null ? min : Number(value);
  const safe = Number.isFinite(num) ? num : min;
  const t = max === min ? 0 : (safe - min) / (max - min);
  const deg = START_DEG + Math.min(1, Math.max(0, t)) * SWEEP_DEG;

  const drag = useRef(null);
  const dialRef = useRef(null);
  const liveRef = useRef(safe);
  const onLiveRef = useRef(onLive);
  const onCommitRef = useRef(onCommit);
  const [dragging, setDragging] = useState(false);
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    onLiveRef.current = onLive;
    onCommitRef.current = onCommit;
  }, [onLive, onCommit]);

  useEffect(() => {
    if (!dragging) liveRef.current = safe;
  }, [safe, dragging]);

  useEffect(() => {
    if (typing && inputRef.current) inputRef.current.focus();
  }, [typing]);

  useEffect(() => {
    const el = dialRef.current;
    if (!el) return undefined;
    const wheel = (e) => {
      if (disabled || typing) return;
      e.preventDefault();
      e.stopPropagation();
      const dir = e.deltaY < 0 ? 1 : -1;
      const mul = e.shiftKey ? 0.1 : 1;
      const v = snap(liveRef.current + dir * step * mul, min, max, step);
      liveRef.current = v;
      onLiveRef.current && onLiveRef.current(v);
      onCommitRef.current && onCommitRef.current(v);
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, [disabled, typing, step, min, max]);

  const apply = (next, commit) => {
    const v = snap(next, min, max, step);
    liveRef.current = v;
    onLiveRef.current && onLiveRef.current(v);
    if (commit) onCommitRef.current && onCommitRef.current(v);
  };

  const onPointerDown = (e) => {
    if (disabled || typing) return;
    if (e.button != null && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = {
      x: e.clientX,
      y: e.clientY,
      val: safe,
      fine: e.shiftKey,
    };
    setDragging(true);
  };

  const onPointerMove = (e) => {
    const d = drag.current;
    if (!d) return;
    e.preventDefault();
    // DJ / mixer: drag right = clockwise = up. Up also increases (Ableton).
    const px = e.shiftKey || d.fine ? 4.2 : 1.15;
    const delta = (e.clientX - d.x - (e.clientY - d.y)) / px;
    const span = max - min;
    // ~140px of drag covers the full range at normal sensitivity
    const next = d.val + (delta / 140) * span;
    apply(next, false);
  };

  const endDrag = (e) => {
    if (!drag.current) return;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
    drag.current = null;
    setDragging(false);
    const v = snap(liveRef.current, min, max, step);
    onCommitRef.current && onCommitRef.current(v);
  };

  const startType = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDraft(String(safe));
    setTyping(true);
  };

  const finishType = () => {
    setTyping(false);
    const n = Number(draft);
    if (!Number.isFinite(n)) return;
    apply(n, true);
  };

  const cls = [
    "rknob",
    changed ? "changed" : "",
    danger ? "closes" : "",
    disabled ? "off" : "",
    dragging ? "drag" : "",
  ].filter(Boolean).join(" ");

  const r = 15;
  const cx = 20;
  const cy = 20;
  const rad = (deg * Math.PI) / 180;
  const x2 = cx + Math.cos(rad) * (r - 3);
  const y2 = cy + Math.sin(rad) * (r - 3);
  const startRad = (START_DEG * Math.PI) / 180;
  const arc = describeArc(cx, cy, r - 1.5, START_DEG, deg);

  return (
    <div className={cls} style={{ "--knob": color }} title={field?.label || ""}>
      <div
        ref={dialRef}
        className="rknob-dial nodrag nowheel nopan"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onDoubleClick={startType}
        role="slider"
        aria-label={field?.label || "knob"}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={safe}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight" || e.key === "ArrowUp") {
            e.preventDefault();
            apply(safe + step * (e.shiftKey ? 0.1 : 1), true);
          } else if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
            e.preventDefault();
            apply(safe - step * (e.shiftKey ? 0.1 : 1), true);
          }
        }}
      >
        <svg viewBox="0 0 40 40" aria-hidden="true">
          <circle className="rknob-ring" cx={cx} cy={cy} r={r} />
          <path className="rknob-track" d={describeArc(cx, cy, r - 1.5, START_DEG, START_DEG + SWEEP_DEG)} />
          {t > 0.004 ? <path className="rknob-arc" d={arc} /> : null}
          <circle className="rknob-cap" cx={cx} cy={cy} r={r - 4.5} />
          <line className="rknob-tick" x1={cx} y1={cy} x2={x2} y2={y2} />
          <circle className="rknob-dot" cx={cx + Math.cos(startRad) * (r - 1.5)} cy={cy + Math.sin(startRad) * (r - 1.5)} r="1.1" />
        </svg>
      </div>
      {typing ? (
        <input
          ref={inputRef}
          className="rknob-edit nodrag nowheel"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={finishType}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              finishType();
            }
            if (e.key === "Escape") setTyping(false);
          }}
          onPointerDown={(e) => e.stopPropagation()}
        />
      ) : (
        <button type="button" className="rknob-val" onDoubleClick={startType}>
          {formatVal(safe, step)}
        </button>
      )}
      <span className="rknob-label">{field?.label}</span>
    </div>
  );
}

function polar(cx, cy, r, deg) {
  const rad = (deg * Math.PI) / 180;
  return [cx + Math.cos(rad) * r, cy + Math.sin(rad) * r];
}

function describeArc(cx, cy, r, startDeg, endDeg) {
  const [sx, sy] = polar(cx, cy, r, startDeg);
  const [ex, ey] = polar(cx, cy, r, endDeg);
  const large = endDeg - startDeg > 180 ? 1 : 0;
  return `M ${sx} ${sy} A ${r} ${r} 0 ${large} 1 ${ex} ${ey}`;
}
