(function () {
  var COLORS = { btc: "#3ec6ff", pct: "#e6a322", bias: "#7cff6b" };
  var wires = [
    { from: "btc", to: "pct" },
    { from: "pct", to: "bias" },
    { from: "bias", to: "bot" }
  ];
  var armed = false;
  var bias = "both";
  var book = null;
  var pending = null;
  var stakeEl = document.getElementById("stake");
  var pctEl = document.getElementById("pct");
  var svg = document.getElementById("wires");

  function money(n) {
    var sign = n < 0 ? "-" : "";
    return sign + "$" + Math.abs(n).toFixed(2);
  }

  function opts() {
    return {
      wires: wires,
      wired: armed,
      bias: bias,
      stake: Number(stakeEl.value) || 1000,
      pct: (Number(pctEl.value) || 0.2) / 100
    };
  }

  function portCenter(node, dir) {
    var clip = document.querySelector('.clip[data-id="' + node + '"]');
    var port = clip.querySelector('.port.' + dir);
    var board = document.getElementById("board");
    var br = board.getBoundingClientRect();
    var pr = port.getBoundingClientRect();
    var sx = 880 / br.width;
    var sy = 320 / br.height;
    return {
      x: (pr.left + pr.width / 2 - br.left) * sx,
      y: (pr.top + pr.height / 2 - br.top) * sy
    };
  }

  function drawWires() {
    svg.replaceChildren();
    wires.forEach(function (w, i) {
      var a = portCenter(w.from, "out");
      var b = portCenter(w.to, "in");
      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      var c = Math.max(40, (b.x - a.x) / 2);
      path.setAttribute("d", "M " + a.x + " " + a.y + " C " + (a.x + c) + " " + a.y + ", " + (b.x - c) + " " + b.y + ", " + b.x + " " + b.y);
      path.setAttribute("class", "wire");
      path.setAttribute("stroke", COLORS[w.from] || "#7cff6b");
      path.dataset.index = String(i);
      path.style.cursor = "pointer";
      path.addEventListener("click", function () {
        wires.splice(i, 1);
        pending = null;
        paint();
      });
      svg.appendChild(path);
    });
  }

  function paint() {
    document.getElementById("pct-label").textContent = (Number(pctEl.value) || 0).toFixed(2) + "%";
    document.getElementById("bias-label").textContent = bias.toUpperCase();
    document.querySelectorAll(".port").forEach(function (el) {
      el.classList.toggle("hot", pending && el.dataset.node === pending && el.dataset.dir === "out");
    });
    drawWires();
    var link = window.RimbaudPaper.chainReaches(wires);
    var status = document.getElementById("status");
    if (!link.wired) status.textContent = "The bot is not wired to BTC. Click an out port, then an in port.";
    else if (!armed) status.textContent = "Wired. ARM paper to run this bot on the last hour of BTC.";
    else if (!link.passedBias) status.textContent = "BIAS is off the path, so this bot takes both directions.";
    else status.textContent = "Paper bot is on the wire. No order leaves this page.";

    if (!book) return;
    var prices = book.candles.map(function (c) { return c.c; });
    if (book.price && prices.length) prices[prices.length - 1] = Number(book.price);
    var run = window.RimbaudPaper.runPaper(prices, opts());
    var earned = run.equity - run.stake;
    document.getElementById("px").textContent = "$" + Number(book.price || run.last || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
    document.getElementById("eq").textContent = money(run.equity);
    var earnEl = document.getElementById("earn");
    earnEl.textContent = (earned >= 0 ? "+" : "") + money(earned);
    earnEl.classList.toggle("down", earned < 0);
    var pos = document.getElementById("pos");
    pos.textContent = run.side > 0 ? "LONG" : run.side < 0 ? "SHORT" : "FLAT";
    pos.classList.toggle("down", run.side < 0);
    document.getElementById("top-price").textContent = "BTC " + document.getElementById("px").textContent + " · " + (book.source || "");
    drawChart(run.curve, prices);
    if (armed && link.wired) {
      status.textContent = run.opens
        ? (run.opens + " paper clip" + (run.opens === 1 ? "" : "s") + " in this hour. " + (run.side ? "One is still open." : "Flat now."))
        : "No clip yet. BTC has not moved the PCT step from the mark.";
    }
  }

  function drawChart(curve, prices) {
    var chart = document.getElementById("chart");
    chart.replaceChildren();
    function line(values, color) {
      if (values.length < 2) return;
      var min = Math.min.apply(null, values);
      var max = Math.max.apply(null, values);
      var span = max - min || 1;
      var d = values.map(function (v, i) {
        var x = 12 + (856 * i) / (values.length - 1);
        var y = 104 - ((v - min) / span) * 84;
        return (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
      }).join(" ");
      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", color);
      path.setAttribute("stroke-width", "2");
      chart.appendChild(path);
    }
    line(prices, "#1d3a22");
    line(curve, "#7cff6b");
  }

  document.getElementById("board").addEventListener("click", function (ev) {
    var port = ev.target.closest(".port");
    if (!port) return;
    var node = port.dataset.node;
    var dir = port.dataset.dir;
    if (dir === "out") {
      pending = pending === node ? null : node;
      paint();
      return;
    }
    if (!pending || pending === node) return;
    wires = wires.filter(function (w) { return w.to !== node && !(w.from === pending && w.to === node); });
    wires.push({ from: pending, to: node });
    pending = null;
    paint();
  });

  document.querySelectorAll(".bias button").forEach(function (btn) {
    btn.addEventListener("click", function () {
      bias = btn.dataset.bias;
      document.querySelectorAll(".bias button").forEach(function (b) { b.classList.toggle("on", b === btn); });
      paint();
    });
  });
  stakeEl.addEventListener("input", paint);
  pctEl.addEventListener("input", paint);
  document.getElementById("arm").addEventListener("click", function () {
    armed = !armed;
    this.textContent = armed ? "DISARM" : "ARM paper";
    paint();
  });
  window.addEventListener("resize", paint);

  function pull() {
    fetch("/api/btc").then(function (res) {
      if (!res.ok) throw new Error("bad");
      return res.json();
    }).then(function (body) {
      book = body;
      document.getElementById("src-tag").textContent = body.source || "BTC";
      paint();
    }).catch(function () {
      document.getElementById("status").textContent = "Live BTC is not reachable from this server right now.";
    });
  }
  pull();
  setInterval(pull, 5000);
})();
