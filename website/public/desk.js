/* Matrix board for the public test account. Wires the standard DMan Maker
   chain and plays it on live BTC prints. Nothing here reaches the desk. */
(function () {
  var D = window.RimbaudDman;
  var book = null;
  var running = false;
  var runToken = 0;
  var phase = "idle";
  var note = "test account ready · LONG armed · press Run live";
  var livePrice = 0;
  var source = "";

  var CATS = [
    { id: "hummingbots", label: "Hummingbots", open: true, items: [
      { name: "HL Copy", ico: "⚡", color: "#4f8cff", hint: "multi" },
      { name: "PMM Simple", ico: "▤", color: "#f0b90b", hint: "BTC / ETH / LTC" },
      { name: "Helliot", ico: "∿", color: "#7ecbff", hint: "BTC / ETH / LTC" },
      { name: "SuperTrend", ico: "≈", color: "#2ee6c7", hint: "BTC / ETH / LTC / TAO" },
      { name: "Shorty", ico: "▾", color: "#ff5d73", hint: "BTC / ETH / LTC" },
      { name: "Dman", ico: "▦", color: "#c084fc", hint: "BTC / ETH / LTC" },
      { name: "Only Up", ico: "▲", color: "#ffd166", hint: "BTC / LTC" },
      { name: "DMan Maker", ico: "⧉", color: "#a78bfa", hint: "BTC / ETH / LTC", board: "n-bot" }
    ]},
    { id: "coins", label: "Coins", open: true, items: [
      { name: "BTC", ico: "●", color: "#f7931a", hint: "wired", board: "n-btc" },
      { name: "ETH", ico: "●", color: "#627eea", hint: "desk coin" },
      { name: "LTC", ico: "●", color: "#345d9d", hint: "desk coin" },
      { name: "TAO", ico: "●", color: "#e8e8e8", hint: "desk coin" }
    ]},
    { id: "wallets", label: "Wallets", open: true, items: [
      { name: "Test account", ico: "◻", color: "#7ecbff", hint: "$1,000 paper", board: "n-acct" }
    ]},
    { id: "settings", label: "Settings", open: false, items: [
      { name: "Controller", ico: "⚙", color: "#a78bfa", hint: "DMan Maker", board: "n-ctrl" }
    ]},
    { id: "bias", label: "BIAS", open: true, items: [
      { name: "DMan Maker BIAS", ico: "◈", color: "#ffc857", hint: "LONG", board: "n-bias" }
    ]},
    { id: "gauges", label: "Gauges", open: false, items: [
      { name: "Condor gauge", ico: "◔", color: "#7ecbff", hint: "desk" },
      { name: "HMM gauge", ico: "◔", color: "#2ee6c7", hint: "desk" }
    ]},
    { id: "strategy", label: "Strategy", open: true, items: [
      { name: "SL", ico: "■", color: "#ff4d6d", hint: "−20 ROE", board: "n-sl" }
    ]},
    { id: "condor", label: "Condor", open: false, items: [
      { name: "Condor", ico: "◇", color: "#ffc857", hint: "desk" }
    ]},
    { id: "traders", label: "Traders", open: false, items: [
      { name: "Copy leaders", ico: "◈", color: "#7ecbff", hint: "wire into Copy" }
    ]},
    { id: "system", label: "System health", open: false, items: [
      { name: "Feed", ico: "♥", color: "#00ff6a", hint: "public BTC" }
    ]},
    { id: "output", label: "Output", open: true, items: [
      { name: "Book", ico: "≡", color: "#9dffc0", hint: "funds and ROE", board: "k-roe" }
    ]}
  ];

  function money(n, signed) {
    if (n == null || !isFinite(n)) return "—";
    var neg = n < -0.004;
    var body = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (signed) return (neg ? "−$" : "+$") + body;
    return (neg ? "−$" : "$") + body;
  }

  function roeText(r) {
    if (r == null || !isFinite(r)) return "—";
    return (r >= 0 ? "+" : "−") + Math.abs(r).toFixed(2) + "%";
  }

  function pxText(n) {
    if (!(n > 0)) return "—";
    return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function qtyText(n) {
    if (!(n > 0)) return "—";
    return n.toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 5 });
  }

  function paintClass(el, n) {
    el.classList.remove("up", "dn");
    if (n > 0.004) el.classList.add("up");
    else if (n < -0.004) el.classList.add("dn");
  }

  function num(name) {
    var el = document.querySelector('[data-field="' + name + '"]');
    var n = el ? parseFloat(el.value) : NaN;
    return n;
  }

  function readConfig() {
    var biasBtn = document.querySelector(".bias button.on");
    return {
      leverage: num("leverage"),
      marginPct: num("margin") / 100,
      takeProfit: num("tp") / 100,
      trailActivation: num("trailArm") / 100,
      trailDelta: num("trailDelta") / 100,
      slRoePct: num("sl"),
      refreshSec: num("refresh"),
      dontGoCrazy: document.querySelector('[data-field="crazy"]').classList.contains("on"),
      afterLossNoRebuy: document.querySelector('[data-field="rebuy"]').classList.contains("on"),
      bias: biasBtn ? biasBtn.getAttribute("data-bias") : "LONG",
      startEquity: 1000
    };
  }

  function view() {
    if (book) return D.snapshot(book);
    var cfg = D.normalize(readConfig());
    return {
      equity: cfg.startEquity,
      free: cfg.startEquity,
      used: 0,
      open: 0,
      realized: 0,
      roe: null,
      side: null,
      entry: 0,
      mark: livePrice || 0,
      qty: 0,
      leverage: cfg.leverage,
      trades: [],
      bias: cfg.bias,
      curve: [{ equity: cfg.startEquity, px: 0, roe: null }],
      status: note
    };
  }

  function linePath(values, w, h) {
    if (values.length < 2) return "";
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var span = (max - min) || 1;
    return values.map(function (v, i) {
      var x = (i / (values.length - 1)) * w;
      var y = h - ((v - min) / span) * (h - 8) - 4;
      return (i ? "L" : "M") + x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
  }

  function paintChart(s) {
    var svg = document.getElementById("chart");
    var pts = [];
    (s.curve || []).forEach(function (p) { if (p.px > 0) pts.push(p.px); });
    svg.textContent = "";
    if (pts.length < 2) return;
    var all = pts.slice();
    if (s.entry > 0) all.push(s.entry);
    var min = Math.min.apply(null, all);
    var max = Math.max.apply(null, all);
    var span = (max - min) || 1;
    var d = pts.map(function (v, i) {
      var x = (i / (pts.length - 1)) * 360;
      var y = 120 - ((v - min) / span) * 112 - 4;
      return (i ? "L" : "M") + x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    if (s.entry > 0) {
      var yLine = 120 - ((s.entry - min) / span) * 112 - 4;
      var line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", "0");
      line.setAttribute("x2", "360");
      line.setAttribute("y1", yLine.toFixed(1));
      line.setAttribute("y2", yLine.toFixed(1));
      line.setAttribute("stroke", "#a78bfa");
      line.setAttribute("stroke-dasharray", "3 3");
      svg.appendChild(line);
    }
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "#00ff6a");
    path.setAttribute("stroke-width", "1.6");
    svg.appendChild(path);
  }

  function paintSpark(s) {
    var host = document.getElementById("spark");
    var values = (s.curve || []).map(function (p) { return p.equity; }).filter(function (n) { return isFinite(n); });
    var d = linePath(values, 160, 36);
    host.textContent = "";
    if (!d) return;
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 160 36");
    svg.setAttribute("width", "160");
    svg.setAttribute("height", "36");
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", s.equity >= 1000 ? "#00ff6a" : "#ff4d6d");
    path.setAttribute("stroke-width", "1.5");
    svg.appendChild(path);
    host.appendChild(svg);
  }

  function paint() {
    var s = view();
    var sl = num("sl");
    var tp = num("tp");
    document.getElementById("sl-big").textContent = "−" + (isFinite(sl) ? sl : 20) + " ROE";
    document.getElementById("k-tpsl").textContent = "+" + (isFinite(tp) ? tp : 2) + "% / −" + (isFinite(sl) ? sl : 20);
    document.getElementById("k-eq").textContent = money(s.equity);
    document.getElementById("k-free").textContent = money(s.free);
    document.getElementById("k-used").textContent = money(s.used);
    var real = document.getElementById("k-real");
    var up = document.getElementById("k-upnl");
    real.textContent = money(s.realized, true);
    up.textContent = money(s.open, true);
    paintClass(real, s.realized);
    paintClass(up, s.open);
    var last = s.trades && s.trades.length ? s.trades[s.trades.length - 1] : null;
    var shown = s.roe != null ? s.roe : (last ? last.roe : null);
    var kroe = document.getElementById("k-roe");
    kroe.textContent = roeText(shown);
    paintClass(kroe, shown || 0);
    if (shown == null) kroe.classList.remove("up", "dn");
    document.querySelector(".roe-kpi .lbl").textContent = s.side || !last ? "ROE" : "Last ROE";
    document.getElementById("k-bias").textContent = s.side || s.bias || "FLAT";
    document.getElementById("status").textContent = book ? (book.status || note) : note;

    document.getElementById("ac-eq").textContent = money(s.equity);
    document.getElementById("ac-free").textContent = money(s.free);
    document.getElementById("ac-used").textContent = money(s.used);
    var ar = document.getElementById("ac-real");
    var au = document.getElementById("ac-upnl");
    ar.textContent = money(s.realized, true);
    au.textContent = money(s.open, true);
    paintClass(ar, s.realized);
    paintClass(au, s.open);

    var side = document.getElementById("bot-side");
    if (s.side) side.textContent = s.side + " · BTC";
    else side.textContent = (s.bias || "FLAT") + (s.bias === "FLAT" ? " · idle" : " · armed");
    var broe = document.getElementById("bot-roe");
    var roeTag = document.getElementById("bot-roe-tag");
    var shownRoe = s.roe != null ? s.roe : (last ? last.roe : null);
    broe.textContent = roeText(shownRoe);
    broe.classList.remove("up", "dn");
    if (shownRoe > 0.004) broe.classList.add("up");
    else if (shownRoe < -0.004) broe.classList.add("dn");
    if (s.side) roeTag.textContent = "open ROE";
    else if (last) roeTag.textContent = "last close · " + last.reason;
    else roeTag.textContent = "ROE while the clip is open";
    document.getElementById("bot-entry").textContent = s.entry > 0 ? pxText(s.entry) : "—";
    document.getElementById("bot-mark").textContent = s.mark > 0 ? pxText(s.mark) : (livePrice ? pxText(livePrice) : "—");
    document.getElementById("bot-lev").textContent = (s.leverage || 40) + "x";
    document.getElementById("bot-qty").textContent = qtyText(s.qty);
    document.getElementById("btc-px").textContent = "mark " + (s.mark > 0 ? pxText(s.mark) : (livePrice ? pxText(livePrice) : "—"));
    if (running && s.mark > 0) document.getElementById("px").textContent = "BTC " + pxText(s.mark);

    var tag = document.getElementById("chart-tag");
    if (phase === "replay") tag.textContent = "replaying the last hour";
    else if (phase === "live") tag.textContent = "live · " + (source || "BTC");
    else if (phase === "stopped") tag.textContent = "stopped";
    else tag.textContent = "last hour, then the live print";

    var runBtn = document.getElementById("run");
    runBtn.textContent = running ? "Stop" : "Run live";
    runBtn.classList.toggle("stop", running);

    paintChart(s);
    paintSpark(s);
  }

  var LINKS = [["n-btc", "n-bias"], ["n-bias", "n-ctrl"], ["n-ctrl", "n-sl"], ["n-sl", "n-bot"]];
  var linkDrag = null;
  var LAYOUT_KEY = "rimbaud-desk-layout-v1";

  function stageOf() { return document.getElementById("stage"); }

  function boardNodes() {
    return Array.prototype.slice.call(stageOf().querySelectorAll(".clip, .chart-card"));
  }

  function portPoint(id, side) {
    var stage = stageOf();
    var el = document.querySelector("#" + id + " .port." + side);
    if (!el || el.closest(".gone")) return null;
    var sr = stage.getBoundingClientRect();
    var r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2 - sr.left, y: r.top + r.height / 2 - sr.top };
  }

  function curve(a, b) {
    var mx = (a.x + b.x) / 2;
    return "M" + a.x.toFixed(1) + " " + a.y.toFixed(1) +
      " C " + mx.toFixed(1) + " " + a.y.toFixed(1) + ", " + mx.toFixed(1) + " " + b.y.toFixed(1) +
      ", " + b.x.toFixed(1) + " " + b.y.toFixed(1);
  }

  function drawWires() {
    var svg = document.getElementById("wires");
    svg.textContent = "";
    LINKS.forEach(function (pair) {
      var a = portPoint(pair[0], "out");
      var b = portPoint(pair[1], "in");
      if (!a || !b) return;
      var d = curve(a, b);
      var hit = document.createElementNS("http://www.w3.org/2000/svg", "path");
      hit.setAttribute("class", "hit");
      hit.setAttribute("d", d);
      hit.dataset.wire = pair[0] + ">" + pair[1];
      var vis = document.createElementNS("http://www.w3.org/2000/svg", "path");
      vis.setAttribute("class", "vis");
      vis.setAttribute("d", d);
      svg.appendChild(hit);
      svg.appendChild(vis);
    });
    if (linkDrag && linkDrag.a && linkDrag.b) {
      var rub = document.createElementNS("http://www.w3.org/2000/svg", "path");
      rub.setAttribute("class", "rubber");
      rub.setAttribute("d", curve(linkDrag.a, linkDrag.b));
      svg.appendChild(rub);
    }
  }

  function growStage() {
    var stage = stageOf();
    var w = 1120;
    var h = 560;
    boardNodes().forEach(function (el) {
      if (el.classList.contains("gone")) return;
      w = Math.max(w, el.offsetLeft + el.offsetWidth + 40);
      h = Math.max(h, el.offsetTop + el.offsetHeight + 40);
    });
    stage.style.width = w + "px";
    stage.style.height = h + "px";
  }

  function saveLayout() {
    var pos = {};
    var extras = [];
    boardNodes().forEach(function (el) {
      pos[el.id] = {
        x: el.offsetLeft,
        y: el.offsetTop,
        gone: el.classList.contains("gone")
      };
      if (el.dataset.extra === "1" && !el.classList.contains("gone")) {
        extras.push({
          id: el.id,
          name: el.dataset.label || "clip",
          cat: el.dataset.cat || "clip",
          color: el.dataset.color || "#a78bfa",
          x: el.offsetLeft,
          y: el.offsetTop
        });
      }
    });
    try {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify({ pos: pos, wires: LINKS, extras: extras }));
    } catch (err) { /* private mode */ }
  }

  function slug(name) {
    return String(name || "clip").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "clip";
  }

  function bindNode(el) {
    if (!el || el.dataset.bound === "1") return;
    el.dataset.bound = "1";
    el.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) return;
      if (e.target.closest("input, button, textarea, select, a, .port")) return;
      var origL = el.offsetLeft;
      var origT = el.offsetTop;
      var sx = e.clientX;
      var sy = e.clientY;
      el.classList.add("dragging");
      try { el.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      function move(ev) {
        var x = Math.max(8, origL + ev.clientX - sx);
        var y = Math.max(8, origT + ev.clientY - sy);
        el.style.left = x + "px";
        el.style.top = y + "px";
        growStage();
        drawWires();
      }
      function up() {
        el.classList.remove("dragging");
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerup", up);
        el.removeEventListener("pointercancel", up);
        saveLayout();
      }
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", up);
      el.addEventListener("pointercancel", up);
      e.preventDefault();
    });
  }

  function makeExtra(item, x, y) {
    var id = "n-x-" + slug(item.name);
    var el = document.getElementById(id);
    if (!el) {
      el = document.createElement("article");
      el.className = "clip extra";
      el.id = id;
      el.dataset.extra = "1";
      el.dataset.label = item.name;
      el.dataset.cat = item.cat || "clip";
      el.dataset.color = item.color || "#a78bfa";
      ["in", "out"].forEach(function (side) {
        var port = document.createElement("i");
        port.className = "port " + side;
        el.appendChild(port);
      });
      var head = document.createElement("header");
      head.textContent = item.cat || "clip";
      var strong = document.createElement("strong");
      strong.textContent = item.name;
      var sub = document.createElement("p");
      sub.className = "sub";
      sub.textContent = "On the board. The test book still runs DMan Maker.";
      el.appendChild(head);
      el.appendChild(strong);
      el.appendChild(sub);
      if (item.color) el.style.borderColor = item.color;
      stageOf().appendChild(el);
      bindNode(el);
    }
    el.classList.remove("gone");
    el.style.left = Math.max(8, x) + "px";
    el.style.top = Math.max(8, y) + "px";
    return el;
  }

  function catalogItems() {
    var list = [];
    CATS.forEach(function (cat) {
      cat.items.forEach(function (item) {
        if (item.board === "k-roe") return;
        list.push({
          name: item.name,
          cat: cat.label,
          catId: cat.id,
          color: item.color,
          board: item.board || ""
        });
      });
    });
    list.push({ name: "BTC chart", cat: "Output", catId: "output", color: "#00ff6a", board: "chart-card" });
    return list;
  }

  function nodeOnBoard(item) {
    var id = item.board || ("n-x-" + slug(item.name));
    var el = document.getElementById(id);
    return !!(el && !el.classList.contains("gone"));
  }

  function placeItem(item, x, y) {
    var el = null;
    if (item.board) {
      el = document.getElementById(item.board);
      if (!el) return;
      el.classList.remove("gone");
      el.style.left = Math.max(8, x) + "px";
      el.style.top = Math.max(8, y) + "px";
    } else {
      el = makeExtra(item, x, y);
    }
    growStage();
    drawWires();
    saveLayout();
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  }

  function removeNode(id) {
    var el = document.getElementById(id);
    if (!el) return;
    LINKS = LINKS.filter(function (w) { return w[0] !== id && w[1] !== id; });
    if (el.dataset.extra === "1") el.remove();
    else el.classList.add("gone");
    drawWires();
    saveLayout();
  }

  function deleteWire(key) {
    LINKS = LINKS.filter(function (w) { return w[0] + ">" + w[1] !== key; });
    drawWires();
    saveLayout();
  }

  function restoreLayout() {
    var raw = null;
    try { raw = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "null"); } catch (err) { raw = null; }
    if (!raw) return;
    (raw.extras || []).forEach(function (item) {
      makeExtra({ name: item.name, cat: item.cat, color: item.color }, item.x || 24, item.y || 24);
    });
    if (raw.pos) {
      Object.keys(raw.pos).forEach(function (id) {
        var el = document.getElementById(id);
        var p = raw.pos[id];
        if (!el || !p) return;
        el.style.left = p.x + "px";
        el.style.top = p.y + "px";
        el.classList.toggle("gone", !!p.gone);
      });
    }
    if (Array.isArray(raw.wires)) {
      LINKS = raw.wires.filter(function (w) { return w && w[0] && w[1] && w[0] !== w[1]; });
    }
    growStage();
  }

  function installBoard() {
    var stage = stageOf();
    boardNodes().forEach(bindNode);
    restoreLayout();
    boardNodes().forEach(bindNode);

    stage.addEventListener("pointerdown", function (e) {
      var port = e.target.closest && e.target.closest(".port.out");
      if (!port || e.button !== 0) return;
      var node = port.closest(".clip");
      if (!node || node.classList.contains("gone")) return;
      var a = portPoint(node.id, "out");
      if (!a) return;
      linkDrag = { from: node.id, a: a, b: { x: a.x, y: a.y } };
      e.preventDefault();
      function move(ev) {
        var sr = stage.getBoundingClientRect();
        linkDrag.b = { x: ev.clientX - sr.left, y: ev.clientY - sr.top };
        linkDrag.a = portPoint(node.id, "out") || linkDrag.a;
        drawWires();
      }
      function up(ev) {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        var hit = document.elementFromPoint(ev.clientX, ev.clientY);
        var inn = hit && hit.closest ? hit.closest(".port.in") : null;
        var dest = inn && inn.closest(".clip");
        if (dest && dest.id !== node.id && !dest.classList.contains("gone")) {
          var exists = LINKS.some(function (w) { return w[0] === node.id && w[1] === dest.id; });
          if (!exists) LINKS.push([node.id, dest.id]);
        }
        linkDrag = null;
        drawWires();
        saveLayout();
      }
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    });

    stage.addEventListener("dragover", function (e) {
      var types = e.dataTransfer ? Array.prototype.slice.call(e.dataTransfer.types || []) : [];
      if (types.indexOf("application/x-rimbaud-node") >= 0 || types.indexOf("text/plain") >= 0) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }
    });
    stage.addEventListener("drop", function (e) {
      var raw = e.dataTransfer && (e.dataTransfer.getData("application/x-rimbaud-node") || e.dataTransfer.getData("text/plain"));
      if (!raw) return;
      e.preventDefault();
      var item = null;
      try { item = JSON.parse(raw); } catch (err) { item = null; }
      if (!item) return;
      var p = stagePoint(e.clientX, e.clientY);
      placeItem(item, p.x - 20, p.y - 16);
      closeMenu();
    });

    buildMenu();
    drawWires();
  }

  function stagePoint(clientX, clientY) {
    var r = stageOf().getBoundingClientRect();
    return { x: clientX - r.left, y: clientY - r.top };
  }

  var menuEl = null;
  var menuCtx = null;

  function closeMenu() {
    if (!menuEl) return;
    menuEl.menu.hidden = true;
    menuEl.backdrop.hidden = true;
    menuCtx = null;
  }

  function buildMenu() {
    var backdrop = document.createElement("div");
    backdrop.className = "ctx-backdrop";
    backdrop.hidden = true;
    var menu = document.createElement("div");
    menu.className = "ctx-menu";
    menu.hidden = true;
    menu.addEventListener("click", function (e) { e.stopPropagation(); });
    menu.addEventListener("contextmenu", function (e) { e.preventDefault(); });
    backdrop.addEventListener("click", closeMenu);
    backdrop.addEventListener("contextmenu", function (e) { e.preventDefault(); closeMenu(); });
    document.body.appendChild(backdrop);
    document.body.appendChild(menu);
    menuEl = { backdrop: backdrop, menu: menu };
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeMenu();
    });
    stageOf().addEventListener("contextmenu", function (e) {
      if (e.target.closest("input, textarea")) return;
      e.preventDefault();
      var wire = e.target.closest("[data-wire]");
      var node = e.target.closest(".clip, .chart-card");
      openMenu(e.clientX, e.clientY, wire ? { kind: "edge", id: wire.dataset.wire } : node ? { kind: "node", id: node.id } : { kind: "pane" });
    });
  }

  function openMenu(x, y, ctx) {
    menuCtx = ctx;
    var menu = menuEl.menu;
    var backdrop = menuEl.backdrop;
    menu.textContent = "";
    var title = document.createElement("div");
    title.className = "ctx-title";
    if (ctx.kind === "node") {
      var node = document.getElementById(ctx.id);
      title.textContent = (node && (node.dataset.label || node.querySelector("header") && node.querySelector("header").textContent)) || "node";
    } else if (ctx.kind === "edge") title.textContent = "wire";
    else title.textContent = "canvas";
    menu.appendChild(title);
    if (ctx.kind === "node" || ctx.kind === "edge") {
      var act = document.createElement("button");
      act.type = "button";
      act.className = "ctx-item danger";
      act.textContent = ctx.kind === "edge" ? "Delete wire" : "Remove from canvas";
      act.addEventListener("click", function () {
        if (ctx.kind === "edge") deleteWire(ctx.id);
        else removeNode(ctx.id);
        closeMenu();
      });
      menu.appendChild(act);
      var sep = document.createElement("div");
      sep.className = "ctx-sep";
      menu.appendChild(sep);
    }
    var addT = document.createElement("div");
    addT.className = "ctx-title";
    addT.textContent = "Add node";
    menu.appendChild(addT);
    var search = document.createElement("input");
    search.className = "tb-search";
    search.placeholder = "search tools…";
    menu.appendChild(search);
    var cats = document.createElement("div");
    cats.className = "ctx-cats";
    menu.appendChild(cats);
    var list = document.createElement("div");
    list.className = "ctx-list";
    menu.appendChild(list);
    var cat = "";
    function paintList() {
      var q = search.value.trim().toLowerCase();
      cats.textContent = "";
      var ids = [{ id: "", label: "all" }];
      CATS.forEach(function (c) { ids.push({ id: c.id, label: c.label }); });
      ids.forEach(function (c) {
        var b = document.createElement("button");
        b.type = "button";
        b.textContent = c.label;
        if (c.id === cat) b.className = "on";
        b.addEventListener("click", function () { cat = c.id; paintList(); });
        cats.appendChild(b);
      });
      list.textContent = "";
      var items = catalogItems().filter(function (item) {
        if (cat && item.catId !== cat) return false;
        if (!q) return true;
        return (item.name + " " + item.cat).toLowerCase().indexOf(q) >= 0;
      });
      if (!items.length) {
        var empty = document.createElement("div");
        empty.className = "ctx-empty";
        empty.textContent = "no tools";
        list.appendChild(empty);
      }
      items.forEach(function (item) {
        var on = nodeOnBoard(item);
        var b = document.createElement("button");
        b.type = "button";
        b.className = "ctx-item" + (on ? " dim" : "");
        var dot = document.createElement("i");
        dot.className = "dot";
        dot.style.background = item.color;
        var lab = document.createElement("span");
        lab.textContent = item.name;
        b.appendChild(dot);
        b.appendChild(lab);
        if (on) {
          var em = document.createElement("em");
          em.textContent = "on";
          b.appendChild(em);
        }
        b.addEventListener("click", function () {
          var p = menuCtx && menuCtx.point ? menuCtx.point : { x: 48, y: 48 };
          placeItem(item, p.x, p.y);
          closeMenu();
        });
        list.appendChild(b);
      });
    }
    search.addEventListener("input", paintList);
    paintList();
    var point = stagePoint(x, y);
    menuCtx.point = point;
    backdrop.hidden = false;
    menu.hidden = false;
    var mw = 268;
    var mh = Math.min(window.innerHeight * 0.72, 520);
    menu.style.left = Math.max(8, Math.min(x, window.innerWidth - mw - 8)) + "px";
    menu.style.top = Math.max(8, Math.min(y, window.innerHeight - 80)) + "px";
    search.focus();
  }

  function buildToolbox() {
    var root = document.getElementById("tb-tools");
    CATS.forEach(function (cat) {
      var box = document.createElement("section");
      box.className = "tb-cat" + (cat.open ? "" : " shut");
      var head = document.createElement("button");
      head.type = "button";
      head.className = "tb-cat-h";
      head.textContent = cat.label;
      head.addEventListener("click", function () { box.classList.toggle("shut"); });
      var rows = document.createElement("div");
      rows.className = "tb-rows";
      cat.items.forEach(function (item) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "tb-item" + (item.board ? " on-board" : "");
        b.dataset.name = (item.name + " " + cat.label + " " + (item.hint || "")).toLowerCase();
        var dot = document.createElement("i");
        dot.className = "dot";
        dot.style.background = item.color;
        var ico = document.createElement("span");
        ico.className = "ico";
        ico.textContent = item.ico;
        var lab = document.createElement("span");
        lab.className = "lab";
        lab.textContent = item.name;
        var st = document.createElement("span");
        st.className = "st" + (item.board ? " on" : "");
        st.textContent = item.board ? "on" : "";
        b.appendChild(dot);
        b.appendChild(ico);
        b.appendChild(lab);
        b.appendChild(st);
        b.draggable = true;
        var dragged = false;
        b.addEventListener("dragstart", function (e) {
          dragged = true;
          var payload = {
            name: item.name,
            cat: cat.label,
            color: item.color,
            board: item.board && item.board !== "k-roe" ? item.board : ""
          };
          e.dataTransfer.setData("application/x-rimbaud-node", JSON.stringify(payload));
          e.dataTransfer.setData("text/plain", JSON.stringify(payload));
          e.dataTransfer.effectAllowed = "copy";
        });
        b.addEventListener("click", function () {
          if (dragged) { dragged = false; return; }
          if (item.board === "k-roe") {
            var statusEl = document.getElementById("status");
            if (statusEl && statusEl.scrollIntoView) statusEl.scrollIntoView({ block: "nearest" });
            return;
          }
          var id = item.board || ("n-x-" + slug(item.name));
          var existing = document.getElementById(id);
          if (existing && !existing.classList.contains("gone")) {
            existing.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
            return;
          }
          var board = document.querySelector(".board");
          placeItem({
            name: item.name,
            cat: cat.label,
            color: item.color,
            board: item.board || ""
          }, (board ? board.scrollLeft : 0) + 28, (board ? board.scrollTop : 0) + 28);
        });
        rows.appendChild(b);
      });
      box.appendChild(head);
      box.appendChild(rows);
      root.appendChild(box);
    });
    document.getElementById("tb-q").addEventListener("input", function () {
      var q = this.value.trim().toLowerCase();
      root.querySelectorAll(".tb-item").forEach(function (el) {
        el.style.display = !q || el.dataset.name.indexOf(q) >= 0 ? "" : "none";
      });
    });
  }

  function pushConfig() {
    if (!book) return;
    var cfg = readConfig();
    delete cfg.bias;
    delete cfg.startEquity;
    D.applyConfig(book, cfg);
  }

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  async function start() {
    var token = ++runToken;
    running = true;
    phase = "replay";
    note = "";
    book = D.createBook(readConfig());
    paint();
    var data;
    try {
      var res = await fetch("/api/btc");
      if (!res.ok) throw new Error("btc");
      data = await res.json();
    } catch (err) {
      if (token !== runToken) return;
      running = false;
      phase = "idle";
      book = null;
      note = "BTC print unavailable. The test account is still $1,000.";
      paint();
      return;
    }
    if (token !== runToken) return;
    source = data.source || "";
    document.getElementById("feed").textContent = source || "—";
    var candles = data.candles || [];
    for (var i = 0; i < candles.length; i++) {
      if (token !== runToken) return;
      D.stepCandle(book, candles[i]);
      paint();
      await sleep(40);
    }
    phase = "live";
    paint();
    while (token === runToken && running) {
      try {
        var liveRes = await fetch("/api/btc");
        var live = await liveRes.json();
        if (token !== runToken) return;
        if (live.price > 0) {
          livePrice = live.price;
          source = live.source || source;
          document.getElementById("feed").textContent = source || "—";
          D.stepPrice(book, live.price, live.asof || Date.now());
          paint();
        }
      } catch (err2) {
        if (token === runToken) document.getElementById("status").textContent = "live print missed — retrying";
      }
      await sleep(4000);
    }
  }

  document.getElementById("run").addEventListener("click", function () {
    if (running) {
      running = false;
      runToken++;
      phase = "stopped";
      note = "stopped · this test stays on screen. Run live starts a fresh $1,000 book.";
      if (book) book.status = note;
      paint();
      return;
    }
    start();
  });

  document.querySelectorAll(".bias button").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".bias button").forEach(function (b) { b.classList.remove("on"); });
      btn.classList.add("on");
      if (book) D.arm(book, btn.getAttribute("data-bias"));
      else note = btn.getAttribute("data-bias") + (btn.getAttribute("data-bias") === "FLAT" ? " · idle until you arm LONG or SHORT" : " armed · press Run live");
      paint();
    });
  });

  document.querySelectorAll("[data-field]").forEach(function (el) {
    el.addEventListener(el.tagName === "BUTTON" ? "click" : "input", function () {
      if (el.classList.contains("bool")) {
        el.classList.toggle("on");
        el.textContent = el.classList.contains("on") ? "ON" : "OFF";
      }
      if (el.getAttribute("data-field") === "sl") {
        document.querySelectorAll('[data-field="sl"]').forEach(function (other) {
          if (other !== el && other.tagName === "INPUT") other.value = el.value;
        });
      }
      pushConfig();
      paint();
    });
  });

  function pullPrice() {
    fetch("/api/btc").then(function (r) { return r.json(); }).then(function (d) {
      if (!(d.price > 0)) return;
      livePrice = d.price;
      source = d.source || source;
      document.getElementById("feed").textContent = source || "—";
      if (!running) {
        document.getElementById("px").textContent = "BTC " + pxText(d.price);
        if (!book) paint();
      }
    }).catch(function () {
      if (!livePrice) document.getElementById("px").textContent = "BTC …";
    });
  }

  document.getElementById("out").addEventListener("click", function () {
    fetch("/api/logout", { method: "POST" }).then(function () { location.href = "/"; });
  });

  buildToolbox();
  installBoard();
  paint();
  window.addEventListener("resize", drawWires);
  pullPrice();
  setInterval(pullPrice, 4000);
})();
