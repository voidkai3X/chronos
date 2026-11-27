#!/usr/bin/env node
/* Unit tests for the risk calculator maths (web/static/calc.js). Run: node tests/calc_test.cjs */
const assert = require("assert");
const C = require("../web/static/calc.js");
const near = (a, b, e = 1e-6) => assert(Math.abs(a - b) <= e * Math.max(1, Math.abs(b)), `${a} != ${b}`);

// crypto, no fees: 1% of 10,000 over a 1.00 stop = 100 coins
let r = C.calc({ type: "crypto", account: 10000, riskPct: 1, entry: 100, sl: 99, tp: 103, includeFees: false });
near(r.units, 100); near(r.potentialLoss, 100); near(r.potentialProfit, 300); near(r.rr, 3); assert.strictEqual(r.side, "long");
near(r.notional, 10000); near(r.leverage, 1); near(r.slPct, 1);
// fees are inside the risk budget: total loss at the stop is still exactly the risk amount
r = C.calc({ type: "crypto", account: 10000, riskPct: 1, entry: 100, sl: 99, tp: 103 });
near(r.potentialLoss, 100); assert(r.units < 100); assert(r.rrNet < r.rr);
near(r.fees, r.units * 0.03 / 100 * 199);
// short side read from the stop
r = C.calc({ type: "crypto", riskAmount: 50, entry: 100, sl: 102, tp: 95, includeFees: false });
assert.strictEqual(r.side, "short"); near(r.units, 25); near(r.potentialProfit, 125); assert.strictEqual(r.riskPct, null);
// forex EURUSD: 20 pip stop, $100 risk -> 0.5 lots, pip value $10/lot
r = C.calc({ type: "forex", symbol: "EURUSD", account: 10000, riskPct: 1, entry: 1.085, sl: 1.083, includeFees: false });
near(r.slPips, 20); near(r.lots, 0.5); near(r.pipValuePerLot, 10);
// USDJPY: quote JPY, converted with 1/price; pip 0.01
r = C.calc({ type: "forex", symbol: "USDJPY", account: 10000, riskPct: 1, entry: 150, sl: 149.5, includeFees: false });
near(r.pipSize, 0.01); near(r.slPips, 50); near(r.pipValuePerLot, 100000 * 0.01 / 150); near(r.potentialLoss, 100);
near(r.units, 100 / (0.5 / 150));
// a cross without a rate is refused, with a rate it works
assert(C.calc({ type: "forex", symbol: "EURGBP", account: 1000, riskPct: 1, entry: 0.85, sl: 0.84 }).error);
r = C.calc({ type: "forex", symbol: "EURGBP", account: 1000, riskPct: 1, entry: 0.85, sl: 0.84, quoteToUsd: 1.27, includeFees: false });
near(r.potentialLoss, 10); near(r.units, 10 / (0.01 * 1.27));
// gold lot = 100 oz
r = C.calc({ type: "xau", account: 10000, riskPct: 1, entry: 2650, sl: 2640, includeFees: false });
near(r.units, 10); near(r.lots, 0.1);
r = C.calc({ type: "xag", account: 10000, riskPct: 1, entry: 30, sl: 29.5, includeFees: false });
near(r.lots, 200 / 5000);
// validation
assert(C.calc({ type: "crypto", account: 1000, riskPct: 1, entry: 100, sl: 100 }).error);
assert(C.calc({ type: "crypto", account: 1000, riskPct: 1, entry: 100, sl: 99, tp: 98 }).error);
assert(C.calc({ type: "crypto", account: 0, riskPct: 1, entry: 100, sl: 99 }).error);
assert(C.calc({ type: "crypto", account: 1000, riskPct: 1, entry: "", sl: 99 }).error);
assert.strictEqual(C.pipSize("GBPJPY"), 0.01); assert.strictEqual(C.pipSize("EURUSD"), 0.0001);
console.log("calc_test: ALL_OK");
