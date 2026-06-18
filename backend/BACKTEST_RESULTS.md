# Top-Picks "Star" Backtest & Model-Improvement Study

---

## ⚠️ CORRECTION & FINAL DECISION (2026-06-16) — realized PnL overturns the "star" study

The 2026-06-13 study below optimised the **open→high "star" metric**. That metric is **not
tradeable**: you cannot reliably sell at the day's high. When we measure the *realized*
return of an actionable strategy — **buy next-day open, sell next-day close** — the picture
changes, and it differs between markets.

**Harness:** `backtest_oc.py`. Same OOS walk-forward, same `pick_final_score` ranking, same
exclusions, same 33-feature matrix. The only thing varied is the **training target**; every
run reports the **same realized open→close PnL** and a whole-liquid-universe baseline.
Universe = the quality liquidity filter (NSE ≥ ₹50 Cr turnover, USA ≥ $250 M). 30-session
OOS, xgb.

Realized **open→close** PnL, edge vs buying the whole liquid universe:

| Training target | NSE Top-25 (edge/day) | NSE Top-5 | USA Top-25 (edge/day) | USA Top-5 |
|---|---|---|---|---|
| **close→close** (`cc`) | +0.263% (**+0.246**) | +0.513% | +0.088% (**+0.052**) | +0.417% |
| open→high (`oh`, was prod) | +0.455% (+0.438) | +0.752% | +0.034% (**−0.002**) | +0.102% |
| open→close (`oc`) | +0.053% (+0.036) | +0.268% | +0.073% (+0.037) | +0.138% |

**Findings (honest):**
1. The **open→high** target is strong on NSE but has **~zero edge on USA** — it was
   effectively NSE-overfit.
2. A direct **open→close** target is the *weakest* nearly everywhere — open→close is close to
   coin-flip noise (median ≈ 0%), so the classifier can't learn a clean ranking from it.
3. **close→close** is the **only target with positive realized edge on BOTH markets** at both
   Top-25 and Top-5.

**Decision:** reverted the production training target from open→high back to **close-to-close**
(`prediction.py`, both markets). The **quality liquidity filter is kept** — it is the larger,
robust win (the quality tier was the only profitable turnover tier in live data). The earlier
"open→close is negative" alarm came from a noisy **5-day, unfiltered** live sample; the 30-day
OOS on the quality universe corrects it.

---

**Date run:** 2026-06-13
**Star metric:** `stars = floor((next-day high − next-day open) / open × 100)`, capped at 5.
1★ = the day's high ran ≥1% above the open. The user's **main target is ≥2★ (open→high ≥ 2%)**.
**Method:** Out-of-sample (walk-forward). Each stock's model is retrained **only on data before**
the test window, so tested days are never seen in training. Ranking uses the same
`pick_final_score` (trend / volatility / RSI re-weighting) and the same hard exclusions
(supertrend == −1, RSI ≥ 85) as production `get_top_picks`. Model = XGBoost (`xgb`).

> **Honesty note.** The first read used a 15-session window, which is only 75 samples at the
> Top-5 level — too small to trust (one flat day swings it 6–7pts). The headline below uses a
> **30-session window (150 Top-5 / 750 Top-25 samples per market)** so the comparison is
> statistically meaningful. Both the wins **and** the limits are stated plainly.

---

## What was tested

Two pipelines, identical in every other respect (same universe, same OOS protocol, same
ranking, same exclusions):

| | **Baseline** (`backtest_stars.py`) | **A+B prototype** (`backtest_oh.py`) |
|---|---|---|
| Training target | next-day **close-to-close** return | next-day **open→high** (the graded metric) |
| Composite weights | 0.40 / 0.30 / 0.20 / 0.10 on P(+1/+2/+3/+5%) | 0.15 / **0.40** / 0.30 / 0.15 (leans on +2%) |
| Features | 27 production features | 27 + **6 intraday/expansion** features |

The 6 added features (lever B), all computed from data **up to day t** to predict day t+1
(no look-ahead): `atr_expansion` (today's range vs its own 20-day avg), `close_pos_range`
(close position inside the day's range), `oh_today` (today's own open→high), `oh_mean_20`
(avg open→high over 20 sessions), `oh_freq2_20` (how often ≥2% open→high in 20 sessions),
`dist_prior_high5` (close vs prior-5-day high — an opening-range-breakout proxy).

**The A+B prototype does NOT touch production.** It lives entirely in `backtest_oh.py`.

---

## Headline — 30-session out-of-sample, xgb (honest numbers)

### NSE (1113-stock liquid universe, 1051 processed)

| Scope | Metric | Baseline | **A+B** | Δ |
|-------|--------|----------|---------|---|
| Top 25 (750 picks) | ≥1★ | 67.6% | **72.0%** | +4.4 |
| | **≥2★ (TARGET)** | 46.8% | **54.0%** | **+7.2** |
| | ≥3★ | 29.5% | **36.9%** | +7.4 |
| | avg O→H | +2.45% | **+2.98%** | +0.53 |
| | median | +1.77% | **+2.18%** | +0.41 |
| Top 5 (150 picks) | ≥1★ | 62.7% | **72.7%** | +10.0 |
| | **≥2★ (TARGET)** | 44.7% | **56.7%** | **+12.0** |
| | ≥3★ | 31.3% | **46.0%** | +14.7 |
| | avg O→H | +2.58% | **+3.43%** | +0.85 |
| | median | +1.61% | **+2.55%** | +0.94 |

### USA (516-stock liquid universe, 514 processed)

| Scope | Metric | Baseline | **A+B** | Δ |
|-------|--------|----------|---------|---|
| Top 25 (750 picks) | ≥1★ | 66.5% | **69.6%** | +3.1 |
| | **≥2★ (TARGET)** | 41.3% | **47.5%** | **+6.2** |
| | ≥3★ | 25.7% | **30.3%** | +4.6 |
| | avg O→H | +2.34% | **+2.60%** | +0.26 |
| | median | +1.60% | **+1.82%** | +0.22 |
| Top 5 (150 picks) | ≥1★ | 72.0% | **73.3%** | +1.3 |
| | **≥2★ (TARGET)** | 52.0% | **54.0%** | **+2.0** |
| | ≥3★ | 32.7% | **38.7%** | +6.0 |
| | avg O→H | +2.91% | **+3.23%** | +0.32 |
| | median | +2.06% | **+2.26%** | +0.20 |

**Bottom line (honest):** Over 30 sessions, A+B improves **every single metric in both markets** —
no exceptions. The main target (≥2★) rises **+7.2pts (NSE Top-25)** and **+12.0pts (NSE Top-5)**;
**+6.2pts (USA Top-25)** and **+2.0pts (USA Top-5)**. The ≥3★ and average-gain numbers move up
together, meaning the lift is real signal, not just threshold gaming.

---

## The 15-day result, and why it was misleading

The first (15-session) run showed USA **Top-5** ≥2★ *falling* (61.3% → 54.7%) even though USA
Top-25 rose. That looked like a regression. It was **not** — it was small-sample noise:

| USA Top-5 ≥2★ | Baseline | A+B |
|---------------|----------|-----|
| 15-session (75 samples) | 61.3% | 54.7% |
| **30-session (150 samples)** | **52.0%** | **54.0%** |

Doubling the window flipped the baseline from a fluky 61.3% down to a stable 52.0%, and A+B
came out ahead. **Lesson:** judge the Top-5 only at ≥30 sessions; the 15-day Top-5 is too noisy
to base a decision on. The Top-25 (375–750 samples) was directionally correct the whole time.

---

## Per-day detail — 30-session baseline (production model, for reference)

### NSE Top-25 per day (baseline)

| Date | ≥1★ | ≥2★ | ≥3★ | avg | best |
|------|-----|-----|-----|-----|------|
| 05-01 | 17 | 13 | 9 | +2.92% | +14.27% |
| 05-04 | 17 | 8 | 7 | +2.06% | +11.53% |
| 05-05 | 17 | 13 | 7 | +2.70% | +16.02% |
| 05-06 | 21 | 12 | 9 | +2.88% | +12.09% |
| 05-07 | 16 | 10 | 8 | +2.72% | +9.84% |
| 05-08 | 14 | 10 | 3 | +1.94% | +11.68% |
| 05-11 | 13 | 4 | 2 | +1.15% | +3.61% |
| 05-12 | 23 | 17 | 13 | +3.14% | +6.97% |
| 05-13 | 18 | 11 | 6 | +1.89% | +4.90% |
| 05-14 | 17 | 8 | 4 | +2.19% | +11.45% |
| 05-15 | 19 | 15 | 10 | +3.28% | +13.30% |
| 05-18 | 18 | 11 | 6 | +2.29% | +7.44% |
| 05-19 | 22 | 15 | 13 | +3.75% | +14.89% |
| 05-20 | 17 | 11 | 6 | +2.68% | +17.30% |
| 05-21 | 18 | 12 | 9 | +2.55% | +8.43% |
| 05-22 | 17 | 8 | 4 | +1.74% | +6.20% |
| 05-25 | 20 | 17 | 8 | +2.98% | +12.26% |
| 05-26 | 20 | 15 | 11 | +2.75% | +8.59% |
| 05-27 | 0 | 0 | 0 | 0.00% | *(data gap)* |
| 05-28 | 12 | 8 | 4 | +2.19% | +10.78% |
| 05-29 | 15 | 8 | 6 | +2.07% | +9.89% |
| 06-01 | 19 | 14 | 7 | +2.97% | +10.51% |
| 06-02 | 12 | 9 | 5 | +1.76% | +9.45% |
| 06-03 | 18 | 15 | 11 | +2.49% | +9.39% |
| 06-04 | 20 | 16 | 9 | +3.02% | +10.05% |
| 06-05 | 17 | 13 | 6 | +2.32% | +9.64% |
| 06-08 | 23 | 20 | 12 | +3.70% | +15.49% |
| 06-09 | 9 | 7 | 4 | +1.52% | +7.42% |
| 06-10 | 17 | 12 | 7 | +2.20% | +7.96% |
| 06-11 | 21 | 19 | 15 | +3.76% | +12.79% |

**Aggregate (750):** ≥1★ 67.6% · ≥2★ 46.8% · ≥3★ 29.5% · avg +2.45% · median +1.77% · 9.1% no upside.

### USA Top-25 per day (baseline)

| Date | ≥1★ | ≥2★ | ≥3★ | avg | best |
|------|-----|-----|-----|-----|------|
| 04-30 | 14 | 7 | 4 | +2.15% | +12.30% |
| 05-01 | 15 | 10 | 6 | +1.84% | +5.36% |
| 05-04 | 16 | 10 | 5 | +2.14% | +9.92% |
| 05-05 | 21 | 14 | 12 | +3.04% | +10.40% |
| 05-06 | 11 | 6 | 3 | +1.72% | +12.71% |
| 05-07 | 16 | 12 | 6 | +2.96% | +13.01% |
| 05-08 | 20 | 14 | 11 | +3.41% | +13.02% |
| 05-11 | 17 | 7 | 3 | +1.76% | +6.62% |
| 05-12 | 11 | 5 | 3 | +1.49% | +7.82% |
| 05-13 | 17 | 8 | 7 | +1.92% | +6.23% |
| 05-14 | 13 | 9 | 7 | +1.82% | +7.98% |
| 05-15 | 11 | 5 | 2 | +1.18% | +4.43% |
| 05-18 | 20 | 16 | 11 | +3.31% | +10.35% |
| 05-19 | 23 | 16 | 8 | +3.02% | +14.52% |
| 05-20 | 16 | 9 | 7 | +2.81% | +12.36% |
| 05-21 | 15 | 5 | 1 | +1.68% | +11.27% |
| 05-22 | 21 | 12 | 3 | +2.14% | +6.94% |
| 05-26 | 16 | 6 | 4 | +1.93% | +11.54% |
| 05-27 | 16 | 9 | 8 | +2.46% | +8.18% |
| 05-28 | 13 | 8 | 2 | +1.52% | +4.78% |
| 05-29 | 20 | 16 | 13 | +3.21% | +12.92% |
| 06-01 | 18 | 15 | 11 | +3.38% | +13.57% |
| 06-02 | 17 | 8 | 4 | +1.99% | +7.20% |
| 06-03 | 18 | 17 | 13 | +3.02% | +6.45% |
| 06-04 | 11 | 3 | 1 | +1.19% | +6.73% |
| 06-05 | 17 | 11 | 5 | +1.81% | +4.56% |
| 06-08 | 14 | 11 | 6 | +1.91% | +7.73% |
| 06-09 | 22 | 14 | 9 | +3.15% | +8.76% |
| 06-10 | 21 | 17 | 11 | +4.05% | +13.32% |
| 06-11 | 19 | 10 | 7 | +2.16% | +5.76% |

**Aggregate (750):** ≥1★ 66.5% · ≥2★ 41.3% · ≥3★ 25.7% · avg +2.34% · median +1.60% · 3.3% no upside.

---

## Caveats (still true — read before trading on this)

1. **Open→High is a peak metric, not realized PnL.** Capturing it means selling at the
   intraday high. Downside (Open→Low / max-adverse-excursion) is **not yet measured**, so a
   pick that hit +2% high but also −3% low scores the same as a clean winner. This is the #1
   thing to add before treating the star rate as tradeable.
2. **Window regime.** Both 30-session windows were broadly constructive tapes. Numbers would
   soften in a flat/down regime. A 60–90 day, multi-regime run is the next robustness check.
3. **NSE 2026-05-27** is an all-zero **data gap** (missing next-day OHLC) that drags the NSE
   averages down slightly in both pipelines (so it does not bias the A-vs-B comparison).
4. **Model = `xgb` only.** Production default is the `ensemble` (xgb+lgbm). The improvement
   should be re-confirmed on `ensemble` before shipping.
5. **Prototype, not production.** These A+B numbers come from `backtest_oh.py`. Production
   `prediction.py`/`screener.py` are unchanged. Porting requires editing the target +
   features + composite weights (keeping nse/usa parity) and retraining the universe.

## How to reproduce

```bash
# Baseline (production close-to-close model):
cd /var/www/html/nse && sudo docker compose exec -T backend python backtest_stars.py 30 25 xgb
cd /var/www/html/usa && sudo docker compose exec -T backend python backtest_stars.py 30 25 xgb

# A+B prototype (open->high target + intraday features + reweighted composite):
cd /var/www/html/nse && sudo docker compose exec -T backend python backtest_oh.py 30 25 xgb
cd /var/www/html/usa && sudo docker compose exec -T backend python backtest_oh.py 30 25 xgb

# args: K_DAYS  TOP_N  MODEL(xgb|lgbm|ensemble)
```

## Recommendation & next steps

- **Ship A+B to production** (it is a consistent, every-metric win at the reliable sample size):
  port the open→high target + 6 intraday features into `prediction.py._prepare_data`, the
  reweighted composite into `screener.py`, keep **nse/usa parity** (prediction.py identical
  except the benchmark symbol on line 43), then retrain the universe.
- **Before/after shipping, add Open→Low / MAE** columns so the star rate becomes a tradeable
  (not peak-of-day) statistic.
- **Re-confirm on `ensemble`** and run a **60–90 day** multi-regime backtest.
- **Optional further lift:** class-imbalance handling (`scale_pos_weight`) + probability
  calibration, and a Top-5 magnitude/ATR gate.
