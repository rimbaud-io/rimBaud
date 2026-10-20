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

export const PORT_TIP = {
  botWallet: "Input: Hyperliquid subaccount this hunter trades with.",
  botRisk: "Output (right): realized-win skim — wire into a RISK wallet left. Mapped on compile; no auto HL send.",
  botCoin: "Input: which coin this hunter trades.",
  botIncome: "Output: map positive income to a wallet (no auto HL send yet).",
  botLogs: "Output: send this hunter's logs here.",
  botSl: "Input: stop-loss % ROE — exit when the open is down this far.",
  botBias: "Input: LONG / SHORT / FLAT for the wired coin.",
  botMoon: "Input: exit the long when the coin itself is up this % from live price.",
  botCtrl: "Input: controller knobs (trail, take-profit, rebuy, hold…).",
  botEclose: "Input: emergency flatten — wire Close translator here. Idle does nothing. CLOSE from that Telegram flattens this hunter now.",
  botTrader: "Input: Copy T1 / T2 / T3 — wire one Traders card into each slot.",
  traderOut: "Output: this Copy leader — wire into Copy T1, T2 or T3.",
  walletAddr: "Output: this subaccount — wire into a hunter WALLET.",
  walletIncome: "Input: receive mapped income from a hunter.",
  walletFunds: "Input: USD / funds into this wallet.",
  riskIn: "Input (left): hunter RISK (right). Edit % of win, interval, min USD, destination. No auto HL send.",
  coinOut: "Output: this market — wire into a hunter COIN.",
  slOut: "Output: stop-loss % — wire into hunter SL.",
  moonOut: "Output: moon % — wire into hunter MOON.",
  biasOut: "Output: LONG / SHORT / FLAT — wire into hunter BIAS.",
  biasIn: "Input: Condor REGIME or another BIAS generator.",
  ctrlOut: "Output: controller settings — wire into hunter CTRL.",
  gaugeOut: "Output: Condor per-coin LONG / SHORT / FLAT — wire into hunter BIAS.",
  pumpLive: "Output: pump / post-pump reading — wire into a Stream IN. Does not write BIAS or orders.",
  tgOut: "Output: this Telegram bot — wire into Condor TG.",
  grokOut: "Output: this Grok bot — wire into Condor GROK.",
  condorTg: "Input: Telegram bots Condor may use for alerts and commands.",
  condorGrok: "Input: Grok bot Condor uses to predict BIAS (else heuristic).",
  fundsIn: "Input: USD / income into the wallet list.",
  logsIn: "Input: hunter logs land here.",
  xmlIn: "Input: dump the board to XML.",
  liveIn: "Input: live stream of hunter logs.",
  streamIn: "Input: Telegram / Grok / hunter logs / Condor ticks — wire any OUT here.",
  streamOut: "Output: this bot's messages — wire into a Stream node.",
  xlateIn: "Input: Telegram bot OUT. Translates LONG SHORT FLAT CLOSE OPEN100%.",
  xlateOut: "Output: translated BIAS — wire into one hunter BIAS.",
  deskBiasIn: "Input: Telegram bot OUT. LONG / FLAT / SHORT set desk BIAS. Does not close. Ignores CLOSE / OPEN100%. Do not wire BIAS BOT condor1 here — that bot is GROK report only.",
  grokBiasOut: "Output: GROK BIAS report → Telegram IN (BIAS BOT). Display / post only. Never writes hunter BIAS.",
  closerIn: "Input: a dedicated Telegram bot OUT. This translator ignores LONG SHORT FLAT OPEN100%.",
  closerOut: "Output: emergency CLOSE — wire into hunter E-CLOSE. Idle does nothing.",
  healthLive: "Output: live reading — wire into a Stream IN.",
  healthAlert: "Output: fires only when a max is reached — wire into a Stream IN or Telegram / Email IN.",
  agentIn: "Input: hunter LOG (OPEN/CLOSE alerts) or health ALERT. Not a BIAS command port.",
};
