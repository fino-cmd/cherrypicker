"""
CherryPick — Model Training
Trains a Gradient Boosting regressor on all available historical data,
using the most recent full season as holdout evaluation.

Run:    python train_model.py
Output: models/k_model.pkl
        models/feature_cols.pkl
        models/feature_medians.pkl
        models/eval_summary.json
"""

import json
import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import TimeSeriesSplit

from features import prepare_features

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_PATH  = Path("data/training_data.csv")
MODELS_DIR = Path("models")
MODELS_DIR.mkdir(exist_ok=True)

# ── Tier 1 #3 — Recency weighting ──────────────────────────────────────────────
# Older starts get exponentially less weight than recent ones. The half-life is
# expressed in DAYS — at HALF_LIFE_DAYS days old, the weight drops by 50%.
#
# Why: MLB strikes-out trends drift season-over-season (rule changes, ball spec,
# pitch-clock effects). 2022 data is statistically different from 2026 data.
# Without recency weighting, the model treats them equally, blunting its read
# on current-regime pitching. With a half-life of 365 days:
#   • Same season as today → weight = 1.0
#   • 1 year old           → weight ≈ 0.50
#   • 2 years old          → weight ≈ 0.25
#   • 3 years old          → weight ≈ 0.125
# This reweights ~14k rows toward 2025-2026 without dropping older data
# (which still has signal — pitcher career features, opponent profiles, etc.).
RECENCY_HALF_LIFE_DAYS = 365


def _compute_recency_weights(dates: pd.Series, ref_date: pd.Timestamp) -> np.ndarray:
    """Exponential-decay weights based on age (days from ref_date).
    Returns weights normalized so the most recent row has weight 1.0."""
    age_days = (ref_date - dates).dt.days.clip(lower=0).values.astype(float)
    decay = np.log(2) / float(RECENCY_HALF_LIFE_DAYS)  # half-life in days
    weights = np.exp(-decay * age_days)
    return weights


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_data() -> pd.DataFrame:
    if not DATA_PATH.exists():
        raise FileNotFoundError(
            f"{DATA_PATH} not found.\nRun:  python data_pipeline.py"
        )
    df = pd.read_csv(DATA_PATH)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    log.info("Loaded %d rows from %s", len(df), DATA_PATH)
    return df


def _new_model() -> GradientBoostingRegressor:
    return GradientBoostingRegressor(
        n_estimators     = 400,
        learning_rate    = 0.04,
        max_depth        = 4,
        min_samples_leaf = 20,   # prevents overfitting on small subgroups
        subsample        = 0.8,  # stochastic GBM → reduces variance
        max_features     = 0.7,
        random_state     = 42,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def train():
    df = load_data()

    # ── Time-aware split: NEVER random-split sports data ──────────────────────
    # Holdout = most recent COMPLETE season (last season with >= 300 starts).
    # Current partial season (2026) goes into training — more data = better.
    seasons_available = sorted(df["season"].unique())
    complete_seasons  = [s for s in seasons_available
                         if len(df[df["season"] == s]) >= 300]
    current_season    = max(seasons_available)

    if len(complete_seasons) >= 2:
        holdout_season = complete_seasons[-1]
        # If current season is partial, include it in training
        train_seasons  = [s for s in seasons_available if s != holdout_season]
    else:
        holdout_season = seasons_available[-1]
        train_seasons  = seasons_available[:-1]

    train_df = df[df["season"].isin(train_seasons)].copy()
    test_df  = df[df["season"] == holdout_season].copy()

    log.info("Train: %d rows (seasons: %s) | Test: %d rows (%d)",
             len(train_df), train_seasons, len(test_df), holdout_season)

    if len(train_df) < 500:
        log.warning("Very small training set (%d rows) — check data pipeline", len(train_df))

    X_train, feat_cols = prepare_features(train_df)
    y_train = train_df["actual_strikeouts"].values

    # Recency weights — newest game in train_df is the reference.
    ref_date = train_df["date"].max()
    sample_weights = _compute_recency_weights(train_df["date"], ref_date)
    log.info("Recency weights (half-life %dd): newest=%.3f, oldest=%.3f, mean=%.3f",
             RECENCY_HALF_LIFE_DAYS,
             float(sample_weights.max()), float(sample_weights.min()), float(sample_weights.mean()))

    # Save training-set medians for use at prediction time (avoids leakage)
    train_medians = X_train.median().to_dict()

    # Prepare test features using training medians (no leakage)
    X_test, _ = prepare_features(test_df, medians=train_medians)
    # Align columns in case test set is missing any feature
    for col in feat_cols:
        if col not in X_test.columns:
            X_test[col] = train_medians.get(col, 0.0)
    X_test = X_test[feat_cols]

    y_test = test_df["actual_strikeouts"].values

    print(f"\n{'=' * 60}")
    print(f"  Features ({len(feat_cols)}): {feat_cols}")
    print(f"{'=' * 60}\n")

    # ── 4-fold time-series cross-validation on training set ───────────────────
    # Each fold's validation set is always chronologically after its train set.
    print(f"Time-Series Cross-Validation (4 folds) on training seasons {train_seasons}:")
    print("-" * 45)

    tscv    = TimeSeriesSplit(n_splits=4)
    cv_maes = []

    for fold, (tr_idx, val_idx) in enumerate(tscv.split(X_train), 1):
        X_tr,  X_val = X_train.iloc[tr_idx],  X_train.iloc[val_idx]
        y_tr,  y_val = y_train[tr_idx],        y_train[val_idx]
        w_tr         = sample_weights[tr_idx]

        m = _new_model()
        m.fit(X_tr, y_tr, sample_weight=w_tr)
        preds = m.predict(X_val)
        mae   = mean_absolute_error(y_val, preds)
        cv_maes.append(mae)
        print(f"  Fold {fold}  MAE: {mae:.3f} K's   (val n={len(y_val):,})")

    mean_cv_mae = float(np.mean(cv_maes))
    print("-" * 45)
    print(f"  Mean CV MAE: {mean_cv_mae:.3f} K's\n")

    # ── Final model: train on all 2022–2023 data with recency weights ─────────
    log.info("Training final model on full training set (seasons: %s)...", train_seasons)
    model = _new_model()
    model.fit(X_train, y_train, sample_weight=sample_weights)

    # ── 2024 holdout evaluation ───────────────────────────────────────────────
    test_preds = model.predict(X_test)
    test_mae   = float(mean_absolute_error(y_test, test_preds))

    print(f"{'=' * 60}")
    print(f"  2024 Holdout MAE:  {test_mae:.3f} K's")

    if test_mae > 1.2:
        print("  WARNING: model underfit -- check data pipeline")
    elif test_mae < 0.95:
        print("  Excellent  (MAE < 0.95 K's)")
    elif test_mae < 1.1:
        print("  Good       (MAE < 1.10 K's)")

    cv_gap = test_mae - mean_cv_mae
    if cv_gap > 0.3:
        print(f"  WARNING: possible data leakage -- CV/holdout gap = {cv_gap:.3f}")
    print(f"{'=' * 60}\n")

    # ── Over/under accuracy (what actually matters for betting) ──────────────
    result_df = test_df[["date", "pitcher_name", "k9_season",
                          "implied_max_batters", "actual_strikeouts"]].copy()
    result_df["predicted"] = test_preds

    # Approximate market K-line: season K/9 scaled to expected innings
    if "k9_season" in result_df.columns and "implied_max_batters" in result_df.columns:
        result_df["approx_k_line"] = (
            result_df["k9_season"] * result_df["implied_max_batters"] / 9
        ).clip(2.0, 12.0)

        has_line  = result_df.dropna(subset=["approx_k_line"])
        pred_over = has_line["predicted"]         > has_line["approx_k_line"]
        act_over  = has_line["actual_strikeouts"]  > has_line["approx_k_line"]
        hit_rate  = float((pred_over == act_over).mean())

        print(f"  Over/Under hit rate (approx line):  {hit_rate:.1%}   (n={len(has_line):,})")
        if hit_rate >= 0.57:
            print("  Hit rate above 57% target")
        else:
            print(f"  Hit rate below 57% target -- model may need more features")
        print()

    # ── Feature importance ────────────────────────────────────────────────────
    importances = pd.Series(model.feature_importances_, index=feat_cols)
    importances = importances.sort_values(ascending=False)

    print("Top 15 features by importance:")
    print("-" * 40)
    for feat, imp in importances.head(15).items():
        bar = "#" * int(imp * 400)
        print(f"  {feat:<28} {imp:.4f}  {bar}")
    print()
    print(f"  Top 3: {importances.index[:3].tolist()}")

    # ── Save artefacts ────────────────────────────────────────────────────────
    joblib.dump(model,         MODELS_DIR / "k_model.pkl")
    joblib.dump(feat_cols,     MODELS_DIR / "feature_cols.pkl")
    joblib.dump(train_medians, MODELS_DIR / "feature_medians.pkl")

    summary = {
        "cv_mae":       round(mean_cv_mae, 4),
        "holdout_mae":  round(test_mae, 4),
        "train_rows":   int(len(train_df)),
        "test_rows":    int(len(test_df)),
        "n_features":   len(feat_cols),
        "feature_cols": feat_cols,
        "top_features": importances.index[:5].tolist(),
        "recency_half_life_days": RECENCY_HALF_LIFE_DAYS,
        "weight_min":   float(sample_weights.min()),
        "weight_max":   float(sample_weights.max()),
        "weight_mean":  float(sample_weights.mean()),
    }
    (MODELS_DIR / "eval_summary.json").write_text(json.dumps(summary, indent=2))

    print(f"\n  OK  models/k_model.pkl")
    print(f"  OK  models/feature_cols.pkl")
    print(f"  OK  models/feature_medians.pkl")
    print(f"  OK  models/eval_summary.json")

    return model, feat_cols, summary


if __name__ == "__main__":
    train()
