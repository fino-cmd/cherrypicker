"""
CherryPick — Daily Picks Generator
Fetches today's probable starters from MLB Stats API, runs model predictions,
prompts for K lines from Betano, and outputs a recommendation table.

Usage:
    python daily_picks.py
    python daily_picks.py --date 2025-04-15
"""

import argparse
import csv
import sys
import warnings
from datetime import date as dt_date
from pathlib import Path

warnings.filterwarnings("ignore")   # suppress sklearn feature-name warnings

import httpx
import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from predict import predict_strikeouts

MLB_API      = "https://statsapi.mlb.com/api/v1"
TRAINING_CSV = ROOT / "data" / "training_data.csv"
TRACKER_CSV  = ROOT / "results_tracker.csv"

# ── Park factors ──────────────────────────────────────────────────────────────
PARK_FACTORS = {
    "COL": 112, "CIN": 105, "BOS": 104, "TEX": 103, "PHI": 103,
    "MIL": 102, "LAD": 100, "NYY": 100, "CHC": 100, "ATL":  99,
    "HOU":  99, "DET":  99, "TOR":  98, "SF":   98, "MIN":  98,
    "MIA":  97, "CLE":  97, "STL":  97, "SEA":  97, "SD":   96,
    "WSH":  97, "BAL":  98, "TB":   96, "KC":   98, "LAA":  97,
    "NYM":  97, "PIT":  98, "AZ":   99, "CHW": 100, "OAK":  95,
    "ATH":  95,
}

# MLB API abbreviations that differ from internal abbreviations
MLB_TO_INTERNAL = {
    "ARI": "AZ",  "CWS": "CHW", "KCR": "KC",  "SFG": "SF",
    "SDP": "SD",  "TBR": "TB",  "WSN": "WSH", "ATH": "OAK",
}

def norm_team(t: str) -> str:
    return MLB_TO_INTERNAL.get(str(t).upper(), str(t).upper())


# ── MLB Stats API ─────────────────────────────────────────────────────────────

def mlb_get(path: str, **params) -> dict:
    try:
        r = httpx.get(f"{MLB_API}{path}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        print(f"[WARNING] MLB API {path} -> {exc}")
        return {}


def fetch_starters(game_date: str) -> list[dict]:
    """
    Return one dict per probable pitcher for game_date.
    {pitcher_id, pitcher_name, team, opponent, home_away, park_factor}
    """
    data = mlb_get(
        "/schedule",
        sportId=1,
        date=game_date,
        hydrate="probablePitcher,team",
    )

    starters = []
    seen_ids = set()

    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            game_type = game.get("gameType", "R")
            if game_type not in ("R", "F", "D", "L", "W", "S"):
                continue  # skip spring training exhibition

            teams     = game.get("teams", {})
            home_abbr = norm_team(teams.get("home", {}).get("team", {}).get("abbreviation", ""))

            for side, opp_side in [("home", "away"), ("away", "home")]:
                pitcher = teams.get(side, {}).get("probablePitcher")
                if not pitcher:
                    continue
                pid = pitcher["id"]
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)

                team_abbr = norm_team(teams[side]["team"].get("abbreviation", ""))
                opp_abbr  = norm_team(teams[opp_side]["team"].get("abbreviation", ""))

                starters.append({
                    "pitcher_id":   pid,
                    "pitcher_name": pitcher["fullName"],
                    "team":         team_abbr,
                    "opponent":     opp_abbr,
                    "home_away":    1 if side == "home" else 0,
                    "park_factor":  PARK_FACTORS.get(home_abbr, 100),
                })

    return starters


# ── Training data lookups ─────────────────────────────────────────────────────

def load_training() -> pd.DataFrame:
    if not TRAINING_CSV.exists():
        print(f"[ERROR] {TRAINING_CSV} not found. Run data_pipeline.py first.")
        sys.exit(1)
    return pd.read_csv(TRAINING_CSV)


_PITCHER_FEATURE_COLS = [
    "k9_L3", "k_pct_L15", "k9_L15", "k9_L30", "k9_season",
    "innings_prev_start", "cumulative_pitches_7d", "pitch_limit_flag",
    "implied_max_batters", "csw_pct_L15", "swstr_pct_L15", "stuff_plus",
]

_OPP_STAT_COLS = [
    "opp_k_pct_vs_hand", "opp_chase_pct", "opp_contact_pct", "opp_swstr_pct",
]


def pitcher_baseline(df: pd.DataFrame, pitcher_id: int, pitcher_name: str) -> tuple[dict, str]:
    """
    Returns (feature_dict, source_description).
    Matches by pitcher_id first, then full name, then last name.
    """
    match = df[df["pitcher_id"] == pitcher_id]
    source = "by ID"

    if match.empty:
        match  = df[df["pitcher_name"].str.lower() == pitcher_name.lower()]
        source = "by name"

    if match.empty:
        last   = pitcher_name.split()[-1].lower()
        match  = df[df["pitcher_name"].str.lower().str.endswith(last)]
        source = "by last name"

    if match.empty:
        return {col: None for col in _PITCHER_FEATURE_COLS}, "NOT FOUND"

    row = match.sort_values("date").iloc[-1]
    feats = {
        col: (None if pd.isna(row.get(col)) else float(row[col]))
        for col in _PITCHER_FEATURE_COLS
    }
    return feats, f"{source} ({row['date']}, {int(row['season'])})"


def opp_stats(df: pd.DataFrame, opp_abbr: str) -> dict:
    """
    Average opponent discipline stats when opp_abbr appeared as the opponent.
    Returns empty dict if no data.
    """
    rows = df[df["opponent"] == opp_abbr]
    if rows.empty:
        return {}
    out = {}
    for col in _OPP_STAT_COLS:
        if col in rows.columns:
            val = rows[col].mean()
            out[col] = None if pd.isna(val) else float(val)
    return out


# ── Recommendation ────────────────────────────────────────────────────────────

def recommend(edge: float, confidence: int, pitch_limit: bool) -> str:
    if confidence < 3:
        return "SKIP"
    if abs(edge) < 0.8:
        return "SKIP"
    if pitch_limit:
        return "SKIP*"
    return "OVER" if edge > 0 else "UNDER"


# ── Output helpers ────────────────────────────────────────────────────────────

def print_table(rows: list[dict]) -> None:
    """Print a fixed-width recommendation table."""
    if not rows:
        return

    header = (
        f"{'Pitcher':<24} {'Team':>4}  {'Opp':>4}  "
        f"{'Model':>5}  {'Line':>5}  {'Edge':>6}  "
        f"{'CI':^13}  {'PLF':>3}  {'Conf':>4}  {'Rec':<6}"
    )
    sep = "-" * len(header)

    print()
    print(sep)
    print(header)
    print(sep)

    for r in rows:
        edge_str  = f"{r['edge']:+.2f}" if r["edge"] is not None else "  --"
        ci_str    = f"[{r['ci_low']:.1f}-{r['ci_high']:.1f}]" if r["ci_low"] is not None else "    --    "
        plf_str   = "YES" if r["pitch_limit"] else "no"
        line_str  = f"{r['k_line']:.1f}" if r["k_line"] is not None else " --"
        rec       = r.get("rec", "--")
        # Colour the recommendation with ANSI codes (TTY only)
        if sys.stdout.isatty():
            if rec == "OVER":
                rec_display = f"\033[92m{rec:<6}\033[0m"
            elif rec == "UNDER":
                rec_display = f"\033[94m{rec:<6}\033[0m"
            else:
                rec_display = f"\033[90m{rec:<6}\033[0m"
        else:
            rec_display = f"{rec:<6}"

        print(
            f"{r['pitcher']:<24} {r['team']:>4}  {r['opponent']:>4}  "
            f"{r['model_proj']:>5.1f}  {line_str:>5}  {edge_str:>6}  "
            f"{ci_str:^13}  {plf_str:>3}  {r['confidence']:>4}  {rec_display}"
        )

    print(sep)
    print("  * = SKIP due to pitch-limit risk   |  edge threshold: 0.8K  |  min confidence: 3")
    print()


def append_to_tracker(rows: list[dict], game_date: str) -> None:
    """Append all rows (including skipped lines) to results_tracker.csv."""
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
        for r in rows:
            writer.writerow([
                game_date,
                r["pitcher"],
                r["team"],
                r["opponent"],
                r["k_line"] if r["k_line"] is not None else "",
                r["model_proj"],
                f"{r['edge']:+.2f}" if r["edge"] is not None else "",
                r["ci_low"] if r["ci_low"] is not None else "",
                r["ci_high"] if r["ci_high"] is not None else "",
                "YES" if r["pitch_limit"] else "no",
                r["confidence"],
                "",   # actual_ks — filled in after the game
                "",   # result    — filled in after the game
            ])


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Daily K-prop picks")
    p.add_argument("--date", default=None,
                   help="Game date YYYY-MM-DD (default: today)")
    return p.parse_args()


def main():
    args      = parse_args()
    game_date = args.date or dt_date.today().isoformat()

    print(f"\nCherryPick  {game_date}")
    print("=" * 40)
    print("Fetching probable starters from MLB Stats API...", end=" ", flush=True)

    starters = fetch_starters(game_date)

    if not starters:
        print(f"\n[INFO] No probable pitchers found for {game_date}.")
        print("  Possible reasons: off day, spring training, or pitchers not yet posted.")
        sys.exit(0)

    print(f"{len(starters)} pitchers found.")
    print("Loading training data...", end=" ", flush=True)

    df = load_training()
    print("done.")

    # ── Score every pitcher ───────────────────────────────────────────────────
    print("\nRunning model predictions...")
    scored = []
    for s in starters:
        feats, src   = pitcher_baseline(df, s["pitcher_id"], s["pitcher_name"])
        opp          = opp_stats(df, s["opponent"])

        # Merge: pitcher feats + opponent stats + game context
        features = {**feats, **opp}
        features["home_away"]   = s["home_away"]
        features["park_factor"] = s["park_factor"]
        features["month"]       = dt_date.fromisoformat(game_date).month

        result = predict_strikeouts(features)

        scored.append({
            "pitcher":     s["pitcher_name"],
            "team":        s["team"],
            "opponent":    s["opponent"],
            "model_proj":  result["model_projection"],
            "ci_low":      result["ci_low"],
            "ci_high":     result["ci_high"],
            "pitch_limit": result["pitch_limit_flag"],
            "confidence":  result["confidence"],
            "k_line":      None,
            "edge":        None,
            "rec":         "--",
            "_src":        src,
        })

    # ── Prompt for K lines ────────────────────────────────────────────────────
    print()
    print("-" * 40)
    print("  Enter K lines from Betano (Enter = skip)")
    print("-" * 40)

    output_rows = []
    for r in scored:
        proj      = r["model_proj"]
        ci_str    = f"[{r['ci_low']:.1f}-{r['ci_high']:.1f}]"
        plf_note  = "  [PITCH LIMIT]" if r["pitch_limit"] else ""
        src_note  = f"  ({r['_src']})" if r["_src"] != "NOT FOUND" else "  [NOT IN TRAINING DATA]"

        prompt = (
            f"\n  {r['pitcher']} ({r['team']} vs {r['opponent']})\n"
            f"  Model: {proj:.1f}  CI: {ci_str}  Conf: {r['confidence']}/5"
            f"{plf_note}{src_note}\n"
            f"  K line (or Enter to skip): "
        )

        try:
            raw = input(prompt).strip()
        except EOFError:
            raw = ""

        if not raw:
            continue  # skip this pitcher entirely

        try:
            k_line = float(raw)
        except ValueError:
            print("  [Invalid number - skipping]")
            continue

        edge = round(proj - k_line, 2)
        rec  = recommend(edge, r["confidence"], r["pitch_limit"])

        output_rows.append({**r, "k_line": k_line, "edge": edge, "rec": rec})

    # ── Results ───────────────────────────────────────────────────────────────
    if not output_rows:
        print("\n[INFO] No K lines entered - nothing to display.")
        sys.exit(0)

    print_table(output_rows)
    append_to_tracker(output_rows, game_date)
    print(f"  Saved to {TRACKER_CSV.name}")
    print(f"  Fill in actual_ks + result (W/L/P) after each game.")
    print()


if __name__ == "__main__":
    main()
