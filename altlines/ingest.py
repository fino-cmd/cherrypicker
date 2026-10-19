"""
AltLines — Module 1: Ingestion & cleaning.

Responsibilities
----------------
1. Load and clean historical player game logs (long format, one row per
   player-game-market).
2. Load pre-game alt-line odds and convert them to FAIR implied probabilities
   by removing the book's margin ("vig" / overround).
3. Provide a synthetic demo dataset so the whole pipeline runs with zero API
   keys, plus API hooks with clearly-marked insertion points.

The vig removal matters more than it looks. A book quoting an extreme alt line
at +800 is not offering a 11.1% event; part of that 11.1% is margin. Comparing
our simulated tail to the RAW implied probability would systematically inflate
our EV. We must compare like-for-like: simulated P(event) vs the book's FAIR
P(event). That is exactly what `prepare_odds()` produces.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from altlines.config import (
    ALT_LINES_CSV,
    ASSUMED_MARGIN_SINGLE_SIDED,
    GAME_LOGS_CSV,
    MARKET_SPECS,
    ODDS_API_ALT_SUFFIX,
    ODDS_API_BASE,
    ODDS_API_MARKET_KEYS,
    ODDS_REGIONS,
    RANDOM_SEED,
    SPORT_KEY,
    is_odds_configured,
    odds_api_key,
)
# Tricode normalisation lives with the slate (it is the same vocabulary the
# calendar speaks), so the demo generator and real data agree on team codes.
from altlines.slate import normalize_tricode as _tricode

logger = logging.getLogger(__name__)

# Canonical column order for the two input frames. Kept explicit so downstream
# code can rely on it, and so a misc CSV fails loudly instead of silently.
GAME_LOG_COLUMNS = [
    "date", "season", "week", "game_id", "player_id", "player_name", "team",
    "opponent", "position", "market", "value", "usage", "snap_share",
    "home_away", "rest_days", "opp_def_rating",
]
ODDS_COLUMNS = [
    "snapshot_ts", "event_date", "game_id", "player_id", "player_name", "team",
    "market", "book", "side", "point", "decimal_odds", "main_line",
]


# ══════════════════════════════════════════════════════════════════════════════
# 1a. Odds math — conversions
# ══════════════════════════════════════════════════════════════════════════════

def american_to_decimal(american: float | int | str) -> float:
    """+150 → 2.50 ; -200 → 1.50. Accepts '+150' / '-200' strings."""
    a = float(str(american).replace("+", "").strip())
    if a == 0:
        raise ValueError("American odds cannot be 0")
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def decimal_to_american(decimal: float) -> float:
    """2.50 → +150 ; 1.50 → -200."""
    d = float(decimal)
    if d <= 1.0:
        raise ValueError("Decimal odds must be > 1.0")
    return (d - 1.0) * 100.0 if d >= 2.0 else -100.0 / (d - 1.0)


def decimal_to_implied(decimal: float) -> float:
    """Raw implied probability, margin INCLUDED (overround intact)."""
    return 1.0 / float(decimal)


def implied_to_decimal(p: float) -> float:
    """Fair probability → decimal odds."""
    if not 0 < p < 1:
        raise ValueError(f"Probability must be in (0,1), got {p}")
    return 1.0 / p


# ══════════════════════════════════════════════════════════════════════════════
# 1b. Vig removal (a.k.a. "de-vigging")
# ══════════════════════════════════════════════════════════════════════════════
# Three estimators are provided:
#   multiplicative — proportional scaling. Fast, standard, assumes the book
#                    applies margin evenly. Default.
#   power          — p_fair ∝ p_raw^k. Weights margin onto longshots, which is
#                    closer to how books actually price extreme alt lines.
#   shin           — models an informed-trader share z; the academic standard
#                    for two-way markets, good on high-margin alt ladders.
# Use `power` or `shin` when alt lines carry huge juice; `multiplicative`
# otherwise.

def _shin_prob(p_raw: float, overround: float, z: float) -> float:
    """Shin's (1993) fair-probability formula for a single outcome."""
    if z >= 1.0:
        return p_raw / overround
    return (np.sqrt(z * z + 4.0 * (1.0 - z) * p_raw * p_raw / overround) - z) / (2.0 * (1.0 - z))


def devig_multiplicative(p1: float, p2: float) -> tuple[float, float]:
    s = p1 + p2
    if s <= 0:
        return p1, p2
    return p1 / s, p2 / s


def devig_power(p1: float, p2: float) -> tuple[float, float]:
    """Find k such that p1**k + p2**k == 1 (brentq root-find)."""
    def f(k: float) -> float:
        return p1 ** k + p2 ** k - 1.0

    try:
        k = brentq(f, 1.0, 50.0)
    except ValueError:
        return devig_multiplicative(p1, p2)
    return p1 ** k, p2 ** k


def devig_shin(p1: float, p2: float) -> tuple[float, float]:
    """Find the insider share z making the Shin fair probs sum to 1."""
    s = p1 + p2

    def f(z: float) -> float:
        return _shin_prob(p1, s, z) + _shin_prob(p2, s, z) - 1.0

    try:
        z = brentq(f, 1e-9, 0.5)
    except ValueError:
        return devig_multiplicative(p1, p2)
    return _shin_prob(p1, s, z), _shin_prob(p2, s, z)


_DEVIG_METHODS = {
    "multiplicative": devig_multiplicative,
    "power": devig_power,
    "shin": devig_shin,
}


def remove_vig(probs: list[float], method: str = "multiplicative") -> list[float]:
    """Generic de-vig for n outcomes. Returns fair probs summing to 1."""
    if method not in _DEVIG_METHODS and method != "multiplicative":
        raise ValueError(f"Unknown method '{method}'. Use one of {list(_DEVIG_METHODS)}")
    probs = [float(p) for p in probs]
    if any(p <= 0 for p in probs):
        raise ValueError("Implied probabilities must be > 0")
    if len(probs) == 2 and method in _DEVIG_METHODS:
        return list(_DEVIG_METHODS[method](probs[0], probs[1]))
    # n-way fallback: proportional
    s = sum(probs)
    return [p / s for p in probs]


def fair_prob_one_sided(decimal_odds: float,
                        margin: float = ASSUMED_MARGIN_SINGLE_SIDED) -> float:
    """Fair prob for a line we can only see on ONE side.

    We cannot measure the true overround from a single quote, so we assume the
    book applied `margin` on top of the fair price: decimal = 1 / (p_fair *
    (1+margin))  ⇒  p_fair = p_raw / (1+margin).
    """
    p_raw = decimal_to_implied(decimal_odds)
    return min(p_raw / (1.0 + margin), 0.999)


# ══════════════════════════════════════════════════════════════════════════════
# 1c. Loaders
# ══════════════════════════════════════════════════════════════════════════════

def load_game_logs(path=GAME_LOGS_CSV) -> pd.DataFrame:
    """Read + clean the historical player game logs.

    Cleaning steps: coerce numerics, drop rows with no realised value, drop
    exact duplicates, normalise text, sort chronologically. Kept deliberately
    light — heavy assumptions belong in the simulation layer, not here.
    """
    path = str(path)
    df = pd.read_csv(path)
    missing = [c for c in ("player_id", "market", "value") if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ("value", "usage", "snap_share", "home_away", "rest_days",
                "opp_def_rating", "season", "week"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ("market", "player_name", "team", "opponent", "position", "game_id"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    df = df.dropna(subset=["value"])
    df = df.drop_duplicates(subset=[c for c in ("date", "player_id", "market") if c in df.columns])
    if "date" in df.columns:
        df = df.sort_values(["player_id", "market", "date"]).reset_index(drop=True)
    logger.info("Loaded %d game-log rows from %s", len(df), path)
    return df


def load_odds(path=ALT_LINES_CSV) -> pd.DataFrame:
    """Read pre-game alt-line odds. `decimal_odds` may arrive as American or
    decimal; we normalise everything to decimal + raw implied probability."""
    path = str(path)
    df = pd.read_csv(path)
    if "event_date" in df.columns:
        df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce")
    if "snapshot_ts" in df.columns:
        df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"], errors="coerce")

    if "decimal_odds" not in df.columns:
        if "american_odds" in df.columns:
            df["decimal_odds"] = df["american_odds"].map(american_to_decimal)
        else:
            raise ValueError(f"{path} needs a 'decimal_odds' or 'american_odds' column")

    for col in ("decimal_odds", "point", "main_line"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["side"] = df["side"].astype(str).str.lower().str.strip()
    df = df.dropna(subset=["decimal_odds", "point"])
    df = df[df["decimal_odds"] > 1.0]
    logger.info("Loaded %d odds rows from %s", len(df), path)
    return df.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# 1d. Odds → fair probability
# ══════════════════════════════════════════════════════════════════════════════

def prepare_odds(df: pd.DataFrame, method: str = "multiplicative") -> pd.DataFrame:
    """Attach `p_implied_raw`, `p_fair`, `vig` and `devig_method` columns.

    Strategy: group quotes by (event_date, player, market, point, book). When a
    book shows BOTH sides of the same point we de-vig the pair (accurate). When
    only one side exists we fall back to an assumed margin.

    The returned `p_fair` is what our simulation is compared against — the
    book's honest opinion of the tail, margin stripped out.
    """
    if df.empty:
        return df.assign(p_implied_raw=[], p_fair=[], vig=[], devig_method=[])

    out = df.copy()
    out["p_implied_raw"] = 1.0 / out["decimal_odds"]
    out["p_fair"] = np.nan
    out["vig"] = np.nan
    out["devig_method"] = ""

    id_cols = [c for c in ("event_date", "player_id", "player_name", "market", "point", "book")
               if c in out.columns]

    # One pass over each (book, point) pair group.
    for _, g in out.groupby(id_cols, dropna=False):
        over = g[g["side"] == "over"]
        under = g[g["side"] == "under"]
        if not over.empty and not under.empty:
            p_over, p_under = 1.0 / over.iloc[0]["decimal_odds"], 1.0 / under.iloc[0]["decimal_odds"]
            fair_over, fair_under = remove_vig([p_over, p_under], method=method)
            out.loc[over.index, "p_fair"] = fair_over
            out.loc[under.index, "p_fair"] = fair_under
            out.loc[g.index, "vig"] = (p_over + p_under) - 1.0
            out.loc[g.index, "devig_method"] = method
        else:
            # Single-sided alt line.
            for side_df in (over, under):
                if side_df.empty:
                    continue
                p_fair = fair_prob_one_sided(side_df.iloc[0]["decimal_odds"])
                out.loc[side_df.index, "p_fair"] = p_fair
                out.loc[side_df.index, "vig"] = ASSUMED_MARGIN_SINGLE_SIDED
                out.loc[side_df.index, "devig_method"] = "assumed_margin"

    return out


def best_price_per_line(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse multi-book quotes to the best available price per
    (player, market, point, side). Adds `best_book` and `n_books`."""
    if df.empty:
        return df
    keys = [c for c in ("event_date", "player_id", "player_name", "market", "point", "side")
            if c in df.columns]
    idx = df.groupby(keys, dropna=False)["decimal_odds"].idxmax()
    best = df.loc[idx].copy()
    best["best_book"] = best["book"]
    best["n_books"] = df.groupby(keys, dropna=False)["book"].transform("nunique").loc[idx].values
    return best.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# 1e. API hooks  ← wire your providers here
# ══════════════════════════════════════════════════════════════════════════════

def fetch_game_logs_api(market: str, season: int, **provider_params) -> pd.DataFrame:
    """Fetch historical player game logs for one market from your provider.

    ┌─ HOW TO WIRE THIS UP ────────────────────────────────────────────────────┐
    │ 1. Put your key in altlines/config.py (`_HARDCODED`) or export          │
    │    SPORTS_DATA_API_KEY.                                                 │
    │ 2. Set the base URL in config.SPORTS_API_BASE.                          │
    │ 3. Implement the request below for your provider and map its response   │
    │    onto GAME_LOG_COLUMNS. The ONLY columns the engine truly needs are   │
    │    `player_id`, `market`, `value` and `usage`; the rest add context.    │
    └────────────────────────────────────────────────────────────────────────┘

    Returns a DataFrame shaped like GAME_LOG_COLUMNS (empty on failure).
    """
    import httpx

    from altlines.config import SPORTS_API_BASE, sports_data_api_key

    key = sports_data_api_key()
    if not key:
        logger.warning("No SPORTS_DATA_API_KEY configured — returning empty frame.")
        return pd.DataFrame(columns=GAME_LOG_COLUMNS)

    base = SPORTS_API_BASE.rstrip("/")
    # ── EDIT ME: provider-specific path & params ──────────────────────────────
    url = f"{base}/YOUR_PROVIDER/playerstats/json/{market}"
    params = {"season": season, "key": key, **provider_params}
    try:
        r = httpx.get(url, params=params, timeout=20)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:  # noqa: BLE001 - network boundary, log and move on
        logger.error("Game-log API failed for %s: %s", market, exc)
        return pd.DataFrame(columns=GAME_LOG_COLUMNS)

    # ── EDIT ME: map your provider's rows onto our schema ─────────────────────
    rows = []
    for rec in payload if isinstance(payload, list) else payload.get("data", []):
        rows.append({
            "date":       rec.get("date") or rec.get("GameDate"),
            "season":     rec.get("season") or season,
            "week":       rec.get("week"),
            "player_id":  rec.get("PlayerID") or rec.get("player_id"),
            "player_name": rec.get("Name") or rec.get("player_name"),
            "team":       rec.get("Team"),
            "opponent":   rec.get("Opponent"),
            "position":   rec.get("Position"),
            "market":     market,
            "value":      rec.get("value") or rec.get("FantasyPoints"),
            "usage":      rec.get("usage") or rec.get("targets") or rec.get("attempts"),
            "snap_share": rec.get("snap_share"),
            "home_away":  rec.get("home_away"),
            "rest_days":  rec.get("rest_days"),
            "opp_def_rating": rec.get("opp_def_rating"),
        })
    return pd.DataFrame(rows, columns=GAME_LOG_COLUMNS)


def fetch_alt_lines_api(market: str, event_ids: list[str] | None = None,
                        include_alternates: bool = True) -> pd.DataFrame:
    """Pull pre-game alt-line prices from The Odds API.

    ┌─ HOW TO WIRE THIS UP ────────────────────────────────────────────────────┐
    │ 1. Set THE_ODDS_API_KEY (env) or in config._HARDCODED.                  │
    │ 2. Adjust config.SPORT_KEY (e.g. americanfootball_nfl).                 │
    │ 3. Verify ODDS_API_MARKET_KEYS against your plan; alt ladders are       │
    │    usually "<key>_alternate".                                           │
    │ This function does NOT cost quota when no key is set.                   │
    └────────────────────────────────────────────────────────────────────────┘

    Returns a DataFrame shaped like ODDS_COLUMNS (empty on failure).
    """
    import httpx

    if not is_odds_configured():
        logger.warning("No odds API key configured — returning empty frame.")
        return pd.DataFrame(columns=ODDS_COLUMNS)

    market_key = ODDS_API_MARKET_KEYS.get(market, market)
    if include_alternates:
        market_key = f"{market_key}{ODDS_API_ALT_SUFFIX}"

    events = event_ids
    if not events:
        url = f"{ODDS_API_BASE}/sports/{SPORT_KEY}/events"
        try:
            r = httpx.get(url, params={"apiKey": odds_api_key()}, timeout=20)
            r.raise_for_status()
            events = [e["id"] for e in r.json()]
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not list events: %s", exc)
            return pd.DataFrame(columns=ODDS_COLUMNS)

    rows: list[dict] = []
    for event_id in events:
        url = f"{ODDS_API_BASE}/sports/{SPORT_KEY}/events/{event_id}/odds"
        params = {
            "apiKey":     odds_api_key(),
            "regions":    ODDS_REGIONS,
            "markets":    market_key,
            "oddsFormat": "decimal",
        }
        try:
            r = httpx.get(url, params=params, timeout=20)
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Odds fetch failed for event %s: %s", event_id, exc)
            continue

        for bm in payload.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                for oc in mkt.get("outcomes", []):
                    rows.append({
                        "snapshot_ts":  payload.get("timestamp"),
                        "event_date":   payload.get("commence_time"),
                        "player_id":    oc.get("description"),   # API keys players by name
                        "player_name":  oc.get("description"),
                        "team":         None,
                        "market":       market,
                        "book":         bm.get("key"),
                        "side":         (oc.get("name") or "").lower(),
                        "point":        oc.get("point"),
                        "decimal_odds": oc.get("price"),
                        "main_line":    None,   # fill from the main prop if you fetch both
                    })
    return pd.DataFrame(rows, columns=ODDS_COLUMNS)


# ══════════════════════════════════════════════════════════════════════════════
# 1f. Synthetic demo dataset
# ══════════════════════════════════════════════════════════════════════════════
# Generates plausible game logs + a book price ladder built from a textbook
# "generic Normal curve". This lets you see the whole tool work — and see the
# edge — before plugging in any real data. The books in the demo are priced
# EXACTLY the way the thesis describes, so any +EV the engine finds here is a
# genuine recovery of a deliberately-underpriced fat tail, not noise.

_ARCHETYPES = {
    # name        usage_scale  vol_cv  eff_cv  p_boom boom_mult p_bust bust_mult
    # CVs are tuned so the generated players land on REAL NBA per-game spreads
    # (points sd ≈ 8–12, rebounds sd ≈ 3–5). The volume spread stays modest
    # because a 17-FGA player's shot volume varies by ~±4 attempts, not ±8.
    "steady":    dict(usage_scale=1.00, vol_cv=0.16, eff_cv=0.14, p_boom=0.05, boom_mult=1.5, p_bust=0.06, bust_mult=0.50),
    "volatile":  dict(usage_scale=1.00, vol_cv=0.20, eff_cv=0.20, p_boom=0.10, boom_mult=1.8, p_bust=0.09, bust_mult=0.40),
    "boom_bust": dict(usage_scale=0.95, vol_cv=0.24, eff_cv=0.25, p_boom=0.15, boom_mult=2.0, p_bust=0.12, bust_mult=0.30),
    "low_usage": dict(usage_scale=0.70, vol_cv=0.28, eff_cv=0.30, p_boom=0.15, boom_mult=2.2, p_bust=0.15, bust_mult=0.25),
}

# Per-market generative parameters (a "true" process the engine must reverse).
# `vol_scale` / `eff_scale` adapt the shared archetype CVs to the market:
# PRA is a SUM of three counting stats, so its per-minute production is much
# smoother than raw scoring — using the scoring CV for it would invent a
# phantom boom-or-bust profile that no real player has.
_DEMO_MARKET_PARAMS = {
    "points":   dict(kind="continuous", usage_mu=17.0, base_eff=1.25,   # FGA × pts/FGA
                     vol_scale=1.00, eff_scale=0.90),
    "rebounds": dict(kind="count",      usage_mu=34.0, rate=0.235,      # minutes × reb/min
                     vol_scale=0.90, eff_scale=1.00),
    "assists":  dict(kind="count",      usage_mu=34.0, rate=0.175,      # minutes × ast/min
                     vol_scale=0.90, eff_scale=1.10),
    "threes":   dict(kind="count",      usage_mu=6.5,  rate=0.355,      # 3PA × 3P%
                     vol_scale=0.85, eff_scale=1.30),
    "pra":      dict(kind="continuous", usage_mu=34.0, base_eff=1.05,   # minutes × PRA/min
                     vol_scale=0.75, eff_scale=0.55),
}

_NBA_POSITIONS = ["PG", "SG", "SF", "PF", "C"]

_FIRST = ["Kai", "Ruben", "Marcus", "Diego", "Tobias", "Ade", "Luca", "Jalen",
          "Ivan", "Noah", "Sami", "Oscar", "Theo", "Bruno", "Milo", "Zane"]
_LAST = ["Okafor", "Vega", "Nakamura", "Bianchi", "Osei", "Duarte", "Novak",
         "Costa", "Hale", "Reyes", "Mensah", "Volkov", "Ferrer", "Adeyemi"]
_POS_BY_MARKET = {
    "receiving_yards": "WR", "rushing_yards": "RB", "passing_yards": "QB",
    "receptions": "WR", "receiving_tds": "WR", "touchdowns": "RB",
    "three_pointers": "SG", "goals": "ST",
}


def _nbinom(rng: np.random.Generator, mu: float, var: float, size: int) -> np.ndarray:
    """Negative-binomial draws with a target mean and variance (overdispersed
    count). Falls back to Poisson when the requested variance <= mean."""
    mu = max(mu, 1e-6)
    if var > mu * 1.0001:
        k = mu * mu / (var - mu)
        p = k / (k + mu)
        return rng.negative_binomial(k, p, size=size)
    return rng.poisson(mu, size=size)


def _sim_game_continuous(rng, n, base_eff, ar):
    """Opportunity count × per-opportunity efficiency, with 3 latent regimes."""
    mu = ar["usage_mu"]
    usage = _nbinom(rng, mu, (ar["vol_cv"] * mu) ** 2, n)
    u = rng.random(n)
    mult = np.where(u < ar["p_bust"], ar["bust_mult"],
                    np.where(u < ar["p_bust"] + ar["p_boom"], ar["boom_mult"], 1.0))
    eff = base_eff * mult * rng.lognormal(0.0, ar["eff_cv"], n)
    return usage, usage * eff


def _sim_game_count(rng, n, rate, ar):
    """Opportunity count × Beta-distributed conversion prob → Beta-Binomial.

    IMPORTANT: `eff_cv` is the intended coefficient of variation OF THE RATE.
    For Beta(a, b) with mean m, Var = m(1−m)/(a+b+1), so the concentration that
    yields a given rate-CV is  conc = (1−m) / (cv² · m) − 1  (NOT 1/cv², which
    overshoots the rate dispersion by a factor of ~(1−m)/m).
    """
    mu = ar["usage_mu"]
    usage = _nbinom(rng, mu, (ar["vol_cv"] * mu) ** 2, n)
    conc = max((1.0 - rate) / (max(ar["eff_cv"] ** 2, 1e-6) * rate) - 1.0, 0.5)
    p = rng.beta(rate * conc, (1 - rate) * conc, size=n)
    return usage, rng.binomial(usage, np.clip(p, 0, 1))


def generate_demo_dataset(slate: dict | None = None,
                          n_games: int = 6,
                          players_per_team: int = 4,
                          markets_per_player: int = 3,
                          n_history: int = 30,
                          seed: int = RANDOM_SEED) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build a synthetic (game_logs, odds) pair laid out over a real NBA slate.

    Instead of one flat pool of players, the dataset mirrors the shape of a
    betting board: each game of `slate` contributes both of its (real) teams,
    each team contributes `players_per_team` players, and each player gets
    props in a deterministic subset of `markets_per_player` markets. Every row
    carries the slate's `game_id`, so the "por partido" view is a plain filter
    instead of a guess.

    Players are deliberately spread across archetypes so the output contains
    both boring "steady" profiles (no edge) and true boom-or-bust profiles
    (large edge on extreme overs), which is what the top-5% filter should
    surface.

    Sizing is a latency decision: `players_per_team × 2 × markets_per_player`
    is how many player-markets one click has to simulate, so the defaults
    (4 × 2 × 3 = 24) keep a single game at a few seconds.
    """
    if slate is None:
        from altlines.slate import demo_slate
        slate = demo_slate(seed=seed)
    games = [g for g in (slate.get("games") or [])][:max(1, int(n_games))]
    if not games:
        from altlines.slate import demo_slate as _fallback
        games = _fallback(seed=seed)["games"]
    rng = np.random.default_rng(seed)
    arche_names = list(_ARCHETYPES.keys())
    market_cycle = list(MARKET_SPECS.keys())

    # One (game, team, opponent, is_home) slot per team of every game.
    slots: list[tuple[dict, str, str, int]] = []
    for g in games:
        for side, other in (("home", "away"), ("away", "home")):
            slots.append((
                g,
                _tricode((g.get(side) or {}).get("tricode")),
                _tricode((g.get(other) or {}).get("tricode")),
                int(side == "home"),
            ))
    slots = [s for s in slots if s[1]]

    log_rows: list[dict] = []
    players: list[dict] = []
    pid = 0

    for game, team, opponent, home_away in slots:
        try:
            event_day = pd.Timestamp(str(game.get("date"))[:10])
        except (ValueError, TypeError):
            event_day = pd.Timestamp("2026-10-21")

        for k in range(max(1, int(players_per_team))):
            pid += 1
            # The archetype belongs to the PLAYER, so all of their markets
            # share one personality (a gunner is a gunner on points and threes).
            arch_name = arche_names[(pid - 1) % len(arche_names)]
            base_arch = dict(_ARCHETYPES[arch_name])
            # Guarantee unique display names: walk the surname pool first and
            # only advance the first-name index after a full pass, so no two
            # synthetic players collide (collisions made the tables unreadable).
            name = (f"{_FIRST[(pid // len(_LAST)) % len(_FIRST)]} "
                    f"{_LAST[pid % len(_LAST)]}")
            position = _NBA_POSITIONS[pid % len(_NBA_POSITIONS)]

            own_markets = [market_cycle[(pid + j) % len(market_cycle)]
                           for j in range(max(1, int(markets_per_player)))]

            for market in own_markets:
                mp = _DEMO_MARKET_PARAMS[market]
                ar = dict(base_arch)
                ar["usage_mu"] = mp["usage_mu"] * ar["usage_scale"]
                # Adapt the shared archetype CVs to this market (see
                # _DEMO_MARKET_PARAMS): PRA is a SUM of three counting stats, so
                # its per-minute production is much smoother than raw scoring —
                # using the scoring CV there would invent a phantom boom-or-bust
                # profile no real player has.
                ar["vol_cv"] *= mp.get("vol_scale", 1.0)
                ar["eff_cv"] *= mp.get("eff_scale", 1.0)

                if mp["kind"] == "continuous":
                    usage, val = _sim_game_continuous(rng, n_history,
                                                      mp["base_eff"], ar)
                    val = np.round(val, 1)
                else:
                    usage, val = _sim_game_count(rng, n_history, mp["rate"], ar)

                players.append({
                    "player_id": f"SYN-{pid:04d}", "player_name": name,
                    "market": market, "archetype": arch_name, "values": val,
                    "usage": usage, "position": position, "team": team,
                    "opponent": opponent, "home_away": home_away,
                    "game_id": str(game.get("game_id")), "event_day": event_day,
                })

                # History ends two days before tip-off, one game every 2 days.
                for d in range(n_history):
                    log_rows.append({
                        "date":       event_day - pd.Timedelta(days=2 * (n_history - d)),
                        "season":     int(event_day.year), "week": d + 1,
                        "game_id":    str(game.get("game_id")),
                        "player_id":  f"SYN-{pid:04d}", "player_name": name,
                        "team":       team, "opponent": opponent,
                        "position":   position, "market": market,
                        "value":      float(val[d]), "usage": float(usage[d]),
                        "snap_share": round(float(rng.uniform(0.4, 0.85)), 3),
                        "home_away":  int(home_away),
                        "rest_days":  int(rng.integers(2, 6)),
                        "opp_def_rating": round(float(rng.normal(100, 8)), 1),
                    })

    logs_df = pd.DataFrame(log_rows, columns=GAME_LOG_COLUMNS)

    # ── Build the book ladder off a generic Normal curve ──────────────────────
    # main_line = median (floor + 0.5 to avoid pushes); sigma = market canonical
    # sigma × the book's own curve multiplier; price = 1/(p·(1+margin)) with the
    # margin growing for more extreme lines.
    #
    # `curve_mult` is the DEMO'S CORE PREMISE: a soft book does not only charge
    # more vig, it also *compresses* the tails of its generic curve. Sharp books
    # (pinnacle) keep the dispersion honest. This way the demo reproduces the
    # real phenomenon — the biggest model-vs-market gaps sit at the soft books,
    # not merely at whoever charges the least vig.
    books = {
        #  book          margin_mult  curve_mult
        "pinnacle":   dict(margin_mult=0.75, curve_mult=1.00),
        "draftkings": dict(margin_mult=1.00, curve_mult=0.96),
        "betano":     dict(margin_mult=1.25, curve_mult=0.92),
    }
    odds_rows: list[dict] = []
    from scipy.stats import norm

    for p in players:
        spec = MARKET_SPECS[p["market"]]
        vals = np.asarray(p["values"], dtype=float)
        main = float(np.floor(np.median(vals)) + 0.5)
        # The book's generic curve: ONE sigma per market (not scaled to this
        # player's own line). That is precisely the inefficiency we exploit.
        sigma = float(spec["casino_sigma"])
        step = spec["main_step"]
        # Lines are posted the evening before that game's tip-off.
        event_day = p.get("event_day") or pd.Timestamp("2026-10-21")
        snapshot = event_day - pd.Timedelta(hours=6)

        points = {main}
        for off in spec["alt_offsets"]:
            points.add(main + off * step)   # extreme overs  ← the target
            if main - off * step > 0:
                points.add(main - off * step)

        for point in sorted(points):
            if point <= 0:
                continue
            for book, bcfg in books.items():
                p_over = float(norm.sf(point, loc=main,
                                       scale=sigma * bcfg["curve_mult"]))
                # Books do not post lines they consider ~impossible; skip them so
                # the demo never invents +50000 ladders no operator would offer.
                if min(p_over, 1 - p_over) < 0.012:
                    continue
                p_over = min(max(p_over, 1e-4), 1 - 1e-4)
                margin = (0.045 + 0.35 * (0.5 - min(p_over, 1 - p_over))) \
                    * bcfg["margin_mult"]
                for side, prob in (("over", p_over), ("under", 1 - p_over)):
                    dec = round(1.0 / (prob * (1.0 + margin)), 2)
                    if dec < 1.01:
                        dec = 1.01
                    odds_rows.append({
                        "snapshot_ts": snapshot, "event_date": event_day,
                        "game_id": p.get("game_id"),
                        "player_id": p["player_id"], "player_name": p["player_name"],
                        "team": p.get("team"),
                        "market": p["market"], "book": book, "side": side,
                        "point": float(point), "decimal_odds": dec,
                        "main_line": main,
                    })

    odds_df = pd.DataFrame(odds_rows, columns=ODDS_COLUMNS)
    return logs_df, odds_df


def write_demo_dataset(slate: dict | None = None, n_games: int = 6,
                       players_per_team: int = 4, seed: int = RANDOM_SEED,
                       **kwargs) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate the demo dataset and persist it to data/altlines/*.csv."""
    logs_df, odds_df = generate_demo_dataset(
        slate=slate, n_games=n_games, players_per_team=players_per_team,
        seed=seed, **kwargs)
    GAME_LOGS_CSV.parent.mkdir(parents=True, exist_ok=True)
    logs_df.to_csv(GAME_LOGS_CSV, index=False)
    odds_df.to_csv(ALT_LINES_CSV, index=False)
    logger.info("Wrote demo dataset: %d log rows, %d odds rows",
                len(logs_df), len(odds_df))
    return logs_df, odds_df
