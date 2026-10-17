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

const SLEEP = " The controller stays asleep until the hunter is on the wire. Placing the hunter starts it. Erasing it sleeps it again.";

export const V2_MENU = {
  "hummingbot-v2-bollinger-v1": "BBP long below the band, short above it",
  "hummingbot-v2-bollinger-v2": "Newer BBP. Same long and short thresholds",
  "hummingbot-v2-bollingrid": "Bollinger side, then a grid inside the band",
  "hummingbot-v2-macd-bb": "MACD and the band must both agree",
  "hummingbot-v2-dman-v3": "Official DMan. Band side, many maker levels",
  "hummingbot-v2-supertrend": "Trend up and near the line, else short",
  "hummingbot-v2-ai-livestream": "Outside long or short chance, over MQTT",
  "hummingbot-v2-pmm-dynamic": "Both sides. Spread follows NATR volatility",
  "hummingbot-v2-grid-strike": "One long or short grid across a price band",
  "hummingbot-v2-multi-grid": "Several price bands, each its own grid",
  "hummingbot-v2-stat-arb": "High z-score: long one pair, short the other",
  "hummingbot-v2-hedge": "Perp short sized from a spot balance",
  "hummingbot-v2-pmm-v1": "Older maker. Fixed spreads, inventory skew",
  "hummingbot-v2-pmm-mister": "Holds a base share and protects the profit",
  "hummingbot-v2-qgrid": "Splits quote across grids and can reverse",
  "hummingbot-v2-arb": "Buy the cheap book, sell the rich one",
  "hummingbot-v2-xemm": "Maker quotes, hedged on a second exchange",
  "hummingbot-v2-lp": "CLMM liquidity. Needs Gateway, no key yet",
};

const V2_BODY = {
  "hummingbot-v2-bollinger-v1": "Official directional controller. Bollinger percent says where price sits in the band. Below the long threshold it opens long. Above the short threshold it opens short. Exit is the stop, take-profit, or time limit.",
  "hummingbot-v2-bollinger-v2": "Same band-percent rule as Bollinger V1, in the newer controller file. Long below the long threshold. Short above the short threshold. Length, deviation, and both thresholds are the knobs.",
  "hummingbot-v2-bollingrid": "Bollinger percent picks the side, then a grid of limits is placed inside the band. Start, end, and the limit past the range are fractions of the band width.",
  "hummingbot-v2-macd-bb": "Long only when the band percent is under its long threshold, the MACD line is below zero, and the histogram is rising. Short is the mirror of that. Exit is the stop, take-profit, or time limit.",
  "hummingbot-v2-dman-v3": "Official mean-reversion controller, not the Dman hunter on this desk. Bollinger percent picks the side, then several maker levels step away from the price. The band width can stretch those steps, and each level has its own stop and trail.",
  "hummingbot-v2-supertrend": "Official trend controller, not the Surfn hunter. Long when the trend is up and price is within the percent threshold of the line. Short when the trend is down and price is that close. Length and multiplier are knobs.",
  "hummingbot-v2-ai-livestream": "No candle math. It listens on MQTT for an outside short, neutral, and long probability. Short above its threshold opens short. Otherwise long above its threshold opens long. Otherwise it waits.",
  "hummingbot-v2-pmm-dynamic": "Two-sided market maker, not the PMM Simple hunter. NATR volatility widens and tightens the spreads. MACD can shift the mid. It quotes both sides around that mid and does not take a long or short signal.",
  "hummingbot-v2-grid-strike": "One long grid or one short grid of limits between a start price and an end price, with a limit price past the range. It places the grid while price is inside that range. A filled level closes on take-profit, stop, or time.",
  "hummingbot-v2-multi-grid": "Several Grid Strike ranges in one controller. Each range has its own start, end, side, and share of the quote. A filled level still closes on take-profit, stop, or time.",
  "hummingbot-v2-stat-arb": "Two markets that move together. The spread is the hedge versus a line fit through the dominant. A high z-score buys the dominant and shorts the hedge. A low z-score swaps those sides. Lookback and the entry threshold are knobs.",
  "hummingbot-v2-hedge": "Reads a spot balance and holds a perpetual short of that size times a hedge ratio from 0 to 1. It changes the hedge only when the gap is large enough and enough time has passed. It does not guess direction.",
  "hummingbot-v2-pmm-v1": "Older pure market maker, not the PMM Simple hunter. Buy and sell limits sit at fixed spreads on several levels, and inventory can skew them. Refresh time and a price band are knobs.",
  "hummingbot-v2-pmm-mister": "Market maker that holds inventory between a minimum and a maximum base share. It skips a new order when one is already close in price. An optional take-profit and stop can protect the whole position.",
  "hummingbot-v2-qgrid": "Splits the quote across the assets in its allocation. Each grid range follows the Bollinger width. It can open a grid on the weak side and keep that position for a reversal.",
  "hummingbot-v2-arb": "Cross-exchange. One side buys the first book and sells the second when the gap covers the minimum profit. The other side runs the opposite way. It pauses if the two sides get too far out of balance. No BIAS node.",
  "hummingbot-v2-xemm": "Cross-exchange market maker. Several bid and ask levels on the maker exchange aim at a profit versus the taker book. A maker fill is hedged on the taker. No BIAS node.",
  "hummingbot-v2-lp": "Concentrated-liquidity maker for one CLMM pool. It keeps a single position and moves it when price leaves the range. It needs Gateway. No Hyperliquid key until a wallet is wired. No BIAS node.",
};

const HUNTER = {
  "hummingbot-dman-maker": "Hummingbot calls D-Man Maker V2 a market maker that moves spread and size with indicators. On this desk it is a BIAS gate: LONG and flat market-buys leftover USD, SHORT and flat market-sells it, then holds. Exit is take-profit, trail, or the ROE stop, then the same BIAS re-enters. The open clip stays on its side. dontGoCrazy waits after two +20% ROE wins (30 min or a -30% ROE dip). afterLossNoRebuy waits after a stop or an exchange-flat close until LONG or SHORT is set again. Checked, send every 2 win parks that second win on the risk wallet. Unchecked, every win stays on this account.",
  "hummingbot-bn-swing": "Hummingbot PMM Simple quotes both sides around the mid price and uses no forecast. On this desk, LONG and flat rests one buy limit for leftover USD. While in, it exits on take-profit, trail, or the ROE stop. SHORT waits until a 100% open. The open clip stays on its side.",
  "hummingbot-dman": "Hummingbot D-Man V3 is Bollinger mean reversion. On this desk, 15 minute BBP below 0 opens LONG and BBP above 1 opens SHORT, one clip of leftover USD after the RISK park. Trail arms at 1.5% of price and the trail distance is 0.5% of price, together with the Home ROE stop. The open clip is a single entry.",
  "hummingbot-surfn": "Official SuperTrend V1 on this desk. LONG when the trend is up and price is within 1% of the line. SHORT when the trend is down and price is that close. Length 20, multiplier 4, on the 3 minute chart. BIAS still has to agree. The open clip stays on its side when the trend reverses. Exit is the Home stop, Close, a 100% open, or a scale-out.",
  "hummingbot-helliot": "Elliott 3-wave on 15 minute swings. LONG in the 38.2% to 78.6% retrace after an up impulse. SHORT in that retrace after a down impulse. Target is the 1.618 extension. A dashboard 100%, 50%, or 20% open skips the wave wait. Exits are +93% ROE take-profit, -20% ROE stop, and the structure target.",
  "hummingbot-shortn": "Auto entry stays short when 15 minute Bollinger is high and the 1 hour average is not rising. At +90% ROE it closes and waits for a bounce of at least +20% ROE before shorting again. Open shorts have a -20% ROE stop. A 100% leftover open follows the current BIAS. The open clip stays on its side.",
  "hummingbot-onlyup": "Supertrend rider for BTC, LTC, and TAO. Auto entry is long only: the 1 hour average is rising, the 15 minute Supertrend is long, and price is within 1.2% of the line. Near +90% ROE it sells half and trails the rest under Supertrend or EMA-21. A Supertrend flip down closes the runner. A 100% leftover open follows the current BIAS. The open clip stays on its side. Home stop applies on new risk before the first scale-out.",
  "hummingbot-hl": "A wallet mirror, outside the Hummingbot controller scripts. About every 5 seconds it reads up to three leader wallets. A new leader position opens the same side here. A leader close closes the mirror. A side flip closes the old clip and opens the new one. Two leaders on one coin: the first one wins. The open log is written when the order is sent. Hyperliquid notional floor is $10.",
};

function hunterId(tool) {
  if (!tool) return "";
  const id = String(tool.toolId || "");
  if (id.startsWith("ctrl-") || id.startsWith("wallet-") || id.startsWith("bias-")) {
    const rest = id.replace(/^(ctrl|wallet|bias)-/, "");
    if (rest.startsWith("hummingbot-")) return rest;
  }
  if (id.startsWith("hummingbot-")) return id;
  const data = tool.data || {};
  if (data.botId) return String(data.botId);
  if (data.id && String(data.id).startsWith("hummingbot-")) return String(data.id);
  return "";
}

function bodyFor(id) {
  if (V2_BODY[id]) return V2_BODY[id] + SLEEP;
  return HUNTER[id] || "";
}

function portsFor(tool, hid) {
  const type = tool.type;
  const copy = hid === "hummingbot-hl" || (tool.data && tool.data.kind === "copy");
  if (type === "bot" || type === "homebot") {
    const inn = ["wallet", "coin", "sl", "bias", "moon", "ctrl", "eclose"];
    if (copy) inn.push("trader-1", "trader-2", "trader-3");
    return { inn, out: ["income", "risk", "logs"] };
  }
  if (type === "wallet") return { inn: ["income"], out: ["wallet"] };
  if (type === "ctrl") return { inn: [], out: ["ctrl"] };
  if (type === "bias" || type === "desk_bias") return { inn: ["in"], out: ["bias"] };
  if (type === "coin") return { inn: [], out: ["coin"] };
  if (type === "pct") return { inn: [], out: [tool.data && tool.data.kind === "moon" ? "moon" : "sl"] };
  if (type === "trader") return { inn: [], out: ["trader"] };
  if (type === "funds") return { inn: ["in"], out: ["funds"] };
  if (type === "gauge") return { inn: ["tg", "grok"], out: ["bias", "live"] };
  return { inn: [], out: [] };
}

function copyFor(tool) {
  const hid = hunterId(tool);
  const v2 = hid.startsWith("hummingbot-v2-");
  if (tool.type === "bot" || tool.type === "homebot") {
    return bodyFor(hid) || tool.hint || "Hummingbot hunter. Wire a wallet, a coin, and a controller. The script reads those each tick.";
  }
  if (tool.type === "ctrl" && hid) {
    const body = bodyFor(hid);
    return "Wire ctrl into the hunter. Knobs are read each tick. They do not send orders by themselves. " + (body || tool.hint || "");
  }
  if (tool.type === "wallet" && hid) {
    return "Wallet node. Output is this hunter's subaccount. Wire it into the hunter wallet input. This node does not move funds.";
  }
  if (tool.type === "bias" && hid) {
    return "BIAS node. LONG, SHORT, or FLAT per coin. The hunter treats it as a gate. FLAT stops new entries and leaves an open clip in place.";
  }
  if (tool.toolId === "bias-manual") return "Manual BIAS. LONG, SHORT, or FLAT. Wire bias into a hunter. FLAT stops new entries and leaves an open clip in place.";
  if (tool.toolId === "desk-bias") return "Desk BIAS. One side for the desk. It leaves an open clip in place.";
  if (tool.type === "coin") return "Coin. Wire coin into the hunter. That is the market the script trades.";
  if (tool.toolId === "sl-pct") return "Stop loss percent. Wire sl into the hunter. The script uses it as the ROE stop.";
  if (tool.toolId === "moon-pct") return "Moon percent. Wire moon into the hunter. That is the close target.";
  if (tool.toolId === "funds") return "FUNDS bus. Balances from the Hyperliquid subaccounts. This node does not move funds.";
  if (tool.type === "trader") return "Leader wallet. Wire trader into Copy trader-1, trader-2, or trader-3. The copy hunter mirrors this address.";
  if (v2) return bodyFor(hid) || tool.hint || tool.label;
  return tool.hint || tool.label;
}

function Chips({ tag, names }) {
  if (!names || !names.length) return null;
  return (
    <p className="sheet-ports">
      <b>{tag}</b>{" "}
      {names.map((n) => (
        <span key={tag + n} className="sheet-chip">{n}</span>
      ))}
    </p>
  );
}

export function ToolSheet({ tool }) {
  if (!tool) return null;
  const hid = hunterId(tool);
  const ports = portsFor(tool, hid);
  return (
    <>
      <h3 style={{ color: tool.color || "#f0b90b" }}>{tool.icon ? tool.icon + " " : ""}{tool.label}</h3>
      <Chips tag="IN" names={ports.inn} />
      <Chips tag="OUT" names={ports.out} />
      <p>{copyFor(tool)}</p>
      <p className="health">Click reads this sheet. Drag onto the canvas to place it.</p>
    </>
  );
}
