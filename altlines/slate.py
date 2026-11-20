"""
AltLines — the NBA slate (the "por partido" layer).

The engine simulates player-markets. This module answers the question the UI
asks *first*: **which games are on, and which player-markets belong to each
one?** Without it the tool can only produce one flat league-wide board.

Two sources, merged
-------------------
* **The real calendar.** Fetched from ESPN's public scoreboard endpoint, which
  needs no API key. Gives real matchups, tip-off times, live/final status,
  team tricodes and colours.
* **Your data.** The loaded logs + odds decide which games actually have props
  worth simulating — a real game with no lines in your CSV is shown as
  "sin props", not hidden, so the slate stays honest about coverage.

Matching rule (identical for synthetic and real data)
-----------------------------------------------------
1. A player whose ``team`` equals one of a game's two tricodes is a **real
   match** (this is the path your own data takes).
2. Everything else is assigned **deterministically and balanced** across the
   slate, keeping a player's own team label grouped so teammates land in the
   same game. This is what lets the synthetic demo dataset sit on top of the
   real calendar instead of inventing fake teams of its own.

Both paths are pure functions of the inputs, so the slate a game shows is
reproducible: same date + same data ⇒ same board.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import date as _date_cls
from datetime import datetime, timedelta

import pandas as pd

from altlines.config import RANDOM_SEED

# La "noche NBA" no termina a la medianoche: los partidos de la costa oeste
# empiezan 00:00-01:00 de Argentina. Hasta esta hora de la madrugada "hoy"
# sigue siendo la fecha de ayer, así esos partidos no se van de la lista.
NBA_DAY_CUTOFF_H = 3


def nba_today() -> _date_cls:
    return (datetime.now() - timedelta(hours=NBA_DAY_CUTOFF_H)).date()

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 0. Constants
# ══════════════════════════════════════════════════════════════════════════════

ESPN_SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/"
                   "basketball/nba/scoreboard")

# ESPN's public logo CDN. Fixed URL pattern, so we can render a logo from the
# tricode alone — no extra request and no API key.
LOGO_URL = "https://a.espncdn.com/i/teamlogos/nba/500/{code}.png"

_UA = {"User-Agent": "Mozilla/5.0 (compatible; CherryPick-AltLines/1.0)"}

# Real NBA teams: tricode, city, nickname, primary colour. Used for the offline
# slate and to fill in anything ESPN leaves blank.
NBA_TEAMS: list[tuple[str, str, str, str]] = [
    ("ATL", "Atlanta", "Hawks", "e03a3e"),
    ("BOS", "Boston", "Celtics", "007a33"),
    ("BKN", "Brooklyn", "Nets", "000000"),
    ("CHA", "Charlotte", "Hornets", "1d1160"),
    ("CHI", "Chicago", "Bulls", "ce1141"),
    ("CLE", "Cleveland", "Cavaliers", "860038"),
    ("DAL", "Dallas", "Mavericks", "00538c"),
    ("DEN", "Denver", "Nuggets", "0e2240"),
    ("DET", "Detroit", "Pistons", "c8102e"),
    ("GSW", "Golden State", "Warriors", "1d428a"),
    ("HOU", "Houston", "Rockets", "ce1141"),
    ("IND", "Indiana", "Pacers", "002d62"),
    ("LAC", "LA", "Clippers", "c8102e"),
    ("LAL", "Los Angeles", "Lakers", "552583"),
    ("MEM", "Memphis", "Grizzlies", "5d76a9"),
    ("MIA", "Miami", "Heat", "98002e"),
    ("MIL", "Milwaukee", "Bucks", "00471b"),
    ("MIN", "Minnesota", "Timberwolves", "0c2340"),
    ("NOP", "New Orleans", "Pelicans", "0c2340"),
    ("NYK", "New York", "Knicks", "006bb6"),
    ("OKC", "Oklahoma City", "Thunder", "007ac1"),
    ("ORL", "Orlando", "Magic", "0077c0"),
    ("PHI", "Philadelphia", "76ers", "006bb6"),
    ("PHX", "Phoenix", "Suns", "1d1160"),
    ("POR", "Portland", "Trail Blazers", "e03a3e"),
    ("SAC", "Sacramento", "Kings", "5a2d81"),
    ("SAS", "San Antonio", "Spurs", "c4ced4"),
    ("TOR", "Toronto", "Raptors", "ce1141"),
    ("UTA", "Utah", "Jazz", "002b5c"),
    ("WAS", "Washington", "Wizards", "002b5c"),
]

_TEAMS_BY_TRICODE = {t[0]: t for t in NBA_TEAMS}

# ESPN and nba.com disagree on a few abbreviations. Normalise to the ESPN set
# used above so a real CSV written from another source still joins.
_TRICODE_ALIASES = {
    "GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA",
    "WSH": "WAS", "PHO": "PHX", "BROOKLYN": "BKN", "CHARLOTTE": "CHA",
    "CHICAGO": "CHI", "PHOENIX": "PHX", "SANANTONIO": "SAS",
}


def normalize_tricode(code) -> str:
    """Best-effort tricode normalisation ('' when we cannot tell)."""
    if code is None:
        return ""
    c = str(code).strip().upper().replace(".", "")
    return _TRICODE_ALIASES.get(c, c)


def team_dict(tricode: str, **overrides) -> dict:
    """Team payload for the UI, always with a logo URL and a colour."""
    tr = normalize_tricode(tricode)
    row = _TEAMS_BY_TRICODE.get(tr)
    base = {
        "tricode": tr or "???",
        "city": row[1] if row else "",
        "name": row[2] if row else (tr or "???"),
        "color": (row[3] if row else "5a6b7a"),
    }
    base["display"] = f"{base['city']} {base['name']}".strip()
    base["short"] = base["name"]
    base["logo"] = LOGO_URL.format(code=base["tricode"].lower())
    base["score"] = None
    base.update({k: v for k, v in overrides.items() if v not in (None, "")})
    return base


# ══════════════════════════════════════════════════════════════════════════════
# 1. The live calendar (ESPN, no key)
# ══════════════════════════════════════════════════════════════════════════════

def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_json(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _parse_event(ev: dict) -> dict | None:
    """ESPN event → our game dict. Returns None if either side is missing."""
    comp = (ev.get("competitions") or [{}])[0]
    sides: dict[str, dict | None] = {"home": None, "away": None}
    for c in comp.get("competitors") or []:
        ha = c.get("homeAway")
        if ha not in sides:
            continue
        t = c.get("team") or {}
        sides[ha] = team_dict(
            t.get("abbreviation"),
            city=t.get("location"), name=t.get("name"),
            display=t.get("displayName"),
            color=(t.get("color") or "").lstrip("#").lower() or None,
            logo=t.get("logo") or None,
            score=_int_or_none(c.get("score")),
        )
    if not sides["home"] or not sides["away"]:
        return None

    st = ev.get("status") or {}
    stype = st.get("type") or {}
    tip = ev.get("date") or ""
    venue = comp.get("venue") or ev.get("venue") or {}
    return {
        "game_id": str(ev.get("id") or f"{tip}-{sides['away']['tricode']}"),
        "date": tip[:10],
        "tip_off": tip,
        "status": stype.get("description") or stype.get("detail") or "",
        "state": stype.get("state") or "pre",       # pre | in | post
        "period": st.get("period") or 0,
        "clock": st.get("displayClock") or "",
        # ESPN nests the venue inside the competition, not on the event.
        "venue": venue.get("fullName") or "",
        "home": sides["home"],
        "away": sides["away"],
    }


def fetch_espn_slate(date_str: str | None = None, search_days: int = 7,
                     timeout: float = 10.0, direction: int = 0) -> dict | None:
    """Real NBA slate for `date_str` (YYYY-MM-DD), else the nearest date with games.

    Walks outward from the requested date (±1, ±2 …) so a quiet night still
    lands on a real board instead of an empty screen, and reports the shift in
    `auto_shifted` so the UI can say so out loud.

    `direction` steers the walk when the user is *navigating*:

    - `0`  → `[0, +1, -1, +2, -2, …]`. The default: a quiet night lands on the
      upcoming board, which is the useful one to bet.
    - `-1` → the whole past first (`[0, -1, -2, … -7, +1, …]`), then the future.
      The user pressed "‹": they want the previous slate with games, however far
      back it is. Interleaving ±k is not enough here — from a date whose only
      nearby games are *afterwards*, "‹" would still jump forward and the button
      would look broken.
    - `+1` → the mirror image (`[0, +1, +2, …]` then the past).

    Returns None when the network fails — the caller then falls back to the
    deterministic offline slate. A *successful* empty day is not a failure: it
    just keeps walking.
    """
    base = _parse_date(date_str) or nba_today()

    span = max(int(search_days), 0)
    if direction < 0:
        offsets = [0] + [-k for k in range(1, span + 1)] + [k for k in range(1, span + 1)]
    elif direction > 0:
        offsets = [0] + [k for k in range(1, span + 1)] + [-k for k in range(1, span + 1)]
    else:
        offsets = [0]
        for k in range(1, span + 1):
            offsets.extend([k, -k])

    for off in offsets:
        day = base + timedelta(days=off)
        url = f"{ESPN_SCOREBOARD}?dates={day:%Y%m%d}"
        try:
            payload = _get_json(url, timeout=timeout)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            logger.warning("ESPN slate fetch failed (%s): %s", day, exc)
            return None
        games = [g for g in (_parse_event(e) for e in payload.get("events") or [])
                 if g]
        if games:
            games.sort(key=lambda g: (g.get("tip_off") or ""))
            return {
                "date": f"{day:%Y-%m-%d}",
                "requested_date": f"{base:%Y-%m-%d}",
                "games": games,
                "source": "espn",
                "auto_shifted": off != 0,
                "search_offset_days": off,
            }
    return None


def _parse_date(value) -> _date_cls | None:
    if value is None or value == "":
        return None
    if isinstance(value, _date_cls):
        return value
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 2. Offline slate (deterministic, no network)
# ══════════════════════════════════════════════════════════════════════════════

def demo_slate(date_str: str | None = None, n_games: int = 6,
               seed: int = RANDOM_SEED) -> dict:
    """A realistic offline slate: real teams, plausible tip-off times.

    Used by the CLI without network and as the fallback when ESPN is
    unreachable. Fully deterministic for a given seed.
    """
    day = _parse_date(date_str) or nba_today()
    rng = random.Random(f"{seed}:{day:%Y%m%d}")

    pool = [t[0] for t in NBA_TEAMS]
    rng.shuffle(pool)
    n_games = max(1, min(int(n_games), len(pool) // 2))

    # Tip-offs spread like a real night: 19:00 → 22:30 ET-ish, every ~35 min.
    start = datetime.combine(day, datetime.min.time())
    games = []
    for i in range(n_games):
        away, home = pool[2 * i], pool[2 * i + 1]
        tip = start + timedelta(hours=19, minutes=35 * i)
        games.append({
            "game_id": f"DEMO-{day:%Y%m%d}-G{i + 1:02d}",
            "date": f"{day:%Y-%m-%d}",
            "tip_off": tip.isoformat(timespec="seconds"),
            "status": "Programado",
            "state": "pre",
            "period": 0,
            "clock": "",
            "venue": f"{_TEAMS_BY_TRICODE[home][1]} Arena",
            "home": team_dict(home),
            "away": team_dict(away),
        })
    return {
        "date": f"{day:%Y-%m-%d}",
        "requested_date": f"{day:%Y-%m-%d}",
        "games": games,
        "source": "demo",
        "auto_shifted": False,
        "search_offset_days": 0,
    }


# ── Slate cache ───────────────────────────────────────────────────────────────
# The scoreboard is cheap but not free, and the page asks for it on every
# navigation. 15 minutes is longer than a typical browsing session and shorter
# than the gap between "Scheduled" and "Final".
_SLATE_CACHE: dict = {}
_SLATE_LOCK = threading.Lock()
_SLATE_TTL = 900.0


def get_slate(date_str: str | None = None, offline: bool = False,
              force: bool = False, ttl: float = _SLATE_TTL,
              direction: int = 0) -> dict:
    """Slate for a date, cached, with an automatic offline fallback.

    `direction` only affects which way the ±7-day search walks (see
    `fetch_espn_slate`) and therefore belongs in the cache key: pressing "‹" from
    a day with no games must not reuse the "›" answer.
    """
    key = (str(date_str or ""), bool(offline), int(direction))
    now = time.time()
    with _SLATE_LOCK:
        hit = _SLATE_CACHE.get(key)
        if hit and not force and now - hit["ts"] < ttl:
            slate = dict(hit["slate"])
            slate["cached"] = True
            return slate

    slate = None if offline else fetch_espn_slate(date_str, direction=direction)
    if slate is None:
        slate = demo_slate(date_str)
        slate["source"] = "offline" if offline else "offline-fallback"
    slate["cached"] = False

    with _SLATE_LOCK:
        _SLATE_CACHE[key] = {"ts": now, "slate": slate}
    logger.info("Slate %s: %d games (%s)", slate["date"],
                len(slate["games"]), slate["source"])
    return slate


def clear_slate_cache() -> None:
    with _SLATE_LOCK:
        _SLATE_CACHE.clear()


# ══════════════════════════════════════════════════════════════════════════════
# 3. Player → game
# ══════════════════════════════════════════════════════════════════════════════

GAME_MAP_COLUMNS = ["player_id", "game_id", "team", "opponent", "home_away",
                    "matched"]


def assign_players_to_games(games: list[dict], players) -> pd.DataFrame:
    """Map every player onto a game of the slate.

    `players` is a DataFrame (or list of dicts) with at least `player_id` and
    `team`. Real matches (tricode found in the slate) win; the rest are
    distributed deterministically so that every game gets props to simulate.

    The `matched` flag tells you which path a player took, which is what lets
    the UI be honest: "props sintéticos asignados" vs "línea real".
    """
    if players is None:
        return pd.DataFrame(columns=GAME_MAP_COLUMNS)
    if isinstance(players, pd.DataFrame):
        if players.empty or "player_id" not in players.columns:
            return pd.DataFrame(columns=GAME_MAP_COLUMNS)
        cols = [c for c in ("player_id", "team") if c in players.columns]
        frame = players[cols].drop_duplicates("player_id").copy()
        if "team" not in frame.columns:
            frame["team"] = None
    else:
        frame = pd.DataFrame(list(players))
        if frame.empty or "player_id" not in frame.columns:
            return pd.DataFrame(columns=GAME_MAP_COLUMNS)
        if "team" not in frame.columns:
            frame["team"] = None
        frame = frame.drop_duplicates("player_id")

    # tricode → (game_id, side, tricode, opponent tricode)
    by_tricode: dict[str, tuple[str, str, str, str]] = {}
    for g in games:
        for side, other in (("home", "away"), ("away", "home")):
            tr = normalize_tricode((g.get(side) or {}).get("tricode"))
            if tr and tr not in by_tricode:
                by_tricode[tr] = (g["game_id"], side, tr,
                                  normalize_tricode((g.get(other) or {}).get("tricode")))

    rows: list[dict] = []
    unmatched: list[tuple[str, str]] = []
    for pid, team in zip(frame["player_id"].astype(str), frame["team"]):
        hit = by_tricode.get(normalize_tricode(team))
        if hit:
            gid, side, tr, opp = hit
            rows.append({"player_id": pid, "game_id": gid, "team": tr,
                         "opponent": opp, "home_away": int(side == "home"),
                         "matched": True})
        else:
            unmatched.append((pid, normalize_tricode(team) or "SIN-EQUIPO"))

    if unmatched and games:
        # Keep a team label together so teammates share a game, then spread the
        # labels across the slate's slots as evenly as the slate allows.
        slots = [(g["game_id"], side) for g in games for side in ("home", "away")]
        by_game = {g["game_id"]: g for g in games}
        labels = sorted({lab for _, lab in unmatched})
        slot_of_label = {lab: slots[i % len(slots)] for i, lab in enumerate(labels)}
        for pid, lab in unmatched:
            gid, side = slot_of_label[lab]
            g = by_game[gid]
            tr = normalize_tricode((g[side] or {}).get("tricode"))
            other = "away" if side == "home" else "home"
            rows.append({"player_id": pid, "game_id": gid, "team": tr,
                         "opponent": normalize_tricode((g[other] or {}).get("tricode")),
                         "home_away": int(side == "home"), "matched": False})

    return pd.DataFrame(rows, columns=GAME_MAP_COLUMNS)


# ══════════════════════════════════════════════════════════════════════════════
# 4. The slate the UI renders
# ══════════════════════════════════════════════════════════════════════════════

def build_slate_view(slate: dict, logs: pd.DataFrame | None = None,
                     odds: pd.DataFrame | None = None,
                     mapping: pd.DataFrame | None = None) -> dict:
    """Combine the calendar with your data's coverage, one entry per game.

    Each game carries what the card shows *before* anything is simulated:
    how many player-markets we have, how many priced lines, at which books.
    """
    games = [dict(g) for g in (slate.get("games") or [])]
    if mapping is None:
        mapping = pd.DataFrame(columns=GAME_MAP_COLUMNS)

    logs = logs if logs is not None else pd.DataFrame()
    odds = odds if odds is not None else pd.DataFrame()

    # player_id → game_id
    pmap = {}
    if not mapping.empty:
        pmap = dict(zip(mapping["player_id"].astype(str), mapping["game_id"]))

    def _players_in(gid: str) -> set[str]:
        return {pid for pid, g in pmap.items() if g == gid}

    logs_players = pmap and set()
    for g in games:
        gid = g["game_id"]
        pids = _players_in(gid)

        n_players = len(pids)
        n_markets = 0
        n_lines = 0
        books: list[str] = []

        if pids and not logs.empty and "player_id" in logs.columns:
            sub = logs[logs["player_id"].astype(str).isin(pids)]
            n_markets = int(sub.drop_duplicates(["player_id", "market"]).shape[0]) \
                if "market" in sub.columns else 0
        if pids and not odds.empty and "player_id" in odds.columns:
            sub_o = odds[odds["player_id"].astype(str).isin(pids)]
            n_lines = int(len(sub_o))
            if "book" in sub_o.columns:
                books = sorted(sub_o["book"].dropna().astype(str).unique().tolist())

        g["props"] = {
            "players": n_players,
            "player_markets": n_markets,
            "lines": n_lines,
            "books": books,
            "has_props": n_markets > 0,
        }
        g["simulated"] = False          # the UI flips this once it has results
    return {
        "date": slate.get("date"),
        "requested_date": slate.get("requested_date"),
        "source": slate.get("source"),
        "auto_shifted": bool(slate.get("auto_shifted")),
        "search_offset_days": slate.get("search_offset_days", 0),
        "cached": bool(slate.get("cached")),
        "games": games,
        "n_games": len(games),
        "games_with_props": sum(1 for g in games if g["props"]["has_props"]),
        "matched_players": int(mapping["matched"].sum()) if not mapping.empty else 0,
        "assigned_players": int((~mapping["matched"]).sum()) if not mapping.empty else 0,
    }


def merge_data_games(games: list[dict], logs: pd.DataFrame) -> list[dict]:
    """Add to the slate any game that exists in the data but not in the calendar.

    Real data can carry a `game_id` the calendar does not know (an older date,
    a CSV export). Dropping it would silently hide props, so it is appended
    with a synthetic entry instead.
    """
    if logs is None or logs.empty or "game_id" not in logs.columns:
        return games
    known = {g["game_id"] for g in games}
    extra: list[dict] = []
    for gid, grp in logs.dropna(subset=["game_id"]).groupby("game_id"):
        gid = str(gid)
        if gid in known:
            continue
        home_away = grp["home_away"].iloc[-1] if "home_away" in grp else 1
        team = normalize_tricode(grp["team"].iloc[-1]) if "team" in grp else ""
        opp = normalize_tricode(grp["opponent"].iloc[-1]) if "opponent" in grp else ""
        home, away = (team, opp) if int(home_away or 0) else (opp, team)
        extra.append({
            "game_id": gid,
            "date": str(grp["date"].iloc[-1])[:10] if "date" in grp else None,
            "tip_off": None,
            "status": "Del dataset",
            "state": "pre",
            "period": 0,
            "clock": "",
            "venue": "",
            "home": team_dict(home),
            "away": team_dict(away),
            "from_data": True,
        })
    return games + extra


def game_ids_in(slate: dict) -> list[str]:
    """Just the ids, in tip-off order (handy for the CLI and for tests)."""
    return [g["game_id"] for g in (slate.get("games") or [])]


def find_game(slate: dict, game_id: str) -> dict | None:
    """Find a game by ESPN id, or by a human handle: `BOS@DET`, `DET-BOS`, `BOS`.

    The numeric id is what the API and the data carry, but a person on the
    command line types `--game BOS@DET`. Both have to work, and the order of the
    two tricodes should not matter (nobody remembers who is home).
    """
    needle = str(game_id or "").strip().upper()
    if not needle:
        return None
    games = slate.get("games") or []
    for g in games:                                    # exact id wins
        if str(g["game_id"]) == str(game_id):
            return g
    parts = [p for p in needle.replace("@", "-").split("-") if p]
    for g in games:
        away = str(g["away"]["tricode"]).upper()
        home = str(g["home"]["tricode"]).upper()
        if len(parts) == 1 and parts[0] in (away, home):
            return g
        if len(parts) == 2 and {parts[0], parts[1]} == {away, home} \
                and parts[0] != parts[1]:
            return g
    return None
