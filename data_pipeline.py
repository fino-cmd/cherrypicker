"""
CherryPick — Data Pipeline
Collects 3 seasons (2022–2024) of pitcher game logs and assembles a training
dataset for the strikeout prediction model.

Run:    python data_pipeline.py
Output: data/training_data.csv  (~15k–25k rows, one per pitcher-start)

Data sources (no FanGraphs — blocked with 403):
  - Baseball Savant custom leaderboard: CSW%, WhiffRate%, K%
  - MLB Stats API /stats: K/9, IP, GS
  - MLB Stats API /teams/stats: team batting K%
  - MLB Stats API /people/{id}/stats: per-game logs

Safeguards:
  - tqdm progress bar on the per-pitcher loop
  - Checkpoint system: resumes from data/checkpoint.csv if interrupted
  - 0.5s rate limit between API calls
  - Detailed summary printed at exit
"""

import io
import logging
import time
from datetime import timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SEASONS             = [2022, 2023, 2024]
MLB_API             = "https://statsapi.mlb.com/api/v1"
DATA_DIR            = Path("data")
CHECKPOINT_PATH     = DATA_DIR / "checkpoint.csv"
CHECKPOINT_INTERVAL = 100          # flush to disk every N new rows

DATA_DIR.mkdir(exist_ok=True)

# ── Park run factors (100 = league avg; higher = hitter-friendly) ─────────────
PARK_FACTORS = {
    "COL": 112, "CIN": 105, "BOS": 104, "TEX": 103, "PHI": 103,
    "MIL": 102, "LAD": 100, "NYY": 100, "CHC": 100, "ATL":  99,
    "HOU":  99, "DET":  99, "TOR":  98, "SF":   98, "MIN":  98,
    "MIA":  97, "CLE":  97, "STL":  97, "SEA":  97, "SD":   96,
    "WSH":  97, "BAL":  98, "TB":   96, "KC":   98, "LAA":  97,
    "NYM":  97, "PIT":  98, "AZ":   99, "CHW": 100, "OAK":  95,
    "ATH":  95,
}

MLB_TO_PARK = {
    "ARI": "AZ", "CWS": "CHW", "KCR": "KC", "SFG": "SF",
    "SDP": "SD", "TBR": "TB",  "WSN": "WSH",
}

def norm_team(t: str) -> str:
    return MLB_TO_PARK.get(str(t).upper(), str(t).upper())


# ── MLB Stats API ─────────────────────────────────────────────────────────────

def mlb_get(path: str, **params) -> dict:
    """GET from MLB Stats API with 3 retries and back-off."""
    for attempt in range(3):
        try:
            r = httpx.get(f"{MLB_API}{path}", params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            if attempt == 2:
                log.warning("MLB API %s → %s", path, exc)
                return {}
            time.sleep(2 ** attempt)
    return {}


# ── Innings pitched parser ────────────────────────────────────────────────────
# MLB API returns "6.1" meaning 6⅓ innings (not 6.1 decimal)

def parse_ip(val) -> float:
    try:
        s = str(val).strip()
        if "." in s:
            whole, thirds = s.split(".", 1)
            # MLB API "6.1" means 6+1/3 innings (thirds: 0, 1, or 2 only).
            # If the fractional part has more than 1 digit, the API returned a
            # decimal float like "5.333..." — parse it directly as decimal IP.
            if len(thirds) > 1:
                return float(s)
            return int(whole) + int(thirds) / 3
        return float(s)
    except Exception:
        return 0.0


# ── Stuff+ (Baseball Savant pitch arsenals) ───────────────────────────────────

def load_stuff_plus(year: int) -> pd.DataFrame:
    """
    Fetch strikeout-skill proxy per pitcher using pybaseball.statcast_pitcher_arsenal_stats().

    Uses k_percent (K% per pitch type, weighted by pitch_usage) because it directly
    measures strikeout propensity — more predictive of game K totals than run_value.
    Normalized to Stuff+ scale (mean=100, std=15) for feature compatibility.

    Returns player_id (MLBAM), stuff_plus.
    """
    log.info("  Stuff+ proxy (arsenal k_percent) %d", year)
    try:
        import pybaseball as pb
        pb.cache.enable()
        raw = pb.statcast_pitcher_arsenal_stats(year, minPA=1)

        id_col    = next((c for c in raw.columns
                          if c.lower() in ("pitcher_id", "player_id", "pitcher")), None)
        k_col     = next((c for c in raw.columns
                          if c.lower() == "k_percent"), None)
        usage_col = next((c for c in raw.columns
                          if c.lower() in ("pitch_usage", "pitches", "count")), None)

        if not id_col or not k_col:
            log.warning("  Arsenal: id=%s k_pct=%s — skipping", id_col, k_col)
            return pd.DataFrame(columns=["player_id", "stuff_plus"])

        raw = raw.rename(columns={id_col: "player_id"})
        raw["player_id"] = pd.to_numeric(raw["player_id"], errors="coerce").astype("Int64")
        raw[k_col]       = pd.to_numeric(raw[k_col],       errors="coerce")
        if usage_col:
            raw[usage_col] = pd.to_numeric(raw[usage_col], errors="coerce").fillna(1)
        raw = raw.dropna(subset=["player_id", k_col])

        # Weighted mean K% per pitcher across pitch types (weights = pitch_usage)
        if usage_col:
            agg = (
                raw.groupby("player_id")
                .apply(lambda g: np.average(g[k_col], weights=g[usage_col].fillna(1)),
                       include_groups=False)
                .reset_index()
            )
            agg.columns = ["player_id", "k_pct_arsenal"]
        else:
            agg = raw.groupby("player_id", as_index=False)[k_col].mean()
            agg = agg.rename(columns={k_col: "k_pct_arsenal"})

        # Normalize to Stuff+ scale: mean=100, std=15
        kp  = agg["k_pct_arsenal"]
        mu  = kp.mean()
        std = kp.std()
        if std > 0:
            agg["stuff_plus"] = ((kp - mu) / std) * 15 + 100
        else:
            agg["stuff_plus"] = 100.0

        n_ok   = agg["stuff_plus"].notna().sum()
        sample = agg["stuff_plus"].dropna().head(3).round(1).tolist()
        log.info("    Stuff+ (k_pct): %d pitchers, %d non-null, sample: %s",
                 len(agg), n_ok, sample)
        return agg[["player_id", "stuff_plus"]]

    except Exception as exc:
        log.warning("  load_stuff_plus(%d): %s", year, exc)
        return pd.DataFrame(columns=["player_id", "stuff_plus"])


# ── Team batting discipline (Baseball Savant batter leaderboard) ───────────────

def _discipline_from_pybaseball(year: int) -> dict:
    """
    Primary source: FanGraphs team batting via pybaseball.
    Columns we want: O-Swing%, Contact%, SwStr%
    """
    try:
        import pybaseball as pb
        pb.cache.enable()
        tb = pb.team_batting(year, year)
        col_map = {
            "O-Swing%": "opp_chase_pct",
            "Contact%":  "opp_contact_pct",
            "SwStr%":    "opp_swstr_pct",
        }
        available = {k: v for k, v in col_map.items() if k in tb.columns}
        if not available:
            log.warning("  pybaseball.team_batting missing discipline cols: %s", list(tb.columns))
            return {}
        team_col = next((c for c in tb.columns if c.lower() in ("team", "teamname", "tm")), None)
        if not team_col:
            log.warning("  pybaseball.team_batting: no team column")
            return {}

        out: dict = {}
        for _, row in tb.iterrows():
            abbr = norm_team(str(row[team_col]))
            entry: dict = {}
            for fg_col, out_key in available.items():
                val = row[fg_col]
                if pd.notna(val):
                    # FanGraphs returns as percent (e.g. 30.2) not decimal
                    entry[out_key] = float(val) / 100.0 if float(val) > 1.0 else float(val)
                else:
                    entry[out_key] = np.nan
            out[abbr] = entry
        log.info("    pybaseball team batting: %d teams, cols: %s",
                 len(out), list(available.values()))
        return out
    except Exception as exc:
        log.warning("  _discipline_from_pybaseball(%d): %s", year, exc)
        return {}


def _discipline_from_savant_roster(year: int) -> dict:
    """
    Fallback: fetch individual batter Statcast data, join with team rosters
    from the MLB Stats API, then aggregate by team.
    """
    log.info("  Savant+roster discipline fallback %d", year)

    # Step 1: individual batter stats from Savant custom leaderboard
    url = (
        "https://baseballsavant.mlb.com/leaderboard/custom"
        f"?year={year}&type=batter&filter=&sort=3&sortDir=DESC"
        "&min=100&selections=o_swing_percent,contact_percent,whiff_percent,pa&csv=true"
    )
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        batter_df = pd.read_csv(io.StringIO(r.text))
        time.sleep(0.5)
        log.info("    Savant batters: %d rows, cols: %s",
                 len(batter_df), list(batter_df.columns[:8]))
    except Exception as exc:
        log.error("  Savant batter fetch failed: %s", exc)
        return {}

    for col in ["o_swing_percent", "contact_percent", "whiff_percent", "pa"]:
        if col in batter_df.columns:
            batter_df[col] = pd.to_numeric(batter_df[col], errors="coerce")

    batter_df["player_id"] = pd.to_numeric(
        batter_df.get("player_id", pd.Series()), errors="coerce"
    ).astype("Int64")
    batter_df = batter_df.dropna(subset=["player_id"])

    # Step 2: get team rosters from MLB Stats API
    abbr_map = load_team_abbr(year)   # {team_id: abbr}
    time.sleep(0.3)

    # Fetch all batters' team affiliations via MLB Stats API season hitting stats
    mlb_data = mlb_get("/stats", stats="season", group="hitting", gameType="R",
                       season=year, playerPool="All", limit=3000,
                       sortStat="plateAppearances")
    player_team: dict = {}
    for split in mlb_data.get("stats", [{}])[0].get("splits", []):
        pid  = split.get("player", {}).get("id")
        tid  = split.get("team", {}).get("id")
        abbr = abbr_map.get(tid, "")
        if pid and abbr:
            player_team[int(pid)] = norm_team(abbr)
    log.info("    Team map: %d players", len(player_team))
    time.sleep(0.3)

    # Step 3: join and aggregate
    batter_df["team"] = batter_df["player_id"].map(
        lambda x: player_team.get(int(x), None) if pd.notna(x) else None
    )
    batter_df = batter_df.dropna(subset=["team"])
    log.info("    Batters after team-join: %d (of %d)", len(batter_df), len(batter_df))

    if batter_df.empty:
        log.warning("  No batters matched team roster — discipline lookup failed")
        return {}

    stat_map = {
        "o_swing_percent": "opp_chase_pct",
        "contact_percent":  "opp_contact_pct",
        "whiff_percent":    "opp_swstr_pct",
    }
    present = {raw: out_name for raw, out_name in stat_map.items() if raw in batter_df.columns}
    if not present:
        return {}

    # Simple mean per team (weighted PA adds complexity without meaningful gain)
    stat_cols = list(present.keys())
    team_df = batter_df.groupby("team")[stat_cols].mean().reset_index()

    log.info("    Teams after groupby: %d, sample keys: %s",
             len(team_df), team_df["team"].head(5).tolist())

    out: dict = {}
    for _, row in team_df.iterrows():
        abbr = str(row["team"])
        entry: dict = {}
        for raw, out_key in present.items():
            val = row.get(raw, np.nan)
            entry[out_key] = float(val) / 100.0 if pd.notna(val) else np.nan
        out[abbr] = entry

    log.info("    Roster-join discipline: %d teams, cols: %s",
             len(out), list(present.values()))
    return out


def load_team_batting_discipline(year: int) -> dict:
    """
    Team-level batting discipline: O-Swing%, Contact%, SwStr%.
    Tries pybaseball.team_batting() first; falls back to Savant+roster join.
    Returns {norm_team_abbr: {opp_chase_pct, opp_contact_pct, opp_swstr_pct}}.
    Values in decimal form (0–1).
    """
    log.info("  Team batting discipline %d", year)

    result = _discipline_from_pybaseball(year)
    if len(result) >= 25:   # 30 MLB teams; accept if most are populated
        return result

    log.info("  pybaseball returned only %d teams — trying Savant+roster fallback", len(result))
    fallback = _discipline_from_savant_roster(year)
    if fallback:
        # Merge: fallback fills gaps left by pybaseball
        for team, stats in fallback.items():
            if team not in result:
                result[team] = stats
            else:
                for k, v in stats.items():
                    if pd.isna(result[team].get(k, np.nan)):
                        result[team][k] = v
    return result


# ── Pitcher season stats (Baseball Savant + MLB Stats API) ────────────────────

def load_savant_pitching(year: int) -> pd.DataFrame:
    """
    Fetch pitcher season stats from Baseball Savant custom leaderboard.
    Returns player_id (MLBAM), csw_pct, swstr_pct, k_pct_season (all as decimals).
    """
    log.info("  Baseball Savant pitching %d", year)
    url = (
        "https://baseballsavant.mlb.com/leaderboard/custom"
        f"?year={year}&type=pitcher&filter=&sort=3&sortDir=DESC"
        "&min=1&selections=k_percent,bb_percent,whiff_percent,csw_rate_percent,ip&csv=true"
    )
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        time.sleep(0.5)

        # Rename and normalise
        rename = {
            "player_id":        "player_id",
            "csw_rate_percent": "csw_pct",
            "whiff_percent":    "swstr_pct",
            "k_percent":        "k_pct_season",
        }
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

        for col in ["csw_pct", "swstr_pct", "k_pct_season"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce") / 100.0

        df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce").astype("Int64")
        df = df.dropna(subset=["player_id"])
        log.info("    Savant: %d pitchers", len(df))
        return df[["player_id", "csw_pct", "swstr_pct", "k_pct_season"]]
    except Exception as exc:
        log.error("load_savant_pitching(%d): %s", year, exc)
        return pd.DataFrame(columns=["player_id", "csw_pct", "swstr_pct", "k_pct_season"])


def load_mlb_pitching_season(year: int) -> pd.DataFrame:
    """
    Fetch pitcher season totals from MLB Stats API.
    Returns player_id (MLBAM), name, k9, ip, gs.
    """
    log.info("  MLB API pitching season %d", year)
    data = mlb_get("/stats", stats="season", group="pitching", gameType="R",
                   season=year, playerPool="All", limit=2000,
                   sortStat="inningsPitched")
    rows = []
    for split in data.get("stats", [{}])[0].get("splits", []):
        p = split.get("player", {})
        s = split.get("stat", {})
        k9_raw = s.get("strikeoutsPer9Inn", "0") or "0"
        try:
            k9 = float(k9_raw)
        except (ValueError, TypeError):
            k9 = 0.0
        rows.append({
            "player_id": int(p["id"]) if p.get("id") else None,
            "name":      p.get("fullName", ""),
            "k9":        k9,
            "ip":        parse_ip(s.get("inningsPitched", "0")),
            "gs":        int(s.get("gamesStarted", 0) or 0),
        })
    df = pd.DataFrame(rows).dropna(subset=["player_id"])
    df["player_id"] = df["player_id"].astype("Int64")
    log.info("    MLB API: %d pitcher entries", len(df))
    time.sleep(0.5)
    return df


def load_pitcher_stats(year: int) -> pd.DataFrame:
    """
    Merge Baseball Savant + MLB Stats API into one pitcher table.
    Returns one row per pitcher with: player_id, name, csw_pct, swstr_pct,
    k_pct_season, k9, ip, gs, stuff_plus.
    """
    mlb    = load_mlb_pitching_season(year)
    savant = load_savant_pitching(year)
    stuff  = load_stuff_plus(year)

    merged = mlb.merge(savant, on="player_id", how="left")
    merged = merged.merge(stuff, on="player_id", how="left")
    return merged


# ── Team batting lookup (MLB Stats API) ───────────────────────────────────────

def load_team_abbr(year: int) -> dict:
    """Returns {mlbam_team_id: abbreviation_str}."""
    data = mlb_get("/teams", sportId=1, season=year)
    return {t["id"]: t.get("abbreviation", "") for t in data.get("teams", [])}


def load_team_batting_mlb(year: int) -> dict:
    """
    Returns {team_abbr: {opp_k_pct_vs_hand, opp_chase_pct,
                          opp_contact_pct, opp_swstr_pct}}.
    K% from MLB Stats API; chase/contact/swstr from Savant batter leaderboard.
    """
    log.info("  MLB API team batting %d", year)
    abbr_map = load_team_abbr(year)
    time.sleep(0.5)

    data = mlb_get("/teams/stats", stats="season", group="hitting",
                   gameType="R", season=year, sportId=1)
    out: dict = {}
    for split in data.get("stats", [{}])[0].get("splits", []):
        team = split.get("team", {})
        tid  = team.get("id")
        abbr = abbr_map.get(tid, "")
        if not abbr:
            continue
        s  = split.get("stat", {})
        so = int(s.get("strikeOuts",       0) or 0)
        pa = int(s.get("plateAppearances", 0) or 0)
        out[norm_team(abbr)] = {
            "opp_k_pct_vs_hand": so / pa if pa > 0 else np.nan,
            "opp_chase_pct":     np.nan,
            "opp_contact_pct":   np.nan,
            "opp_swstr_pct":     np.nan,
        }
    log.info("    Team batting K-pct: %d teams", len(out))
    time.sleep(0.5)

    # Merge in discipline stats from Savant
    discipline = load_team_batting_discipline(year)
    for abbr, disc in discipline.items():
        if abbr in out:
            out[abbr].update(disc)
        else:
            out[abbr] = {
                "opp_k_pct_vs_hand": np.nan,
                **disc,
            }

    return out


# ── Per-game logs (MLB Stats API) ─────────────────────────────────────────────

def get_game_logs(mlb_id: int, year: int,
                  abbr_map: dict | None = None) -> pd.DataFrame:
    """
    Fetch one pitcher's game-by-game log for a season.

    abbr_map: {team_id: abbreviation} from load_team_abbr(year).
    The game-log API returns team.id but NOT team.abbreviation, so abbr_map
    is required for correct team/opponent fields.
    """
    data   = mlb_get(f"/people/{mlb_id}/stats",
                     stats="gameLog", group="pitching", season=year)
    splits = data.get("stats", [{}])[0].get("splits", [])
    if not splits:
        return pd.DataFrame()

    rows = []
    for s in splits:
        stat     = s.get("stat", {})
        team_obj = s.get("team",     {})
        opp_obj  = s.get("opponent", {})

        # Prefer abbr_map lookup (game logs lack 'abbreviation' field)
        if abbr_map:
            team_abbr = abbr_map.get(team_obj.get("id"), "")
            opp_abbr  = abbr_map.get(opp_obj.get("id"),  "")
        else:
            team_abbr = team_obj.get("abbreviation", "")
            opp_abbr  = opp_obj.get("abbreviation",  "")

        rows.append({
            "date":      s.get("date", "")[:10],
            "game_pk":   s.get("game", {}).get("gamePk"),
            "team":      norm_team(team_abbr),
            "opponent":  norm_team(opp_abbr),
            "home_away": 1 if s.get("isHome", False) else 0,
            "k":         int(stat.get("strikeOuts",      0) or 0),
            "ip":        parse_ip(stat.get("inningsPitched", "0")),
            "bb":        int(stat.get("baseOnBalls",     0) or 0),
            "hits":      int(stat.get("hits",            0) or 0),
            "pitches":   int(stat.get("numberOfPitches", 0) or 0),
        })

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df[df["ip"] >= 2.0].reset_index(drop=True)


# ── Rolling pitcher features ──────────────────────────────────────────────────

def add_rolling_features(starts: pd.DataFrame) -> pd.DataFrame:
    """
    For each row i, compute features using only rows 0..i-1 (no leakage).
    Returns starts with rolling columns appended.
    """
    records = []
    for i in range(len(starts)):
        cur   = starts.iloc[i]
        prev  = starts.iloc[:i]

        rest  = int((cur["date"] - starts.iloc[i - 1]["date"]).days) if i > 0 else 5

        c15   = cur["date"] - timedelta(days=15)
        c30   = cur["date"] - timedelta(days=30)
        c7    = cur["date"] - timedelta(days=7)

        l15   = prev[prev["date"] >= c15]
        l30   = prev[prev["date"] >= c30]
        l3    = prev.tail(3)
        l7    = prev[prev["date"] >= c7]
        prev1 = prev.tail(1)

        def k9(d):
            ip = d["ip"].sum()
            return float(d["k"].sum() / ip * 9) if ip > 0 else np.nan

        def k_pct(d):
            ks = int(d["k"].sum())
            bf = ks + int(d["bb"].sum()) + int(d["hits"].sum())
            return ks / bf if bf > 0 else np.nan

        ip_prev = parse_ip(prev1["ip"].iloc[0]) if len(prev1) else 5.0

        if rest <= 3:
            pitch_limit = 70
        elif rest == 4:
            pitch_limit = 82 if ip_prev >= 7.0 else 88
        else:
            pitch_limit = 100

        records.append({
            "rest_days":             rest,
            "k9_L3":                 k9(l3),
            "k_pct_L15":             k_pct(l15),
            "k9_L15":                k9(l15),
            "k9_L30":                k9(l30),
            "innings_prev_start":    ip_prev,
            "cumulative_pitches_7d": int(l7["pitches"].sum()),
            "pitch_limit_flag":      int(rest <= 3 or ip_prev >= 7.0),
            "estimated_pitch_limit": pitch_limit,
            "implied_max_batters":   round(pitch_limit / 3.8, 1),
        })

    feat = pd.DataFrame(records, index=starts.index)
    return pd.concat([starts.reset_index(drop=True),
                      feat.reset_index(drop=True)], axis=1)


# ── Checkpoint helpers ────────────────────────────────────────────────────────

def load_checkpoint() -> tuple[list[dict], set[tuple[int, int]]]:
    """
    Returns (existing_rows, already_done_set).
    already_done_set contains (pitcher_id, season) pairs already collected.
    """
    if not CHECKPOINT_PATH.exists():
        return [], set()
    try:
        df = pd.read_csv(CHECKPOINT_PATH, low_memory=False)
        rows = df.to_dict("records")
        done = {(int(r["pitcher_id"]), int(r["season"])) for r in rows
                if pd.notna(r.get("pitcher_id")) and pd.notna(r.get("season"))}
        log.info("Checkpoint loaded: %d rows, %d pitchers already done",
                 len(rows), len(done))
        return rows, done
    except Exception as exc:
        log.warning("Could not read checkpoint (%s) — starting fresh", exc)
        return [], set()


def save_checkpoint(rows: list[dict]) -> None:
    """Overwrite checkpoint.csv with all rows collected so far."""
    try:
        pd.DataFrame(rows).to_csv(CHECKPOINT_PATH, index=False)
    except Exception as exc:
        log.warning("Checkpoint save failed: %s", exc)


# ── Main pipeline ─────────────────────────────────────────────────────────────

def build_training_data() -> pd.DataFrame:

    # ── Resume from checkpoint if available ──────────────────────────────────
    all_rows, already_done = load_checkpoint()
    new_rows_since_flush   = 0
    dropped_counts: list[str] = []   # for summary

    for year in SEASONS:
        log.info("==  Season %d  ==", year)

        pitcher_stats = load_pitcher_stats(year)
        if pitcher_stats.empty:
            log.warning("No pitcher stats for %d — skipping", year)
            continue

        # Load abbr_map once per season so get_game_logs can resolve team IDs
        abbr_map = load_team_abbr(year)
        time.sleep(0.3)

        team_batting = load_team_batting_mlb(year)

        # Filter to genuine starters
        all_count = len(pitcher_stats)
        starters  = pitcher_stats[
            (pitcher_stats["gs"] >= 8) & (pitcher_stats["ip"] >= 30)
        ].copy().reset_index(drop=True)

        dropped_n = all_count - len(starters)
        dropped_counts.append(f"{year}: {dropped_n} pitchers dropped (< 8 GS or < 30 IP)")
        log.info("  Qualifying starters: %d  (dropped %d)", len(starters), dropped_n)

        # ── Per-pitcher loop with tqdm progress bar ───────────────────────────
        pbar = tqdm(
            starters.iterrows(),
            total=len(starters),
            desc=f"  {year} pitchers",
            unit="pitcher",
            ncols=80,
            leave=True,
        )

        for _, pr in pbar:
            mlb_id = int(pr["player_id"])
            name   = pr.get("name", f"id={mlb_id}")

            # ── Skip if already in checkpoint ─────────────────────────────────
            if (mlb_id, year) in already_done:
                pbar.set_postfix(status="skip (cached)")
                continue

            pbar.set_postfix(pitcher=str(name)[:20])

            s_csw   = pr.get("csw_pct",    np.nan)
            s_swstr = pr.get("swstr_pct",  np.nan)
            s_k9    = pr.get("k9",         np.nan)
            s_stuff = pr.get("stuff_plus", np.nan)

            # Per-game logs via MLB API
            starts = get_game_logs(mlb_id, year, abbr_map=abbr_map)
            time.sleep(0.5)   # rate limit between API calls

            if len(starts) < 8:
                already_done.add((mlb_id, year))   # mark done so we don't retry
                continue

            enriched = add_rolling_features(starts)
            enriched = enriched.iloc[5:].copy()    # drop first 5 (insufficient history)

            for _, g in enriched.iterrows():
                opp       = str(g.get("opponent", ""))
                opp_stats = team_batting.get(opp, {})
                park_team = g["team"] if int(g.get("home_away", 0)) == 1 else opp

                try:
                    month = pd.to_datetime(g["date"]).month
                except Exception:
                    month = 6

                all_rows.append({
                    "season":                  year,
                    "date":                    str(g["date"])[:10],
                    "pitcher_id":              mlb_id,
                    "pitcher_name":            name,
                    "team":                    str(g.get("team", "")),
                    "opponent":                opp,
                    "actual_strikeouts":       int(g.get("k", 0)),
                    "rest_days":               g.get("rest_days", 5),
                    "k9_L3":                   g.get("k9_L3"),
                    "k_pct_L15":               g.get("k_pct_L15"),
                    "k9_L15":                  g.get("k9_L15"),
                    "k9_L30":                  g.get("k9_L30"),
                    "innings_prev_start":      g.get("innings_prev_start", 5.0),
                    "cumulative_pitches_7d":   g.get("cumulative_pitches_7d", 0),
                    "pitch_limit_flag":        int(g.get("pitch_limit_flag", 0)),
                    "estimated_pitch_limit":   g.get("estimated_pitch_limit", 100),
                    "implied_max_batters":     g.get("implied_max_batters", 26.3),
                    "csw_pct_L15":             s_csw,
                    "swstr_pct_L15":           s_swstr,
                    "k9_season":               s_k9,
                    "xfip_season":             np.nan,
                    "stuff_plus":              s_stuff,
                    "opp_k_pct_vs_hand":       opp_stats.get("opp_k_pct_vs_hand",  np.nan),
                    "opp_chase_pct":           opp_stats.get("opp_chase_pct",       np.nan),
                    "opp_contact_pct":         opp_stats.get("opp_contact_pct",     np.nan),
                    "opp_swstr_pct":           opp_stats.get("opp_swstr_pct",       np.nan),
                    "park_factor":             PARK_FACTORS.get(park_team, 100),
                    "home_away":               int(g.get("home_away", 0)),
                    "month":                   month,
                    "temperature_f":           np.nan,
                    "wind_speed_mph":          np.nan,
                    "wind_to_from":            0,
                })
                new_rows_since_flush += 1

            already_done.add((mlb_id, year))

            # ── Checkpoint every N new rows ───────────────────────────────────
            if new_rows_since_flush >= CHECKPOINT_INTERVAL:
                save_checkpoint(all_rows)
                new_rows_since_flush = 0
                pbar.set_postfix(pitcher=str(name)[:20], status=f"ckpt@{len(all_rows)}")

        log.info("Season %d complete — total rows so far: %d", year, len(all_rows))

    # Final checkpoint flush before cleaning
    save_checkpoint(all_rows)
    log.info("Final checkpoint saved (%d raw rows)", len(all_rows))

    # ── Assemble and clean ────────────────────────────────────────────────────
    df = pd.DataFrame(all_rows)
    if df.empty:
        log.error("Dataset is empty — check API connectivity")
        return df

    before = len(df)
    df = df[df["actual_strikeouts"] >= 0].dropna(subset=["actual_strikeouts"])
    df = df[df[["k_pct_L15", "k9_L3"]].notna().any(axis=1)]
    log.info("Dropped %d rows with missing target/rolling features", before - len(df))

    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    for col in numeric_cols:
        if df[col].isna().any():
            df[f"{col}_imputed"] = df[col].isna().astype(int)
            df[col] = df[col].fillna(df[col].median())

    df = df.sort_values("date").reset_index(drop=True)

    out_path = DATA_DIR / "training_data.csv"
    df.to_csv(out_path, index=False)
    log.info("Saved → %s", out_path)

    # ── Summary ───────────────────────────────────────────────────────────────
    _print_summary(df, dropped_counts)

    return df


# ── Summary printer ───────────────────────────────────────────────────────────

def _print_summary(df: pd.DataFrame, dropped_counts: list[str]) -> None:
    w = 58
    sep = "-" * w

    csw_pct  = df["csw_pct_L15"].notna().mean() * 100         if "csw_pct_L15"       in df.columns else 0.0
    opp_pct  = df["opp_k_pct_vs_hand"].notna().mean() * 100   if "opp_k_pct_vs_hand" in df.columns else 0.0
    seasons  = sorted(df["season"].unique().tolist())          if "season"            in df.columns else []
    by_s     = df["season"].value_counts().sort_index().to_dict() if "season"         in df.columns else {}

    print(f"\n{sep}")
    print(f"  {'DATASET SUMMARY':^{w - 4}}")
    print(sep)
    print(f"  Total rows collected :  {len(df):>8,}")
    print(f"  Seasons covered      :  {seasons}")
    for s, n in by_s.items():
        print(f"    {s}               :  {n:>8,} rows")
    print(sep)
    print(f"  Avg K / start        :  {df['actual_strikeouts'].mean():>8.2f}")
    print(f"  K std dev            :  {df['actual_strikeouts'].std():>8.2f}")
    print(sep)
    print(f"  csw_pct_L15 not-null :  {csw_pct:>7.1f}%")
    print(f"  opp_k_pct not-null   :  {opp_pct:>7.1f}%")
    print(sep)
    print(f"  Pitcher drops by season:")
    for line in dropped_counts:
        print(f"    - {line}")
    print(f"{sep}\n")


# ── Fast patch: fill null feature columns in existing CSV ─────────────────────

def patch_existing_data() -> pd.DataFrame:
    """
    Load an already-built training_data.csv and populate the 4 previously-null
    feature columns (stuff_plus, opp_chase_pct, opp_contact_pct, opp_swstr_pct)
    by fetching only the season-level lookup tables — no per-game API calls.

    Called automatically when training_data.csv exists but these columns are null.
    """
    out_path = DATA_DIR / "training_data.csv"
    if not out_path.exists():
        log.error("training_data.csv not found — run full pipeline first")
        return pd.DataFrame()

    df = pd.read_csv(out_path, low_memory=False)
    log.info("Loaded %d rows from %s", len(df), out_path)

    # Reset previously-failed fills (0.0 is invalid for stuff_plus ~100; ~0.29 for chase)
    # so the lookup below overwrites them with real values.
    TARGET_COLS = ["stuff_plus", "opp_chase_pct", "opp_contact_pct", "opp_swstr_pct"]
    for col in TARGET_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            # Treat 0.0 as missing — no valid stat is exactly 0
            df.loc[df[col] == 0.0, col] = np.nan

    for year in sorted(df["season"].dropna().unique()):
        year = int(year)
        mask = df["season"] == year
        log.info("== Patching season %d (%d rows) ==", year, mask.sum())

        # ── Stuff+ ────────────────────────────────────────────────────────────
        stuff_df = load_stuff_plus(year)
        if not stuff_df.empty:
            stuff_map = (
                stuff_df.dropna(subset=["player_id"])
                .set_index("player_id")["stuff_plus"]
                .to_dict()
            )
            df.loc[mask, "stuff_plus"] = (
                df.loc[mask, "pitcher_id"].map(stuff_map)
            )
            filled = df.loc[mask, "stuff_plus"].notna().sum()
            log.info("  stuff_plus filled: %d / %d rows", filled, mask.sum())

        # ── Team batting discipline ────────────────────────────────────────────
        discipline = load_team_batting_discipline(year)
        log.info("  Discipline keys (%d): %s", len(discipline), sorted(discipline.keys())[:8])
        opp_sample = df.loc[mask, "opponent"].unique()[:8].tolist()
        log.info("  Opponent sample in CSV: %s", opp_sample)
        for out_col, stat_key in [
            ("opp_chase_pct",   "opp_chase_pct"),
            ("opp_contact_pct", "opp_contact_pct"),
            ("opp_swstr_pct",   "opp_swstr_pct"),
        ]:
            if out_col not in df.columns:
                df[out_col] = np.nan
            team_map = {t: v.get(stat_key, np.nan) for t, v in discipline.items()}
            df.loc[mask, out_col] = df.loc[mask, "opponent"].map(team_map)
            filled = df.loc[mask, out_col].notna().sum()
            log.info("  %s filled: %d / %d rows", out_col, filled, mask.sum())

    # Re-apply median imputation for any remaining nulls
    TARGET_COLS = ["stuff_plus", "opp_chase_pct", "opp_contact_pct", "opp_swstr_pct"]
    for col in TARGET_COLS:
        if col in df.columns:
            null_n = df[col].isna().sum()
            if null_n:
                med = df[col].median()
                df[col] = df[col].fillna(med if pd.notna(med) else 0.0)
                log.info("  %s: imputed %d remaining nulls with median=%.4f",
                         col, null_n, med if pd.notna(med) else 0.0)

    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(out_path, index=False)
    log.info("Patched data saved → %s  (%d rows)", out_path, len(df))

    # Quick null report
    print("\n-- Null rates after patch --")
    for col in TARGET_COLS:
        if col in df.columns:
            pct = df[col].isna().mean() * 100
            print(f"  {col:<22} {pct:.1f}% null")
    print()

    return df


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    out_path = DATA_DIR / "training_data.csv"
    if out_path.exists():
        # Always patch when CSV exists: fills null/zero feature columns using
        # season-level lookups without re-fetching per-game logs.
        patch_existing_data()
    else:
        build_training_data()
