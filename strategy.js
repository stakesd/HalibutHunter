/*
 * Blackjack basic strategy for three rule sets (6 decks):
 *
 *  Tropical Bayamón (default): no hole card, but a dealer blackjack only takes
 *    your original bet (doubles and splits are refunded). Double on hard 7-11
 *    and soft hands.
 *  US: dealer takes a hole card and peeks for blackjack. Double on any two cards.
 *  UK: no hole card, and a dealer blackjack takes doubles and splits too
 *    ("loseAll"). Double on hard 9-11 only, no double after split.
 *
 * When a dealer blackjack can only cost your original bet (Tropical, US), the
 * strategy is the same as standard American strategy. Toggles: dealer hits soft
 * 17 (h17), late surrender, double after split (das).
 *
 * Common: split once, split aces get one card each, blackjack pays 3:2, no insurance.
 *
 * Raw chart codes:
 *   H hit, S stand, P split, D double (else hit), Ds double (else stand),
 *   Rh surrender (else hit), Rs surrender (else stand), Rp surrender (else split)
 * recommend() resolves these to a playable action: H, S, D, P or R.
 */
(function (root) {
  const RANKS = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K'];
  const DEALER_COLS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]; // 11 = Ace

  // double: 'any' (any two cards), 'soft7to11' (hard 7-11 and soft hands), 'nineToEleven' (hard 9-11)
  const PRESETS = {
    tropical: { name: 'Tropical Bayamón', peek: false, loseAll: false, double: 'soft7to11', das: true, h17: false, surrender: false },
    us: { name: 'US (hole card)', peek: true, loseAll: false, double: 'any', das: true, h17: false, surrender: false },
    uk: { name: 'UK (no hole card)', peek: false, loseAll: true, double: 'nineToEleven', das: false, h17: false, surrender: false },
  };
  const UK = PRESETS.uk;

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

  // ---- Strategy tables as rules (d = dealer up value, 11 = Ace) ----

  // Can this two-card hand be doubled at all under these rules?
  function doubleAllowed(total, soft, R) {
    if (R.double === 'any') return true;
    if (soft) return R.double === 'soft7to11';
    if (R.double === 'soft7to11') return total >= 7 && total <= 11;
    return total >= 9 && total <= 11;
  }
  const softDoubles = (R) => R.double !== 'nineToEleven';

  function hardAction(total, d, R) {
    if (!R.loseAll) {
      if (R.surrender) {
        if (total === 16 && d >= 9) return 'Rh';
        if (total === 15 && (d === 10 || (R.h17 && d === 11))) return 'Rh';
        if (total === 17 && R.h17 && d === 11) return 'Rs';
      }
      if (total <= 8) return 'H';
      if (total === 9) return between(d, 3, 6) ? 'D' : 'H';
      if (total === 10) return d <= 9 ? 'D' : 'H';
      if (total === 11) return d <= 10 || R.h17 ? 'D' : 'H';
      if (total === 12) return between(d, 4, 6) ? 'S' : 'H';
      if (total <= 16) return d <= 6 ? 'S' : 'H';
      return 'S';
    }
    // UK: dealer blackjack takes everything
    if (total <= 8) return 'H';
    if (total === 9) return between(d, 3, 6) ? 'D' : 'H';
    if (total === 10 || total === 11) return d <= 9 ? 'D' : 'H';
    if (total === 12) return between(d, 4, 6) ? 'S' : 'H';
    if (total <= 16) return d <= 6 ? 'S' : 'H';
    return 'S';
  }

  function softAction(total, d, R) {
    if (softDoubles(R)) {
      if (total <= 14) return between(d, 5, 6) ? 'D' : 'H';      // A,2 A,3
      if (total <= 16) return between(d, 4, 6) ? 'D' : 'H';      // A,4 A,5
      if (total === 17) return between(d, 3, 6) ? 'D' : 'H';     // A,6
      if (total === 18) {                                        // A,7
        if (between(d, 3, 6) || (R.h17 && d === 2)) return 'Ds';
        return d <= 8 ? 'S' : 'H';
      }
      if (total === 19) return R.h17 && d === 6 ? 'Ds' : 'S';   // A,8
      return 'S';
    }
    // UK: soft hands can't be doubled
    if (total <= 17) return 'H';
    if (total === 18) return d <= 8 ? 'S' : 'H';
    return 'S';
  }

  // Raw code if the pair should be split (or surrendered), otherwise null (play as a total).
  function pairAction(v, d, R) {
    if (!R.loseAll && R.das) { // double after split allowed
      switch (v) {
        case 11: return 'P';
        case 10: return null;
        case 9: return (d <= 9 && d !== 7) ? 'P' : 'S';
        case 8: return R.surrender && R.h17 && d === 11 ? 'Rp' : 'P';
        case 7: return d <= 7 ? 'P' : null;
        case 6: return d <= 6 ? 'P' : null;
        case 5: return null;
        case 4: return between(d, 5, 6) ? 'P' : null;
        case 3:
        case 2: return d <= 7 ? 'P' : null;
      }
      return null;
    }
    if (!R.loseAll) { // no double after split
      switch (v) {
        case 11: return 'P';
        case 10: return null;
        case 9: return (d <= 9 && d !== 7) ? 'P' : 'S';
        case 8: return R.surrender && R.h17 && d === 11 ? 'Rp' : 'P';
        case 7: return d <= 7 ? 'P' : null;
        case 6: return between(d, 3, 6) ? 'P' : null;
        case 5:
        case 4: return null;
        case 3:
        case 2: return between(d, 4, 7) ? 'P' : null;
      }
      return null;
    }
    switch (v) { // UK: no hole card, dealer blackjack takes everything, no double after split
      case 11: return d <= 10 ? 'P' : null;
      case 2:
      case 3: return between(d, 4, 7) ? 'P' : null;
      case 4:
      case 5:
      case 10: return null;
      case 6: return between(d, 3, 6) ? 'P' : null;
      case 7: return d <= 7 ? 'P' : null;
      case 8: return d <= 9 ? 'P' : null;
      case 9: return (d <= 9 && d !== 7) ? 'P' : 'S';
    }
    return null;
  }

  // Turn a raw chart code into an action the player can actually take.
  function resolve(code, opts, fallbackTotalAction) {
    switch (code) {
      case 'D': return opts.canDouble ? 'D' : 'H';
      case 'Ds': return opts.canDouble ? 'D' : 'S';
      case 'Rh': return opts.canSurrender ? 'R' : 'H';
      case 'Rs': return opts.canSurrender ? 'R' : 'S';
      case 'Rp': return opts.canSurrender ? 'R' : (opts.canSplit ? 'P' : fallbackTotalAction());
      default: return code;
    }
  }

  /**
   * Recommend an action.
   * @param {string[]} ranks    player's cards, e.g. ['A', '7']
   * @param {string}   dealerUp dealer's up card rank
   * @param {{canDouble:boolean, canSplit:boolean, canSurrender?:boolean}} opts
   * @param {object}   [R]      rules (defaults to UK)
   * @returns {'H'|'S'|'D'|'P'|'R'}
   */
  function recommend(ranks, dealerUp, opts, R = UK) {
    const d = cardValue(dealerUp);
    const { total, soft } = handTotal(ranks);
    const byTotal = () => resolve(soft ? softAction(total, d, R) : hardAction(total, d, R), opts, () => 'H');

    if (opts.canSplit && ranks.length === 2 && cardValue(ranks[0]) === cardValue(ranks[1])) {
      const p = pairAction(cardValue(ranks[0]), d, R);
      if (p) return resolve(p, opts, byTotal);
    }
    return byTotal();
  }

  function describeHand(ranks, canSplit) {
    if (canSplit && ranks.length === 2 && cardValue(ranks[0]) === cardValue(ranks[1])) {
      const r = ranks[0] === 'A' ? 'A' : String(cardValue(ranks[0]));
      return `Pair of ${r}s`;
    }
    const { total, soft } = handTotal(ranks);
    return `${soft ? 'Soft' : 'Hard'} ${total}`;
  }

  // ---- Chart data (raw codes, for the reference screen) ----
  function chart(R = UK) {
    const hard = [];
    for (let t = 5; t <= 17; t++) {
      hard.push({ label: t === 17 ? '17+' : String(t), key: `h${t}`, cells: DEALER_COLS.map(d => hardAction(t, d, R)) });
    }
    const soft = [];
    for (let t = 13; t <= 20; t++) {
      soft.push({ label: t === 20 ? 'A,9+' : `A,${t - 11}`, key: `s${t}`, cells: DEALER_COLS.map(d => softAction(t, d, R)) });
    }
    const pairs = [];
    for (const v of [11, 10, 9, 8, 7, 6, 5, 4, 3, 2]) {
      const label = v === 11 ? 'A,A' : `${v},${v}`;
      pairs.push({
        label, key: `p${v}`,
        cells: DEALER_COLS.map(d => {
          const p = pairAction(v, d, R);
          if (p) return p;
          const { total, soft } = handTotal(v === 11 ? ['A', 'A'] : [String(v), String(v)]);
          return soft ? softAction(total, d, R) : hardAction(total, d, R);
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
    if (soft) return `s${Math.min(Math.max(total, 13), 20)}`;
    return `h${Math.min(Math.max(total, 5), 17)}`;
  }

  const api = { RANKS, DEALER_COLS, PRESETS, doubleAllowed, cardValue, handTotal, recommend, describeHand, chart, chartKey };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.Strategy = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
