// Run: node --test
const test = require('node:test');
const assert = require('node:assert');
const S = require('../strategy.js');
const full = { canDouble: true, canSplit: true };
const rec = (cards, up, opt = full) => S.recommend(cards, up, opt);

test('hard totals', () => {
  assert.equal(rec(['10', '6'], '10'), 'H');
  assert.equal(rec(['10', '6'], '6'), 'S');
  assert.equal(rec(['10', '2'], '3'), 'H');
  assert.equal(rec(['10', '2'], '4'), 'S');
  assert.equal(rec(['5', '4'], '3'), 'D');
  assert.equal(rec(['5', '4'], '2'), 'H');
  assert.equal(rec(['10', '7'], 'A'), 'S');
});
test('no hole card: no doubling 10/11 vs 10 or Ace', () => {
  assert.equal(rec(['6', '5'], '9'), 'D');
  assert.equal(rec(['6', '5'], 'K'), 'H');
  assert.equal(rec(['6', '4'], 'A'), 'H');
});
test('soft totals never double', () => {
  assert.equal(rec(['A', '6'], '4'), 'H');
  assert.equal(rec(['A', '7'], '8'), 'S');
  assert.equal(rec(['A', '7'], '9'), 'H');
  assert.equal(rec(['A', '8'], '6'), 'S');
});
test('pairs', () => {
  assert.equal(rec(['A', 'A'], '10'), 'P');
  assert.equal(rec(['A', 'A'], 'A'), 'H');
  assert.equal(rec(['8', '8'], '9'), 'P');
  assert.equal(rec(['8', '8'], 'Q'), 'H');
  assert.equal(rec(['9', '9'], '7'), 'S');
  assert.equal(rec(['9', '9'], '8'), 'P');
  assert.equal(rec(['K', 'Q'], '6'), 'S');
  assert.equal(rec(['5', '5'], '6'), 'D');
  assert.equal(rec(['2', '2'], '3'), 'H');
  assert.equal(rec(['2', '2'], '4'), 'P');
});
test('double unavailable falls back to hit', () => {
  assert.equal(rec(['6', '5'], '6', { canDouble: false, canSplit: false }), 'H');
  assert.equal(rec(['8', '8'], '6', { canDouble: false, canSplit: false }), 'S');
});
test('chart is complete', () => {
  const c = S.chart();
  for (const rows of [c.hard, c.soft, c.pairs]) for (const r of rows) assert.equal(r.cells.length, 10);
});

// ---- San Juan / US rules (hole card, double any two, DAS) ----
const US = S.PRESETS.us;
const US_H17_SUR = { ...US, h17: true, surrender: true };
const usRec = (cards, up, R = US, opt = { canDouble: true, canSplit: true, canSurrender: R.surrender }) =>
  S.recommend(cards, up, opt, R);

test('US: dealer peeks, so double 10/11 vs 10', () => {
  assert.equal(usRec(['6', '5'], 'K'), 'D');
  assert.equal(usRec(['6', '5'], 'A'), 'H');          // S17
  assert.equal(usRec(['6', '5'], 'A', US_H17_SUR), 'D'); // H17
  assert.equal(usRec(['6', '4'], '10'), 'H');
});
test('US: soft doubles', () => {
  assert.equal(usRec(['A', '2'], '5'), 'D');
  assert.equal(usRec(['A', '2'], '4'), 'H');
  assert.equal(usRec(['A', '6'], '3'), 'D');
  assert.equal(usRec(['A', '7'], '4'), 'D');
  assert.equal(usRec(['A', '7'], '2'), 'S');
  assert.equal(usRec(['A', '7'], '2', US_H17_SUR), 'D');
  assert.equal(usRec(['A', '7'], '9'), 'H');
  assert.equal(usRec(['A', '8'], '6', US_H17_SUR), 'D');
  // Ds falls back to stand when you can't double (e.g. three cards)
  assert.equal(usRec(['A', '4', '3'], '5', US, { canDouble: false, canSplit: false }), 'S');
});
test('US: pairs with double after split', () => {
  assert.equal(usRec(['8', '8'], 'A'), 'P');
  assert.equal(usRec(['A', 'A'], 'A'), 'P');
  assert.equal(usRec(['4', '4'], '5'), 'P');
  assert.equal(usRec(['6', '6'], '2'), 'P');
  assert.equal(usRec(['2', '2'], '2'), 'P');
  assert.equal(usRec(['9', '9'], '7'), 'S');
  assert.equal(usRec(['5', '5'], '9'), 'D');
});
test('US: late surrender', () => {
  assert.equal(usRec(['10', '6'], '10', US_H17_SUR), 'R');
  assert.equal(usRec(['10', '5'], 'A', US_H17_SUR), 'R');
  assert.equal(usRec(['10', '7'], 'A', US_H17_SUR), 'R');
  assert.equal(usRec(['8', '8'], 'A', US_H17_SUR), 'R');
  assert.equal(usRec(['10', '6'], '10'), 'H'); // no surrender offered
});

// ---- Tropical Bayamón: no hole card, original bets only, double hard 7-11 + soft ----
const TROP = S.PRESETS.tropical;
const tRec = (cards, up, R = TROP) => S.recommend(cards, up, { canDouble: true, canSplit: true, canSurrender: R.surrender }, R);

test('Tropical: original bets only means double/split vs 10 and Ace', () => {
  assert.equal(tRec(['6', '5'], 'K'), 'D');
  assert.equal(tRec(['6', '4'], '9'), 'D');
  assert.equal(tRec(['8', '8'], 'A'), 'P');
  assert.equal(tRec(['A', 'A'], 'A'), 'P');
  assert.equal(tRec(['A', '6'], '4'), 'D');
});
test('Tropical: doubling allowed on hard 7-11 and soft hands only', () => {
  assert.equal(S.doubleAllowed(7, false, TROP), true);
  assert.equal(S.doubleAllowed(11, false, TROP), true);
  assert.equal(S.doubleAllowed(12, false, TROP), false);
  assert.equal(S.doubleAllowed(6, false, TROP), false);
  assert.equal(S.doubleAllowed(18, true, TROP), true);
});
test('No double after split changes small pairs', () => {
  const noDas = { ...TROP, das: false };
  assert.equal(tRec(['2', '2'], '2', noDas), 'H');
  assert.equal(tRec(['2', '2'], '4', noDas), 'P');
  assert.equal(tRec(['4', '4'], '5', noDas), 'H');
  assert.equal(tRec(['6', '6'], '2', noDas), 'H');
  assert.equal(tRec(['4', '4'], '5'), 'P');
});
