"""
CherryPick — Evaluate a pitcher against a K line
Usage:
    python eval_vs_lines.py "Corbin Burnes" 5.5
    python eval_vs_lines.py "Corbin Burnes" 5.5 --opp-k-pct 0.24 --park 102 --rest 5
    python eval_vs_lines.py "Corbin Burnes" 5.5 --home --no-limit

Options:
    --opp-k-pct FLOAT     Opposing team K% vs pitcher's hand (e.g. 0.24)
    --park INT            Park factor (default: 100)
    --rest INT            Days rest (default: 5)
    --home                Pitcher is at home (default: away)
    --no-limit            Override pitch_limit_flag to 0
    --month INT           Month 1-12 (default: current month)
    --temp FLOAT          Temperature °F (default: 72)
    --wind FLOAT          Wind speed mph (default: 5)
    --innings FLOAT       Innings pitched in previous start (default: pitcher median)
    --log                 Append this prediction to results_tracker.csv
"""

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# ── Resolve project root so this runs from any cwd ───────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from predict import predict_strikeouts

TRAINING_CSV = ROOT / "data" / "training_data.csv"
TRACKER_CSV  = ROOT / "results_tracker.csv"

# ── Recommendation thresholds ─────────────────────────────────────────────────
MIN_EDGE       = 0.8   # K below this → SKIP
MIN_CONFIDENCE = 3     # confidence score below this → SKIP


def load_pitcher_baseline(name: str) -> dict:
    """
    Pull the most recent row for this pitcher from training data.
    Returns a dict of feature values to use as the prediction baseline.
    Raises SystemExit with a helpful message if not found.
    """
    df = pd.read_csv(TRAINING_CSV)
    df["pitcher_name_lower"] = df["pitcher_name"].str.lower()
    query = name.strip().lower()

    matches = df[df["pitcher_name_lower"] == query]
    if matches.empty:
        # Fuzzy fallback: substring match
        matches = df[df["pitcher_name_lower"].str.contains(query, na=False)]

    if matches.empty:
        close = df["pitcher_name"].drop_duplicates().sort_values().tolist()
        suggestions = [p for p in close if query.split()[0] in p.lower()][:5]
        hint = "\n  ".join(suggestions) if suggestions else "(no close matches)"
        print(f"\n[ERROR] Pitcher '{name}' not found in training data.")
        print(f"  Possible matches:\n  {hint}")
        sys.exit(1)

    if len(matches["pitcher_name"].unique()) > 1:
        names = matches["pitcher_name"].unique().tolist()
        print(f"[WARNING] Multiple pitchers matched: {names}")
        print(f"  Using: {names[0]}")
        matches = matches[matches["pitcher_name"] == names[0]]

    row = matches.sort_values("date").iloc[-1]
    print(f"\n  Pitcher found : {row['pitcher_name']}")
    print(f"  Baseline from : {row['date']}  ({row['season']} season)")

    feature_cols = [
        "k9_L3", "k_pct_L15", "k9_L15", "k9_L30", "k9_season",
        "innings_prev_start", "cumulative_pitches_7d", "pitch_limit_flag",
        "implied_max_batters", "csw_pct_L15", "swstr_pct_L15",
        "stuff_plus", "opp_k_pct_vs_hand", "opp_chase_pct",
        "opp_contact_pct", "opp_swstr_pct", "park_factor",
        "home_away", "rest_days", "month", "temperature_f",
        "wind_speed_mph", "wind_to_from",
    ]
    return {col: (None if pd.isna(row.get(col)) else row.get(col))
            for col in feature_cols}


def recommend(edge: float, confidence: int, pitch_limit: bool) -> str:
    if confidence < MIN_CONFIDENCE:
        return "SKIP  (low confidence)"
    if abs(edge) < MIN_EDGE:
        return "SKIP  (edge too thin)"
    if pitch_limit:
        return "SKIP  (pitch limit risk)"
    return "OVER" if edge > 0 else "UNDER"


def print_result(pitcher: str, k_line: float, result: dict) -> None:
    proj       = result["model_projection"]
    ci_low     = result["ci_low"]
    ci_high    = result["ci_high"]
    edge       = result["edge_vs_line"]
    confidence = result["confidence"]
    plf        = result["pitch_limit_flag"]
    rec        = recommend(edge, confidence, plf)

    edge_sign = "+" if edge >= 0 else ""

    print()
    print("=" * 48)
    print(f"  {pitcher}")
    print("=" * 48)
    print(f"  K line            : {k_line}")
    print(f"  Model projection  : {proj}")
    print(f"  CI (+/-1 std)     : [{ci_low} - {ci_high}]")
    print(f"  Edge              : {edge_sign}{edge}")
    print(f"  Confidence        : {confidence} / 5")
    print(f"  Pitch limit flag  : {'YES' if plf else 'no'}")
    print("-" * 48)
    print(f"  RECOMMENDATION    : {rec}")
    print("=" * 48)
    print()


def append_to_tracker(pitcher: str, k_line: float, result: dict) -> None:
    proj  = result["model_projection"]
    edge  = result["edge_vs_line"]
    rec   = recommend(edge, result["confidence"], result["pitch_limit_flag"])
    today = date.today().isoformat()

    write_header = not TRACKER_CSV.exists() or TRACKER_CSV.stat().st_size == 0
    with open(TRACKER_CSV, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow([
                "date", "pitcher", "team", "opponent",
                "k_line", "model_proj", "edge",
                "ci_low", "ci_high", "pitch_limit_flag", "confidence",
                "actual_ks", "result",
            ])
        writer.writerow([
            today, pitcher, "", "",
            k_line, proj,
            f"{edge:+.2f}" if edge is not None else "",
            result["ci_low"], result["ci_high"],
            "YES" if result["pitch_limit_flag"] else "no",
            result["confidence"],
            "", "",
        ])

    print(f"  Logged to {TRACKER_CSV.name}  (fill in actual_ks + result after the game)")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Evaluate a pitcher K prop vs model projection")
    p.add_argument("pitcher", help="Pitcher full name (e.g. 'Corbin Burnes')")
    p.add_argument("k_line",  type=float, help="Book K line (e.g. 5.5)")
    p.add_argument("--opp-k-pct",  type=float, dest="opp_k_pct",
                   help="Opposing lineup K%% vs pitcher's hand (e.g. 0.24)")
    p.add_argument("--park",   type=int,   default=None, help="Park factor (default: 100)")
    p.add_argument("--rest",   type=int,   default=None, help="Days rest (default: 5)")
    p.add_argument("--home",   action="store_true",      help="Pitcher is at home")
    p.add_argument("--no-limit", action="store_true",    help="Force pitch_limit_flag=0")
    p.add_argument("--month",  type=int,   default=None, help="Month 1-12 (default: today)")
    p.add_argument("--temp",   type=float, default=None, help="Temperature °F")
    p.add_argument("--wind",   type=float, default=None, help="Wind speed mph")
    p.add_argument("--innings", type=float, default=None,
                   help="IP in previous start (default: pitcher median from training)")
    p.add_argument("--log",    action="store_true",
                   help="Append prediction to results_tracker.csv")
    return p.parse_args()


def main():
    args = parse_args()

    features = load_pitcher_baseline(args.pitcher)

    # Apply CLI overrides
    features["k_line"] = args.k_line

    if args.opp_k_pct is not None:
        features["opp_k_pct_vs_hand"] = args.opp_k_pct

    if args.park is not None:
        features["park_factor"] = args.park

    if args.rest is not None:
        features["rest_days"] = args.rest

    if args.home:
        features["home_away"] = 1
    else:
        features["home_away"] = features.get("home_away") or 0

    if args.no_limit:
        features["pitch_limit_flag"] = 0

    if args.month is not None:
        features["month"] = args.month
    else:
        features["month"] = date.today().month

    if args.temp is not None:
        features["temperature_f"] = args.temp

    if args.wind is not None:
        features["wind_speed_mph"] = args.wind

    if args.innings is not None:
        features["innings_prev_start"] = args.innings

    result = predict_strikeouts(features)
    print_result(args.pitcher, args.k_line, result)

    if args.log:
        append_to_tracker(args.pitcher, args.k_line, result)


if __name__ == "__main__":
    main()
