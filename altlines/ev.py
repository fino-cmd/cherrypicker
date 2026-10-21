"""
AltLines — Module 3: Edge comparison & Expected Value.

Takes the two halves produced earlier —
    • our simulated tail probability (simulation.py)
    • the book's FAIR implied probability (ingest.prepare_odds)
— aligns them line-by-line, and computes:

    discrepancy = P_model − P_fair      (how wrong the price is)
    ev          = P_win·(odds−1) − P_loss  (per 1 unit staked, push-safe)
    kelly       = full-Kelly stake fraction (then scaled by a safety factor)
    fair_odds   = what the price SHOULD be, given our distribution

`ev` is the number that actually pays: it uses the price you can really get,
not the fair price. `discrepancy` is the diagnostic the brief asks for.
"""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from altlines import config as cfg
from altlines.config import MARKET_SPECS, get_spec
from altlines.ingest import best_price_per_line, prepare_odds
from altlines.config import RANDOM_SEED
from altlines.simulation import (
    anchor_to_line,
    casino_curve_prob,
    player_rng,
    prob_over,
    prob_push,
    prob_under,
)

# Fraction of full Kelly we actually stake. Full Kelly maximises log-growth but
# is brutally volatile and assumes our probability is exact — it never is.
# 0.25 ("quarter Kelly") is the industry-standard risk haircut.
KELLY_FRACTION = 0.25
MAX_STAKE_UNITS = 3.0

# Lines priced beyond this decimal (~+4900) are effectively un-bettable: books
# cap alt ladders well before it, and that is exactly the region where OUR
# probability error dominates the edge. Keeping them makes the board look like a
# lottery. Override with --max-odds 0 to see everything.
MAX_DECIMAL_ODDS = 50.0


# ══════════════════════════════════════════════════════════════════════════════
# 3a. Core single-line math
# ══════════════════════════════════════════════════════════════════════════════

def ev_per_unit(p_win: float, decimal_odds: float, p_push: float = 0.0) -> float:
    """Expected profit per 1 unit staked. A push refunds the stake (0 P/L)."""
    p_loss = max(0.0, 1.0 - p_win - p_push)
    return p_win * (decimal_odds - 1.0) - p_loss


def fair_decimal_odds(p_win: float, p_push: float = 0.0) -> float:
    """The decimal price at which EV == 0 (i.e. what the book SHOULD pay)."""
    if p_win <= 0:
        return float("inf")
    return (1.0 - p_win - p_push) / p_win


def discrepancy(p_model: float, p_fair: float) -> float:
    """Absolute probability gap between us and the de-vigged market."""
    return p_model - p_fair


@lru_cache(maxsize=1)
def _tail_calibration() -> dict:
    """Per-market maps written by altlines.tools.backtest ({} when absent)."""
    try:
        return json.loads(cfg.TAIL_CALIBRATION_JSON.read_text(encoding="utf-8"))["markets"]
    except Exception:  # noqa: BLE001 - no file = no calibration
        return {}


def calibrate_p_over(p_over: float, market: str) -> float:
    """Backtest map p_model → observed frequency of the OVER (identity when the
    market has no map). Isotonic = piecewise-linear knots; Platt = logistic on
    the logit."""
    entry = _tail_calibration().get(market)
    if not entry:
        return p_over
    if entry["method"] == "isotonic":
        return float(np.interp(p_over, entry["isotonic"]["x"], entry["isotonic"]["y"]))
    p = min(max(p_over, 1e-4), 1 - 1e-4)
    z = entry["platt"]["a"] * np.log(p / (1 - p)) + entry["platt"]["b"]
    return float(1.0 / (1.0 + np.exp(-z)))


def blend_with_market(p_model: float, p_fair: float, w: float) -> float:
    """Average the engine and the de-vigged market in logit space.

    w = 1 is the engine alone, w = 0 the market alone. Picking the largest EV
    among hundreds of lines systematically picks the lines where the engine
    erred upward (the winner's curse); shrinking toward the market is the
    standard cure. The first settled games showed exactly that pattern.
    """
    if w >= 1.0:
        return p_model
    a = min(max(p_model, 1e-4), 1 - 1e-4)
    b = min(max(p_fair, 1e-4), 1 - 1e-4)
    z = w * np.log(a / (1 - a)) + (1 - w) * np.log(b / (1 - b))
    return float(1.0 / (1.0 + np.exp(-z)))


def kelly_fraction(p_win: float, decimal_odds: float, p_push: float = 0.0) -> float:
    """Full-Kelly fraction for a win/lose/push bet. Clipped to [0, 1]."""
    p_loss = max(0.0, 1.0 - p_win - p_push)
    dec_m1 = decimal_odds - 1.0
    denom = dec_m1 * (p_win + p_loss)
    if denom <= 0:
        return 0.0
    f = (p_win * dec_m1 - p_loss) / denom
    return float(np.clip(f, 0.0, 1.0))


# ══════════════════════════════════════════════════════════════════════════════
# 3b. Build the value table
# ══════════════════════════════════════════════════════════════════════════════

def _lookup_sims(sims_store: dict, player_id, player_name, market):
    """Sims are keyed by (player_id, market); fall back to name (API feeds)."""
    for key in ((player_id, market), (str(player_name), market), (player_name, market)):
        if key in sims_store:
            return sims_store[key]
    return None


def build_value_table(summary_df: pd.DataFrame, odds_df: pd.DataFrame,
                      sims_store: dict, devig_method: str = "multiplicative",
                      min_prob: float = 0.0,
                      max_odds: float = MAX_DECIMAL_ODDS,
                      anchor: bool = False,
                      seed: int = RANDOM_SEED,
                      calibrate: bool | None = None,
                      model_weight: float | None = None) -> pd.DataFrame:
    """Cross every quoted alt line against our simulated distribution.

    Returns one row per (player, market, book-best price, point, side) with the
    edge and EV columns. Rows we cannot price (no sims / no odds) are dropped.

    `min_prob` discards lines whose OUR win probability is below a floor —
    useful to avoid 2% moonshots that look huge in EV% but almost never land.
    `max_odds` discards un-bettable longshots (see MAX_DECIMAL_ODDS); pass 0 to
    disable the cap.
    `anchor` puts each player's simulated median on the market's main line
    (see simulation.anchor_to_line): the edge left is purely one of shape.
    `level_gap` (model median − main line) is reported either way.
    """
    if odds_df.empty or summary_df.empty:
        return pd.DataFrame()

    odds = prepare_odds(odds_df, method=devig_method)
    odds = best_price_per_line(odds)          # keep the best price per line

    meta = summary_df.set_index(["player_id", "market"])
    rows: list[dict] = []
    anchored: dict = {}

    for _, o in odds.iterrows():
        market = o["market"]
        if market not in MARKET_SPECS:
            continue
        spec = get_spec(market)
        sims = _lookup_sims(sims_store, o.get("player_id"), o.get("player_name"), market)
        if sims is None:
            continue

        main_line = _main_line(o)
        raw_median = float(np.median(sims))
        scale = 1.0
        if anchor and pd.notna(o.get("main_line")):
            akey = (o.get("player_id"), market, main_line)
            if akey not in anchored:
                rng = player_rng(seed, str(o.get("player_id")), f"{market}|anchor")
                anchored[akey] = anchor_to_line(sims, main_line, spec["family"], rng)
            sims, scale = anchored[akey]

        point = float(o["point"])
        side = o["side"]
        p_push = prob_push(sims, point)
        if side == "over":
            p_win = prob_over(sims, point)
            p_curve = casino_curve_prob(point, _main_line(o), spec)
        else:
            p_win = prob_under(sims, point)
            p_curve = 1.0 - casino_curve_prob(point, _main_line(o), spec)
        p_raw = p_win
        if (cfg.APPLY_TAIL_CALIBRATION if calibrate is None else calibrate):
            p_over_cal = calibrate_p_over(prob_over(sims, point), market)
            p_win = p_over_cal if side == "over" else max(0.0, 1.0 - p_over_cal - p_push)
        dec = float(o["decimal_odds"])
        if max_odds and dec > max_odds:
            continue
        p_fair = float(o["p_fair"])
        # Mezcla con el mercado (ver blend_with_market): la probabilidad con la
        # que se calcula el EV ya no es la del motor solo.
        p_engine = p_win
        w = cfg.MODEL_WEIGHT if model_weight is None else model_weight
        p_win = blend_with_market(p_engine, p_fair, w)
        if p_win < min_prob:
            continue
        ev = ev_per_unit(p_win, dec, p_push)
        kf = kelly_fraction(p_win, dec, p_push)

        try:
            m = meta.loc[(o.get("player_id"), market)]
            profile = m["profile"]
            sim_mean = m["sim_mean"]
            sim_median = m["sim_median"]
            sim_skew = m["sim_skew"]
            sim_cv = m["sim_cv"]
        except KeyError:
            profile, sim_mean, sim_median, sim_skew, sim_cv = "?", np.nan, np.nan, np.nan, np.nan

        rows.append({
            "player_id":   o.get("player_id"),
            "player":      o.get("player_name"),
            "team":        o.get("team"),
            "market":      market,
            "market_label": spec["label"],
            "side":        side.upper(),
            "point":       point,
            "main_line":   main_line,
            "level_gap":   round(raw_median - main_line, 2),
            "anchor_scale": round(scale, 3),
            "profile":     profile,
            "sim_mean":    sim_mean,
            "sim_median":  sim_median,
            "sim_skew":    sim_skew,
            "sim_cv":      sim_cv,
            "book":        o.get("best_book"),
            "decimal_odds": dec,
            "p_model":     round(p_win, 4),
            "p_model_raw": round(p_raw, 4),
            "p_engine":    round(p_engine, 4),
            "p_fair":      round(p_fair, 4),
            "p_curve":     round(float(p_curve), 4),
            "discrepancy": round(discrepancy(p_win, p_fair), 4),
            "curve_ratio": round(p_win / p_curve, 3) if p_curve > 0 else np.nan,
            "ev":          round(ev, 4),
            "ev_pct":      round(ev * 100, 2),
            "kelly":       round(kf, 4),
            "stake_units": round(min(kf * KELLY_FRACTION, 1.0) * MAX_STAKE_UNITS, 2),
            "fair_odds":   round(fair_decimal_odds(p_win, p_push), 2),
            "vig":         round(float(o.get("vig", np.nan)), 4) if pd.notna(o.get("vig")) else np.nan,
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values("ev", ascending=False).reset_index(drop=True)


def _main_line(odds_row) -> float:
    """Main line for the player-market; falls back to the quoted point."""
    ml = odds_row.get("main_line")
    if ml is None or (isinstance(ml, float) and np.isnan(ml)):
        return float(odds_row["point"])
    return float(ml)


# ══════════════════════════════════════════════════════════════════════════════
# 3c. Noise filter → the top slice
# ══════════════════════════════════════════════════════════════════════════════

def select_top_pct(df: pd.DataFrame, pct: float = 5.0, metric: str = "ev",
                   require_positive: bool = True) -> pd.DataFrame:
    """Keep only the top `pct`% of opportunities ranked by `metric`.

    Defaults rank by `ev` after dropping anything with EV <= 0 — i.e. "the best
    5% of genuinely +EV bets". Set metric="discrepancy" to rank purely by how
    far our probability sits from the market's, as the brief describes.
    """
    if df.empty:
        return df
    pool = df[df[metric] > 0].copy() if require_positive else df.copy()
    if pool.empty:
        return pool
    n = max(1, int(np.ceil(len(pool) * pct / 100.0)))
    return pool.nlargest(n, metric).sort_values(metric, ascending=False).reset_index(drop=True)


def select_board(df: pd.DataFrame, pct: float = 5.0, metric: str = "ev",
                 min_prob: float | None = None, max_odds: float | None = None,
                 max_per_player: int | None = None) -> pd.DataFrame:
    """The recommended board: the top `pct`% by size, but chosen with care.

    How many: `pct`% of the lines with positive `metric` (same size as before).
    Which ones:
      • only lines with p ≥ min_prob and price ≤ max_odds: long shots are where
        a model's error is largest and where it gets amplified by the price;
      • one line per (player, market): OVER 9.5 / 10.5 / 11.5 rebounds of the
        same player are ONE bet on his night, not three;
      • at most `max_per_player` lines per player across markets.
    """
    if df.empty:
        return df
    min_prob = cfg.BOARD_MIN_PROB if min_prob is None else min_prob
    max_odds = cfg.BOARD_MAX_ODDS if max_odds is None else max_odds
    max_per_player = cfg.BOARD_MAX_PER_PLAYER if max_per_player is None else max_per_player
    positive = df[df[metric] > 0]
    n = max(1, int(np.ceil(len(positive) * pct / 100.0))) if len(positive) else 0
    pool = positive
    if "level_ok" in pool.columns:
        pool = pool[pool["level_ok"].astype(bool)]
    pool = pool[(pool["p_model"] >= min_prob) & (pool["decimal_odds"] <= max_odds)]
    if pool.empty or n == 0:
        return pool.iloc[0:0]
    pool = pool.sort_values(metric, ascending=False)
    pool = pool.drop_duplicates(["player_id", "market"], keep="first")
    pool = pool[pool.groupby("player_id").cumcount() < max_per_player]
    return pool.head(n).reset_index(drop=True)


def top_n(df: pd.DataFrame, n: int = 20, metric: str = "ev") -> pd.DataFrame:
    """Convenience for a fixed-size shortlist."""
    if df.empty:
        return df
    return df.nlargest(n, metric).reset_index(drop=True)
