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

export function money(n, d) {
  if (n === null || n === undefined || n === "" || Number.isNaN(Number(n))) return "—";
  const x = Number(n);
  const sign = x < 0 ? "-" : "";
  return (
    sign +
    "$" +
    Math.abs(x).toLocaleString(undefined, {
      minimumFractionDigits: d == null ? 2 : d,
      maximumFractionDigits: d == null ? 2 : d,
    })
  );
}

export function pct(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  const x = Number(n);
  const sign = x > 0 ? "+" : "";
  return sign + x.toFixed(2) + "%";
}

export function shortAddr(a) {
  if (!a) return "—";
  const s = String(a);
  if (s.length < 14) return s;
  return s.slice(0, 8) + "…" + s.slice(-6);
}

export function clsPnl(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "";
  return Number(n) >= 0 ? "pos" : "neg";
}

export function fmtSecs(s) {
  if (s == null || Number.isNaN(Number(s))) return "—";
  const n = Math.max(0, Number(s));
  if (n < 60) return n + "s";
  const m = Math.floor(n / 60);
  const r = n % 60;
  if (m < 60) return m + "m " + r + "s";
  return Math.floor(m / 60) + "h " + (m % 60) + "m";
}
