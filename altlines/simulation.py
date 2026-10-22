"""
AltLines — Module 2: Monte Carlo distribution engine  ("the edge").

What this module does, and why it is NOT a regression
-----------------------------------------------------
A regression predicts a single number — typically near the *median* of a
player's outcomes. Books then wrap a generic, symmetric curve around that
number and read extreme alt lines off it. For a boom-or-bust player the truth
is right-skewed with a fat right tail, so the book's curve understates the
extreme over. That gap is the edge.

Instead of predicting a point, we RECONSTRUCT THE WHOLE DISTRIBUTION:

    opportunities   ~ NegativeBinomial(mu, var)     # usage/volume risk
    per-unit value  ~ 3-regime mixture (bust / base / boom) × lognormal noise
    value           = opportunities × per-unit value

For counting props we use a Beta-Binomial (opportunities × a Beta-distributed
conversion rate), naturally over-dispersed relative to the Poisson/binomial
curve books like to assume.

The single most important design decision here is the VARIANCE BUDGET
--------------------------------------------------------------------------
Naively multiplying a regime mixture by a full-strength lognormal DOUBLE-COUNTS
the variance, inflating the tail until the mean is dominated by a handful of
draws. The bulk of the distribution then collapses toward zero and every alt
line looks like a giant +EV trap. That is a false edge, and it is the classic
way these tools fool their authors.

So we split the player's observed variance into:
    (a) volume variance        — from the opportunity counts
    (b) between-regime variance — the boom/bust structure
    (c) within-regime variance  — residual lognormal noise
and then CALIBRATE (c) numerically so the simulated total variance matches the
player's own EWMA-weighted variance. The result keeps the skew (from a+b) while
staying honest about the level and spread. Finally we rescale the sample MEAN to
our projection — we centre on the mean, the book centres on the median.

Requires >= 10,000 iterations per player (default 20,000, cheap).
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
from scipy import stats

from altlines.config import (
    HALFLIFE_VAR_DEFAULT,
    LEVEL_BLEND_FAST,
    N_SIM_DEFAULT,
    P_INACTIVE_DEFAULT,
    RANDOM_SEED,
    SHRINKAGE_K0,
    get_spec,
)

# Bounds for the calibration knob so the fit can never run away.
_SIGMA_BOUNDS = (0.02, 1.20)      # continuous: within-regime lognormal sigma
_CONC_BOUNDS = (0.50, 400.0)      # count: Beta concentration (higher = tighter)
_CALIB_ITERS = 5
_CALIB_TOL = 0.05                 # stop once |sim_var/target_var - 1| < 5%


# ── Weighted-statistics helpers (recency weighting) ───────────────────────────

def _ewma_weights(n: int, halflife: float = 6.0) -> np.ndarray:
    """Exponentially-decaying weights, most recent observation last & heaviest.

    halflife is in GAMES: an observation `halflife` games older counts half.
    """
    if n <= 0:
        return np.array([])
    ages = np.arange(n)[::-1]                     # 0 = most recent
    w = 0.5 ** (ages / max(halflife, 1e-9))
    return w / w.sum()


def _wmean(a: np.ndarray, w: np.ndarray) -> float:
    m = np.isfinite(a)
    if not m.any():
        return float("nan")
    return float(np.sum(w[m] * a[m]) / np.sum(w[m]))


def _wvar(a: np.ndarray, w: np.ndarray) -> float:
    m = np.isfinite(a)
    if m.sum() < 2:
        return float("nan")
    mu = _wmean(a, w)
    return float(np.sum(w[m] * (a[m] - mu) ** 2) / np.sum(w[m]))


def _wquantile(a: np.ndarray, w: np.ndarray, q: float) -> float:
    """Weighted quantile via cumulative weight crossing."""
    m = np.isfinite(a)
    if not m.any():
        return float("nan")
    a, w = a[m], w[m]
    order = np.argsort(a)
    a, w = a[order], w[order]
    cw = np.cumsum(w) / np.sum(w)
    return float(np.interp(q, cw, a))


def _wfrac(a: np.ndarray, w: np.ndarray, condition: np.ndarray) -> float:
    """Weighted fraction of rows satisfying `condition`."""
    m = np.isfinite(a)
    if not m.any():
        return 0.0
    return float(np.sum(w[m] * condition[m].astype(float)) / np.sum(w[m]))


def _nbinom(rng: np.random.Generator, mu: float, var: float, size: int) -> np.ndarray:
    """Overdispersed count draws with a target mean/variance (Poisson fallback)."""
    mu = max(float(mu), 1e-6)
    if not np.isfinite(var) or var <= mu * 1.0001:
        return rng.poisson(mu, size=size)
    k = mu * mu / (var - mu)
    p = k / (k + mu)
    return rng.negative_binomial(k, p, size=size)


# ══════════════════════════════════════════════════════════════════════════════
# 2a. Projection: logs → distribution parameters
# ══════════════════════════════════════════════════════════════════════════════

def league_prior_var(spec: dict, mean: float) -> float | None:
    """League variance prior for a player at level `mean`.

    `league_sd` holds at `league_ref_mean`; away from it the variance scales
    linearly with the mean (overdispersed-count behaviour). Without this a
    single absolute sd would hand every low-volume player a fat tail.
    """
    league_sd = spec.get("league_sd")
    if not league_sd:
        return None
    ref = spec.get("league_ref_mean")
    if not ref:
        return float(league_sd) ** 2
    return float(league_sd) ** 2 * max(mean, 0.25 * ref) / float(ref)


def project_player(logs: pd.DataFrame, spec: dict, halflife: float = 6.0,
                   halflife_var: float | None = None,
                   level_mult: float = 1.0) -> dict:
    """Turn one player's log history for one market into simulation parameters.

    Method-of-moments on the player's OWN recency-weighted logs. Swap in a
    Bayesian/EM fit if you prefer; this returned dict is the only contract the
    simulator needs.

    `level_mult` is tonight's context (altlines/context.py: rest, home/away,
    opponent, teammates out) as one multiplier of the VOLUME. It scales
    vol_mu and the level together — the mean is pinned to the level after the
    simulation, so scaling the volume alone would be undone — and both
    variances by the same factor (var ∝ mean, the league prior's law), so the
    per-unit shape (regimes, conversion rate) is untouched.
    """
    logs = logs.sort_values("date") if "date" in logs.columns else logs
    v = pd.to_numeric(logs["value"], errors="coerce").to_numpy(float)
    if "usage" in logs.columns:
        u = pd.to_numeric(logs["usage"], errors="coerce").to_numpy(float)
    else:
        u = v.copy()
    u = np.nan_to_num(u, nan=0.0)
    keep = np.isfinite(v)
    v, u = v[keep], u[keep]
    n = len(v)
    if n == 0:
        raise ValueError("project_player called with an empty history")

    family = spec["family"]
    w = _ewma_weights(n, halflife)
    # Slower weights for anything variance-related (see HALFLIFE_VAR_DEFAULT).
    hl_var = halflife_var if halflife_var else max(halflife * 3.0,
                                                  HALFLIFE_VAR_DEFAULT)
    w_var = _ewma_weights(n, hl_var)

    # ── Level & spread targets, straight from the logs ────────────────────────
    # Count markets already produce zeros naturally (0 threes, 0 assists is a
    # normal NBA game). Injecting an extra "did not play" spike on top of that
    # double-counts the left tail and manufactures edges on deep UNDERS, so the
    # spike only applies to continuous markets, where a played game can never
    # produce a 0.
    p_inactive = 0.0 if family == "count" else float(P_INACTIVE_DEFAULT)
    # Blend fast EWMA (reacts to real role changes) with the full sample (stable).
    value_mean = max(0.0, LEVEL_BLEND_FAST * _wmean(v, w)
                     + (1.0 - LEVEL_BLEND_FAST) * float(np.mean(v)))
    value_var = _wvar(v, w_var)
    m = float(level_mult) if level_mult and np.isfinite(level_mult) else 1.0

    # Empirical-Bayes shrinkage of the variance toward the market prior. A
    # 12-game sample can easily show a 1.5x inflated sd by chance; extrapolating
    # that to a 3-sigma alt line manufactures fake edges. Long histories keep
    # most of their own variance; short ones lean on `league_sd`.
    shrink_w = n / (n + SHRINKAGE_K0)
    prior_var = league_prior_var(spec, value_mean)
    if prior_var and np.isfinite(value_var):
        value_var = shrink_w * value_var + (1.0 - shrink_w) * prior_var
    # Context last, after the prior (which already scales with the mean).
    value_mean *= m
    value_var *= m
    target_mean = value_mean * (1.0 - p_inactive)

    # ── Volume (opportunity) distribution ─────────────────────────────────────
    # Same blend as the level: a stable opportunity estimate, lightly weighted
    # toward recent games.
    vol_mu = max(0.0, LEVEL_BLEND_FAST * _wmean(u, w)
                 + (1.0 - LEVEL_BLEND_FAST) * float(np.mean(u)))
    vol_var = _wvar(u, w_var)
    if not np.isfinite(vol_var) or vol_var <= 0:
        vol_var = vol_mu * 1.5
    vol_mu *= m
    vol_var *= m

    params = {
        "n_games": n,
        "family": spec["family"],
        "vol_mu": vol_mu,
        "vol_var": float(vol_var),
        "p_inactive": p_inactive,
        "level_mult": m,
    }

    # ── Per-unit behaviour differs by family ──────────────────────────────────
    if spec["family"] == "continuous":
        if not np.isfinite(value_var) or value_var <= 0:
            value_var = (0.10 * max(target_mean, 1.0)) ** 2
        params["target_var"] = float(max(value_var, (0.05 * target_mean) ** 2))

        mask = u > 0
        unit = np.where(mask, v / np.where(mask, u, 1.0), np.nan)
        unit_mu = _wmean(unit, w)
        if not np.isfinite(unit_mu) or unit_mu <= 0:
            unit_mu = value_mean / max(vol_mu, 1e-6)
        unit_mu = max(unit_mu, 1e-6)

        # Three latent regimes, read off the logs (robust quantiles).
        # NOTE: detected with the SLOW weights, not the fast ones. Regimes are a
        # property of the player-season; reading them off a 6-game hot streak is
        # how a mixture model starts manufacturing fat tails (i.e. fake edges).
        unit_med = _wquantile(unit, w_var, 0.50)
        if not np.isfinite(unit_med) or unit_med <= 0:
            unit_med = unit_mu
        boom_ratio = float(np.clip(_wquantile(unit, w_var, 0.90) / unit_mu, 1.15, 3.2))
        bust_ratio = float(np.clip(_wquantile(unit, w_var, 0.10) / unit_mu, 0.05, 0.85))
        p_boom = float(np.clip(_wfrac(unit, w_var, unit > 1.5 * unit_med), 0.0, 0.35))
        p_bust = float(np.clip(_wfrac(unit, w_var, unit < 0.5 * unit_med), 0.0, 0.35))

        # ── Variance budget ───────────────────────────────────────────────────
        # Split the TOTAL value variance into volume (a), between-regime (b) and
        # within-regime (c). See the module docstring for why this matters.
        mu_mult = p_bust * bust_ratio + (1.0 - p_bust - p_boom) * 1.0 + p_boom * boom_ratio
        e_unit = unit_mu * mu_mult
        # variance of a product: Var(V) ≈ E[u]²Var[e] + Var[u]E[e]² + Var[u]Var[e]
        unit_var_budget = max(params["target_var"] - vol_var * e_unit ** 2, 0.0) / \
            max(vol_mu ** 2 + vol_var, 1e-9)

        v_mix = (p_bust * (unit_mu * bust_ratio - e_unit) ** 2 +
                 (1.0 - p_bust - p_boom) * (unit_mu - e_unit) ** 2 +
                 p_boom * (unit_mu * boom_ratio - e_unit) ** 2)
        if v_mix > 0.85 * unit_var_budget and v_mix > 0:
            s = np.sqrt(0.85 * unit_var_budget / v_mix)
            boom_ratio = 1.0 + (boom_ratio - 1.0) * s
            bust_ratio = 1.0 - (1.0 - bust_ratio) * s
            v_mix = 0.85 * unit_var_budget

        residual_unit_var = max(unit_var_budget - v_mix, 0.0)
        # lognormal: Var = mu²(e^{σ²}-1)  ⇒  σ² = ln(1 + Var/mu²)
        sigma = float(np.sqrt(np.log(1.0 + residual_unit_var / max(e_unit ** 2, 1e-9))))
        params.update({
            "unit_mu": float(unit_mu),
            "unit_cv": float(np.clip(sigma, *_SIGMA_BOUNDS)),
            "p_boom": p_boom,
            "p_bust": p_bust,
            "boom_ratio": float(boom_ratio),
            "bust_ratio": float(bust_ratio),
            "target_mean": float(max(target_mean, 0.0)),
        })

    else:  # family == "count" → Beta-Binomial
        if not np.isfinite(value_var) or value_var <= 0:
            value_var = value_mean * 1.5
        # Poisson-like floor: a count can never be less dispersed than Poisson.
        params["target_var"] = float(max(value_var, target_mean * 1.05))

        # Conversion rate = VALUE ÷ OPPORTUNITY. Weighted *pooled* ratio
        # (Σw·v / Σw·u) instead of the mean of per-game ratios: dividing by a
        # small, noisy usage inflates the estimate badly.
        num = float(np.sum(w * v))
        den = float(np.sum(w * u))
        rate_mu = float(np.clip(num / den if den > 0 else 0.0, 1e-4, 0.95))

        # ── Variance budget (same idea as the continuous path) ────────────────
        # Counts carry an IRREDUCIBLE binomial term that products of continuous
        # variables do not have. With u ⟂ p:
        #   Var(V) = E[u·p(1−p)] + Var(u·p)
        #          = vol_mu·rate(1−rate) + rate_var·(vol_mu² − vol_mu + vol_var)
        #            + vol_var·rate²
        # ⇒ rate_var = (target_var − vol_mu·rate(1−rate) − vol_var·rate²)
        #              / (vol_mu² − vol_mu + vol_var)
        # Omitting the binomial term (or reading Var(p) straight off `value/usage`)
        # double-counts dispersion, fattening every tail into a fake edge.
        binom_term = vol_mu * rate_mu * (1.0 - rate_mu)
        denom = max(vol_mu ** 2 - vol_mu + vol_var, 1e-9)
        rate_var = max(params["target_var"] - binom_term - vol_var * rate_mu ** 2, 0.0) / denom
        # Numerical guard only — a near-deterministic conversion rate is a
        # legitimate model outcome (it just means volume drives the variance).
        rate_var = max(rate_var, 1e-4 * rate_mu * (1.0 - rate_mu))

        # Beta(a, b): mean = rate_mu, var = rate_var
        conc = rate_mu * (1.0 - rate_mu) / max(rate_var, 1e-9) - 1.0
        params.update({
            "rate_mu": rate_mu,
            "rate_var": float(rate_var),
            "conc": float(np.clip(conc, *_CONC_BOUNDS)),
            "target_mean": float(max(target_mean, 0.0)),
        })

    return params


# ══════════════════════════════════════════════════════════════════════════════
# 2b. Simulation
# ══════════════════════════════════════════════════════════════════════════════

def simulate_player(params: dict, spec: dict, n_iter: int = N_SIM_DEFAULT,
                    rng: np.random.Generator | None = None,
                    dispersion: float | None = None) -> np.ndarray:
    """Draw `n_iter` possible outcomes for one player-market.

    `dispersion` overrides the within-regime knob (lognormal sigma for
    continuous markets, Beta concentration for count markets). The calibration
    loop uses it; normal callers can ignore it.

    Returns a 1-D array of simulated stat values — the full distribution.
    """
    rng = rng or np.random.default_rng()
    inactive = rng.random(n_iter) < params["p_inactive"]
    vol = _nbinom(rng, params["vol_mu"], params["vol_var"], n_iter)

    if spec["family"] == "continuous":
        sigma = params["unit_cv"] if dispersion is None else dispersion
        u = rng.random(n_iter)
        mult = np.where(
            u < params["p_bust"], params["bust_ratio"],
            np.where(u < params["p_bust"] + params["p_boom"], params["boom_ratio"], 1.0),
        )
        unit = params["unit_mu"] * mult * rng.lognormal(0.0, max(sigma, 1e-3), n_iter)
        val = vol * unit
    else:  # count → Beta-Binomial
        conc = params["conc"] if dispersion is None else dispersion
        a = max(params["rate_mu"] * conc, 1e-6)
        b = max((1.0 - params["rate_mu"]) * conc, 1e-6)
        p_conv = rng.beta(a, b, n_iter)
        val = rng.binomial(vol.astype(int), np.clip(p_conv, 0.0, 1.0))

    val = np.asarray(val, dtype=float)
    val[inactive] = 0.0

    # Pin the MEAN to our projection; keep the skew/kurtosis untouched.
    target = params["target_mean"]
    if val.mean() > 0 and target > 0:
        val *= target / val.mean()

    return np.round(val, 3)


def calibrate_player(params: dict, spec: dict, n_iter: int = N_SIM_DEFAULT,
                     rng: np.random.Generator | None = None,
                     iters: int = _CALIB_ITERS) -> np.ndarray:
    """Simulate and numerically tune the within-regime spread so the simulated
    variance matches the player's own history. Prevents the false-edge failure
    mode described in the module docstring.
    """
    rng = rng or np.random.default_rng()
    continuous = spec["family"] == "continuous"
    target_var = params.get("target_var", np.nan)
    disp = params["unit_cv"] if continuous else params["conc"]
    lo, hi = _SIGMA_BOUNDS if continuous else _CONC_BOUNDS

    best: tuple[float, np.ndarray, float] | None = None
    for _ in range(max(int(iters), 1)):
        sims = simulate_player(params, spec, n_iter, rng, dispersion=disp)
        if not np.isfinite(target_var) or target_var <= 0:
            return sims
        var = float(np.var(sims, ddof=1))
        err = abs(var - target_var) / target_var if target_var > 0 else 0.0
        if best is None or err < best[0]:
            best = (err, sims, disp)
        if err < _CALIB_TOL or var <= 0:
            break
        # Continuous: bigger sigma → bigger variance.
        # Count: bigger concentration → SMALLER variance (inverse direction).
        ratio = target_var / var
        disp = float(np.clip(disp * np.sqrt(ratio), lo, hi)) if continuous \
            else float(np.clip(disp * np.sqrt(1.0 / ratio), lo, hi))

    if best is None:                                   # pragma: no cover
        return simulate_player(params, spec, n_iter, rng)
    params["calibrated_dispersion"] = best[2]
    return best[1]


# ══════════════════════════════════════════════════════════════════════════════
# 2c. Distribution summaries & tail probabilities
# ══════════════════════════════════════════════════════════════════════════════

def summarize(sims: np.ndarray) -> dict:
    """Full-distribution read-out, including the shape stats that expose the
    market's blind spot (skew, excess kurtosis, mean−median gap)."""
    sims = np.asarray(sims, dtype=float)
    qs = np.percentile(sims, [1, 5, 10, 25, 50, 75, 90, 95, 99])
    mean, med, sd = float(sims.mean()), float(np.median(sims)), float(sims.std(ddof=1))
    return {
        "sim_mean":         round(mean, 2),
        "sim_median":       round(med, 2),
        "sim_std":          round(sd, 2),
        "sim_cv":           round(sd / mean, 3) if mean > 0 else np.nan,
        "sim_skew":         round(float(stats.skew(sims)), 3),
        "sim_kurtosis":     round(float(stats.kurtosis(sims)), 3),
        "mean_minus_median": round(mean - med, 2),
        "p01": round(qs[0], 2), "p05": round(qs[1], 2), "p10": round(qs[2], 2),
        "p25": round(qs[3], 2), "p50": round(qs[4], 2), "p75": round(qs[5], 2),
        "p90": round(qs[6], 2), "p95": round(qs[7], 2), "p99": round(qs[8], 2),
        "zero_frac": round(float((sims <= 0).mean()), 4),
    }


def profile_label(summary: dict, spec: dict) -> str:
    """Cheap heuristic tag used by the dashboard to explain 'why' a line is on
    the list. High skew / high CV = exactly the profile books misprice."""
    skew, cv = summary["sim_skew"], summary["sim_cv"]
    if not np.isfinite(skew):
        skew = 0.0
    if not np.isfinite(cv):
        cv = 0.0
    if skew > 1.0 or cv > 0.85:
        return "BOOM-BUST"
    if skew > 0.5 or cv > 0.55:
        return "VOLATILE"
    return "STEADY"


def prob_over(sims: np.ndarray, point: float) -> float:
    """P(X > point). For half-point lines this is the win prob of the over."""
    return float((np.asarray(sims) > point).mean())


def prob_under(sims: np.ndarray, point: float) -> float:
    return float((np.asarray(sims) < point).mean())


def prob_push(sims: np.ndarray, point: float) -> float:
    """P(X == point) — non-zero only on whole-number lines."""
    return float((np.asarray(sims) == point).mean())


def casino_curve_prob(point: float, main_line: float, spec: dict,
                      sigma_ratio: float | None = None) -> float:
    """The book's *generic symmetric curve*, reproduced.

    Represents the mechanical way alt lines are derived: a Normal centred on
    the main line (≈ the player's median) with ONE sigma per market. We use it
    to quantify exactly how wrong the book is for a given player (the
    `curve_ratio` column).

    Sigma resolution order: explicit `sigma_ratio` override → the market's
    absolute `casino_sigma` (preferred: books use a per-market sigma, not one
    scaled to each player's own line) → `casino_sigma_ratio × main_line`.
    """
    if sigma_ratio is not None:
        sigma = max(sigma_ratio * max(main_line, 1.0), 0.5)
    elif spec.get("casino_sigma"):
        # One sigma per market, rescaled to the line's level the same way the
        # league prior is (sd ∝ √mean); at the reference level it is exactly
        # `casino_sigma`.
        sigma = float(spec["casino_sigma"])
        ref = spec.get("league_ref_mean")
        if ref:
            sigma *= float(np.sqrt(max(main_line, 0.25 * ref) / float(ref)))
    else:
        sigma = max(spec.get("casino_sigma_ratio", 0.40) * max(main_line, 1.0), 0.5)
    p = float(stats.norm.sf(point, loc=main_line, scale=sigma))
    return min(max(p, 1e-9), 1 - 1e-9)


def tail_mismatch(p_sim: float, p_casino: float) -> dict:
    """Compare our simulated tail to the book's curve."""
    if p_casino <= 0:
        return {"tail_ratio": np.nan, "tail_diff": np.nan}
    return {
        "tail_ratio": round(p_sim / p_casino, 3),
        "tail_diff":  round(p_sim - p_casino, 4),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2d. Universe: run every player-market at once
# ══════════════════════════════════════════════════════════════════════════════

def player_rng(seed: int, player_id: str, market: str) -> np.random.Generator:
    """A deterministic RNG stream **per (player, market)**.

    Why this exists: `simulate_universe` used to create ONE generator and hand
    it to every player in `groupby` order, so a player's draws depended on who
    else happened to be in the run. That made it impossible to simulate a
    subset — pick one game out of the slate and you would get different
    numbers than the league-wide board, which is unacceptable for a tool whose
    whole point is trustworthy probabilities.

    Deriving the stream from (seed, player_id, market) makes each player's
    simulation independent of the rest of the universe:

        league run  ==  per-game run  ==  CLI run   (same params, same seed)

    `hashlib` is used instead of Python's `hash()` because the latter is salted
    per process and would break reproducibility across runs.
    """
    digest = hashlib.blake2b(f"{player_id}|{market}".encode("utf-8"),
                             digest_size=8).digest()
    stream = int.from_bytes(digest, "little")
    return np.random.default_rng(np.random.SeedSequence([int(seed), stream]))


def anchor_to_line(sims: np.ndarray, main_line: float, family: str,
                   rng: np.random.Generator | None = None,
                   max_scale: float = 2.0) -> tuple[np.ndarray, float]:
    """Move the simulated distribution so its MEDIAN sits on the market's main
    line, keeping the player's own SHAPE (CV and skew are scale-invariant).

    This is the thesis applied literally: the book gets the median right (it
    has injury news, roles, minutes we do not); it gets the tails wrong because
    it prices them off one generic curve. Anchoring makes every remaining edge
    an edge of SHAPE, not a disagreement about the player's level — which is
    exactly where a model fed only on box scores is least trustworthy.

    How the level moves by a factor k without inventing tail:

      continuous  x' = k·m + √k·(x − m)   (m = sim median)
                  median → k·m = main line; spread grows √k, i.e. variance
                  ∝ mean, the same law the league prior uses. Multiplying the
                  whole draw by k would grow the spread by k and fatten the
                  tail on every upward anchor.
      count       binomial thinning (k < 1) or Poisson thickening (k > 1) of
                  each draw: events are removed / added one at a time, as with
                  fewer / more attempts. Multiplying a count by 1.25 would turn
                  4 threes into 5 and triple the probability of the deep alt.
                  k is found by bisection so that P(x' > main line) = ½, with
                  common random numbers so the search is monotone.

    Returns (anchored sims, k).
    """
    x = np.asarray(sims, dtype=float)
    if not np.isfinite(main_line) or main_line <= 0:
        return x, 1.0
    if family != "count":
        m = float(np.median(x))
        if m <= 0:
            return x, 1.0
        k = float(np.clip(main_line / m, 1.0 / max_scale, max_scale))
        return np.clip(k * m + np.sqrt(k) * (x - m), 0.0, None), k

    rng = rng or np.random.default_rng(0)
    xi = np.clip(np.rint(x), 0, None).astype(np.int64)
    # Re-seeding the same stream for every k = common random numbers.
    stream = int(rng.integers(2**31))

    def _move(k: float) -> np.ndarray:
        r = np.random.default_rng(stream)
        if k <= 1.0:
            return r.binomial(xi, k)
        return xi + r.poisson((k - 1.0) * xi)

    def _p_over(k: float) -> float:
        return float(np.mean(_move(k) > main_line))

    lo, hi = 1.0 / max_scale, max_scale
    if _p_over(hi) < 0.5:
        return _move(hi).astype(float), hi
    if _p_over(lo) > 0.5:
        return _move(lo).astype(float), lo
    for _ in range(30):
        mid = 0.5 * (lo + hi)
        if _p_over(mid) < 0.5:
            lo = mid
        else:
            hi = mid
    k = 0.5 * (lo + hi)
    return _move(k).astype(float), k


def simulate_universe(logs: pd.DataFrame, markets: list[str] | None = None,
                      n_iter: int = N_SIM_DEFAULT,
                      seed: int = RANDOM_SEED,
                      halflife: float = 6.0,
                      halflife_var: float | None = None,
                      level_mults: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Simulate every (player, market) present in `logs`.

    `level_mults` = {(player_id, market): multiplier} from altlines/context.py
    (tonight's rest / home / opponent / teammates out); missing keys = 1.0.

    Returns:
        summary_df  — one row per player-market with the full distribution
                      read-out and a profile label.
        sims_store  — {(player_id, market): np.ndarray} used later to price
                      specific alt lines in ev.py.
    """
    markets = markets or sorted(logs["market"].dropna().unique())
    logs = logs[logs["market"].isin(markets)]

    rows: list[dict] = []
    sims_store: dict[tuple[str, str], np.ndarray] = {}

    for (pid, market), grp in logs.groupby(["player_id", "market"], sort=False):
        spec = get_spec(market)
        try:
            params = project_player(grp, spec, halflife=halflife,
                                    halflife_var=halflife_var,
                                    level_mult=(level_mults or {}).get((str(pid), market), 1.0))
            # Per-player stream: makes this player's result independent of who
            # else is in `logs`, so a single game simulates identically to the
            # league-wide board (see player_rng).
            sims = calibrate_player(params, spec, n_iter=n_iter,
                                    rng=player_rng(seed, str(pid), str(market)))
        except Exception as exc:  # noqa: BLE001 - one bad player must not kill the run
            print(f"[WARN] {pid}/{market}: {exc}")
            continue

        sims_store[(pid, market)] = sims
        summary = summarize(sims)
        row = {
            "player_id":   pid,
            "player_name": grp["player_name"].iloc[-1] if "player_name" in grp else pid,
            "team":        grp["team"].iloc[-1] if "team" in grp else None,
            "market":      market,
            "market_label": spec["label"],
            "n_games":     params["n_games"],
            "proj_mean":   round(params["target_mean"], 2),
            "proj_usage":  round(params["vol_mu"], 2),
            "p_inactive":  round(params["p_inactive"], 3),
            "level_mult":  round(params["level_mult"], 4),
            "profile":     profile_label(summary, spec),
        }
        row.update(summary)
        rows.append(row)

    summary_df = pd.DataFrame(rows)
    if not summary_df.empty:
        summary_df = summary_df.sort_values(["market", "sim_mean"],
                                            ascending=[True, False]).reset_index(drop=True)
    return summary_df, sims_store
