/*
 * UK blackjack basic strategy.
 *
 * Rules modelled (typical UK casino table):
 *  - 6 decks, dealer stands on all 17s
 *  - European "no hole card": the dealer takes a second card only after all
 *    players have finished. If the dealer then has blackjack, the player loses
 *    the whole stake, including any doubles and splits.
 *  - Double down on hard 9, 10 or 11 only (first two cards)
 *  - Split once only, no double after split, split aces get one card each
 *  - Blackjack pays 3:2, no surrender, never take insurance
 *
 * Actions: 'H' hit, 'S' stand, 'D' double, 'P' split.
 */
(function (root) {
  const RANKS = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K'];
  const DEALER_COLS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]; // 11 = Ace

  function cardValue(rank) {
    if (rank === 'A') return 11;
    if (rank === 'J' || rank === 'Q' || rank === 'K') return 10;
    return Number(rank);
  }

  function handTotal(ranks) {
    let total = 0;
    let aces = 0;
    for (const r of ranks) {
      total += cardValue(r);
      if (r === 'A') aces++;
    }
    while (total > 21 && aces > 0) {
      total -= 10;
      aces--;
    }
    return { total, soft: aces > 0 };
  }

  const between = (d, lo, hi) => d >= lo && d <= hi;

  // ---- Strategy tables, written as rules (d = dealer up value, 11 = Ace) ----

  function hardAction(total, d) {
    if (total <= 8) return 'H';
    if (total === 9) return between(d, 3, 6) ? 'D' : 'H';
    if (total === 10 || total === 11) return d <= 9 ? 'D' : 'H'; // no-hole-card: hit vs 10/A
    if (total === 12) return between(d, 4, 6) ? 'S' : 'H';
    if (total <= 16) return d <= 6 ? 'S' : 'H';
    return 'S';
  }

  function softAction(total, d) {
    // Doubling soft hands is not allowed (hard 9-11 only).
    if (total <= 17) return 'H';
    if (total === 18) return d <= 8 ? 'S' : 'H';
    return 'S';
  }

  // Returns 'P' if the pair should be split, otherwise null (play as a total).
  function pairAction(pairValue, d) {
    switch (pairValue) {
      case 11: return d <= 10 ? 'P' : null; // A,A: hit vs dealer Ace (no hole card)
      case 2:
      case 3: return between(d, 4, 7) ? 'P' : null;
      case 4:
      case 5:
      case 10: return null;
      case 6: return between(d, 3, 6) ? 'P' : null;
      case 7: return d <= 7 ? 'P' : null;
      case 8: return d <= 9 ? 'P' : null; // 8,8: hit vs 10/A (no hole card)
      case 9: return (d <= 9 && d !== 7) ? 'P' : 'S';
    }
    return null;
  }

  /**
   * Recommend an action.
   * @param {string[]} ranks    player's cards, e.g. ['A', '7']
   * @param {string}   dealerUp dealer's up card rank
   * @param {{canDouble:boolean, canSplit:boolean}} opts
   */
  function recommend(ranks, dealerUp, opts) {
    const d = cardValue(dealerUp);
    const { total, soft } = handTotal(ranks);

    if (opts.canSplit && ranks.length === 2 && cardValue(ranks[0]) === cardValue(ranks[1])) {
      const p = pairAction(cardValue(ranks[0]), d);
      if (p) return p;
    }

    let a = soft ? softAction(total, d) : hardAction(total, d);
    if (a === 'D' && !opts.canDouble) a = 'H';
    return a;
  }

  function describeHand(ranks, canSplit) {
    if (canSplit && ranks.length === 2 && cardValue(ranks[0]) === cardValue(ranks[1])) {
      const r = ranks[0] === 'A' ? 'A' : String(cardValue(ranks[0]));
      return `Pair of ${r}s`;
    }
    const { total, soft } = handTotal(ranks);
    return `${soft ? 'Soft' : 'Hard'} ${total}`;
  }

  // ---- Chart data (for the reference screen) ----
  function chart() {
    const hard = [];
    for (let t = 5; t <= 17; t++) {
      hard.push({ label: t === 17 ? '17+' : String(t), key: `h${t}`, cells: DEALER_COLS.map(d => hardAction(t, d)) });
    }
    const soft = [];
    for (let t = 13; t <= 19; t++) {
      soft.push({ label: t === 19 ? 'A,8+' : `A,${t - 11}`, key: `s${t}`, cells: DEALER_COLS.map(d => softAction(t, d)) });
    }
    const pairs = [];
    for (const v of [11, 10, 9, 8, 7, 6, 5, 4, 3, 2]) {
      const label = v === 11 ? 'A,A' : `${v},${v}`;
      pairs.push({
        label, key: `p${v}`,
        cells: DEALER_COLS.map(d => {
          const p = pairAction(v, d);
          if (p) return p;
          const { total, soft } = handTotal(v === 11 ? ['A', 'A'] : [String(v), String(v)]);
          return soft ? softAction(total, d) : hardAction(total, d);
        }),
      });
    }
    return { cols: DEALER_COLS, hard, soft, pairs };
  }

  // Which chart row a hand belongs to (for highlighting).
  function chartKey(ranks, canSplit) {
    if (canSplit && ranks.length === 2 && cardValue(ranks[0]) === cardValue(ranks[1])) {
      return `p${cardValue(ranks[0])}`;
    }
    const { total, soft } = handTotal(ranks);
    if (soft) return `s${Math.min(Math.max(total, 13), 19)}`;
    return `h${Math.min(Math.max(total, 5), 17)}`;
  }

  const api = { RANKS, DEALER_COLS, cardValue, handTotal, recommend, describeHand, chart, chartKey };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.Strategy = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
