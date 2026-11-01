# Halibut Hunter: UK Blackjack Trainer

An iPhone web app for practising **basic strategy** for UK-style blackjack.
Add it to your Home Screen and it runs full screen and offline, like a native app.

## Rules (typical UK casino)

- 6 decks, dealer stands on all 17s
- **No hole card (ENHC)**: the dealer takes their second card after you act.
  If the dealer has blackjack you lose your doubles and splits too.
- Double on hard 9, 10 or 11 only
- Split once, no double after split, split aces get one card each
- Blackjack pays 3:2, no surrender, never take insurance

Because there's no hole card, the strategy differs from the US chart: **hit** 10 and 11 vs a 10 or Ace,
**hit** 8,8 vs 10 or Ace, and **hit** A,A vs an Ace.

## Modes

- **Play**: full hands from a 6-deck shoe with a £10 flat bet. Every decision is graded.
- **Drill**: doubles & splits only. Every hand is a pair or a hard 9/10/11 against an even spread of dealer cards. One decision per hand.
- **Chart**: the full strategy chart, with your last hand highlighted.
- **Stats**: accuracy, streaks, and the hands you miss most (saved on your phone).

## Install on iPhone

1. In the GitHub repo go to **Settings → Pages**, set *Source* to **Deploy from a branch**,
   branch **main**, folder **/ (root)**, and save.
2. After a minute, open `https://<your-username>.github.io/halibuthunter/` in **Safari**.
3. Tap **Share → Add to Home Screen**.

## Development

Plain HTML/CSS/JS with no build step. `strategy.js` holds all the strategy rules.

```bash
python3 -m http.server   # then open http://localhost:8000
node --test              # strategy unit tests
```

When you change app files, bump `VERSION` in `sw.js` so installed copies update.
