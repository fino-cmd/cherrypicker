"""
AltLines — game context: rest, home/away, opponent, teammates out.

The engine projects a player from his own logs only. That is the right SHAPE
but it ignores tonight: a back-to-back, the road, a rival that gives up
rebounds, a starting guard listed OUT. This module turns that context into ONE
number per (player, market) — a multiplier of the projected volume/level that
`simulation.project_player(level_mult=…)` applies to `vol_mu` and the level
(variance scales with it: var ∝ mean, the same law the league prior and
`anchor_to_line` use).

Every effect is measured the same way, on the cached ESPN logs (0 Odds API
credits): for each player-game, `value ÷ baseline`, where the baseline is the
player's own mean over his previous 20 games. Pooled over players, that ratio
by opponent / rest / home says how much tonight's context moves a player away
from his recent level — which is exactly what the engine's level is.

  rest  league effect by rest category (b2b, 1 day, 2+), then the player's own
        ratio shrunk toward it (`CTX_K_PLAYER` games);
  home  same, home vs road;
  opp   the opponent's pooled ratio for that market (pace AND defense in one),
        shrunk toward 1 with `CTX_K_OPP` player-games; last season counts
        `CTX_PREV_SEASON_W`;
  inj   rotation teammates OUT → their typical minutes are redistributed
        (position first, then everyone) among the active rotation, weighted by
        minutes × headroom; the player's multiplier is (min + boost) / min.

All lookups use only games strictly BEFORE the target date, so the walk-forward
backtest (`tools/backtest.py --variants …`) never sees the future. The live
pipeline builds the same tables from the same cache.
"""

from __future__ import annotations

import json
import logging
import re
import time

import numpy as np
import pandas as pd

from altlines import config as cfg

logger = logging.getLogger(__name__)

MARKETS = ("points", "rebounds", "assists", "threes", "pra")
_BASE_WINDOW, _BASE_MIN = 20, 8


def _cache_dir():
    return cfg.DATA_DIR / "cache"


def _day(d) -> int:
    """Date-like → integer day number (fast searchsorted keys)."""
    return int(np.datetime64(pd.Timestamp(d).date(), "D").astype(np.int64))


def rest_category(days) -> str:
    if days is None or not np.isfinite(days) or days >= 3:
        return "2d+"
    return "b2b" if days <= 1 else "1d"


def market_value(g, market: str) -> float:
    if market == "pra":
        return float(g["pts"] + g["reb"] + g["ast"])
    return float(g[{"points": "pts", "rebounds": "reb", "assists": "ast",
                    "threes": "tpm"}[market]])


# ══════════════════════════════════════════════════════════════════════════════
# 1. Data: every cached ESPN game log
# ══════════════════════════════════════════════════════════════════════════════

def load_positions() -> dict[str, str]:
    out = {}
    for p in (_cache_dir() / "roster").glob("*.json"):
        try:
            for a in json.loads(p.read_text(encoding="utf-8")):
                out.setdefault(str(a["athlete_id"]), a.get("position") or "")
        except Exception:  # noqa: BLE001 - a corrupt cache file is just a miss
            continue
    return out


def load_cached_games(seasons: list[int] | None = None) -> pd.DataFrame:
    """All cached gamelogs (regular season, minutes > 0), one row per player-game."""
    from altlines.slate import normalize_tricode
    rows = []
    for p in (_cache_dir() / "gamelog").glob("*.json"):
        aid, _, season = p.stem.rpartition("_")
        if not season.isdigit() or (seasons and int(season) not in seasons):
            continue
        try:
            games = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for g in games or []:
            if g.get("playoffs") or not g.get("date") or not g.get("min") or g["min"] <= 0:
                continue
            rows.append({"athlete_id": aid, "season": int(season),
                         "event_id": str(g["event_id"]), "date": g["date"],
                         "team": normalize_tricode(g.get("team")),
                         "opponent": normalize_tricode(g.get("opponent")),
                         "home": int(g.get("home_away") or 0), "min": float(g["min"]),
                         "pts": g["pts"], "reb": g["reb"], "ast": g["ast"], "tpm": g["tpm"]})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["day"] = df["date"].values.astype("datetime64[D]").astype(np.int64)
    df = (df.dropna(subset=["pts", "reb", "ast", "tpm"])
            .drop_duplicates(["athlete_id", "event_id"])
            .sort_values(["athlete_id", "date"]).reset_index(drop=True))
    df["pra"] = df["pts"] + df["reb"] + df["ast"]
    pos = load_positions()
    df["position"] = df["athlete_id"].map(pos).fillna("")
    return df


def ratio_table(games: pd.DataFrame) -> pd.DataFrame:
    """Long frame: one row per (player-game, market) with value and baseline
    (= the player's mean over his previous 20 games, ≥ 8 needed)."""
    g = games.copy()
    g["rest_days"] = g.groupby("athlete_id")["day"].diff()
    g["rest"] = [rest_category(d) for d in g["rest_days"].to_numpy(float)]
    col = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "tpm", "pra": "pra"}
    parts = []
    for m in MARKETS:
        base = (g.groupby("athlete_id")[col[m]]
                 .transform(lambda s: s.shift(1).rolling(_BASE_WINDOW, min_periods=_BASE_MIN).mean()))
        part = g[["athlete_id", "season", "day", "opponent", "home", "rest"]].copy()
        part["market"], part["v"], part["b"] = m, g[col[m]].to_numpy(float), base.to_numpy(float)
        parts.append(part[np.isfinite(part["b"]) & (part["b"] > 0)])
    return pd.concat(parts, ignore_index=True)


class _Cum:
    """Σv, Σb and n per key and season, queried strictly before a day.

    The current season counts fully up to the day; the previous season counts
    whole, with weight `prev_w`.
    """

    def __init__(self, rt: pd.DataFrame, keys: list[str]):
        self.idx: dict[tuple, tuple] = {}
        for k, g in rt.groupby(keys + ["season"], sort=False):
            g = g.sort_values("day")
            self.idx[k if isinstance(k, tuple) else (k,)] = (
                g["day"].to_numpy(np.int64),
                np.concatenate([[0.0], np.cumsum(g["v"].to_numpy(float))]),
                np.concatenate([[0.0], np.cumsum(g["b"].to_numpy(float))]))

    def get(self, key: tuple, season: int, day: int, prev_w: float) -> tuple[float, float, float]:
        sv = sb = n = 0.0
        cur = self.idx.get((*key, season))
        if cur is not None:
            i = int(np.searchsorted(cur[0], day, "left"))
            sv, sb, n = cur[1][i], cur[2][i], float(i)
        prev = self.idx.get((*key, season - 1))
        if prev is not None and prev_w > 0:
            sv += prev_w * prev[1][-1]
            sb += prev_w * prev[2][-1]
            n += prev_w * len(prev[0])
        return sv, sb, n


def _shrink(raw: float, prior: float, n: float, k: float) -> float:
    if not np.isfinite(raw):
        return prior
    return prior + n / (n + k) * (raw - prior)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Rest / home / opponent
# ══════════════════════════════════════════════════════════════════════════════

class ContextModel:
    """Pooled ratio tables + per-player history, all queried as of a day."""

    def __init__(self, games: pd.DataFrame):
        rt = ratio_table(games)
        self.lg = _Cum(rt, ["market"])
        self.opp = _Cum(rt, ["market", "opponent"])
        self.rest = _Cum(rt, ["market", "rest"])
        self.home = _Cum(rt, ["market", "home"])
        self.player = {(aid, m): g.sort_values("day")
                       for (aid, m), g in rt.groupby(["athlete_id", "market"], sort=False)}

    def _league_ratio(self, cum: _Cum, key: tuple, market: str, season: int, day: int) -> tuple[float, float]:
        w = cfg.CTX_PREV_SEASON_W
        sv, sb, n = cum.get((market, *key), season, day, w)
        lv, lb, _ = self.lg.get((market,), season, day, w)
        if sb <= 0 or lb <= 0 or lv <= 0:
            return 1.0, 0.0
        return (sv / sb) / (lv / lb), n

    def _player_ratio(self, aid: str, market: str, day: int, col: str, val) -> tuple[float, float]:
        g = self.player.get((aid, market))
        if g is None:
            return np.nan, 0.0
        g = g[g["day"] < day]
        if len(g) < 10:
            return np.nan, 0.0
        tot = g["v"].sum() / g["b"].sum()
        s = g[g[col] == val]
        if s.empty or s["b"].sum() <= 0 or tot <= 0:
            return np.nan, 0.0
        return (s["v"].sum() / s["b"].sum()) / tot, float(len(s))

    def factors(self, aid: str, market: str, season: int, day: int, opponent: str,
                home: int, rest_days: float | None) -> dict:
        rest = rest_category(rest_days)
        out = {}
        lr, _ = self._league_ratio(self.rest, (rest,), market, season, day)
        pr, pn = self._player_ratio(aid, market, day, "rest", rest)
        out["rest"] = _shrink(pr, lr, pn, cfg.CTX_K_PLAYER)
        lh, _ = self._league_ratio(self.home, (int(home),), market, season, day)
        ph, hn = self._player_ratio(aid, market, day, "home", int(home))
        out["home"] = _shrink(ph, lh, hn, cfg.CTX_K_PLAYER)
        ro, n = self._league_ratio(self.opp, (opponent,), market, season, day)
        out["opp"] = _shrink(ro, 1.0, n, cfg.CTX_K_OPP)
        return out


def combine(f: dict, use: tuple[str, ...]) -> float:
    m = 1.0
    for k in use:
        m *= f.get(k, 1.0)
    return float(np.clip(m, cfg.CTX_MULT_MIN, cfg.CTX_MULT_MAX))


# ══════════════════════════════════════════════════════════════════════════════
# 3. Teammates out → minutes redistribution
# ══════════════════════════════════════════════════════════════════════════════

def redistribute(out: list[tuple[float, str]], active: list[tuple[str, float, str]],
                 target: str, redist: float | None = None) -> float:
    """Extra minutes `target` gets when the `out` players (typical minutes,
    position) do not play.

    The freed minutes go `INJ_SAME_POS_FRAC` to the active players of the same
    position and the rest to everybody, each pool split ∝ minutes × headroom
    (a 36-minute starter has little room left; a 16-minute backup has a lot).
    Only `INJ_REDIST` of them reach the rotation: the rest go to deep bench
    that is not in `active`.
    """
    redist = cfg.INJ_REDIST if redist is None else redist
    if not out or redist <= 0:
        return 0.0

    def w(m):
        return m * max(0.0, 1.0 - m / cfg.INJ_MAX_MIN)

    tgt = next((a for a in active if a[0] == target), None)
    if tgt is None:
        return 0.0
    same = cfg.INJ_SAME_POS_FRAC
    pos_pool: dict[str, float] = {}
    for _, m, p in active:
        pos_pool[p] = pos_pool.get(p, 0.0) + w(m)
    pool_all = sum(pos_pool.values())
    boost = common = 0.0
    for m, pos in out:
        if pos_pool.get(pos, 0.0) > 0:
            if tgt[2] == pos:
                boost += same * m * w(tgt[1]) / pos_pool[pos]
            common += (1.0 - same) * m
        else:                       # nobody of that position: all of it is common
            common += m
    if pool_all > 0:
        boost += common * w(tgt[1]) / pool_all
    return redist * boost


class TeamBook:
    """Who played for whom, to rebuild 'teammate X was out' for past games."""

    def __init__(self, games: pd.DataFrame):
        self.by_player = {}
        for aid, g in games.groupby("athlete_id", sort=False):
            g = g.sort_values("day")
            self.by_player[aid] = (g["day"].to_numpy(np.int64), g["team"].to_numpy(object),
                                   g["min"].to_numpy(float), g["event_id"].to_numpy(object),
                                   g["season"].to_numpy(int), (g["position"].iloc[0] or "F"))
        self.team_players: dict[tuple[str, int], set[str]] = {}
        for (team, season), g in games.groupby(["team", "season"], sort=False):
            self.team_players[(team, int(season))] = set(g["athlete_id"])
        self.event_players = games.groupby("event_id")["athlete_id"].agg(set).to_dict()

    def _recent(self, aid: str, day: int, n: int = 10):
        d, t, mins, _, _, _ = self.by_player[aid]
        i = int(np.searchsorted(d, day, "left"))
        return d[max(0, i - n):i], t[max(0, i - n):i], mins[max(0, i - n):i], i

    def game_context(self, aid: str, team: str, season: int, day: int, event_id: str,
                     recent_cut: int) -> tuple[list, list]:
        """(out, active) for player `aid` in the game `event_id` of `team`.

        out    = rotation teammates (≥ 5 of their last 10 games, mean ≥
                 `INJ_OUT_MIN` min), last seen with `team`, still on it later in
                 the season, recent (played on/after `recent_cut`, the day of the
                 player's 3rd-to-last game) and NOT in this game;
        active = rotation players (mean ≥ `INJ_ACTIVE_MIN`) who did play it.
        """
        played = self.event_players.get(event_id, set())
        out, active = [], []
        for q in self.team_players.get((team, season), ()):
            if q not in self.by_player:
                continue
            d, t, mins, i = self._recent(q, day)
            if len(d) < 5 or t[-1] != team:
                continue
            typ = float(mins.mean())
            pos = self.by_player[q][5]
            if q in played or q == aid:
                if typ >= cfg.INJ_ACTIVE_MIN or q == aid:
                    active.append((q, typ, pos))
                continue
            if typ < cfg.INJ_OUT_MIN or d[-1] < recent_cut:
                continue
            fd, ft, _, _, fs, _ = self.by_player[q]
            later = (fd[i:] > day) & (ft[i:] == team) & (fs[i:] == season)
            if later.any():
                out.append((typ, pos))
        return out, active


# ══════════════════════════════════════════════════════════════════════════════
# 4. ESPN injury report (real mode)
# ══════════════════════════════════════════════════════════════════════════════

_ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
_ID_RE = re.compile(r"/id/(\d+)")


def game_injuries(event_id: str) -> list[dict]:
    """The injury list ESPN attaches to one game's summary (for replays of a
    finished night: the league report has moved on by then)."""
    from altlines import live
    from altlines.slate import normalize_tricode
    cached = live._cache_get("injuries", f"game_{event_id}", None)
    if cached is not None:
        return cached
    data = live._get_json("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary",
                          params={"event": event_id}).json()
    out = []
    for team in data.get("injuries") or []:
        tr = normalize_tricode((team.get("team") or {}).get("abbreviation"))
        for inj in team.get("injuries") or []:
            a = inj.get("athlete") or {}
            m = next((_ID_RE.search(str(ln.get("href") or "")) for ln in a.get("links") or []
                      if _ID_RE.search(str(ln.get("href") or ""))), None)
            if m:
                out.append({"athlete_id": m.group(1), "name": a.get("displayName") or "",
                            "team": tr, "position": "", "status": str(inj.get("status") or "")})
    live._cache_put("injuries", f"game_{event_id}", out)
    return out


def fetch_injuries(refresh: bool = False) -> list[dict]:
    """League-wide ESPN injury report: [{athlete_id, name, team, position, status}].

    Public endpoint, no key; cached `INJ_REPORT_TTL_S`.
    """
    from altlines import live
    from altlines.slate import normalize_tricode
    cached = None if refresh else live._cache_get("injuries", "nba", cfg.INJ_REPORT_TTL_S)
    if cached is not None:
        return cached
    try:
        data = live._get_json(_ESPN_INJURIES).json()
    except Exception as exc:  # noqa: BLE001 - network boundary
        logger.warning("ESPN injuries failed: %s", exc)
        return live._cache_get("injuries", "nba", None) or []
    out = []
    for team in data.get("injuries") or []:
        for inj in team.get("injuries") or []:
            a = inj.get("athlete") or {}
            aid = None
            for ln in a.get("links") or []:
                m = _ID_RE.search(str(ln.get("href") or ""))
                if m:
                    aid = m.group(1)
                    break
            if not aid:
                continue
            out.append({"athlete_id": aid, "name": a.get("displayName") or "",
                        "team": normalize_tricode((a.get("team") or {}).get("abbreviation")),
                        "position": ((a.get("position") or {}).get("abbreviation")) or "",
                        "status": str(inj.get("status") or ""),
                        "date": inj.get("date")})
    live._cache_put("injuries", "nba", out)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 5. Real mode: one multiplier per (player, market) for tonight
# ══════════════════════════════════════════════════════════════════════════════

_MODEL_CACHE: dict = {"ts": 0.0, "season": None, "model": None}


def _model_for(season: int) -> ContextModel | None:
    if (_MODEL_CACHE["model"] is not None and _MODEL_CACHE["season"] == season
            and time.time() - _MODEL_CACHE["ts"] < cfg.LIVE_LOGS_TTL_S):
        return _MODEL_CACHE["model"]
    games = load_cached_games([season - 1, season])
    if games.empty:
        return None
    model = ContextModel(games)
    _MODEL_CACHE.update(ts=time.time(), season=season, model=model)
    return model


def _abbr(team) -> str | None:
    return team.get("abbreviation") if isinstance(team, dict) else team


def _pos(p: str) -> str:
    p = (p or "F").upper()
    return p[0] if p[0] in "GFC" else "F"


def live_multipliers(players: list[dict], tonight: str, season: int,
                     home_team: str, away_team: str,
                     injuries: list[dict] | None = None) -> list[dict]:
    """Context multipliers for the players of ONE game.

    `players`: [{player_id, athlete_id, team, position, games}] where `games`
    are that player's raw ESPN games (both seasons). `injuries` overrides the
    league report (a replay passes the game's own list). Returns one dict per
    (player, market) with the factors and the final `mult` the engine applies.
    """
    use = tuple(k for k, on in (("rest", cfg.CONTEXT_REST), ("home", cfg.CONTEXT_HOME),
                                ("opp", cfg.CONTEXT_OPP)) if on)
    if not use and not cfg.CONTEXT_INJURIES:
        return []
    from altlines import live
    day = _day(tonight)
    model = _model_for(season) if use else None
    if injuries is None:
        injuries = fetch_injuries() if cfg.CONTEXT_INJURIES else []
    elif not cfg.CONTEXT_INJURIES:
        injuries = []
    out_by_team: dict[str, list[dict]] = {}
    for inj in injuries:
        if inj["status"].lower() == "out":
            out_by_team.setdefault(inj["team"], []).append(inj)

    # Active rotation of each team that has someone OUT (ESPN logs, free, cached).
    rotations: dict[str, tuple[list, list]] = {}
    for team in (home_team, away_team):
        outs = out_by_team.get(team) or []
        if not outs:
            continue
        out_ids = {o["athlete_id"] for o in outs}
        known = {p["athlete_id"]: p for p in players if p["team"] == team}
        active, out_list = [], []
        for a in live.fetch_roster(team):
            aid = a["athlete_id"]
            games = (known[aid]["games"] if aid in known else
                     live.fetch_athlete_games(aid, season, current=True)
                     + live.fetch_athlete_games(aid, season - 1, current=False))
            games = sorted((g for g in games if g.get("min") and g["min"] > 0
                            and g.get("date") and g["date"] < tonight[:10]
                            and not g.get("playoffs")),
                           key=lambda g: g["date"])[-10:]
            if len(games) < 5:
                continue
            typ = float(np.mean([g["min"] for g in games]))
            if aid in out_ids:
                # Same rule as the backtest: his minutes must have been THIS
                # team's (a newcomer's absence takes nothing from anyone's history).
                from altlines.slate import normalize_tricode
                if typ >= cfg.INJ_OUT_MIN and normalize_tricode(_abbr(games[-1].get("team"))) == team:
                    out_list.append((typ, _pos(a["position"]), a["name"], games[-1]["date"]))
            elif typ >= cfg.INJ_ACTIVE_MIN:
                active.append((aid, typ, _pos(a["position"])))
        rotations[team] = (out_list, active)

    rows = []
    for p in players:
        # Strictly before tonight: a backfill of a past night must not see it.
        games = sorted((g for g in p["games"] if g.get("min") and g["min"] > 0
                        and g.get("date") and g["date"] < tonight[:10]
                        and not g.get("playoffs")), key=lambda g: g["date"])
        if not games:
            continue
        rest_days = day - _day(games[-1]["date"])
        home = int(p["team"] == home_team)
        opponent = away_team if home else home_team
        inj_mult, inj_names = 1.0, []
        if p["team"] in rotations:
            out_list, active = rotations[p["team"]]
            # Same recency rule as the backtest: an absence the player's recent
            # games already reflect is not news.
            cut = games[-3]["date"] if len(games) >= 3 else games[0]["date"]
            outs = [(m, pos) for m, pos, _, last in out_list if last >= cut]
            inj_names = [nm for _, _, nm, last in out_list if last >= cut]
            typ = float(np.mean([g["min"] for g in games[-10:]]))
            if outs and typ > 0:
                act = list(active)
                if not any(a[0] == p["athlete_id"] for a in act):
                    act.append((p["athlete_id"], typ, _pos(p["position"])))
                boost = redistribute(outs, act, p["athlete_id"])
                inj_mult = float(np.clip((typ + boost) / typ, 1.0, cfg.INJ_MULT_MAX))
        for market in MARKETS:
            f = (model.factors(p["athlete_id"], market, season, day, opponent, home, rest_days)
                 if model is not None else {})
            ctx = combine(f, use) if use else 1.0
            rows.append({"player_id": p["player_id"], "market": market,
                         "rest": round(f.get("rest", 1.0), 4), "home": round(f.get("home", 1.0), 4),
                         "opp": round(f.get("opp", 1.0), 4), "inj": round(inj_mult, 4),
                         "teammates_out": inj_names,
                         "mult": round(ctx * inj_mult, 4)})
    return rows
