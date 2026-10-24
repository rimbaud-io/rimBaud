/* Paper book for one wired bot. Prices are public BTC prints. No orders. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.RimbaudPaper = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function clamp(n, lo, hi) {
    return Math.min(hi, Math.max(lo, n));
  }

  function chainReaches(wires) {
    var from = {};
    wires.forEach(function (w) { from[w.to] = w.from; });
    var n = "bot";
    var seen = {};
    var passedBias = false;
    while (from[n] && !seen[n]) {
      seen[n] = true;
      n = from[n];
      if (n === "bias") passedBias = true;
    }
    return { wired: n === "btc", passedBias: passedBias };
  }

  function runPaper(prices, opts) {
    opts = opts || {};
    var pct = clamp(Number(opts.pct) || 0.002, 0.0005, 0.05);
    var bias = opts.bias === "long" || opts.bias === "short" ? opts.bias : "both";
    var stake = clamp(Number(opts.stake) || 1000, 1, 100000);
    var link = chainReaches(opts.wires || []);
    var wired = !!opts.wired && link.wired;
    var useBias = link.passedBias ? bias : "both";
    var clean = [];
    (prices || []).forEach(function (p) {
      p = Number(p);
      if (p > 0 && isFinite(p)) clean.push(p);
    });
    var curve = [];
    if (!clean.length) {
      return {
        equity: stake, realized: 0, upnl: 0, side: 0, entry: 0,
        opens: 0, curve: [stake], anchor: 0, wired: wired, useBias: useBias, stake: stake
      };
    }
    var anchor = clean[0];
    var side = 0;
    var entry = 0;
    var realized = 0;
    var opens = 0;
    for (var i = 0; i < clean.length; i++) {
      var price = clean[i];
      if (wired) {
        if (side === 0) {
          var chg = (price - anchor) / anchor;
          var want = 0;
          if (chg >= pct && useBias !== "short") want = 1;
          else if (chg <= -pct && useBias !== "long") want = -1;
          if (want) {
            side = want;
            entry = price;
            opens += 1;
          }
        } else {
          var ret = side * (price - entry) / entry;
          if (ret <= -pct || ret >= pct * 2) {
            realized += stake * ret;
            side = 0;
            entry = 0;
            anchor = price;
          }
        }
      }
      var mark = side ? stake * side * (price - entry) / entry : 0;
      curve.push(stake + realized + mark);
    }
    var last = clean[clean.length - 1];
    var upnl = side ? stake * side * (last - entry) / entry : 0;
    return {
      equity: stake + realized + upnl,
      realized: realized,
      upnl: upnl,
      side: side,
      entry: entry,
      opens: opens,
      curve: curve,
      anchor: anchor,
      wired: wired,
      useBias: useBias,
      stake: stake,
      last: last
    };
  }

  return { runPaper: runPaper, chainReaches: chainReaches };
});
