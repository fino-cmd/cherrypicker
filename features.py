"""
CherryPick — Feature Engineering
Applied identically to training data and live prediction inputs.
No I/O — pure transformation functions.
"""

import numpy as np
import pandas as pd

# ── Temporal weighting ────────────────────────────────────────────────────────
# GBM doesn't require normalisation, but explicit weighted features help the
# model learn recency bias faster without relying solely on tree depth.

def _wavg(a, b, c, wa=0.40, wb=0.35, wc=0.25) -> pd.Series:
    """Vectorised weighted average of three Series, ignoring NaN.

    Inputs are coerced to numeric upfront to avoid pandas 3.x object-dtype
    downcast warnings when columns arrive as strings/mixed (CSV reads).
    """
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    c = pd.to_numeric(c, errors="coerce")
    num = a.fillna(0) * wa + b.fillna(0) * wb + c.fillna(0) * wc
    den = a.notna().astype(float) * wa + \
          b.notna().astype(float) * wb + \
          c.notna().astype(float) * wc
    return (num / den.replace(0, np.nan)).where(den > 0)


def apply_temporal_weighting(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # K/9 — we have L15, L30, and season-level values
    df["weighted_k9"] = _wavg(
        df.get("k9_L15",    pd.Series(np.nan, index=df.index)),
        df.get("k9_L30",    pd.Series(np.nan, index=df.index)),
        df.get("k9_season", pd.Series(np.nan, index=df.index)),
    )

    # CSW% — season proxy only (game-level requires raw Statcast)
    df["weighted_csw"] = df.get("csw_pct_L15", pd.Series(np.nan, index=df.index))

    # SwStr%
    df["weighted_swstr"] = df.get("swstr_pct_L15", pd.Series(np.nan, index=df.index))

    # K% (rolling, from game logs)
    df["weighted_k_pct"] = _wavg(
        df.get("k_pct_L15", pd.Series(np.nan, index=df.index)),
        df.get("k_pct_L15", pd.Series(np.nan, index=df.index)),  # no L30 K%, use same
        df.get("k_pct_L15", pd.Series(np.nan, index=df.index)),
    )

    return df


# ── Interaction features ──────────────────────────────────────────────────────
# These encode domain knowledge that the model can discover alone, but adding
# them explicitly speeds up learning and improves generalisation on small folds.

def add_interactions(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    csw  = df.get("csw_pct_L15",      pd.Series(np.nan, index=df.index))
    opp_k = df.get("opp_k_pct_vs_hand", pd.Series(np.nan, index=df.index))
    stuff = df.get("stuff_plus",        pd.Series(np.nan, index=df.index))
    chase = df.get("opp_chase_pct",     pd.Series(np.nan, index=df.index))
    imb   = df.get("implied_max_batters", pd.Series(np.nan, index=df.index))
    k9_l3 = df.get("k9_L3",            pd.Series(np.nan, index=df.index))
    pf    = df.get("park_factor",       pd.Series(100.0,  index=df.index))

    # High CSW + high-K lineup → strongest over signal
    df["csw_x_opp_k"]   = csw   * opp_k

    # Elite stuff vs a chasing lineup
    df["stuff_x_chase"]  = stuff * chase

    # Pitch limit dampens even great CSW matchups
    df["limit_x_csw"]    = imb   * csw

    # Recent dominance vs opponent K rate
    df["k9_x_opp_k"]     = k9_l3 * opp_k

    # Park factor inverted: lower park factor = more pitcher-friendly = more Ks
    # Rescaled so 100 → 1.0, 112 → 0.88, 95 → 1.05
    df["park_adj"]        = (200 - pf) / 100.0

    return df


# ── Feature column registry ───────────────────────────────────────────────────
# Ordered list used as the model's input contract.
# Must match exactly between training and prediction time.

FEATURE_COLS = [
    # Core pitcher skill
    "csw_pct_L15",
    "swstr_pct_L15",
    "k_pct_L15",
    "stuff_plus",
    "k9_L3",
    "k9_L15",
    "k9_L30",
    "k9_season",
    # Pitch load
    "rest_days",
    "innings_prev_start",
    "cumulative_pitches_7d",
    "pitch_limit_flag",
    "implied_max_batters",
    # Opponent
    "opp_k_pct_vs_hand",
    "opp_k_pct_lineup",
    "lineup_confirmed",
    "opp_chase_pct",
    "opp_contact_pct",
    "opp_swstr_pct",
    # Context
    "park_factor",
    "home_away",
    "month",
    "temperature_f",
    "wind_speed_mph",
    "wind_to_from",
    # Temporal weighted composites
    "weighted_k9",
    "weighted_csw",
    "weighted_swstr",
    "weighted_k_pct",
    # Interaction terms
    "csw_x_opp_k",
    "stuff_x_chase",
    "limit_x_csw",
    "k9_x_opp_k",
    "park_adj",
]


# ── Main entry point ──────────────────────────────────────────────────────────

def prepare_features(df: pd.DataFrame,
                     medians: dict | None = None) -> tuple[pd.DataFrame, list[str]]:
    """
    Apply temporal weighting and interactions, then return (X, feature_cols).

    Args:
        df:      Raw dataframe (from CSV or live input).
        medians: Training-set medians dict for imputation.  Pass None during
                 training (medians computed from df itself); pass the saved
                 dict at prediction time to avoid leakage.

    Returns:
        X:            DataFrame with exactly the available FEATURE_COLS.
        feature_cols: List of column names in X (subset of FEATURE_COLS).
    """
    df = apply_temporal_weighting(df)
    df = add_interactions(df)

    # Intersect with available columns
    available = [c for c in FEATURE_COLS if c in df.columns]
    X = df[available].copy()

    for col in X.columns:
        X[col] = pd.to_numeric(X[col], errors="coerce")

    # Impute NaN
    if medians is not None:
        # Prediction path: use saved training medians (no data leakage)
        for col in X.columns:
            if X[col].isna().any():
                X[col] = X[col].fillna(medians.get(col, 0.0))
    else:
        # Training path: fit medians on current df
        for col in X.columns:
            if X[col].isna().any():
                med = X[col].median()
                X[col] = X[col].fillna(med if pd.notna(med) else 0.0)

    return X, available
