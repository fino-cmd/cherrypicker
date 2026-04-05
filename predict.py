"""
CherryPick — Prediction Interface
This module is imported by FastAPI to score tonight's pitcher starts.

Usage:
    from predict import predict_strikeouts

    result = predict_strikeouts({
        "csw_pct_L15":       0.285,
        "swstr_pct_L15":     0.122,
        "k_pct_L15":         0.265,
        "stuff_plus":        108.0,
        "k9_L3":             9.4,
        "rest_days":         5,
        "innings_prev_start":6.0,
        "cumulative_pitches_7d": 95,
        "pitch_limit_flag":  0,
        "implied_max_batters": 26.3,
        "opp_k_pct_vs_hand": 0.235,
        "opp_chase_pct":     0.305,
        "opp_contact_pct":   0.785,
        "opp_swstr_pct":     0.118,
        "park_factor":       99,
        "home_away":         1,
        "month":             6,
        "temperature_f":     72.0,
        "wind_speed_mph":    8.0,
        "wind_to_from":      1,
        "k_line":            6.5,      # optional — from odds API
    })

    # Returns:
    # {
    #   "model_projection": 7.2,
    #   "ci_low": 5.8,
    #   "ci_high": 8.6,
    #   "std_dev": 1.4,
    #   "pitch_limit_flag": False,
    #   "confidence": 4,
    #   "edge_vs_line": 0.7
    # }
"""

from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from features import prepare_features

_MODELS_DIR = Path(__file__).parent / "models"

# Lazy-loaded singletons — loaded once on first call
_model       = None
_feat_cols   = None
_medians     = None


def _load() -> None:
    global _model, _feat_cols, _medians

    if _model is not None:
        return

    model_path   = _MODELS_DIR / "k_model.pkl"
    feat_path    = _MODELS_DIR / "feature_cols.pkl"
    median_path  = _MODELS_DIR / "feature_medians.pkl"

    if not model_path.exists():
        raise FileNotFoundError(
            "Model not found. Run:  python train_model.py"
        )

    _model     = joblib.load(model_path)
    _feat_cols = joblib.load(feat_path)
    _medians   = joblib.load(median_path) if median_path.exists() else {}


# ── Core prediction ───────────────────────────────────────────────────────────

def predict_strikeouts(pitcher_features: dict) -> dict:
    """
    Score one pitcher's upcoming start.

    Args:
        pitcher_features: Dict with feature values for tonight's game.
                          Unknown/missing keys are filled with training medians.
                          Optionally include 'k_line' (float) for edge calculation.

    Returns:
        Dict with model_projection, confidence interval, std_dev,
        pitch_limit_flag, confidence score (1–5), and edge vs line.
    """
    _load()

    row = pd.DataFrame([pitcher_features])

    # Apply identical feature engineering to training
    try:
        X, _ = prepare_features(row, medians=_medians)
    except Exception:
        X = pd.DataFrame([{}])

    # Align to exact training column order; fill any gaps with training median
    for col in _feat_cols:
        if col not in X.columns:
            X[col] = _medians.get(col, 0.0)
    X = X[_feat_cols].copy()

    # Final safety fill
    for col in X.columns:
        if X[col].isna().any():
            X[col] = X[col].fillna(_medians.get(col, 0.0))

    # ── Point prediction ──────────────────────────────────────────────────────
    k_proj = float(_model.predict(X)[0])

    # ── Confidence interval via individual tree predictions ───────────────────
    # GradientBoostingRegressor.estimators_ has shape (n_estimators, 1).
    # Each estimator is a DecisionTreeRegressor — predict on residuals, not
    # directly. We use the spread across base learners as a variance proxy.
    tree_preds = np.array([
        est[0].predict(X)[0]
        for est in _model.estimators_
    ])
    std     = float(np.std(tree_preds))
    ci_low  = round(max(k_proj - 1.0 * std, 0.0), 1)
    ci_high = round(k_proj + 1.0 * std, 1)

    # ── Edge vs market line ───────────────────────────────────────────────────
    k_line = pitcher_features.get("k_line")
    edge   = round(float(k_proj) - float(k_line), 2) if k_line is not None else None

    return {
        "model_projection": round(k_proj, 1),
        "ci_low":           ci_low,
        "ci_high":          ci_high,
        "std_dev":          round(std, 2),
        "pitch_limit_flag": bool(pitcher_features.get("pitch_limit_flag", 0)),
        "confidence":       _confidence_score(pitcher_features, std),
        "edge_vs_line":     edge,
    }


def _confidence_score(features: dict, std: float) -> int:
    """
    1–5 score reflecting how much quality data backed this prediction.
    FastAPI exposes this as the confidence_level field in /api/pitchers/today.
    """
    score = 0
    if features.get("csw_pct_L15")       is not None: score += 2
    if features.get("opp_k_pct_vs_hand") is not None: score += 1
    if features.get("stuff_plus")        is not None: score += 1
    if std < 1.2:                                       score += 1
    return min(score, 5)


# ── Batch scoring ─────────────────────────────────────────────────────────────

def predict_batch(pitcher_list: list[dict]) -> list[dict]:
    """Score multiple pitchers at once (used in nightly batch jobs)."""
    return [predict_strikeouts(p) for p in pitcher_list]


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample = {
        "csw_pct_L15":         0.285,
        "swstr_pct_L15":       0.122,
        "k_pct_L15":           0.265,
        "stuff_plus":          108.0,
        "k9_L3":               9.4,
        "rest_days":           5,
        "innings_prev_start":  6.0,
        "cumulative_pitches_7d": 95,
        "pitch_limit_flag":    0,
        "implied_max_batters": 26.3,
        "opp_k_pct_vs_hand":   0.235,
        "opp_chase_pct":       0.305,
        "opp_contact_pct":     0.785,
        "opp_swstr_pct":       0.118,
        "park_factor":         99,
        "home_away":           1,
        "month":               6,
        "temperature_f":       72.0,
        "wind_speed_mph":      8.0,
        "wind_to_from":        1,
        "k_line":              6.5,
    }

    result = predict_strikeouts(sample)
    print("\nSample prediction:")
    for k, v in result.items():
        print(f"  {k:<22} {v}")
