/* Easy open-wire frame. Drag clips, wire ports, and copy controllers
   on and off the board. The DMan Maker chain replays the last public
   BTC hour on a paper book. No orders. */
(function () {
  var D = window.RimbaudDman;
  if (!D) return;
  var frame = document.getElementById("easy-frame");
  var stage = document.getElementById("easy-stage");
  var tray = document.getElementById("easy-tray");
  if (!frame || !stage || !tray) return;

  var bias = "LONG";
  var crazy = true;
  var rebuy = true;
  var feed = null;
  var timer = 0;
  var linkDrag = null;
  var LINKS = [["e-btc", "e-bias"], ["e-bias", "e-ctrl"], ["e-ctrl", "e-sl"], ["e-sl", "e-bot"]];
  var LAYOUT_KEY = "rimbaud-easy-layout-v2";
  var CHAIN = [
    ["e-btc", "e-bias", "the coin and the bias"],
    ["e-bias", "e-ctrl", "the bias and the controller"],
    ["e-ctrl", "e-sl", "the controller and the stop"],
    ["e-sl", "e-bot", "the stop and the hunter"]
  ];
  var HOME = {
    "e-btc": { x: 12, y: 156 },
    "e-bias": { x: 196, y: 136 },
    "e-ctrl": { x: 412, y: 12 },
    "e-sl": { x: 680, y: 156 },
    "e-bot": { x: 868, y: 140 }
  };
  var TRAY = [];

  function num(id, fallback) {
    var el = document.getElementById(id);
    var n = el ? Number(el.value) : NaN;
    return isFinite(n) ? n : fallback;
  }

  function money(n) {
    var neg = n < -0.004;
    var body = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (neg ? "−$" : "$") + body;
  }

  function roeText(r) {
    if (r == null || !isFinite(r)) return "—";
    return (r >= 0 ? "+" : "−") + Math.abs(r).toFixed(2) + "%";
  }

  function cfg() {
    var sl = num("easy-sl", 20);
    return {
      bias: bias,
      dontGoCrazy: crazy,
      afterLossNoRebuy: rebuy,
      takeProfit: num("easy-tp", 2) / 100,
      trailActivation: num("easy-arm", 1.5) / 100,
      trailDelta: num("easy-delta", 0.5) / 100,
      refreshSec: num("easy-refresh", 30),
      slRoePct: sl,
      marginPct: num("easy-margin", 100) / 100,
      leverage: num("easy-lev", 40)
    };
  }

  function syncSl(from, to) {
    var a = document.getElementById(from);
    var b = document.getElementById(to);
    if (!a || !b) return;
    b.value = a.value;
    var big = document.getElementById("easy-sl-big");
    if (big) big.textContent = "−" + String(a.value || "20").replace(/^-/, "") + " ROE";
  }

  function paintSpark(curve) {
    var svg = document.getElementById("easy-spark");
    if (!svg) return;
    var pts = (curve || []).map(function (p) { return p.equity; }).filter(function (n) { return isFinite(n); });
    if (pts.length < 2) {
      svg.innerHTML = "";
      return;
    }
    var lo = Math.min.apply(null, pts);
    var hi = Math.max.apply(null, pts);
    if (hi - lo < 0.01) { lo -= 1; hi += 1; }
    var d = pts.map(function (y, i) {
      var x = (i / (pts.length - 1)) * 320;
      var yy = 68 - ((y - lo) / (hi - lo)) * 60;
      return (i ? "L" : "M") + x.toFixed(1) + " " + yy.toFixed(1);
    }).join(" ");
    svg.innerHTML = '<path d="' + d + '" fill="none" stroke="#3a1c04" stroke-width="2"/>';
  }

  function paint(snap) {
    var eq = document.getElementById("easy-eq");
    var roe = document.getElementById("easy-roe");
    var real = document.getElementById("easy-real");
    var status = document.getElementById("easy-status");
    var side = document.getElementById("easy-side");
    var bot = document.getElementById("easy-bot");
    if (eq) eq.textContent = money(snap.equity);
    if (roe) roe.textContent = roeText(snap.roe);
    if (real) real.textContent = money(snap.realized);
    if (status) status.textContent = snap.status || "Paper hour played.";
    if (side) {
      if (snap.side) side.textContent = snap.side + " open";
      else if ((snap.bias || bias) === "FLAT") side.textContent = "FLAT";
      else side.textContent = (snap.bias || bias) + " armed";
    }
    if (bot) {
      if (liveBook && snap.side) bot.textContent = "Rebuy open. Entry " + money(snap.entry) + ".";
      else if (liveBook) bot.textContent = "Rebuy is flat. Reset rebuy enters again.";
      else if (snap.side) bot.textContent = "Open clip. Entry " + money(snap.entry) + ".";
      else bot.textContent = "No open clip at the end of the hour.";
    }
    paintSim(snap);
    paintLog(snap);
    var copy = paintRisk(snap && snap.trades);
    if (copy.amount > 0) {
      if (eq) eq.textContent = money(snap.equity - copy.amount);
      if (real) real.textContent = money((snap.realized || 0) - copy.amount);
    }
    paintSpark(snap.curve);
  }

  function knobLine() {
    var c = cfg();
    var next = num("easy-next", 0.18);
    return "TP " + (Math.round(c.takeProfit * 1000) / 10) + "% · next in " +
      (Math.round(next * 100) / 100) + "% · SL " + c.slRoePct + " ROE · " +
      c.leverage + "× · margin " + Math.round(c.marginPct * 100) + "%";
  }

  function paintSim(snap) {
    var sim = document.getElementById("easy-sim");
    var status = document.getElementById("easy-status");
    var line = knobLine();
    var side = (snap && snap.side) || (snap && snap.bias) || bias;
    if (sim) {
      if (liveBook && snap.side && snap.entry > 0) {
        sim.textContent = "Entered " + pxText(snap.entry) + " · " + snap.side + " · " + line + ". Paper only.";
      } else if (liveBook) {
        sim.textContent = "Flat. Reset rebuy enters again at the live price. " + side + " · " + line + ".";
      } else if (snap && snap.slHold) {
        sim.textContent = "Held after the stop. Reset rebuy clears that hold and enters at the live price. " + side + " · " + line + ".";
      } else {
        sim.textContent = "Reset rebuy enters at the live price with the knobs now. " + side + " · " + line + ".";
      }
    }
    if (status && liveBook) {
      if (snap.side) status.textContent = "Rebuy simulation · " + (snap.status || snap.side);
      else if (snap.slHold) status.textContent = "Rebuy simulation stopped on SL. Reset rebuy enters again.";
      else status.textContent = "Rebuy simulation · " + (snap.status || "flat");
    }
    var open = !!(liveBook && snap && snap.side);
    var buttons = document.querySelectorAll("[data-reset-in]");
    for (var i = 0; i < buttons.length; i++) buttons[i].classList.toggle("on", open);
  }

  function riskWired() {
    var el = document.getElementById("e-risk");
    if (!el || el.classList.contains("gone")) return false;
    return LINKS.some(function (w) { return w[0] === "e-ctrl" && w[1] === "e-risk"; });
  }

  function riskCopy(trades) {
    var wins = [];
    (trades || []).forEach(function (t) {
      if (t && t.pnl > 0.004 && t.reason !== "reset IN") wins.push(t);
    });
    var amount = 0;
    var count = 0;
    var last = 0;
    wins.forEach(function (t, i) {
      if ((i + 1) % 2 === 0) {
        amount += t.pnl;
        count += 1;
        last = t.pnl;
      }
    });
    return { amount: amount, count: count, wins: wins.length, last: last };
  }

  function paintRisk(trades) {
    var amt = document.getElementById("easy-risk-amt");
    var note = document.getElementById("easy-risk-note");
    var wired = riskWired();
    var copy = wired ? riskCopy(trades) : { amount: 0, count: 0, wins: 0, last: 0 };
    if (amt) amt.textContent = money(copy.amount);
    if (note) {
      if (!wired) note.textContent = "Wire the extra out on the controller to this in. Every second win is copied here.";
      else if (!copy.count) note.textContent = "Wired. The 2nd win is copied here, then every second win.";
      else note.textContent = copy.count + (copy.count === 1 ? " win copied." : " wins copied.") + " Last " + money(copy.last) + ".";
    }
    return copy;
  }

  var SVGNS = "http://www.w3.org/2000/svg";
  var chartView = null;
  var frozenScale = null;
  var chartDrag = null;
  var scaleMode = "price";
  var userScale = null;
  var liveBook = null;
  var holdChecked = false;
  var pollTimer = 0;
  var logPinned = true;
  var logKey = "";

  function pxText(n) {
    if (!(n > 0)) return "—";
    return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function svgEl(name, attrs) {
    var node = document.createElementNS(SVGNS, name);
    Object.keys(attrs).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    return node;
  }

  function polar(cx, cy, r, deg) {
    var rad = deg * Math.PI / 180;
    return { x: cx + r * Math.sin(rad), y: cy - r * Math.cos(rad) };
  }

  function arcPath(cx, cy, r, a0, a1) {
    var p0 = polar(cx, cy, r, a0);
    var p1 = polar(cx, cy, r, a1);
    var delta = a1 - a0;
    while (delta < 0) delta += 360;
    var large = delta > 180 ? 1 : 0;
    return "M" + p0.x.toFixed(2) + " " + p0.y.toFixed(2) +
      "A" + r + " " + r + " 0 " + large + " 1 " +
      p1.x.toFixed(2) + " " + p1.y.toFixed(2);
  }

  function paintKnob(id) {
    var input = document.getElementById(id);
    if (!input) return;
    var knob = input.closest(".knob");
    if (!knob) return;
    var read = knob.querySelector(".knob-read");
    if (read) read.textContent = input.value;
    var svg = knob.querySelector(".knob-svg");
    if (!svg) return;
    svg.textContent = "";
    var min = Number(input.min);
    var max = Number(input.max);
    var val = Number(input.value);
    var t = (isFinite(max) && max > min) ? (val - min) / (max - min) : 0;
    if (t < 0) t = 0;
    if (t > 1) t = 1;
    var start = 225;
    var sweep = 270;
    var ang = start + sweep * t;
    svg.appendChild(svgEl("circle", {
      cx: 22, cy: 22, r: 13, fill: "#fff6ec", stroke: "#3a1c04", "stroke-width": 1
    }));
    svg.appendChild(svgEl("path", {
      d: arcPath(22, 22, 17.2, start, start + sweep),
      fill: "none", stroke: "#c85a00", "stroke-width": 3, "stroke-linecap": "round"
    }));
    if (t > 0.004) {
      svg.appendChild(svgEl("path", {
        d: arcPath(22, 22, 17.2, start, ang),
        fill: "none", stroke: "#3a1c04", "stroke-width": 3, "stroke-linecap": "round"
      }));
    }
    var p0 = polar(22, 22, 4, ang);
    var p1 = polar(22, 22, 11, ang);
    svg.appendChild(svgEl("line", {
      x1: p0.x.toFixed(2), y1: p0.y.toFixed(2),
      x2: p1.x.toFixed(2), y2: p1.y.toFixed(2),
      stroke: "#8a2800", "stroke-width": 2, "stroke-linecap": "round"
    }));
    svg.appendChild(svgEl("circle", { cx: 22, cy: 22, r: 2.2, fill: "#3a1c04" }));
  }

  function setNum(id, raw, live) {
    var el = document.getElementById(id);
    if (!el) return;
    var min = Number(el.min);
    var max = Number(el.max);
    var step = Number(el.step);
    if (!(step > 0)) step = 1;
    var n = Number(raw);
    if (!isFinite(n)) return;
    if (isFinite(min)) n = Math.max(min, n);
    if (isFinite(max)) n = Math.min(max, n);
    var dec = 0;
    var stepText = String(el.step || step);
    var dot = stepText.indexOf(".");
    if (dot >= 0) dec = stepText.length - dot - 1;
    n = Math.round(n / step) * step;
    if (isFinite(min)) n = Math.max(min, n);
    if (isFinite(max)) n = Math.min(max, n);
    var text = n.toFixed(dec);
    var changed = el.value !== text;
    el.value = text;
    paintKnob(id);
    if (id === "easy-sl") syncSl("easy-sl", "easy-sl2");
    if (!changed) return;
    if (live) play();
    else schedule();
  }

  function installKnobs() {
    var knobs = document.querySelectorAll("#e-ctrl .knob");
    for (var i = 0; i < knobs.length; i++) {
      (function (knob) {
        var input = knob.querySelector("input");
        if (!input) return;
        paintKnob(input.id);
        knob.addEventListener("pointerdown", function (e) {
          if (e.button !== 0) return;
          e.preventDefault();
          e.stopPropagation();
          var startY = e.clientY;
          var start = Number(input.value);
          var min = Number(input.min);
          var max = Number(input.max);
          var range = (isFinite(max) && isFinite(min)) ? (max - min) : 1;
          try { knob.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
          function move(ev) {
            var px = ev.shiftKey ? 400 : 140;
            setNum(input.id, start + ((startY - ev.clientY) / px) * range, true);
          }
          function up() {
            knob.removeEventListener("pointermove", move);
            knob.removeEventListener("pointerup", up);
            knob.removeEventListener("pointercancel", up);
          }
          knob.addEventListener("pointermove", move);
          knob.addEventListener("pointerup", up);
          knob.addEventListener("pointercancel", up);
        });
        knob.addEventListener("wheel", function (e) {
          e.preventDefault();
          var step = Number(input.step) || 1;
          var dir = e.deltaY < 0 ? 1 : -1;
          setNum(input.id, Number(input.value) + dir * step * (e.shiftKey ? 0.1 : 1), true);
        }, { passive: false });
        knob.tabIndex = 0;
        knob.addEventListener("keydown", function (e) {
          var step = Number(input.step) || 1;
          if (e.key === "ArrowUp" || e.key === "ArrowRight") {
            setNum(input.id, Number(input.value) + step, true);
            e.preventDefault();
          } else if (e.key === "ArrowDown" || e.key === "ArrowLeft") {
            setNum(input.id, Number(input.value) - step, true);
            e.preventDefault();
          }
        });
      })(knobs[i]);
    }
  }

  function pxFromY(y) {
    if (!chartView) return 0;
    var t = (y - chartView.padT) / chartView.plotH;
    return chartView.hi - t * (chartView.hi - chartView.lo);
  }

  function applyLinePrice(key, px) {
    if (!chartView || !(chartView.entry > 0) || !(px > 0)) return;
    var entry = chartView.entry;
    var long = chartView.side === "LONG";
    var lev = chartView.lev > 0 ? chartView.lev : 1;
    var pct = long ? (px / entry - 1) * 100 : (1 - px / entry) * 100;
    if (key === "win") setNum("easy-tp", pct, true);
    else if (key === "arm") setNum("easy-arm", pct, true);
    else if (key === "sl") setNum("easy-sl", -pct * lev, true);
    else if (key === "next") {
      var tp = num("easy-tp", 2) / 100;
      var winPx = long ? entry * (1 + tp) : entry * (1 - tp);
      if (!(winPx > 0)) return;
      var reb = long ? (1 - px / winPx) * 100 : (px / winPx - 1) * 100;
      setNum("easy-next", reb, true);
    } else if (key === "trail") {
      var extreme = chartView.extreme;
      if (!(extreme > 0)) return;
      var delta = long ? (1 - px / extreme) * 100 : (px / extreme - 1) * 100;
      setNum("easy-delta", delta, true);
    }
  }

  function hoverLine(key) {
    var svg = document.getElementById("easy-candles");
    if (!svg) return;
    var nodes = svg.querySelectorAll("[data-line]");
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var on = key && node.getAttribute("data-line") === key;
      node.setAttribute("stroke-width", on ? "3.2" : (node.getAttribute("data-w") || "1.6"));
    }
    svg.style.cursor = key ? "ns-resize" : "";
  }

  function installChartDrag() {
    var svg = document.getElementById("easy-candles");
    if (!svg || svg.dataset.drag === "1") return;
    svg.dataset.drag = "1";
    function viewY(ev) {
      var r = svg.getBoundingClientRect();
      if (!r.height) return 0;
      return ((ev.clientY - r.top) / r.height) * 300;
    }
    function nearest(y) {
      if (!chartView || !chartView.lines) return null;
      var best = null;
      var bestD = 10;
      chartView.lines.forEach(function (line) {
        var d = Math.abs(line.y - y);
        if (d < bestD) { bestD = d; best = line; }
      });
      return best;
    }
    function priceFromDrag(y) {
      var plotH = chartDrag.plotH || 1;
      var lo = chartDrag.lo;
      var hi = chartDrag.hi;
      if (!chartDrag.outside) {
        return chartDrag.startPx + (chartDrag.startY - y) * ((hi - lo) / plotH);
      }
      var dir = y - chartDrag.startY;
      var toward = chartDrag.above ? (chartDrag.startPx - lo) : (hi - chartDrag.startPx);
      if (!(toward > 0)) toward = Math.max(hi - lo, 1);
      return chartDrag.startPx - (dir / plotH) * toward;
    }
    svg.addEventListener("pointermove", function (ev) {
      if (chartDrag) {
        applyLinePrice(chartDrag.key, priceFromDrag(viewY(ev)));
        return;
      }
      var hit = nearest(viewY(ev));
      hoverLine(hit ? hit.key : "");
    });
    svg.addEventListener("pointerdown", function (ev) {
      if (ev.button !== 0) return;
      var hit = nearest(viewY(ev));
      if (!hit || !chartView) return;
      ev.preventDefault();
      ev.stopPropagation();
      frozenScale = { lo: chartView.lo, hi: chartView.hi };
      chartDrag = {
        key: hit.key,
        startY: viewY(ev),
        startPx: hit.px,
        outside: !!hit.outside,
        above: !!hit.above,
        plotH: chartView.plotH,
        lo: chartView.lo,
        hi: chartView.hi
      };
      svg.style.cursor = "ns-resize";
      try { svg.setPointerCapture(ev.pointerId); } catch (err) { /* ignore */ }
    });
    function endDrag() {
      if (!chartDrag) return;
      chartDrag = null;
      frozenScale = null;
      hoverLine("");
      play();
    }
    svg.addEventListener("pointerup", endDrag);
    svg.addEventListener("pointercancel", endDrag);
    svg.addEventListener("wheel", function (ev) {
      if (scaleMode !== "price" || chartDrag || !chartView) return;
      ev.preventDefault();
      var y = viewY(ev);
      var plotTop = chartView.padT;
      var plotBot = chartView.padT + chartView.plotH;
      if (y < plotTop) y = plotTop;
      if (y > plotBot) y = plotBot;
      var px = pxFromY(y);
      var factor = ev.deltaY < 0 ? (1 / 1.15) : 1.15;
      var lo = chartView.lo;
      var hi = chartView.hi;
      var nlo = px - (px - lo) * factor;
      var nhi = px + (hi - px) * factor;
      var mid = (nlo + nhi) / 2;
      if (!(mid > 0)) return;
      var span = nhi - nlo;
      var minSpan = mid * 0.0015;
      var maxSpan = mid * 0.12;
      if (span < minSpan) {
        nlo = mid - minSpan / 2;
        nhi = mid + minSpan / 2;
      } else if (span > maxSpan) {
        nlo = mid - maxSpan / 2;
        nhi = mid + maxSpan / 2;
      }
      if (!(nhi > nlo)) return;
      userScale = { lo: nlo, hi: nhi };
      play();
    }, { passive: false });
  }

  function replay() {
    var book = D.createBook(cfg());
    var marks = [];
    var candles = (feed && feed.candles) || [];
    candles.forEach(function (c, i) {
      var before = book.side;
      var n = book.trades.length;
      D.stepCandle(book, c);
      if (!before && book.side) marks.push({ i: i, kind: "in", px: book.entry, side: book.side });
      if (book.trades.length > n) {
        var t = book.trades[book.trades.length - 1];
        marks.push({ i: i, kind: "out", px: t.price, reason: t.reason, side: t.side, entry: t.entry });
      }
    });
    if (feed && feed.price) {
      var before = book.side;
      var n = book.trades.length;
      var lastI = Math.max(0, candles.length - 1);
      D.stepPrice(book, feed.price, feed.asof || Date.now());
      if (!before && book.side) marks.push({ i: lastI, kind: "in", px: book.entry, side: book.side });
      if (book.trades.length > n) {
        var t = book.trades[book.trades.length - 1];
        marks.push({ i: lastI, kind: "out", px: t.price, reason: t.reason, side: t.side, entry: t.entry });
      }
    }
    return { book: book, marks: marks };
  }

  function focusClip(book) {
    if (book.side && book.entry > 0) {
      return {
        side: book.side,
        entry: book.entry,
        lev: book.levUsed || book.cfg.leverage,
        trailOn: book.trailOn,
        extreme: book.extreme,
        open: true,
        held: false
      };
    }
    var last = book.trades.length ? book.trades[book.trades.length - 1] : null;
    if (!last || !(last.entry > 0)) return null;
    return {
      side: last.side,
      entry: last.entry,
      lev: last.leverage || book.cfg.leverage,
      trailOn: false,
      extreme: 0,
      open: false,
      held: !!(book.slHold && book.cfg.afterLossNoRebuy),
      outPx: last.price,
      outReason: last.reason || ""
    };
  }

  function clipLevels(book, clip) {
    var long = clip.side === "LONG";
    var entry = clip.entry;
    var lev = clip.lev > 0 ? clip.lev : 1;
    var tp = book.cfg.takeProfit;
    var arm = book.cfg.trailActivation;
    var win = long ? entry * (1 + tp) : entry * (1 - tp);
    var armPx = long ? entry * (1 + arm) : entry * (1 - arm);
    var slDist = (book.cfg.slRoePct / 100) / lev;
    var sl = long ? entry * (1 - slDist) : entry * (1 + slDist);
    var nextFrac = num("easy-next", 0.18) / 100;
    var nextIn = long ? win * (1 - nextFrac) : win * (1 + nextFrac);
    var trail = 0;
    if (clip.trailOn && clip.extreme > 0) {
      trail = long
        ? clip.extreme * (1 - book.cfg.trailDelta)
        : clip.extreme * (1 + book.cfg.trailDelta);
    }
    return { win: win, arm: armPx, sl: sl, nextIn: nextIn, trail: trail };
  }

  function paintChart(book, marks) {
    var svg = document.getElementById("easy-candles");
    var note = document.getElementById("easy-chart-note");
    if (!svg) return;
    svg.textContent = "";
    var candles = (feed && feed.candles) || [];
    var W = 960;
    var H = 300;
    var padL = 10;
    var padR = 108;
    var padT = 16;
    var padB = 16;
    svg.appendChild(svgEl("rect", { x: 0, y: 0, width: W, height: H, fill: "#f8a868" }));
    if (candles.length < 2) {
      chartView = null;
      svg.appendChild(svgEl("text", {
        x: 16, y: 40, fill: "#5a3010", "font-size": 13, "font-family": "IBM Plex Mono, monospace"
      })).textContent = "Waiting for the public BTC candles.";
      return;
    }
    var clip = book ? focusClip(book) : null;
    var levels = clip ? clipLevels(book, clip) : null;
    var candlePx = [];
    candles.forEach(function (c) { candlePx.push(c.h, c.l); });
    if (feed && feed.price > 0) candlePx.push(feed.price);
    if (clip && clip.entry > 0) candlePx.push(clip.entry);
    var allPx = candlePx.slice();
    if (clip && levels) {
      allPx.push(levels.win, levels.arm, levels.sl, levels.nextIn);
      if (levels.trail > 0) allPx.push(levels.trail);
    }
    var base = scaleMode === "levels" ? allPx : candlePx;
    if (scaleMode === "price" && userScale) base = [userScale.lo, userScale.hi];
    var lo = Math.min.apply(null, base);
    var hi = Math.max.apply(null, base);
    var span = hi - lo || 1;
    if (!(scaleMode === "price" && userScale)) {
      var pad = scaleMode === "price" ? 0.12 : 0.08;
      lo -= span * pad;
      hi += span * pad;
    }
    if (frozenScale) {
      lo = frozenScale.lo;
      hi = frozenScale.hi;
    }
    var above = [];
    var below = [];
    function park(px, color, key, text) {
      if (!(px > 0) || scaleMode !== "price") return false;
      if (chartDrag && chartDrag.key === key && !chartDrag.outside) return false;
      if (px > hi) {
        above.push({ px: px, color: color, key: key, text: text, above: true });
        return true;
      }
      if (px < lo) {
        below.push({ px: px, color: color, key: key, text: text, above: false });
        return true;
      }
      return false;
    }
    if (clip && levels) {
      park(levels.arm, "#c45c00", "arm", "ARM  " + pxText(levels.arm));
      park(levels.nextIn, "#2450d0", "next", (clip.held ? "NEXT IN held  " : "NEXT IN  ") + pxText(levels.nextIn));
      park(levels.win, "#018898", "win", "WIN  " + pxText(levels.win));
      park(levels.sl, "#d01228", "sl", "SL " + Math.round(book.cfg.slRoePct) + "%  " + pxText(levels.sl));
      if (levels.trail > 0) park(levels.trail, "#e04810", "trail", "TRAIL  " + pxText(levels.trail));
    }
    above.sort(function (a, b) { return b.px - a.px; });
    below.sort(function (a, b) { return a.px - b.px; });
    padT = 16 + above.length * 16;
    padB = 16 + below.length * 16;
    var plotW = W - padL - padR;
    var plotH = H - padT - padB;
    if (plotH < 40) plotH = 40;
    function yOf(px) {
      var y = padT + ((hi - px) / (hi - lo || 1)) * plotH;
      if (y < padT) return padT;
      if (y > padT + plotH) return padT + plotH;
      return y;
    }
    function inWindow(px) {
      return px >= lo && px <= hi;
    }
    function isParked(key) {
      var i;
      for (i = 0; i < above.length; i++) if (above[i].key === key) return true;
      for (i = 0; i < below.length; i++) if (below[i].key === key) return true;
      return false;
    }
    var liveY = feed && feed.price > 0 ? yOf(feed.price) : null;
    for (var g = 0; g <= 4; g++) {
      var gy = padT + (plotH * g) / 4;
      var gpx = hi - ((hi - lo) * g) / 4;
      svg.appendChild(svgEl("line", {
        x1: padL, x2: padL + plotW, y1: gy.toFixed(1), y2: gy.toFixed(1),
        stroke: "#c85a00", "stroke-dasharray": "3 4"
      }));
      if (liveY != null && Math.abs(gy - liveY) < 14) continue;
      var gl = svgEl("text", {
        x: padL + plotW + 8, y: (gy + 4).toFixed(1), fill: g === 0 || g === 4 ? "#3a1c04" : "#5a3010",
        "font-size": 11, "font-family": "IBM Plex Mono, monospace"
      });
      gl.textContent = pxText(gpx);
      svg.appendChild(gl);
    }
    var cw = plotW / candles.length;
    candles.forEach(function (c, i) {
      var x = padL + i * cw;
      var mid = x + cw / 2;
      var up = c.c >= c.o;
      svg.appendChild(svgEl("line", {
        x1: mid.toFixed(1), x2: mid.toFixed(1),
        y1: yOf(c.h).toFixed(1), y2: yOf(c.l).toFixed(1),
        stroke: "#3a1c04", "stroke-width": 1
      }));
      var y1 = yOf(Math.max(c.o, c.c));
      var y2 = yOf(Math.min(c.o, c.c));
      var body = {
        x: (x + 1).toFixed(1),
        y: y1.toFixed(1),
        width: Math.max(2, cw - 2).toFixed(1),
        height: Math.max(1, y2 - y1).toFixed(1),
        fill: up ? "#3a1c04" : "#fff6ec"
      };
      if (!up) { body.stroke = "#3a1c04"; body["stroke-width"] = "1"; }
      svg.appendChild(svgEl("rect", body));
    });
    var lines = [];
    function addLine(px, color, key) {
      if (!(px > 0)) return;
      var y = yOf(px);
      var attrs = {
        x1: padL + 2, x2: padL + plotW - 2,
        y1: y.toFixed(1), y2: y.toFixed(1),
        stroke: color,
        "stroke-width": (key && chartDrag && chartDrag.key === key) ? "3.2" : "1.6",
        "stroke-dasharray": "5 4"
      };
      if (key) {
        attrs["data-line"] = key;
        attrs["data-w"] = "1.6";
        lines.push({ key: key, y: y, px: px });
      }
      svg.appendChild(svgEl("line", attrs));
    }
    var labels = [];
    function addLabel(px, text, color, right) {
      if (!(px > 0)) return;
      labels.push({ y: yOf(px), text: text, color: color, right: right, px: px });
    }
    if (clip && levels) {
      if (inWindow(clip.entry)) addLine(clip.entry, "#3a1c04");
      if (!isParked("arm")) addLine(levels.arm, "#6a3a08", "arm");
      if (!isParked("next")) addLine(levels.nextIn, "#243e8a", "next");
      if (!isParked("win")) addLine(levels.win, "#024e58", "win");
      if (!isParked("sl")) addLine(levels.sl, "#8a1020", "sl");
      if (levels.trail > 0 && !isParked("trail")) addLine(levels.trail, "#8a2800", "trail");
      if (inWindow(clip.entry)) addLabel(clip.entry, "IN  " + pxText(clip.entry), "#3a1c04", false);
      if (!isParked("arm")) addLabel(levels.arm, "ARM  " + pxText(levels.arm), "#c45c00", false);
      if (!isParked("next")) addLabel(levels.nextIn, (clip.held ? "NEXT IN held  " : "NEXT IN  ") + pxText(levels.nextIn), "#2450d0", false);
      if (!isParked("win")) addLabel(levels.win, "WIN  " + pxText(levels.win), "#018898", true);
      if (!isParked("sl")) addLabel(levels.sl, "SL " + Math.round(book.cfg.slRoePct) + "%  " + pxText(levels.sl), "#d01228", true);
      if (levels.trail > 0 && !isParked("trail")) addLabel(levels.trail, "TRAIL  " + pxText(levels.trail), "#e04810", true);
    }
    if (feed && feed.price > 0 && inWindow(feed.price)) {
      var py = yOf(feed.price);
      svg.appendChild(svgEl("line", {
        x1: padL, x2: padL + plotW,
        y1: py.toFixed(1), y2: py.toFixed(1),
        stroke: "#3a1c04", "stroke-width": 1.4
      }));
      var live = svgEl("text", {
        x: padL + plotW + 8, y: (py + 4).toFixed(1), fill: "#3a1c04",
        "font-size": 12, "font-family": "IBM Plex Mono, monospace"
      });
      live.textContent = pxText(feed.price);
      svg.appendChild(live);
    }
    function spread(group) {
      group.sort(function (a, b) { return a.y - b.y; });
      for (var i = 1; i < group.length; i++) {
        if (group[i].y < group[i - 1].y + 18) group[i].y = group[i - 1].y + 18;
      }
      for (var j = group.length - 2; j >= 0; j--) {
        if (group[j].y > group[j + 1].y - 18) group[j].y = group[j + 1].y - 18;
      }
    }
    var lefts = labels.filter(function (l) { return !l.right; });
    var rights = labels.filter(function (l) { return l.right; });
    spread(lefts);
    spread(rights);
    function paintName(x, y, text, color, anchor) {
      var node = svgEl("text", {
        x: x, y: y.toFixed(1), fill: color,
        stroke: "#fff6ec", "stroke-width": "5", "stroke-linejoin": "round",
        "paint-order": "stroke",
        "font-size": "15", "font-weight": "600",
        "font-family": "IBM Plex Mono, monospace",
        "text-anchor": anchor || "start"
      });
      node.textContent = text;
      svg.appendChild(node);
    }
    labels.forEach(function (lab) {
      var anchor = lab.right ? "end" : "start";
      var x = lab.right ? (padL + plotW - 8) : (padL + 8);
      var yy = Math.max(padT + 12, Math.min(padT + plotH - 4, lab.y));
      paintName(x, yy, lab.text, lab.color, anchor);
    });
    function drawChip(chip, y) {
      var on = chartDrag && chartDrag.key === chip.key;
      svg.appendChild(svgEl("line", {
        x1: padL + 2,
        x2: padL + 28,
        y1: y.toFixed(1),
        y2: y.toFixed(1),
        stroke: chip.color,
        "stroke-width": on ? "3.2" : "2",
        "data-line": chip.key,
        "data-w": "2"
      }));
      paintName(padL + 34, y + 4, chip.text, chip.color, "start");
      lines.push({ key: chip.key, y: y, px: chip.px, outside: true, above: chip.above });
    }
    function chipY(chip, rest) {
      if (!chartDrag || chartDrag.key !== chip.key || !chartDrag.outside) return rest;
      var edge = chip.above ? padT : (padT + plotH);
      var span = chip.above ? (chartDrag.startPx - hi) : (lo - chartDrag.startPx);
      if (!(span > 0)) return rest;
      var moved = chip.above ? (chartDrag.startPx - chip.px) : (chip.px - chartDrag.startPx);
      var t = moved / span;
      if (t < 0) t = 0;
      if (t > 1) t = 1;
      return rest + (edge - rest) * t;
    }
    above.forEach(function (chip, i) { drawChip(chip, chipY(chip, 13 + i * 16)); });
    below.forEach(function (chip, i) { drawChip(chip, chipY(chip, H - 6 - i * 16)); });
    (marks || []).forEach(function (m) {
      if (!candles[m.i] || !(m.px > 0) || !inWindow(m.px)) return;
      var x = padL + m.i * cw + cw / 2;
      var y = yOf(m.px);
      var col = m.kind === "in" ? "#3a1c04" : (String(m.reason || "").indexOf("SL") === 0 ? "#8a1020" : "#024e58");
      svg.appendChild(svgEl("rect", {
        x: (x - 4).toFixed(1), y: (y - 4).toFixed(1), width: 8, height: 8,
        fill: col, stroke: "#fff6ec", "stroke-width": 1
      }));
    });
    chartView = {
      lo: lo,
      hi: hi,
      padT: padT,
      plotH: plotH,
      entry: clip ? clip.entry : 0,
      side: clip ? clip.side : "",
      lev: clip && clip.lev > 0 ? clip.lev : 1,
      extreme: clip && clip.extreme > 0 ? clip.extreme : 0,
      lines: lines
    };
    if (chartDrag) svg.style.cursor = "ns-resize";
    var resetBtn = document.getElementById("easy-reset-in");
    if (resetBtn) {
      resetBtn.classList.toggle("on", !!(book && book.live && clip && clip.open));
      resetBtn.textContent = "Reset IN";
    }
    if (note) {
      var dragNote = scaleMode === "levels"
        ? "Levels fits the settings. Drag a line and the knob follows."
        : "Price fits this hour. Drag a line, or a setting at the edge, and the knob follows.";
      if (book && book.live && clip && clip.open) {
        note.textContent = "IN entered at " + pxText(clip.entry) + ". This clip is open on the test account with the settings now. WIN, ARM, and SL follow the knobs. No order is sent.";
      } else if (book && book.live) {
        note.textContent = "The live clip is flat. Reset rebuy enters a new one at the live price with the settings now.";
      } else if (!book) note.textContent = "BTC print only. Wire the chain to see the open, the out, and the rebuy.";
      else if (!clip) note.textContent = "No open on this hour. FLAT stays idle.";
      else if (clip.open) note.textContent = dragNote + " IN is this open. WIN is the out. NEXT IN is the rebuy after that out.";
      else if (clip.held) note.textContent = dragNote + " OUT was the stop. NEXT IN stays held until LONG or SHORT is set again.";
      else note.textContent = dragNote + " OUT was " + (clip.outReason || "the close") + ".";
    }
  }

  function chainGap() {
    for (var i = 0; i < CHAIN.length; i++) {
      var a = document.getElementById(CHAIN[i][0]);
      var b = document.getElementById(CHAIN[i][1]);
      var pair = CHAIN[i][2];
      if (!a || !b || a.classList.contains("gone") || b.classList.contains("gone")) {
        return pair + " are not both on the board";
      }
      var wired = LINKS.some(function (w) { return w[0] === CHAIN[i][0] && w[1] === CHAIN[i][1]; });
      if (!wired) return "the wire between " + pair + " is open";
    }
    return "";
  }

  function play() {
    var gap = chainGap();
    if (gap) {
      paint({
        equity: 1000,
        roe: null,
        realized: 0,
        status: gap.charAt(0).toUpperCase() + gap.slice(1) + ". Connect the DMan Maker chain to see the hour.",
        side: null,
        bias: "FLAT",
        entry: 0,
        curve: []
      });
      var side = document.getElementById("easy-side");
      if (side) side.textContent = "Not wired";
      paintChart(null, null);
      return;
    }
    if (!feed) {
      var status = document.getElementById("easy-status");
      if (status) status.textContent = "Waiting for the public BTC print.";
      paintChart(null, null);
      return;
    }
    if (!liveBook && !holdChecked && feed.price > 0) {
      holdChecked = true;
      var peeked = replay();
      if (peeked.book.slHold && (bias === "LONG" || bias === "SHORT")) {
        startRebuy(peeked.book);
        play();
        return;
      }
      paint(bookSnap(peeked.book));
      paintChart(peeked.book, peeked.marks);
      return;
    }
    if (liveBook) {
      paintLive();
      return;
    }
    var run = replay();
    paint(bookSnap(run.book));
    paintChart(run.book, run.marks);
  }

  function bookSnap(book) {
    var snap = D.snapshot(book);
    snap.trades = book && book.trades ? book.trades : [];
    return snap;
  }

  function tradeTime(ts) {
    var n = Number(ts);
    if (!(n > 0)) return "—";
    if (n < 1e12) n *= 1000;
    var d = new Date(n);
    if (isNaN(d.getTime())) return "—";
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    function two(v) { return (v < 10 ? "0" : "") + v; }
    return months[d.getMonth()] + " " + two(d.getDate()) + " " +
      two(d.getHours()) + ":" + two(d.getMinutes()) + ":" + two(d.getSeconds());
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function paintLog(snap) {
    var scroll = document.getElementById("easy-log-scroll");
    var body = document.getElementById("easy-log-body");
    var count = document.getElementById("easy-log-count");
    var empty = document.getElementById("easy-log-empty");
    if (!scroll || !body) return;
    var trades = (snap && snap.trades) || [];
    var last = trades.length ? trades[trades.length - 1] : null;
    var key = trades.length + ":" + (last ? last.ts + ":" + last.reason + ":" + last.price : "");
    var open = snap && snap.side && snap.entry > 0
      ? snap.side + " open at " + pxText(snap.entry)
      : "No open clip";
    if (count) {
      count.textContent = trades.length + " closed · " + open;
    }
    if (empty) empty.hidden = trades.length > 0;
    var table = scroll.querySelector("table");
    if (table) table.hidden = trades.length === 0;
    if (key === logKey) {
      if (logPinned) scroll.scrollTop = scroll.scrollHeight;
      return;
    }
    logKey = key;
    var html = "";
    for (var i = 0; i < trades.length; i++) {
      var t = trades[i];
      var pnl = Number(t.pnl) || 0;
      var cls = pnl > 0.004 ? "win" : (pnl < -0.004 ? "loss" : "");
      html += "<tr" + (cls ? " class=\"" + cls + "\"" : "") + ">" +
        "<td>" + esc(tradeTime(t.ts)) + "</td>" +
        "<td>" + esc(t.side || "—") + "</td>" +
        "<td>" + esc(pxText(t.entry)) + "</td>" +
        "<td>" + esc(pxText(t.price)) + "</td>" +
        "<td>" + esc(t.reason || "—") + "</td>" +
        "<td>" + esc(money(pnl)) + "</td>" +
        "<td>" + esc(roeText(t.roe)) + "</td></tr>";
    }
    body.innerHTML = html;
    if (logPinned) scroll.scrollTop = scroll.scrollHeight;
  }

  function paintLive() {
    D.applyConfig(liveBook, cfg());
    liveBook.live = true;
    if (feed.price > 0) D.stepPrice(liveBook, feed.price, feed.asof || Date.now());
    var i = Math.max(0, ((feed.candles || []).length) - 1);
    var marks = [];
    if (liveBook.side && liveBook.entry > 0) {
      marks.push({ i: i, kind: "in", px: liveBook.entry, side: liveBook.side });
    } else if (liveBook.trades.length) {
      var last = liveBook.trades[liveBook.trades.length - 1];
      if (last.reason !== "reset IN") {
        marks.push({ i: i, kind: "out", px: last.price, reason: last.reason, side: last.side, entry: last.entry });
      }
    }
    paint(bookSnap(liveBook));
    paintChart(liveBook, marks);
    var src = document.getElementById("easy-chart-src");
    if (src && feed) src.textContent = (feed.source || "public") + " · live rebuy";
  }

  function startRebuy(book) {
    D.applyConfig(book, cfg());
    D.resetEntry(book, feed.price, feed.asof || Date.now());
    book.live = true;
    liveBook = book;
    userScale = null;
    frozenScale = null;
    armPoll();
  }

  function enterLive() {
    if (chainGap() || !feed || !(feed.price > 0)) return;
    if (bias !== "LONG" && bias !== "SHORT") {
      var status = document.getElementById("easy-status");
      if (status) status.textContent = "Set LONG or SHORT. Reset rebuy enters that side at the live price.";
      return;
    }
    holdChecked = true;
    if (!liveBook) liveBook = replay().book;
    startRebuy(liveBook);
    play();
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(play, 120);
  }

  function slug(name) {
    return String(name || "clip").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "clip";
  }

  function clips() {
    return Array.prototype.slice.call(stage.querySelectorAll(".clip"));
  }

  function portPoint(id, side) {
    var sel = side === "risk"
      ? "#" + id + " .port.out.risk"
      : side === "out"
        ? "#" + id + " .port.out:not(.risk)"
        : "#" + id + " .port.in";
    var el = document.querySelector(sel);
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
    var svg = document.getElementById("easy-wires");
    if (!svg) return;
    svg.textContent = "";
    LINKS.forEach(function (pair) {
      var a = portPoint(pair[0], pair[2] === "risk" ? "risk" : "out");
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
    var w = 1100;
    var h = 480;
    clips().forEach(function (el) {
      if (el.classList.contains("gone")) return;
      w = Math.max(w, el.offsetLeft + el.offsetWidth + 48);
      h = Math.max(h, el.offsetTop + el.offsetHeight + 36);
    });
    stage.style.width = w + "px";
    stage.style.height = h + "px";
  }

  function saveLayout() {
    var pos = {};
    var extras = [];
    clips().forEach(function (el) {
      pos[el.id] = { x: el.offsetLeft, y: el.offsetTop, gone: el.classList.contains("gone") };
      if (el.dataset.extra === "1" && !el.classList.contains("gone")) {
        extras.push({
          id: el.id,
          name: el.dataset.label || "clip",
          cat: el.dataset.cat || "Controller",
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

  function onBoard(item) {
    var id = item.risk ? "e-risk" : (item.board || ("e-x-" + slug(item.name)));
    var el = document.getElementById(id);
    return !!(el && !el.classList.contains("gone"));
  }

  function paintTray() {
    var tools = document.getElementById("easy-tools");
    if (!tools) return;
    Array.prototype.forEach.call(tools.querySelectorAll(".easy-tool"), function (btn) {
      var on = btn.dataset.board
        ? onBoard({ board: btn.dataset.board })
        : onBoard({ name: btn.dataset.name });
      btn.classList.toggle("on", on);
      var st = btn.querySelector(".st");
      if (st) st.textContent = on ? "on" : "add";
    });
  }

  function inPortAt(clientX, clientY) {
    var hit = document.elementFromPoint(clientX, clientY);
    var inn = hit && hit.closest ? hit.closest(".port.in") : null;
    if (inn && stage.contains(inn)) return inn;
    var ports = stage.querySelectorAll(".clip:not(.gone) .port.in");
    var best = null;
    var bestD = 28;
    Array.prototype.forEach.call(ports, function (port) {
      var r = port.getBoundingClientRect();
      var dx = clientX - (r.left + r.width / 2);
      var dy = clientY - (r.top + r.height / 2);
      var d = Math.sqrt(dx * dx + dy * dy);
      if (d < bestD) { bestD = d; best = port; }
    });
    return best;
  }

  function stagePoint(clientX, clientY) {
    var r = stage.getBoundingClientRect();
    return { x: clientX - r.left, y: clientY - r.top };
  }

  function reveal(el) {
    var sc = document.getElementById("easy-scroll");
    if (!sc || !el) return;
    var left = el.offsetLeft - 16;
    var top = el.offsetTop - 16;
    if (left < sc.scrollLeft || left + el.offsetWidth > sc.scrollLeft + sc.clientWidth) {
      sc.scrollLeft = Math.max(0, left);
    }
    if (top < sc.scrollTop || top + 80 > sc.scrollTop + sc.clientHeight) {
      sc.scrollTop = Math.max(0, top);
    }
  }

  function bindNode(el) {
    if (!el || el.dataset.bound === "1") return;
    el.dataset.bound = "1";
    el.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) return;
      if (e.target.closest("input, button, textarea, select, a, .port, .knob")) return;
      var origL = el.offsetLeft;
      var origT = el.offsetTop;
      var sx = e.clientX;
      var sy = e.clientY;
      el.classList.add("dragging");
      try { el.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      function move(ev) {
        el.style.left = Math.max(8, origL + ev.clientX - sx) + "px";
        el.style.top = Math.max(8, origT + ev.clientY - sy) + "px";
        growStage();
        drawWires();
      }
      function up(ev) {
        el.classList.remove("dragging");
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerup", up);
        el.removeEventListener("pointercancel", up);
        var tr = tray.getBoundingClientRect();
        var ontoTray = ev.clientX >= tr.left && ev.clientX <= tr.right && ev.clientY >= tr.top && ev.clientY <= tr.bottom;
        if (ontoTray) {
          removeNode(el.id);
          return;
        }
        saveLayout();
      }
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", up);
      el.addEventListener("pointercancel", up);
      e.preventDefault();
    });
  }

  function makeRisk(x, y) {
    var el = document.getElementById("e-risk");
    if (!el) {
      el = document.createElement("article");
      el.className = "clip risk";
      el.id = "e-risk";
      el.dataset.extra = "1";
      el.dataset.risk = "1";
      el.dataset.label = "Add risk wallet";
      el.dataset.cat = "Wallet";
      el.dataset.color = "#ff4d6d";
      var port = document.createElement("i");
      port.className = "port in";
      port.title = "In. Drop the controller's extra out here.";
      el.appendChild(port);
      var head = document.createElement("header");
      head.textContent = "RISK wallet";
      var strong = document.createElement("strong");
      strong.id = "easy-risk-amt";
      strong.textContent = "$0.00";
      var sub = document.createElement("p");
      sub.id = "easy-risk-note";
      sub.textContent = "Wire the extra out on the controller to this in. Every second win is copied here.";
      el.appendChild(head);
      el.appendChild(strong);
      el.appendChild(sub);
      el.style.borderColor = "#ff4d6d";
      stage.appendChild(el);
      bindNode(el);
    }
    el.classList.remove("gone");
    el.style.left = Math.max(8, x) + "px";
    el.style.top = Math.max(8, y) + "px";
    return el;
  }

  function makeExtra(item, x, y) {
    var id = "e-x-" + slug(item.name);
    var el = document.getElementById(id);
    if (!el) {
      el = document.createElement("article");
      el.className = "clip extra";
      el.id = id;
      el.dataset.extra = "1";
      el.dataset.label = item.name;
      el.dataset.cat = item.cat || "Controller";
      el.dataset.color = item.color || "#a78bfa";
      ["in", "out"].forEach(function (side) {
        var port = document.createElement("i");
        port.className = "port " + side;
        port.title = side === "out" ? "Out. Drag onto an in port." : "In";
        el.appendChild(port);
      });
      var head = document.createElement("header");
      head.textContent = item.cat || "Controller";
      var strong = document.createElement("strong");
      strong.textContent = item.name;
      var sub = document.createElement("p");
      sub.textContent = "Copied in. The paper hour still runs the wired DMan Maker.";
      el.appendChild(head);
      el.appendChild(strong);
      el.appendChild(sub);
      if (item.color) el.style.borderColor = item.color;
      stage.appendChild(el);
      bindNode(el);
    }
    el.classList.remove("gone");
    el.style.left = Math.max(8, x) + "px";
    el.style.top = Math.max(8, y) + "px";
    return el;
  }

  function placeItem(item, x, y) {
    var el = null;
    if (item.risk) {
      el = makeRisk(x, y);
    } else if (item.board) {
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
    paintTray();
    schedule();
    reveal(el);
  }

  function removeNode(id) {
    var el = document.getElementById(id);
    if (!el) return;
    LINKS = LINKS.filter(function (w) { return w[0] !== id && w[1] !== id; });
    if (el.dataset.extra === "1") el.remove();
    else el.classList.add("gone");
    growStage();
    drawWires();
    saveLayout();
    paintTray();
    schedule();
  }

  function deleteWire(key) {
    LINKS = LINKS.filter(function (w) { return w[0] + ">" + w[1] !== key; });
    drawWires();
    saveLayout();
    schedule();
  }

  function standardWire() {
    clips().forEach(function (el) {
      if (el.dataset.extra === "1") el.remove();
    });
    Object.keys(HOME).forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.classList.remove("gone");
      el.style.left = HOME[id].x + "px";
      el.style.top = HOME[id].y + "px";
    });
    LINKS = CHAIN.map(function (row) { return [row[0], row[1]]; });
    growStage();
    drawWires();
    saveLayout();
    paintTray();
    schedule();
    var sc = document.getElementById("easy-scroll");
    if (sc) { sc.scrollLeft = 0; sc.scrollTop = 0; }
  }

  function restoreLayout() {
    var raw = null;
    try { raw = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "null"); } catch (err) { raw = null; }
    if (!raw) return;
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
      LINKS = raw.wires.filter(function (w) {
        return w && w[0] && w[1] && w[0] !== w[1] && w[0] !== "e-risk" && w[1] !== "e-risk" && w[2] !== "risk";
      });
    }
  }

  function buildTray() {
    var root = document.getElementById("easy-tools");
    TRAY.forEach(function (item) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "easy-tool";
      b.draggable = true;
      b.dataset.name = item.name;
      if (item.board) b.dataset.board = item.board;
      var dot = document.createElement("i");
      dot.className = "dot";
      dot.style.background = item.color;
      var lab = document.createElement("span");
      lab.className = "lab";
      lab.textContent = item.name;
      if (item.risk) b.dataset.risk = "1";
      var st = document.createElement("span");
      st.className = "st";
      st.textContent = "add";
      b.appendChild(dot);
      b.appendChild(lab);
      b.appendChild(st);
      var dragged = false;
      b.addEventListener("dragstart", function (e) {
        dragged = true;
        var payload = { name: item.name, cat: item.cat, color: item.color, board: item.board || "", risk: !!item.risk };
        e.dataTransfer.setData("application/x-rimbaud-node", JSON.stringify(payload));
        e.dataTransfer.setData("text/plain", JSON.stringify(payload));
        e.dataTransfer.effectAllowed = "copy";
      });
      b.addEventListener("click", function () {
        if (dragged) { dragged = false; return; }
        if (onBoard(item)) {
          var id = item.risk ? "e-risk" : (item.board || ("e-x-" + slug(item.name)));
          reveal(document.getElementById(id));
          return;
        }
        var sc = document.getElementById("easy-scroll");
        var x = item.risk ? 12 : (sc ? sc.scrollLeft : 0) + 28;
        var y = item.risk ? 330 : (sc ? sc.scrollTop : 0) + 28;
        placeItem(item, x, y);
      });
      root.appendChild(b);
    });
  }

  var menuEl = null;
  var menuCtx = null;

  function closeMenu() {
    if (!menuEl) return;
    menuEl.menu.hidden = true;
    menuEl.backdrop.hidden = true;
    menuCtx = null;
  }

  function openMenu(x, y, ctx) {
    menuCtx = ctx;
    var menu = menuEl.menu;
    menu.textContent = "";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = ctx.kind === "edge" ? "Delete wire" : "Take off the board";
    btn.addEventListener("click", function () {
      if (menuCtx && menuCtx.kind === "edge") deleteWire(menuCtx.id);
      else if (menuCtx && menuCtx.kind === "node") removeNode(menuCtx.id);
      closeMenu();
    });
    menu.appendChild(btn);
    menu.hidden = false;
    menuEl.backdrop.hidden = false;
    menu.style.left = Math.max(8, x) + "px";
    menu.style.top = Math.max(8, y) + "px";
  }

  function installBoard() {
    clips().forEach(bindNode);
    restoreLayout();
    clips().forEach(bindNode);
    growStage();

    stage.addEventListener("pointerdown", function (e) {
      var port = e.target.closest && e.target.closest(".port.out");
      if (!port || e.button !== 0) return;
      var node = port.closest(".clip");
      if (!node || node.classList.contains("gone")) return;
      var which = port.classList.contains("risk") ? "risk" : "out";
      var a = portPoint(node.id, which);
      if (!a) return;
      linkDrag = { from: node.id, port: which, a: a, b: { x: a.x, y: a.y } };
      e.preventDefault();
      e.stopPropagation();
      function move(ev) {
        var sr = stage.getBoundingClientRect();
        linkDrag.b = { x: ev.clientX - sr.left, y: ev.clientY - sr.top };
        linkDrag.a = portPoint(node.id, linkDrag.port) || linkDrag.a;
        drawWires();
      }
      function up(ev) {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        var inn = inPortAt(ev.clientX, ev.clientY);
        var dest = inn && inn.closest(".clip");
        if (dest && dest.id !== node.id && !dest.classList.contains("gone")) {
          var riskLink = linkDrag.port === "risk" || dest.id === "e-risk";
          if (riskLink && node.id === "e-ctrl" && dest.id === "e-risk") {
            var already = LINKS.some(function (w) { return w[0] === "e-ctrl" && w[1] === "e-risk"; });
            if (!already) LINKS.push(["e-ctrl", "e-risk", "risk"]);
          } else if (!riskLink) {
            var exists = LINKS.some(function (w) { return w[0] === node.id && w[1] === dest.id; });
            if (!exists) LINKS.push([node.id, dest.id]);
          }
        }
        linkDrag = null;
        drawWires();
        saveLayout();
        schedule();
      }
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    });

    var scroller = document.getElementById("easy-scroll");
    function acceptDrop(e) {
      var types = e.dataTransfer ? Array.prototype.slice.call(e.dataTransfer.types || []) : [];
      var r = stage.getBoundingClientRect();
      var inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
      return inside && (types.indexOf("application/x-rimbaud-node") >= 0 || types.indexOf("text/plain") >= 0);
    }
    scroller.addEventListener("dragover", function (e) {
      if (!acceptDrop(e)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    });
    scroller.addEventListener("drop", function (e) {
      if (!acceptDrop(e)) return;
      var raw = e.dataTransfer && (e.dataTransfer.getData("application/x-rimbaud-node") || e.dataTransfer.getData("text/plain"));
      if (!raw) return;
      e.preventDefault();
      var item = null;
      try { item = JSON.parse(raw); } catch (err) { item = null; }
      if (!item || !item.name) return;
      var p = stagePoint(e.clientX, e.clientY);
      placeItem(item, p.x - 24, p.y - 16);
      closeMenu();
    });

    var backdrop = document.createElement("div");
    backdrop.className = "easy-backdrop";
    backdrop.hidden = true;
    var menu = document.createElement("div");
    menu.className = "easy-menu";
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
    stage.addEventListener("contextmenu", function (e) {
      if (e.target.closest("input, textarea, .knob")) return;
      var wire = e.target.closest("[data-wire]");
      var node = e.target.closest(".clip");
      if (!wire && !node) return;
      e.preventDefault();
      openMenu(e.clientX, e.clientY, wire ? { kind: "edge", id: wire.dataset.wire } : { kind: "node", id: node.id });
    });

    drawWires();
    paintTray();
  }

  frame.addEventListener("click", function (ev) {
    var resetBtn = ev.target.closest && ev.target.closest("[data-reset-in]");
    if (resetBtn) {
      enterLive();
      return;
    }
    var scaleBtn = ev.target.closest && ev.target.closest("[data-scale]");
    if (scaleBtn) {
      scaleMode = scaleBtn.getAttribute("data-scale") === "levels" ? "levels" : "price";
      userScale = null;
      frozenScale = null;
      frame.querySelectorAll("[data-scale]").forEach(function (el) {
        el.classList.toggle("on", el === scaleBtn);
      });
      play();
      return;
    }
    var b = ev.target.closest && ev.target.closest("[data-bias]");
    if (b) {
      bias = b.getAttribute("data-bias");
      var group = b.closest("[role='group']") || frame;
      group.querySelectorAll("[data-bias]").forEach(function (el) {
        el.classList.toggle("on", el === b);
      });
      if (liveBook) {
        D.arm(liveBook, bias);
        if ((bias === "LONG" || bias === "SHORT") && liveBook.side !== bias && feed && feed.price > 0) {
          D.resetEntry(liveBook, feed.price, feed.asof || Date.now());
          liveBook.live = true;
        }
      }
      schedule();
      return;
    }
    var flag = ev.target.closest && ev.target.closest("[data-bool]");
    if (flag) {
      var key = flag.getAttribute("data-bool");
      if (key === "crazy") crazy = !crazy;
      if (key === "rebuy") rebuy = !rebuy;
      var on = key === "crazy" ? crazy : rebuy;
      flag.classList.toggle("on", on);
      flag.textContent = on ? "on" : "off";
      schedule();
    }
  });

  frame.addEventListener("input", function (ev) {
    if (ev.target.id === "easy-sl") syncSl("easy-sl", "easy-sl2");
    if (ev.target.id === "easy-sl2") {
      syncSl("easy-sl2", "easy-sl");
      paintKnob("easy-sl");
    }
    schedule();
  });

  function takeFeed(data) {
    feed = data || null;
    var printed = feed && feed.price
      ? "$" + Number(feed.price).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : "";
    var px = document.getElementById("easy-px");
    if (px && printed) px.textContent = printed + (feed.source ? " · " + feed.source : "");
    var chartPx = document.getElementById("easy-chart-px");
    if (chartPx && printed) chartPx.textContent = printed;
    var src = document.getElementById("easy-chart-src");
    if (src && feed) src.textContent = (feed.source || "public") + " · last hour";
    play();
  }

  function load() {
    fetch("/api/btc").then(function (res) {
      if (!res.ok) throw new Error("btc");
      return res.json();
    }).then(takeFeed).catch(function () {
      var status = document.getElementById("easy-status");
      if (status && !feed) status.textContent = "The public BTC print is not available right now.";
    });
  }

  function armPoll() {
    clearInterval(pollTimer);
    pollTimer = setInterval(load, liveBook ? 4000 : 15000);
  }

  var reset = document.getElementById("easy-reset");
  if (reset) reset.addEventListener("click", standardWire);

  window.addEventListener("resize", function () {
    growStage();
    drawWires();
  });
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(function () { growStage(); drawWires(); });
  }
  var logScroll = document.getElementById("easy-log-scroll");
  if (logScroll) {
    logScroll.addEventListener("scroll", function () {
      logPinned = logScroll.scrollHeight - logScroll.scrollTop - logScroll.clientHeight < 28;
    });
  }
  syncSl("easy-sl", "easy-sl2");
  installKnobs();
  installChartDrag();
  buildTray();
  installBoard();
  load();
  armPoll();
})();
