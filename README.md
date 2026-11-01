# Blackjack Trainer

An iPhone web app for practising blackjack **basic strategy**, with a focus on doubles and splits.
Add it to your Home Screen and it runs full screen and offline, like a native app.

## Rule sets (Rules & stats tab)

**Tropical Bayamón** (default): 6 decks, no hole card, but a dealer blackjack only takes your
original bet (doubles and splits are refunded). Double on hard 7-11 and soft hands. Because extra
money is never lost to a dealer blackjack, strategy matches standard American strategy.

**US (hole card)**: dealer peeks for blackjack, double on any two cards.

**UK (no hole card)**: dealer blackjack takes doubles and splits too. Double on hard 9-11 only,
no double after split. That's why UK strategy hits 10/11 vs 10 or Ace, and hits 8,8 and A,A vs an Ace.

Toggles (Tropical and US): dealer hits/stands soft 17, double after split, surrender.
All: split once, split aces get one card each, blackjack pays 3:2, never take insurance.

## Modes

- **Play**: full hands from a 6-deck shoe with a 10-unit flat bet. Every decision is graded.
- **Drill**: doubles & splits only. Every hand is a pair, a hard 9/10/11, or a soft double
  candidate (when the rules allow soft doubles), against an even spread of dealer cards. One decision per hand.
- **Chart**: the full strategy chart for your rules, with your hand highlighted
  (also on the table via the Chart button).
- **Rules & stats**: table rules, accuracy, streaks, and the hands you miss most.

## Install on iPhone

1. In the GitHub repo go to **Settings → Pages**, set *Source* to **Deploy from a branch**,
   branch **main**, folder **/ (root)**, and save.
2. After a minute, open `https://stakesd.github.io/<repo-name>/` in **Safari**.
3. Tap **Share → Add to Home Screen**.

## Development

Plain HTML/CSS/JS with no build step. `strategy.js` holds all the strategy rules.

```bash
python3 -m http.server   # then open http://localhost:8000
node --test              # strategy unit tests
```

When you change app files, bump `VERSION` in `sw.js` so installed copies update.
