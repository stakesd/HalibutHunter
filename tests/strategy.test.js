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
