"""
AltLines — Configuration & market registry.

Single source of truth for:
  • filesystem paths
  • API-key lookup  ← THIS IS THE PLACE TO PLUG YOUR OWN KEYS
  • the declarative spec of every player-prop market

Why a registry instead of hard-coded logic: the Monte Carlo engine
(simulation.py) and the EV module (ev.py) must be market-agnostic. By
describing each market as a dict — distribution family, opportunity driver,
and the casino's "generic curve" ratio — we can add a new prop (say, NBA
three-pointers or NFL interceptions) by adding one entry here, with zero
changes to the math modules.

The core thesis this tool exploits:
  Sportsbooks anchor the MAIN line on the median of a player's history and
  then price every alternative ("alt") line off a *standardised, symmetric*
  curve (usually Normal) with a generic sigma. For boom-or-bust players the
  true distribution is right-skewed with fat tails, so the book mechanically
  UNDERPRICES the extreme over. That gap between the real tail and the
  book's curve is the edge.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parent.parent
DATA_DIR    = ROOT / "data" / "altlines"
GAME_LOGS_CSV = DATA_DIR / "player_game_logs.csv"
ALT_LINES_CSV = DATA_DIR / "alt_lines_odds.csv"
REPORT_HTML   = ROOT / "altlines_report.html"

# ── API keys ──────────────────────────────────────────────────────────────────
# ┌──────────────────────── INSERT YOUR KEYS HERE ───────────────────────────┐
# │ You can either export these as environment variables (recommended) or    │
# │ hardcode them in the `_HARDCODED` dict below.                            │
# │                                                                          │
# │  • THE_ODDS_API_KEY   → https://the-odds-api.com                         │
# │                         Pulls the alt-line prices. The repo's existing   │
# │                         backend/odds_api_client.py already reads this    │
# │                         same variable, so one key serves both tools.     │
# │                         Free tier = 500 requests/month.                  │
# │                                                                          │
# │  • SPORTS_DATA_API_KEY → your game-log provider. Works with any of:      │
# │                         sportsdata.io, Sportradar, MySportsFeeds,        │
# │                         or a self-hosted endpoint.                       │
# └──────────────────────────────────────────────────────────────────────────┘
_HARDCODED = {
    # "THE_ODDS_API_KEY":   "paste-your-key-here",
    # "SPORTS_DATA_API_KEY": "paste-your-key-here",
}

_ODDS_ENV_NAMES    = ("THE_ODDS_API_KEY", "ODDS_API_KEY")

# The MLB backend keeps its key in backend/.env. Load it here too so the CLI
# (`python -m altlines.run_pipeline --live`) sees the same key as the web API.
# Real environment variables still win (override=False).
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend" / ".env", override=False)
except Exception:  # noqa: BLE001 - python-dotenv is optional
    pass
_SPORTS_ENV_NAMES  = ("SPORTS_DATA_API_KEY", "SPORTSDATA_API_KEY", "SPORTRADAR_API_KEY")


def odds_api_key() -> str | None:
    """Return the odds API key from (in order) env vars or the hardcoded dict."""
    for name in _ODDS_ENV_NAMES:
        val = os.getenv(name) or _HARDCODED.get(name)
        if val:
            return val
    return None


def sports_data_api_key() -> str | None:
    """Return the game-log API key from env vars or the hardcoded dict."""
    for name in _SPORTS_ENV_NAMES:
        val = os.getenv(name) or _HARDCODED.get(name)
        if val:
            return val
    return None


def is_odds_configured() -> bool:
    key = odds_api_key()
    return bool(key and len(key) > 10)


def is_sports_data_configured() -> bool:
    key = sports_data_api_key()
    return bool(key and len(key) > 5)


# ── Endpoints (edit to point at your provider) ────────────────────────────────
# The Odds API v4 — same base the CherryPick backend uses.
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Provider-specific game-log endpoint. Replace the {placeholders} with the
# exact path/params your provider documents, then wire it up in
# ingest.fetch_game_logs_api(). Left intentionally generic.
SPORTS_API_BASE = os.getenv("SPORTS_API_BASE", "https://api.sportsdata.io/v3")

# Sport the odds feed should query. Change as needed:
#   basketball_nba | americanfootball_nfl | soccer_epl | ...
SPORT_KEY = os.getenv("ALTLINES_SPORT_KEY", "basketball_nba")
# Each region IS billed: credits = markets × regions per event call.
ODDS_REGIONS = "us,us2,eu,uk"

# Maps our internal market key → The Odds API market key.
# NOTE: The Odds API exposes MAIN props under the keys below; ALT lines usually
# live under an "<key>_alternate" variant (e.g. player_points_alternate).
# Confirm the exact keys for your plan at https://the-odds-api.com/liveapi/guides/v4/#get-odds
# and adjust here — no code changes are needed elsewhere.
ODDS_API_MARKET_KEYS: dict[str, str] = {
    "points":     "player_points",
    "rebounds":   "player_rebounds",
    "assists":    "player_assists",
    "threes":     "player_threes",
    "pra":        "player_points_rebounds_assists",
}
ODDS_API_ALT_SUFFIX = "_alternate"   # append when requesting the alt-line ladder

# ── Real-data mode (altlines/live.py) ────────────────────────────────────────
# Markets pulled in real mode. Each one costs 1 credit per region per game.
LIVE_MARKETS = ("points", "rebounds", "assists", "threes", "pra")
# Alt trays (DraftKings, FanDuel, BetRivers, Bovada, BetOnline…) are US-only.
LIVE_ODDS_REGIONS = "us"
# The main market doubles the cost; the main line is rebuilt from the alt tray
# itself (live.consensus_main_line), so it is off by default.
LIVE_INCLUDE_MAIN = False
# Playoff games have another rotation (starters +5 min, bench out). And at the
# start of a season they would be the "most recent" games the EWMA leans on.
LIVE_INCLUDE_PLAYOFFS = False
# Games under this fraction of the player's median minutes are dropped
# (injury exits, minutes restrictions): the simulation is conditional on the
# projected role, as the brief asks ("minutos proyectados").
LIVE_MIN_MINUTES_FRAC = 0.5
# With anchoring on, the model's median is moved onto the market's main line.
# If that move is bigger than ±25 % the model and the market disagree on the
# player's LEVEL (role change, injury news, stale data) and the shape we would
# transplant is not trustworthy — such lines are kept in the table but never
# reach the top-5 % board.
LIVE_MAX_ANCHOR_DRIFT = 0.25
# Offseason guard: when the newest logged game is older than this, the level
# half-life is stretched so last season's final weeks do not dictate the level.
LIVE_STALE_DAYS = 45
LIVE_STALE_HALFLIFE = 30.0
LIVE_ODDS_TTL_S   = 20 * 60        # re-opening a game within 20 min is free
# True = odds come ONLY from the disk cache (any age); a miss raises instead of
# paying credits. Set by `run_pipeline --odds-cache-only` (backfills, tests).
LIVE_ODDS_CACHE_ONLY = False
LIVE_EVENTS_TTL_S = 30 * 60        # the events list is free anyway
LIVE_ROSTER_TTL_S = 12 * 3600
LIVE_LOGS_TTL_S   = 6 * 3600       # current season; past seasons never expire

# ── Simulation defaults ───────────────────────────────────────────────────────
N_SIM_DEFAULT      = 20_000   # spec asks for ≥ 10,000; 20k is cheap insurance
RANDOM_SEED        = 20261018
# Probability the player doesn't play / is inactive on a given slate. Estimated
# from logs when possible; this is the fallback.
P_INACTIVE_DEFAULT = 0.03

# ── Vig / devig defaults ──────────────────────────────────────────────────────
# Margin the book applies to a single-sided alt line when we cannot pair it
# with the opposite side (so we can't measure the true overround). 5.5% is a
# sane mid-range for alt lines at retail books.
ASSUMED_MARGIN_SINGLE_SIDED = 0.055

# Empirical-Bayes shrinkage strength for the player's variance estimate.
# target_var = w·obs_var + (1−w)·league_sd², with w = n_games/(n_games+K0).
# Small histories lean on the league prior; long histories lean on the player.
# This is the main guard against \"false edges\" manufactured out of 10-game noise.
# K0 is deliberately LARGE: even a full season is a small sample for estimating a
# variance, and the whole point is to bet the skew, not a noisy dispersion.
SHRINKAGE_K0 = 25.0

# ── Recency weighting ────────────────────────────────────────────────────────
# LEVEL moves fast (a player's role changes week to week) but VOLATILITY is a
# property of the player-season. Using one short half-life for BOTH is a classic
# way to manufacture edges: an 8-game effective window produces a variance
# estimate that swings ±50% by chance, and a 3-sigma tail amplifies that into a
# phantom +200% EV. So we weight the mean aggressively and the variance slowly.
HALFLIFE_DEFAULT = 6.0        # games — half-life for the MEAN / level
HALFLIFE_VAR_DEFAULT = 18.0   # games — half-life for the VARIANCE / volatility

# How much of the projection comes from the fast EWMA vs the player's own full
# sample. A 6-game half-life is ~9 effective games: one cold week and the model
# suddenly loves the UNDER on a player who did not change. Blending keeps the
# model responsive to real role changes while refusing to bet a 3-game slump.
# 1.0 = pure EWMA (maximum reactivity), 0.0 = pure season average.
LEVEL_BLEND_FAST = 0.65


# ── Market registry ───────────────────────────────────────────────────────────
# Every market declares:
#   label              human-readable name
#   family             "continuous" → volume × efficiency mixture
#                      "count"      → volume × Beta-Binomial conversion
#   usage              column in the game-log frame holding the opportunity
#                      count (targets, carries, shot attempts, touches …)
#   vol_min_scale      multiplicative floor so tiny samples don't produce a
#                      degenerate volume distribution
#   casino_sigma_ratio sigma the book's generic Normal curve uses, as a
#                      fraction of the main line. Books are ~0.35–0.50 of the
#                      line for yardage props. THIS is what we beat.
#   main_step          typical increment of the main/alt line (used only for
#                      generating demo lines — real lines come from the odds API)
#   alt_offsets        line offsets (in main_step units) used in demo data
#   zero_inflated      whether a mass at 0 is expected (DNP / no volume)
MARKET_SPECS: dict[str, dict] = {
    # ── NBA player props ──────────────────────────────────────────────────────
    # `usage` = the opportunity driver in the game-log frame:
    #   points / pra  → field-goal attempts / minutes played
    #   rebounds / assists → minutes played
    #   threes        → three-point attempts
    #
    # `league_sd`  = canonical TRUE per-game spread for the market (from league
    #                data). Used as the empirical-Bayes prior and as a sanity
    #                anchor for what "normal" dispersion looks like.
    # `casino_sigma` = the sigma inside the book's generic symmetric curve.
    #                Books use ONE sigma per market for every player, which is
    #                exactly why high-variance players get mispriced. It is set
    #                slightly BELOW league_sd to encode the structural edge.
    # `league_ref_mean` = the per-game mean at which `league_sd` holds. Spread
    #                grows with volume (var ∝ mean, like any count), so both
    #                the shrinkage prior and the book's curve are rescaled to
    #                each player's own level: a 7-ppg bench guy is NOT given a
    #                9-point sd, which would invent fat tails out of thin air.
    "points": {
        "label": "Points",
        "family": "continuous",
        "usage": "usage",                 # = FGA
        "unit_label": "pts/FGA",
        "league_sd": 9.0,
        "league_ref_mean": 21.0,
        "casino_sigma": 8.6,
        "main_step": 5.0,
        "alt_offsets": [1, 2, 3],
        "zero_inflated": True,
    },
    "rebounds": {
        "label": "Rebounds",
        "family": "count",
        "usage": "usage",                 # = minutes
        "unit_label": "reb/min",
        "league_sd": 3.2,
        "league_ref_mean": 8.0,
        "casino_sigma": 3.0,
        "main_step": 2.0,
        "alt_offsets": [1, 2, 3],
        "zero_inflated": True,
    },
    "assists": {
        "label": "Assists",
        "family": "count",
        "usage": "usage",                 # = minutes
        "unit_label": "ast/min",
        "league_sd": 2.9,
        "league_ref_mean": 6.0,
        "casino_sigma": 2.8,
        "main_step": 2.0,
        "alt_offsets": [1, 2, 3],
        "zero_inflated": True,
    },
    "threes": {
        "label": "Three-Pointers Made",
        "family": "count",
        "usage": "usage",                 # = 3PA
        "unit_label": "3P%",
        "league_sd": 1.45,
        "league_ref_mean": 2.3,
        "casino_sigma": 1.45,
        "main_step": 1.0,
        "alt_offsets": [1, 2, 3],
        "zero_inflated": True,
    },
    "pra": {
        "label": "Pts+Reb+Ast",
        "family": "continuous",
        "usage": "usage",                 # = minutes
        "unit_label": "PRA/min",
        "league_sd": 11.0,
        "league_ref_mean": 35.0,
        "casino_sigma": 10.4,
        "main_step": 5.0,
        "alt_offsets": [1, 2, 3],
        "zero_inflated": False,
    },
}


def get_spec(market: str) -> dict:
    """Return the spec for a market key, raising a clear error when unknown."""
    try:
        return MARKET_SPECS[market]
    except KeyError as exc:
        known = ", ".join(sorted(MARKET_SPECS))
        raise KeyError(f"Unknown market '{market}'. Known markets: {known}") from exc
