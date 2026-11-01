(() => {
  'use strict';
  const S = window.Strategy;
  const $ = (sel) => document.querySelector(sel);

  const DECKS = 6;
  const BET = 10;
  const SUITS = ['♠', '♥', '♦', '♣'];
  const ACTION_NAMES = { H: 'Hit', S: 'Stand', D: 'Double', P: 'Split' };
  const STORE_KEY = 'hh-blackjack-v1';

  // ---------- Persistent stats ----------
  const defaults = () => ({ decisions: 0, correct: 0, streak: 0, best: 0, hands: 0, bankroll: 1000, missed: {}, mode: 'play' });
  let stats;
  try { stats = Object.assign(defaults(), JSON.parse(localStorage.getItem(STORE_KEY)) || {}); }
  catch { stats = defaults(); }
  const save = () => localStorage.setItem(STORE_KEY, JSON.stringify(stats));

  // ---------- Shoe ----------
  let shoe = [];
  function newShoe() {
    shoe = [];
    for (let d = 0; d < DECKS; d++) for (const s of SUITS) for (const r of S.RANKS) shoe.push({ rank: r, suit: s });
    for (let i = shoe.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [shoe[i], shoe[j]] = [shoe[j], shoe[i]];
    }
  }
  const randSuit = () => SUITS[Math.floor(Math.random() * 4)];
  const randRank = () => S.RANKS[Math.floor(Math.random() * 13)];
  function draw() {
    if (shoe.length === 0) newShoe();
    return shoe.pop();
  }

  // Drill mode focuses on double and split decisions: every hand is either a
  // pair or a hard 9, 10 or 11. Dealer up cards are spread evenly across the
  // chart columns so every cell gets practised.
  const HARD_DOUBLES = {
    9: [['2', '7'], ['3', '6'], ['4', '5']],
    10: [['2', '8'], ['3', '7'], ['4', '6']],
    11: [['2', '9'], ['3', '8'], ['4', '7'], ['5', '6']],
  };
  const TENS = ['10', 'J', 'Q', 'K'];
  const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
  function drillStart() {
    let a, b;
    if (Math.random() < 0.5) {
      const v = pick(['A', '2', '3', '4', '5', '6', '7', '8', '9', 'T']);
      [a, b] = v === 'T' ? [pick(TENS), pick(TENS)] : [v, v];
    } else {
      [a, b] = pick(HARD_DOUBLES[pick([9, 10, 11])]);
    }
    const cards = [a, b].map(r => ({ rank: r, suit: randSuit() }));
    if (Math.random() < 0.5) cards.reverse();
    const upVal = pick(['2', '3', '4', '5', '6', '7', '8', '9', 'T', 'A']);
    const up = upVal === 'T' ? pick(TENS) : upVal;
    return { player: cards, up: { rank: up, suit: randSuit() } };
  }

  // ---------- Round state ----------
  let st = null; // { hands:[], active, dealer:[], phase, splitUsed }

  const ranksOf = (cards) => cards.map(c => c.rank);
  const isPair = (h) => h.cards.length === 2 && S.cardValue(h.cards[0].rank) === S.cardValue(h.cards[1].rank);
  const isNatural = (cards) => cards.length === 2 && S.handTotal(ranksOf(cards)).total === 21;

  function options(h) {
    const { total, soft } = S.handTotal(ranksOf(h.cards));
    return {
      canDouble: h.cards.length === 2 && !h.fromSplit && !soft && total >= 9 && total <= 11,
      canSplit: isPair(h) && !st.splitUsed,
    };
  }

  function deal() {
    if (shoe.length < 52 * DECKS * 0.25) newShoe(); // reshuffle at 75% penetration
    clearFeedback();
    $('#outcome').textContent = '';

    if (stats.mode === 'drill') {
      const d = drillStart();
      st = { hands: [{ cards: d.player, bet: BET }], active: 0, dealer: [d.up], phase: 'player', splitUsed: false };
      $('#outcome').textContent = 'Doubles & splits drill';
      render();
      return;
    }

    const p = [draw(), draw()];
    const up = draw();
    st = { hands: [{ cards: p, bet: BET }], active: 0, dealer: [up], phase: 'player', splitUsed: false };
    if (isNatural(p)) { finishRound(); return; }
    render();
  }

  // ---------- Decisions ----------
  function act(action) {
    if (!st || st.phase !== 'player') return;
    const h = st.hands[st.active];
    const opt = options(h);
    if (action === 'D' && !opt.canDouble) { explainBlocked('D', h); return; }
    if (action === 'P' && !opt.canSplit) { explainBlocked('P', h); return; }

    const ranks = ranksOf(h.cards);
    const up = st.dealer[0].rank;
    const rec = S.recommend(ranks, up, opt);
    grade(action, rec, ranks, up, opt);

    if (stats.mode === 'drill') {
      st.phase = 'done';
      render();
      return;
    }
    apply(action);
  }

  // Tell the player why Double / Split isn't available instead of silently ignoring the tap.
  function explainBlocked(action, h) {
    const ranks = ranksOf(h.cards);
    let why;
    if (action === 'D') {
      if (h.fromSplit) why = 'No doubling after a split.';
      else if (h.cards.length > 2) why = 'You can only double on your first two cards.';
      else why = `UK rules: double on hard 9, 10 or 11 only. You have ${S.describeHand(ranks, false)}.`;
    } else {
      if (!isPair(h)) why = 'You can only split a pair (two cards of the same value).';
      else why = 'Only one split is allowed per hand.';
    }
    showFeedback('hint', `Can't ${ACTION_NAMES[action].toLowerCase()} here`, why, '');
  }

  function grade(action, rec, ranks, up, opt) {
    const ok = action === rec;
    const desc = `${S.describeHand(ranks, opt.canSplit)} vs ${up === 'A' ? 'Ace' : S.cardValue(up)}`;
    stats.decisions++;
    if (ok) {
      stats.correct++;
      stats.streak++;
      stats.best = Math.max(stats.best, stats.streak);
    } else {
      stats.streak = 0;
      stats.missed[desc] = (stats.missed[desc] || 0) + 1;
    }
    save();
    showFeedback(ok ? 'good' : 'bad',
      ok ? `✓ ${ACTION_NAMES[rec]} is right` : `✗ Should ${ACTION_NAMES[rec]} (you chose ${ACTION_NAMES[action]})`,
      desc, tipFor(ranks, up, opt, rec));
  }

  function tipFor(ranks, up, opt, rec) {
    const d = S.cardValue(up);
    const { total, soft } = S.handTotal(ranks);
    const pairVal = opt.canSplit ? S.cardValue(ranks[0]) : 0;
    if (d >= 10 && ((total === 10 || total === 11) && !soft && ranks.length === 2 || pairVal === 8 || pairVal === 11)) {
      return 'No hole card: dealer may still have blackjack, so don\'t put extra money out vs 10 or Ace.';
    }
    if (rec === 'D' && opt.canDouble === false) return 'Doubling isn\'t allowed here, so hit.';
    if (!soft && total >= 12 && total <= 16 && d <= 6 && rec === 'S') return 'Dealer shows a bust card: let them take the risk.';
    if (!soft && total >= 12 && total <= 16 && d >= 7) return 'Dealer likely makes 17+, so improve your hand.';
    if (soft && total === 18 && d >= 9) return 'Soft 18 is weak vs 9, 10 or Ace, and you can\'t bust by hitting.';
    if (pairVal === 10) return 'Never split tens: 20 is already a great hand.';
    if (pairVal === 5) return 'Treat 5,5 as hard 10.';
    if (pairVal === 4) return 'Treat 4,4 as hard 8.';
    return '';
  }

  function apply(action) {
    const h = st.hands[st.active];
    if (action === 'H') {
      h.cards.push(draw());
      const t = S.handTotal(ranksOf(h.cards)).total;
      if (t >= 21) h.done = true;
    } else if (action === 'S') {
      h.done = true;
    } else if (action === 'D') {
      h.bet *= 2;
      h.doubled = true;
      h.cards.push(draw());
      h.done = true;
    } else if (action === 'P') {
      st.splitUsed = true;
      const [c1, c2] = h.cards;
      const aces = c1.rank === 'A';
      const h1 = { cards: [c1, draw()], bet: BET, fromSplit: true };
      const h2 = { cards: [c2, draw()], bet: BET, fromSplit: true };
      if (aces) { h1.done = true; h2.done = true; } // one card each on split aces
      for (const x of [h1, h2]) if (S.handTotal(ranksOf(x.cards)).total === 21) x.done = true;
      st.hands.splice(st.active, 1, h1, h2);
    }
    advance();
  }

  function advance() {
    while (st.active < st.hands.length && st.hands[st.active].done) st.active++;
    if (st.active >= st.hands.length) finishRound();
    else render();
  }

  // ---------- Dealer & settlement ----------
  function finishRound() {
    st.phase = 'done';
    st.active = -1;
    const dealer = st.dealer;
    dealer.push(draw()); // no hole card: second card comes now
    const dealerBJ = isNatural(dealer);
    const playerBJ = st.hands.length === 1 && isNatural(st.hands[0].cards);
    const anyLive = st.hands.some(h => S.handTotal(ranksOf(h.cards)).total <= 21);

    if (!dealerBJ && !playerBJ && anyLive) {
      while (S.handTotal(ranksOf(dealer)).total < 17) dealer.push(draw());
    }
    const dt = S.handTotal(ranksOf(dealer)).total;

    let net = 0;
    for (const h of st.hands) {
      const pt = S.handTotal(ranksOf(h.cards)).total;
      let r;
      if (playerBJ) r = dealerBJ ? ['push', 0, 'Push'] : ['win', h.bet * 1.5, 'Blackjack!'];
      else if (pt > 21) r = ['lose', -h.bet, 'Bust'];
      else if (dealerBJ) r = ['lose', -h.bet, 'Dealer BJ'];
      else if (dt > 21 || pt > dt) r = ['win', h.bet, 'Win'];
      else if (pt === dt) r = ['push', 0, 'Push'];
      else r = ['lose', -h.bet, 'Lose'];
      h.result = r;
      net += r[1];
    }
    stats.bankroll += net;
    stats.hands++;
    save();
    const sign = net > 0 ? '+' : net < 0 ? '−' : '±';
    $('#outcome').innerHTML = `<span class="res ${net > 0 ? 'win' : net < 0 ? 'lose' : 'push'}">${sign}£${Math.abs(net)}</span>`;
    render();
  }

  // ---------- Rendering ----------
  const shown = new WeakSet(); // cards already on screen (only animate new ones)
  function cardHTML(c) {
    if (!c) return '<div class="card back"></div>';
    const red = c.suit === '♥' || c.suit === '♦';
    const fresh = !shown.has(c);
    shown.add(c);
    return `<div class="card${red ? ' red' : ''}${fresh ? ' new' : ''}"><div class="tl">${c.rank}<small>${c.suit}</small></div><div class="mid">${c.suit}</div></div>`;
  }

  function totalLabel(cards) {
    const { total, soft } = S.handTotal(ranksOf(cards));
    if (isNatural(cards)) return 'BJ';
    return soft && total < 21 ? `${total - 10}/${total}` : String(total);
  }

  function render() {
    const drillPending = stats.mode === 'drill';
    // Dealer
    const dCards = st ? st.dealer.map(cardHTML).join('') + (st.dealer.length === 1 ? cardHTML(null) : '') : '';
    $('#dealer-cards').innerHTML = dCards;
    $('#dealer-total').textContent = st ? totalLabel(st.dealer) : '';

    // Player
    $('#player-hands').innerHTML = !st ? '<div class="label">Tap Deal to start</div>' : st.hands.map((h, i) => {
      const res = h.result ? ` · <span class="res ${h.result[0]}">${h.result[2]}</span>` : '';
      const dbl = h.doubled ? ' · ×2' : '';
      return `<div class="hand${i === st.active && st.phase === 'player' ? ' active' : ''}">
        <div class="cards">${h.cards.map(cardHTML).join('')}</div>
        <div class="label">You <b>${totalLabel(h.cards)}</b>${dbl}${res}</div></div>`;
    }).join('');

    // Buttons
    const playing = st && st.phase === 'player';
    const opt = playing ? options(st.hands[st.active]) : { canDouble: false, canSplit: false };
    document.querySelectorAll('.act').forEach(b => {
      const a = b.dataset.act;
      b.disabled = !playing;
      b.classList.toggle('off', playing && ((a === 'D' && !opt.canDouble) || (a === 'P' && !opt.canSplit)));
    });
    $('#btn-hint').disabled = !playing;
    $('#btn-deal').disabled = playing;
    $('#btn-deal').textContent = drillPending ? 'Next hand' : 'Deal';

    // Scorebar
    $('#sb-acc').textContent = stats.decisions ? `${Math.round(100 * stats.correct / stats.decisions)}%` : '–';
    $('#sb-streak').textContent = stats.streak;
    $('#sb-bank').textContent = `£${stats.bankroll}`;
    $('#sb-bank').parentElement.style.display = drillPending ? 'none' : '';
  }

  function showFeedback(kind, big, desc, tip) {
    const el = $('#feedback');
    el.className = `feedback ${kind}`;
    el.innerHTML = `<span class="big">${big}</span>${desc}${tip ? `<span class="tip">${tip}</span>` : ''}`;
  }
  function clearFeedback() { $('#feedback').className = 'feedback hidden'; }

  function hint() {
    if (!st || st.phase !== 'player') return;
    const h = st.hands[st.active];
    const opt = options(h);
    const ranks = ranksOf(h.cards);
    const up = st.dealer[0].rank;
    const rec = S.recommend(ranks, up, opt);
    showFeedback('hint', `Hint: ${ACTION_NAMES[rec]}`,
      `${S.describeHand(ranks, opt.canSplit)} vs ${up === 'A' ? 'Ace' : S.cardValue(up)}`, tipFor(ranks, up, opt, rec));
    highlight = { key: S.chartKey(ranks, opt.canSplit), col: S.cardValue(up) };
  }

  // ---------- Chart ----------
  let highlight = null;
  function renderChart(target = '#chart') {
    const c = S.chart();
    const hlKey = highlight && highlight.key;
    const hlCol = highlight && highlight.col;
    const head = `<tr><th></th>${c.cols.map(d => `<th class="${d === hlCol ? 'hlc' : ''}">${d === 11 ? 'A' : d}</th>`).join('')}</tr>`;
    const table = (title, rows) => `<h3 class="chart-title">${title}</h3><table class="strat">${head}${rows.map(r =>
      `<tr class="${r.key === hlKey ? 'hl' : ''}"><th class="rowh">${r.label}</th>${r.cells.map((a, i) =>
        `<td class="k-${a}${r.key === hlKey && c.cols[i] === hlCol ? ' hl' : ''}">${a}</td>`).join('')}</tr>`).join('')}</table>`;
    $(target).innerHTML = table('Hard totals', c.hard) + table('Soft totals', c.soft) + table('Pairs', c.pairs);
  }

  // ---------- Stats ----------
  function renderStats() {
    const acc = stats.decisions ? `${Math.round(100 * stats.correct / stats.decisions)}%` : '–';
    const cells = [[acc, 'Accuracy'], [stats.decisions, 'Decisions'], [stats.streak, 'Current streak'],
      [stats.best, 'Best streak'], [stats.hands, 'Hands played'], [`£${stats.bankroll}`, 'Bankroll']];
    $('#stat-grid').innerHTML = cells.map(([v, l]) => `<div class="stat"><b>${v}</b><small>${l}</small></div>`).join('');
    const missed = Object.entries(stats.missed).sort((a, b) => b[1] - a[1]).slice(0, 8);
    $('#missed').innerHTML = missed.length
      ? missed.map(([k, n]) => `<li>${k} <small>(${n}×)</small></li>`).join('')
      : '<li>Nothing yet. Nice!</li>';
  }

  // ---------- Navigation ----------
  function showView(name) {
    document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === `view-${name}`));
    document.querySelectorAll('.tabbar button').forEach(b => b.classList.toggle('active', b.dataset.view === name));
    if (name === 'chart') renderChart();
    if (name === 'stats') renderStats();
  }

  function setMode(mode) {
    stats.mode = mode;
    save();
    $('#mode-play').classList.toggle('active', mode === 'play');
    $('#mode-drill').classList.toggle('active', mode === 'drill');
    showView('table');
    deal();
  }

  // ---------- Wire up ----------
  document.querySelectorAll('.act').forEach(b => b.addEventListener('click', () => {
    highlight = { key: S.chartKey(ranksOf(st.hands[st.active].cards), options(st.hands[st.active]).canSplit), col: S.cardValue(st.dealer[0].rank) };
    act(b.dataset.act);
  }));
  $('#btn-deal').addEventListener('click', deal);
  $('#btn-hint').addEventListener('click', hint);
  $('#btn-chart').addEventListener('click', () => {
    if (st && st.dealer.length) {
      const h = st.hands[st.active] || st.hands[st.hands.length - 1];
      const canSplit = st.phase === 'player' ? options(h).canSplit : false;
      if (st.phase === 'player') highlight = { key: S.chartKey(ranksOf(h.cards), canSplit), col: S.cardValue(st.dealer[0].rank) };
    }
    renderChart('#sheet-chart');
    $('#sheet').classList.add('open');
    const hl = document.querySelector('#sheet-chart tr.hl');
    if (hl) hl.scrollIntoView({ block: 'center' });
  });
  $('#sheet').addEventListener('click', () => $('#sheet').classList.remove('open'));
  $('#mode-play').addEventListener('click', () => setMode('play'));
  $('#mode-drill').addEventListener('click', () => setMode('drill'));
  document.querySelectorAll('.tabbar button').forEach(b => b.addEventListener('click', () => showView(b.dataset.view)));
  $('#btn-reset').addEventListener('click', () => {
    if (!confirm('Reset all stats and bankroll?')) return;
    const mode = stats.mode;
    stats = defaults();
    stats.mode = mode;
    save();
    renderStats();
    render();
  });

  newShoe();
  $('#mode-play').classList.toggle('active', stats.mode === 'play');
  $('#mode-drill').classList.toggle('active', stats.mode === 'drill');
  render();

  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});

  // test hook
  window.__bj = { act, deal, get state() { return st; }, get stats() { return stats; } };
})();
