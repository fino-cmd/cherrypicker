"""
AltLines — JSON bridge for the web app (the NBA tab).

This module is the ONLY thing the FastAPI backend imports from the AltLines
package. It runs the exact same pipeline the CLI runs and returns plain,
JSON-serialisable dicts, so the frontend never has to know anything about
pandas, numpy or the simulation internals.

Design notes
------------
* The pipeline is CPU-bound (20k Monte Carlo draws per player-market) and takes
  a few seconds, so the result is cached in-process with a short TTL. The
  backend endpoint is a plain `def` (not `async def`) so FastAPI runs it in its
  threadpool and it can never block the MLB endpoints.
* Nothing here is shared with the CherryPick MLB model: separate data files,
  separate math, separate cache. If AltLines is broken, MLB keeps working.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime

import pandas as pd

from altlines import config as cfg
from altlines import slate as slate_mod

logger = logging.getLogger(__name__)

# Measured cost of one player-market (20k iterations + the calibration loop).
# Used only to print an honest "≈ Ns" on each slate card so nobody clicks a
# 40-player game at halftime expecting it to be instant.
SIM_SECONDS_PER_PLAYER_MARKET = 0.24

# In-process cache — the simulation is deterministic for a given seed/params, so
# re-running it on every page load would be pure waste.
_CACHE: dict = {"key": None, "ts": 0.0, "payload": None}
_CACHE_TTL = 300.0          # seconds
# Per-key locks: a slow single-game simulation must never be run twice
# concurrently by two clicks, and must not block a different game.
_RUN_LOCKS: dict = {}
_RUN_LOCK = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → list of dicts, JSON-safe.

    `to_json` is used on purpose: it converts NaN/Inf to null and numpy scalars
    to plain JSON numbers, which is exactly what the frontend needs. (A naive
    to_dict() leaks numpy types that blow up json.dumps.)
    """
    if df is None or df.empty:
        return []
    return json.loads(df.to_json(orient="records"))


def _profile_board(summary: pd.DataFrame, n: int = 12) -> list[dict]:
    """The distribution profiles worth explaining: biggest mean − median gaps.

    This is the whole thesis in one table — a player whose MEAN sits well above
    the book's anchor (the median) is exactly where the symmetric curve is
    wrong.
    """
    if summary is None or summary.empty:
        return []
    cols = [
        "player_id", "player_name", "market", "market_label", "profile",
        "n_games", "sim_mean", "sim_median", "mean_minus_median", "sim_skew",
        "sim_cv",
    ]
    cols = [c for c in cols if c in summary.columns]
    board = summary.sort_values("mean_minus_median", ascending=False).head(n)
    return _records(board[cols])


def _market_breakdown(table: pd.DataFrame) -> list[dict]:
    """How many priced lines each market contributed (for the UI legend)."""
    if table is None or table.empty:
        return []
    grp = (table.groupby(["market", "market_label"], as_index=False)
                .agg(lines=("point", "size"),
                     best_ev=("ev_pct", "max"),
                     median_ev=("ev_pct", "median")))
    return _records(grp.sort_values("lines", ascending=False))


# ══════════════════════════════════════════════════════════════════════════════
# Payload
# ══════════════════════════════════════════════════════════════════════════════

def _key_lock(key) -> threading.Lock:
    """One lock per cache key, created on demand."""
    with _RUN_LOCK:
        lock = _RUN_LOCKS.get(key)
        if lock is None:
            lock = _RUN_LOCKS[key] = threading.Lock()
        return lock


def build_payload(n_iter: int = cfg.N_SIM_DEFAULT,
                  top_pct: float = 5.0,
                  metric: str = "ev",
                  devig_method: str = "multiplicative",
                  max_odds: float = 50.0,
                  min_ev: float = 0.0,
                  halflife: float = cfg.HALFLIFE_DEFAULT,
                  halflife_var: float | None = None,
                  seed: int | None = None,
                  demo: bool = True,
                  logs_path: str | None = None,
                  odds_path: str | None = None,
                  refresh: bool = False,
                  game: str | None = None,
                  date: str | None = None,
                  offline: bool = False,
                  live: bool = False) -> dict:
    """Run the AltLines pipeline and return the frontend payload.

    `live=True` prices REAL alt trays (The Odds API, costs credits) against
    REAL game logs (ESPN). Use it per game: a league-wide live run pays for
    every game of the night.

    `demo=True` regenerates the synthetic NBA dataset *over the slate* (no API
    key needed). Point `logs_path`/`odds_path` at real CSVs to price real games.

    `game` restricts the simulation to one game of the slate. That is safe and
    consistent because the engine seeds per (player, market): a single-game run
    returns bit-identical probabilities to the league-wide run, only faster
    (the fewer player-markets, the less work).
    """
    # A single source of truth for the demo seed: the engine default. The web
    # endpoint therefore returns exactly what `python -m altlines.run_pipeline
    # --demo` prints, which makes bug reports trivial to reproduce.
    seed = cfg.RANDOM_SEED if seed is None else int(seed)
    game = str(game) if game else None
    key = (n_iter, top_pct, metric, devig_method, max_odds, min_ev, halflife,
           halflife_var, seed, demo, logs_path, odds_path, game,
           str(date or ""), bool(offline), bool(live))

    def _hit(now: float):
        if (not refresh and _CACHE["payload"] is not None
                and _CACHE["key"] == key and now - _CACHE["ts"] < _CACHE_TTL):
            payload = dict(_CACHE["payload"])
            payload["cached"] = True
            payload["cache_age_s"] = round(now - _CACHE["ts"], 1)
            return payload
        return None

    now = time.time()
    cached = _hit(now)
    if cached is not None:
        return cached

    # Serialise identical requests: two clicks on the same card (or two tabs)
    # must not each burn a full simulation, and a long run must not block the
    # other games.
    with _key_lock(key):
        cached = _hit(time.time())
        if cached is not None:
            return cached

        # Imported here so that merely importing this module never drags in the
        # whole engine (nice for tests and for the API's startup time).
        from altlines.run_pipeline import run

        # The slate must be the very same one the UI listed, otherwise the
        # game ids would not match the cards.
        if live:
            demo, offline = False, False
        slate = slate_mod.get_slate(date, offline=offline) if (demo or game or live) else None

        # Accept the handle a person types (`BOS@DET`) as well as the ESPN id;
        # resolve it once here and work with the id from then on.
        resolved_game = game
        if game and slate is not None:
            hit = slate_mod.find_game(slate, game)
            if hit is not None:
                resolved_game = str(hit["game_id"])

        t0 = time.time()
        try:
            artifacts = run(
                demo=demo, logs_path=logs_path, odds_path=odds_path,
                n_iter=n_iter, top_pct=top_pct, metric=metric,
                min_ev=min_ev, devig_method=devig_method,
                max_odds=max_odds, halflife=halflife, halflife_var=halflife_var,
                seed=seed, write_html=False, quiet=True,
                game=resolved_game, slate=slate, offline=offline, write_data=False,
                live=live, refresh_odds=bool(live and refresh),
                log_origin="api",
            )
        except SystemExit as exc:                   # run() uses SystemExit for setup errors
            raise RuntimeError(str(exc)) from exc

        table = artifacts["value_table"]
        shortlist = artifacts["shortlist"]
        summary = artifacts["summary"]

    # ── Slate metadata ────────────────────────────────────────────────────────
    # For a single game we return just that game's card; for the league run we
    # return the whole board so the UI can render the grid from one call.
    game_meta = None
    slate_view = None
    if slate is not None:
        view = slate_mod.build_slate_view(slate, artifacts["logs"],
                                          artifacts["odds"], artifacts["mapping"])
        for g in view["games"]:
            g["est_seconds"] = round(
                g["props"]["player_markets"] * SIM_SECONDS_PER_PLAYER_MARKET + 1.0, 1)
            g["simulated"] = resolved_game is not None and \
                str(g["game_id"]) == str(resolved_game)
        if resolved_game is None:
            slate_view = view
        else:
            game_meta = next((g for g in view["games"]
                              if str(g["game_id"]) == str(resolved_game)), None)
            if game_meta is None:
                raise RuntimeError(
                    f"El partido {game} no está en el calendario de "
                    f"{view.get('date')}. Volvé a pedir la lista de partidos.")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_s": round(time.time() - t0, 2),
        "cached": False,
        "source": "live" if live else ("demo" if demo else "csv"),
        "live": artifacts.get("live_meta"),
        "sport": "NBA",
        "date": slate.get("date") if slate else None,
        "slate_source": slate.get("source") if slate else None,
        "game": game_meta,
        "slate": slate_view,
        "params": {
            "n_iter": n_iter,
            "top_pct": top_pct,
            "metric": metric,
            "devig_method": devig_method,
            "max_odds": max_odds,
            "min_ev": min_ev,
            "halflife": halflife,
            "halflife_var": halflife_var,
            "seed": seed,
            "game": resolved_game,
        },
        "counts": {
            "player_markets": int(len(summary)) if summary is not None else 0,
            "games_of_history": int(summary["n_games"].sum()) if summary is not None and not summary.empty else 0,
            "lines_priced": int(len(table)) if table is not None else 0,
            "lines_reported": int(len(shortlist)) if shortlist is not None else 0,
        },
        "markets": [
            {"key": k, "label": v["label"], "family": v["family"]}
            for k, v in cfg.MARKET_SPECS.items()
        ],
        "books": sorted(table["book"].dropna().unique().tolist()) if table is not None and not table.empty else [],
        "market_breakdown": _market_breakdown(table),
        "profiles": _profile_board(summary),
        "edges": _records(shortlist),
        "all_edges": _records(table),
    }

    _CACHE.update({"key": key, "ts": time.time(), "payload": payload})
    logger.info("AltLines payload built in %.2fs — %s%d lines -> %d reported",
                payload["elapsed_s"],
                f"game {resolved_game}: " if resolved_game else "",
                payload["counts"]["lines_priced"],
                payload["counts"]["lines_reported"])
    return payload


def edges_payload(**kwargs) -> dict:
    """Convenience alias used by the FastAPI endpoint."""
    return build_payload(**kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# Slate board (no simulation)
# ══════════════════════════════════════════════════════════════════════════════
# The page needs the list of games BEFORE it can let anyone choose one, and
# simulating the whole league just to draw a grid would be absurd (a full night
# is ~40s of CPU). This endpoint only reads the data's *shape* — which players
# exist, at which books, for which game — so the grid paints immediately.

_SLATE_BOARD_CACHE: dict = {"key": None, "ts": 0.0, "payload": None}


def slate_payload(date: str | None = None,
                  offline: bool = False,
                  demo: bool = True,
                  logs_path: str | None = None,
                  odds_path: str | None = None,
                  seed: int | None = None,
                  n_games: int = 6,
                  refresh: bool = False,
                  direction: int = 0,
                  live: bool = False) -> dict:
    """Today's games (real calendar) + what we can price in each one.

    `live=True` never spends Odds API credits here: it lists the real calendar
    and only marks which games have an event on The Odds API (a free call).
    Props are counted once a game is simulated.

    `direction` is the navigation hint ("‹" sends -1): it decides which way the
    search walks when the requested day has no games, so the arrow the user
    pressed is the direction they get.
    """
    seed = cfg.RANDOM_SEED if seed is None else int(seed)
    key = ("slate", str(date or ""), bool(offline), bool(demo), logs_path,
           odds_path, seed, int(n_games), int(direction), bool(live))
    now = time.time()
    if (not refresh and _SLATE_BOARD_CACHE["payload"] is not None
            and _SLATE_BOARD_CACHE["key"] == key
            and now - _SLATE_BOARD_CACHE["ts"] < _CACHE_TTL):
        payload = dict(_SLATE_BOARD_CACHE["payload"])
        payload["cached"] = True
        payload["cache_age_s"] = round(now - _SLATE_BOARD_CACHE["ts"], 1)
        return payload

    # Local imports keep numpy/pandas/scipy out of the API's import path.
    from altlines.ingest import generate_demo_dataset, load_game_logs, load_odds

    t0 = time.time()
    slate = slate_mod.get_slate(date, offline=False if live else offline,
                                direction=direction)

    if live:
        return _live_slate_payload(slate, t0, seed, int(n_games), refresh)
    if demo:
        logs, odds = generate_demo_dataset(slate=slate, n_games=n_games, seed=seed)
    else:
        logs = load_game_logs(logs_path or cfg.GAME_LOGS_CSV)
        odds = load_odds(odds_path or cfg.ALT_LINES_CSV)

    cols = [c for c in ("player_id", "team") if c in logs.columns]
    mapping = slate_mod.assign_players_to_games(slate["games"], logs[cols])
    # Anything in the data but not in tonight's calendar is appended, not
    # dropped: hiding props would be worse than showing an extra game.
    games = slate_mod.merge_data_games(list(slate["games"]), logs)
    view = slate_mod.build_slate_view({**slate, "games": games}, logs, odds, mapping)
    for g in view["games"]:
        g["est_seconds"] = round(
            g["props"]["player_markets"] * SIM_SECONDS_PER_PLAYER_MARKET + 1.0, 1)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_s": round(time.time() - t0, 2),
        "cached": False,
        "sport": "NBA",
        "data_source": "demo" if demo else "csv",
        "slate_source": view.get("source"),
        "date": view.get("date"),
        "requested_date": view.get("requested_date"),
        "auto_shifted": view.get("auto_shifted"),
        "search_offset_days": view.get("search_offset_days"),
        "params": {"n_iter": cfg.N_SIM_DEFAULT, "seed": seed, "top_pct": 5.0,
                   "devig_method": "multiplicative", "n_games": int(n_games)},
        "markets": [
            {"key": k, "label": v["label"], "family": v["family"]}
            for k, v in cfg.MARKET_SPECS.items()
        ],
        "books": sorted(odds["book"].dropna().astype(str).unique().tolist())
                  if odds is not None and not odds.empty and "book" in odds.columns else [],
        "games": view["games"],
        "n_games": view["n_games"],
        "games_with_props": view["games_with_props"],
        "matched_players": view["matched_players"],
        "assigned_players": view["assigned_players"],
    }

    _SLATE_BOARD_CACHE.update({"key": key, "ts": time.time(), "payload": payload})
    logger.info("Slate board: %d games (%s) in %.2fs", payload["n_games"],
                payload["slate_source"], payload["elapsed_s"])
    return payload


def _live_slate_payload(slate: dict, t0: float, seed: int, n_games: int,
                        refresh: bool) -> dict:
    """The calendar for real mode: no props yet, but honest about which games
    the odds feed covers (listing events is free)."""
    from altlines import live as live_mod

    events, odds_error = [], None
    try:
        events = live_mod.list_odds_events(refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - missing key / network
        odds_error = str(exc)

    view = slate_mod.build_slate_view(slate)
    for g in view["games"]:
        ev = live_mod.match_event(g, events) if events else None
        g["odds_event_id"] = ev["id"] if ev else None
        g["has_odds_event"] = ev is not None
        # Clickable iff the odds feed has the game and it has not finished.
        g["props"] = {**(g.get("props") or {}),
                      "has_props": bool(ev) and g.get("state") != "post"}
        # ~25 player-markets is a typical real game; good enough for the timer.
        g["est_seconds"] = round(25 * SIM_SECONDS_PER_PLAYER_MARKET + 4.0, 1)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_s": round(time.time() - t0, 2),
        "cached": False,
        "sport": "NBA",
        "data_source": "live",
        "odds_error": odds_error,
        "quota": live_mod.quota_status(),
        "credits_per_game": len(cfg.LIVE_MARKETS) * len(cfg.LIVE_ODDS_REGIONS.split(","))
                            * (2 if cfg.LIVE_INCLUDE_MAIN else 1),
        "slate_source": view.get("source"),
        "date": view.get("date"),
        "requested_date": view.get("requested_date"),
        "auto_shifted": view.get("auto_shifted"),
        "search_offset_days": view.get("search_offset_days"),
        "params": {"n_iter": cfg.N_SIM_DEFAULT, "seed": seed, "top_pct": 5.0,
                   "devig_method": "multiplicative", "n_games": n_games},
        "markets": [
            {"key": k, "label": v["label"], "family": v["family"]}
            for k, v in cfg.MARKET_SPECS.items()
        ],
        "books": [],
        "games": view["games"],
        "n_games": view["n_games"],
        "games_with_props": sum(1 for g in view["games"] if g["has_odds_event"]),
        "matched_players": 0,
        "assigned_players": 0,
    }
    return payload


def clear_cache() -> None:
    """Drop every cached payload (used by --refresh or tests)."""
    _CACHE.update({"key": None, "ts": 0.0, "payload": None})
    _SLATE_BOARD_CACHE.update({"key": None, "ts": 0.0, "payload": None})
    slate_mod.clear_slate_cache()
