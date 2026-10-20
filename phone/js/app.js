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

(() => {
  const $ = (s) => document.querySelector(s);
  const seatsEl = $("#seats");
  const toastEl = $("#toast");
  const metaEl = $("#meta-line");
  let apiBase = localStorage.getItem("rb_api") || "";
  let token = localStorage.getItem("rb_token") || "";
  let pollMs = 4000;
  let timer = 0;

  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    setTimeout(() => toastEl.classList.remove("show"), 2800);
  }

  function money(n) {
    const x = Number(n) || 0;
    const s = Math.abs(x).toFixed(2);
    return (x < 0 ? "-$" : "$") + s;
  }

  function shortAddr(a) {
    a = String(a || "");
    if (a.length < 12) return a;
    return a.slice(0, 6) + "…" + a.slice(-4);
  }

  function headers() {
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = "Bearer " + token;
    return h;
  }

  function url(path) {
    const b = (apiBase || "").replace(/\/$/, "");
    return b + path;
  }

  async function loadConfig() {
    try {
      const r = await fetch("config.json", { cache: "no-store" });
      if (r.ok) {
        const c = await r.json();
        if (!apiBase && c.apiBase) apiBase = c.apiBase;
        if (!token && c.token) token = c.token;
        if (c.pollMs) pollMs = c.pollMs;
      }
    } catch (_) {}
    $("#apiBase").value = apiBase;
    $("#token").value = token;
  }

  function saveSettings() {
    apiBase = $("#apiBase").value.trim().replace(/\/$/, "");
    token = $("#token").value.trim();
    localStorage.setItem("rb_api", apiBase);
    localStorage.setItem("rb_token", token);
    toast("saved endpoint");
    refresh();
  }

  function render(data) {
    const seats = (data && data.seats) || [];
    metaEl.textContent =
      "matrices  " + (data.n_up || 0) + "/" + (data.n || seats.length) +
      " connected    GET only    never CLOSE/OPEN";
    if (!seats.length) {
      seatsEl.innerHTML = '<div class="card down"><div class="err">no seats — set API base in Settings</div></div>';
      return;
    }
    seatsEl.innerHTML = seats.map((s) => {
      const down = !s.ok;
      const upnl = Number(s.upnl) || 0;
      const copy = s.copy ? shortAddr(s.copy) : "";
      return `<article class="card${down ? " down" : ""}">
        <div class="row">
          <span class="lamp"></span>
          <span class="name">${escapeHtml(s.name || s.id)}</span>
          <span class="host">${escapeHtml((s.host || "") + ":" + (s.port || ""))}</span>
          <span class="role">${escapeHtml(s.role || "")}</span>
        </div>
        <div class="metrics">
          ${down ? "" : `eq ${money(s.eq)} &nbsp; uPnL <span class="${upnl>=0?"up":"dn"}">${money(s.upnl)}</span>
          &nbsp; pos ${s.n_pos} &nbsp; hunters ${s.n_live}/${s.n_bots} &nbsp; BIAS ${escapeHtml(s.bias || "-")}<br>
          <span class="gold">HL leftover ${Math.round(s.ip_left)} / ${Math.round(s.ip_cap)}</span>
          ${copy ? "<br>Copy " + escapeHtml(copy) : ""}`}
        </div>
        ${down ? `<div class="err">${escapeHtml(s.error || "down")} — USB seat until join, or UFW 5004 on wg-rimbaud</div>` : ""}
      </article>`;
    }).join("");
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  async function refresh() {
    try {
      const r = await fetch((apiBase ? apiBase : "") + "/api/fleet", {
        headers: headers(),
        cache: "no-store",
      });
      const d = await r.json();
      render(d);
    } catch (e) {
      metaEl.textContent = "gateway unreachable — set static IP in Settings";
    }
  }

  async function sendBias(bias) {
    if (!confirm("Desk BIAS " + bias + "?\nDoes NOT close positions.")) return;
    try {
      const r = await fetch((apiBase || "") + "/api/bias", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ bias, closes: false }),
      });
      const d = await r.json();
      toast(d.ok ? ("BIAS " + bias + " · clips untouched") : (d.error || "bias failed"));
      refresh();
    } catch (e) {
      toast("bias failed — gateway?");
    }
  }

  $("#save").addEventListener("click", saveSettings);
  document.querySelectorAll(".panic button").forEach((b) => {
    b.addEventListener("click", () => sendBias(b.dataset.bias));
  });

  loadConfig().then(() => {
    refresh();
    timer = setInterval(refresh, pollMs);
  });
})();
