/* Test-account book for the standard DMan Maker chain.
   Live BTC prints only. No orders, no desk, no keys.
   Entry matches the hunter: market-open leftover margin when BIAS is
   LONG or SHORT. FLAT stays idle and never flips an open clip.
   Exits: stop-loss ROE, take-profit as a price percent, then trail. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.RimbaudDman = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function clamp(n, lo, hi) {
    n = Number(n);
    if (!isFinite(n)) n = lo;
    return Math.min(hi, Math.max(lo, n));
  }

  function standardConfig() {
    return {
      leverage: 40,
      marginPct: 1,
      minMarginUsd: 1,
      minNotionalUsd: 10,
      takeProfit: 0.02,
      trailActivation: 0.015,
      trailDelta: 0.005,
      slRoePct: 20,
      cooldownSec: 15,
      dontGoCrazy: true,
      afterLossNoRebuy: true,
      doubleWinRoe: 20,
      doubleWinWaitSec: 1800,
      doubleWinDipRoe: 30,
      refreshSec: 30,
      bias: "LONG",
      startEquity: 1000,
      dcaSpreads: [0.002, 0.004, 0.008, 0.012]
    };
  }

  function normalize(cfg) {
    var s = standardConfig();
    var c = Object.assign({}, s, cfg || {});
    c.leverage = Math.round(clamp(c.leverage, 1, 50));
    c.marginPct = clamp(c.marginPct, 0.01, 1);
    c.minMarginUsd = clamp(c.minMarginUsd, 0, 100000);
    c.minNotionalUsd = clamp(c.minNotionalUsd, 0, 100000);
    c.takeProfit = clamp(c.takeProfit, 0.001, 0.5);
    c.trailActivation = clamp(c.trailActivation, 0.001, 0.2);
    c.trailDelta = clamp(c.trailDelta, 0.001, 0.1);
    c.slRoePct = clamp(c.slRoePct, 1, 100);
    c.cooldownSec = clamp(c.cooldownSec, 0, 3600);
    c.doubleWinRoe = clamp(c.doubleWinRoe, 1, 500);
    c.doubleWinWaitSec = clamp(c.doubleWinWaitSec, 0, 86400);
    c.doubleWinDipRoe = clamp(c.doubleWinDipRoe, 1, 500);
    c.refreshSec = Math.round(clamp(c.refreshSec, 10, 600));
    c.startEquity = clamp(c.startEquity, 10, 1000000);
    c.dontGoCrazy = !!c.dontGoCrazy;
    c.afterLossNoRebuy = !!c.afterLossNoRebuy;
    var b = String(c.bias || "FLAT").toUpperCase();
    c.bias = (b === "LONG" || b === "SHORT") ? b : "FLAT";
    return c;
  }

  function createBook(cfg) {
    var c = normalize(cfg);
    return {
      cfg: c,
      cash: c.startEquity,
      side: null,
      entry: 0,
      qty: 0,
      margin: 0,
      levUsed: c.leverage,
      trailOn: false,
      extreme: 0,
      lastCloseTs: 0,
      slHold: false,
      winRoes: [],
      trades: [],
      curve: [{ equity: c.startEquity, px: 0, roe: null }],
      status: c.bias === "FLAT"
        ? "FLAT — idle until LONG or SHORT"
        : c.bias + " armed · test account",
      mark: 0,
      lastTs: 0
    };
  }

  function roeAt(side, entry, px, lev) {
    if (!side || !(entry > 0) || !(px > 0) || !(lev > 0)) return null;
    var move = side === "SHORT" ? (entry - px) / entry : (px - entry) / entry;
    return move * lev * 100;
  }

  function upnl(book, px) {
    if (!book.side || !(book.qty > 0) || !(px > 0)) return 0;
    if (book.side === "SHORT") return book.qty * (book.entry - px);
    return book.qty * (px - book.entry);
  }

  function snapshot(book) {
    var px = book.mark;
    var open = upnl(book, px);
    var equity = book.cash + open;
    var used = book.side ? book.margin : 0;
    var free = Math.max(0, equity - used);
    var lev = book.side ? book.levUsed : book.cfg.leverage;
    var roe = book.side ? roeAt(book.side, book.entry, px, book.levUsed) : null;
    var realized = 0;
    book.trades.forEach(function (t) { realized += t.pnl || 0; });
    return {
      equity: equity,
      free: free,
      used: used,
      open: open,
      realized: realized,
      roe: roe,
      side: book.side,
      entry: book.entry,
      mark: px,
      qty: book.qty,
      leverage: lev,
      status: book.status,
      trades: book.trades.slice(-12),
      slHold: book.slHold,
      bias: book.cfg.bias,
      curve: book.curve
    };
  }

  function remember(book) {
    var s = snapshot(book);
    book.curve.push({ equity: s.equity, px: book.mark, roe: s.roe });
    if (book.curve.length > 240) book.curve.shift();
  }

  function arm(book, bias) {
    var b = String(bias || "").toUpperCase();
    if (b !== "LONG" && b !== "SHORT" && b !== "FLAT") return snapshot(book);
    book.cfg.bias = b;
    if (b === "LONG" || b === "SHORT") {
      book.slHold = false;
      book.status = book.side
        ? book.side + " clip stays · bias " + b
        : b + " armed";
    } else if (!book.side) {
      book.status = "FLAT — idle until LONG or SHORT";
    }
    return snapshot(book);
  }

  function applyConfig(book, patch) {
    var bias = book.cfg.bias;
    var slHold = book.slHold;
    var next = Object.assign({}, book.cfg, patch || {});
    if (!patch || patch.bias == null) next.bias = bias;
    book.cfg = normalize(next);
    book.slHold = slHold;
    return snapshot(book);
  }

  function crazyHold(book, px, ts) {
    if (!book.cfg.dontGoCrazy || book.winRoes.length < 2) return false;
    var a = book.winRoes[book.winRoes.length - 1];
    var b = book.winRoes[book.winRoes.length - 2];
    if (!(a.roe >= book.cfg.doubleWinRoe) || !(b.roe >= book.cfg.doubleWinRoe)) return false;
    var elapsed = a.ts ? (ts - a.ts) / 1000 : 0;
    var dip = book.cfg.doubleWinDipRoe / 100 / book.cfg.leverage;
    var dipped = false;
    if (a.px > 0 && px > 0 && a.side === "LONG") dipped = px <= a.px * (1 - dip);
    if (a.px > 0 && px > 0 && a.side === "SHORT") dipped = px >= a.px * (1 + dip);
    if (elapsed >= book.cfg.doubleWinWaitSec || dipped) return false;
    book.status = "dontGoCrazy — two +" + book.cfg.doubleWinRoe + "% ROE wins";
    return true;
  }

  function tryEnter(book, px, ts, force) {
    if (book.side) return false;
    var bias = book.cfg.bias;
    if (bias !== "LONG" && bias !== "SHORT") {
      book.status = "FLAT — idle until LONG or SHORT";
      return false;
    }
    if (!force && book.slHold && book.cfg.afterLossNoRebuy) {
      book.status = "afterLossNoRebuy — last close was SL; set LONG or SHORT again";
      return false;
    }
    if (!force && book.lastCloseTs && (ts - book.lastCloseTs) / 1000 < book.cfg.cooldownSec) {
      book.status = "cooldown " + book.cfg.cooldownSec + "s";
      return false;
    }
    if (!force && crazyHold(book, px, ts)) return false;
    var margin = Math.floor(book.cash * book.cfg.marginPct * 100) / 100;
    if (margin < book.cfg.minMarginUsd) {
      book.status = "free cash under min margin";
      return false;
    }
    var notional = margin * book.cfg.leverage;
    if (notional < book.cfg.minNotionalUsd) {
      var needed = book.cfg.minNotionalUsd / book.cfg.leverage;
      if (needed > book.cash * 0.8) {
        book.status = "notional under min";
        return false;
      }
      margin = Math.ceil(needed * 100) / 100;
      notional = margin * book.cfg.leverage;
    }
    book.side = bias;
    book.entry = px;
    book.qty = notional / px;
    book.margin = margin;
    book.levUsed = book.cfg.leverage;
    book.trailOn = false;
    book.extreme = px;
    book.mark = px;
    book.status = bias + " BTC @ " + px.toFixed(2);
    return true;
  }

  function closePos(book, px, ts, reason) {
    var pnl = upnl(book, px);
    var roe = roeAt(book.side, book.entry, px, book.levUsed);
    book.cash += pnl;
    book.trades.push({
      ts: ts,
      side: book.side,
      entry: book.entry,
      price: px,
      pnl: pnl,
      roe: roe,
      reason: reason,
      leverage: book.levUsed
    });
    book.winRoes.push({ roe: roe, ts: ts, px: px, side: book.side });
    if (book.winRoes.length > 2) book.winRoes = book.winRoes.slice(-2);
    if (book.cfg.afterLossNoRebuy && String(reason).toUpperCase().indexOf("SL") === 0) {
      book.slHold = true;
    }
    book.side = null;
    book.entry = 0;
    book.qty = 0;
    book.margin = 0;
    book.trailOn = false;
    book.extreme = 0;
    book.lastCloseTs = ts;
    book.mark = px;
    book.status = reason;
  }

  function slPrice(book) {
    var dist = book.cfg.slRoePct / 100 / book.levUsed;
    return book.side === "LONG" ? book.entry * (1 - dist) : book.entry * (1 + dist);
  }

  function tpPrice(book) {
    return book.side === "LONG"
      ? book.entry * (1 + book.cfg.takeProfit)
      : book.entry * (1 - book.cfg.takeProfit);
  }

  function armPrice(book) {
    return book.side === "LONG"
      ? book.entry * (1 + book.cfg.trailActivation)
      : book.entry * (1 - book.cfg.trailActivation);
  }

  function hit(from, to, level) {
    var lo = Math.min(from, to);
    var hi = Math.max(from, to);
    return level >= lo && level <= hi;
  }

  function alreadyOut(book, px) {
    var long = book.side === "LONG";
    var tp = tpPrice(book);
    var sl = slPrice(book);
    if (long ? px <= sl : px >= sl) {
      var slRoe = roeAt(book.side, book.entry, px, book.levUsed);
      return "SL ROE " + (slRoe == null ? "" : slRoe.toFixed(2) + "%");
    }
    if (long ? px >= tp : px <= tp) return "TP +" + (book.cfg.takeProfit * 100).toFixed(2) + "%";
    if (book.trailOn && book.extreme > 0) {
      var stop = long
        ? book.extreme * (1 - book.cfg.trailDelta)
        : book.extreme * (1 + book.cfg.trailDelta);
      if (long ? px <= stop : px >= stop) return "TRAIL";
    }
    return "";
  }

  function walkSegment(book, from, to, ts) {
    if (!book.side || !(from > 0) || !(to > 0)) return;
    /* A tightened stop or target can sit behind the current print.
       The desk checks the mid itself, not only a cross. */
    if (alreadyOut(book, from) && alreadyOut(book, to)) {
      closePos(book, to, ts, alreadyOut(book, to));
      return;
    }
    var long = book.side === "LONG";
    var up = to >= from;
    var favorable = long ? up : !up;
    if (favorable) {
      var tp = tpPrice(book);
      if (hit(from, to, tp)) {
        closePos(book, tp, ts, "TP +" + (book.cfg.takeProfit * 100).toFixed(2) + "%");
        return;
      }
      var armAt = armPrice(book);
      if (book.trailOn || hit(from, to, armAt)) {
        book.trailOn = true;
        book.extreme = long
          ? Math.max(book.extreme || to, to)
          : Math.min(book.extreme || to, to);
      }
      book.mark = to;
      return;
    }
    var hits = [];
    var sl = slPrice(book);
    if (hit(from, to, sl)) {
      var slRoe = roeAt(book.side, book.entry, sl, book.levUsed);
      hits.push({ px: sl, reason: "SL ROE " + (slRoe == null ? "" : slRoe.toFixed(2) + "%") });
    }
    if (book.trailOn && book.extreme > 0) {
      var stop = long
        ? book.extreme * (1 - book.cfg.trailDelta)
        : book.extreme * (1 + book.cfg.trailDelta);
      if (hit(from, to, stop)) hits.push({ px: stop, reason: "TRAIL" });
    }
    if (!hits.length) {
      book.mark = to;
      return;
    }
    hits.sort(function (a, b) { return Math.abs(a.px - from) - Math.abs(b.px - from); });
    closePos(book, hits[0].px, ts, hits[0].reason);
  }

  function markOpen(book) {
    if (!book.side) return;
    var r = roeAt(book.side, book.entry, book.mark, book.levUsed);
    book.status = book.side + "  ROE " + (r >= 0 ? "+" : "") + r.toFixed(2) + "%";
  }

  function resetEntry(book, px, ts) {
    px = Number(px);
    ts = Number(ts) || Date.now();
    if (!(px > 0)) return snapshot(book);
    if (book.cfg.bias !== "LONG" && book.cfg.bias !== "SHORT") {
      book.mark = px;
      book.status = "FLAT — set LONG or SHORT to enter";
      return snapshot(book);
    }
    if (book.side) closePos(book, px, ts, "reset IN");
    book.slHold = false;
    book.lastCloseTs = 0;
    tryEnter(book, px, ts, true);
    book.mark = px;
    book.lastTs = ts;
    if (book.side) markOpen(book);
    remember(book);
    return snapshot(book);
  }

  function stepPrice(book, px, ts) {
    px = Number(px);
    ts = Number(ts) || 0;
    if (!(px > 0)) return snapshot(book);
    if (!book.side) {
      tryEnter(book, px, ts);
      if (!book.side) book.mark = px;
    } else {
      walkSegment(book, book.mark || px, px, ts);
      if (book.side) {
        book.mark = px;
        markOpen(book);
      }
    }
    book.lastTs = ts;
    remember(book);
    return snapshot(book);
  }

  function stepCandle(book, candle) {
    var o = Number(candle.o);
    var h = Number(candle.h);
    var l = Number(candle.l);
    var c = Number(candle.c);
    var t = Number(candle.t) || 0;
    if (!(o > 0 && h > 0 && l > 0 && c > 0)) return snapshot(book);
    var was = !!book.side;
    if (!was) {
      tryEnter(book, o, t);
      if (!book.side) {
        book.mark = c;
        book.lastTs = t;
        remember(book);
        return snapshot(book);
      }
    } else {
      walkSegment(book, book.mark || o, o, t);
      if (!book.side) {
        book.lastTs = t;
        remember(book);
        return snapshot(book);
      }
    }
    var seq = c >= o ? [l, h, c] : [h, l, c];
    var from = o;
    for (var i = 0; i < seq.length; i++) {
      if (!book.side) break;
      walkSegment(book, from, seq[i], t);
      from = seq[i];
    }
    if (book.side) {
      book.mark = c;
      markOpen(book);
    }
    book.lastTs = t;
    remember(book);
    return snapshot(book);
  }

  return {
    standardConfig: standardConfig,
    normalize: normalize,
    createBook: createBook,
    snapshot: snapshot,
    arm: arm,
    applyConfig: applyConfig,
    resetEntry: resetEntry,
    stepPrice: stepPrice,
    stepCandle: stepCandle,
    roeAt: roeAt
  };
});
