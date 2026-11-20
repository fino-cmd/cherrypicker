"""
Futbol — la jornada: panorama de remates de cada partido y picks.

    python -m futbol.run [--leagues arg,bra,eng] [--days 4] [--refresh-odds] [--no-lineups]
    python -m futbol.run --check-lineups      # re-simula los partidos que confirmaron formación

Por partido:
  • remates y al arco esperados de cada equipo (y su rango 10–90 %);
  • la formación: CONFIRMADA (ESPN, ~1 h antes) o el XI ESPERADO (futbol.lineups),
    con esquema y probabilidad de titularidad de cada jugador;
  • por jugador: P(remates ≥ 1, 2, 3, 4) y P(al arco ≥ 1, 2, 3), cuota justa
    y CUOTA MÍNIMA; las cuotas de bet365 AR, Betano y Betsson (OddsPapi).

Cuando un partido confirma formación, `check_lineups()` lo vuelve a simular
SOLO a él con la formación real (gratis: no pide cuotas nuevas).

Salida: consola + data/futbol/jornada.json (lo lee la app).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from futbol import config as cfg
from futbol import apifootball as af
from futbol import espn
from futbol import highlightly as hl
from futbol import lineups as lu
from futbol import market as mkt
from futbol import model as fm
from futbol import oddspapi as op
from futbol import photos
from futbol import picklog
from futbol import pitchapi as pa

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

# Margen pedido sobre la cuota justa para considerar que paga (cubre el error
# del modelo): cuota mínima = cuota justa × (1 + MIN_EDGE).
MIN_EDGE = 0.05
# Picks: líneas "jugables" (ni regalos ni loterías).
PICK_P_MIN, PICK_P_MAX = 0.30, 0.85
# Jugadas del mismo partido se ganan o se pierden juntas (si el partido sale
# trabado, caen todas). Tope: la mejor por jugador y pocas por partido.
PICKS_PER_PLAYER, PICKS_PER_MATCH = 1, 3


def _cap_picks(picks: list[dict]) -> list[dict]:
    out, per_player = [], {}
    for k in sorted(picks, key=lambda k: -(k.get("edge") or 0)):
        if per_player.get(k["player_id"], 0) >= PICKS_PER_PLAYER or len(out) >= PICKS_PER_MATCH:
            continue
        per_player[k["player_id"]] = per_player.get(k["player_id"], 0) + 1
        out.append(k)
    return out
SANITY_X = 3.0
# Suplente+ (bet365), Suplente de Oro (Betano), Suplente (Betsson): si el
# titular sale, la apuesta sigue con el que entra y se suman los dos. Sólo
# tiene sentido para titulares: hace falta P(titular) ≥ SUBPLUS_MIN_START.
# Ese mismo umbral filtra todas las jugadas: sin formación, sólo probables
# titulares; con formación, sólo los once confirmados.
from futbol import books as _books  # noqa: E402
SUBPLUS_BOOKS = _books.sub_promo_ids()   # 'sub_promo' en futbol/books.json
SUBPLUS_MIN_START = 0.6
JORNADA = cfg.DATA_DIR / "jornada.json"
# Ventana en la que se le pregunta a ESPN si ya salió la formación.
LINEUP_WINDOW_H = 2.0


def _player_ids(keys: pd.Series, cands: dict) -> dict:
    """player_key → player_id, comparando cada nombre una sola vez (hay muchas filas por jugador)."""
    return {k: op.match_player(k, cands) for k in keys.dropna().unique()}


def _book_table(odds: pd.DataFrame) -> pd.DataFrame:
    if odds.empty:
        return odds
    o = odds[odds["side"] == "over"].copy()
    o["p_book"] = op.fair_prob(o)
    return o


def _context(leagues, refresh_odds: bool, n_sim: int, allow_fetch: bool = True) -> dict:
    """Todo lo que se comparte entre partidos: historia, modelos, cuotas, fotos."""
    teams, players = espn.build_history(leagues, verbose=False)
    try:
        odds = _book_table(op.fetch_all(refresh=refresh_odds, allow_fetch=allow_fetch))
    except Exception as exc:  # noqa: BLE001
        print(f"  sin cuotas: {exc}")
        odds = pd.DataFrame()
    now = pd.Timestamp(datetime.now(timezone.utc).date())
    hist = fm.History(players, teams)
    return {"teams": teams, "players": players, "hist": hist, "odds": odds, "now": now,
            "goals": mkt.team_goals(teams, players), "gms": {}, "anchor": mkt.load_anchor(),
            "asof": now + pd.Timedelta(days=1), "pri": hist.priors(now), "tms": {},
            "photo_of": photos._load(), "n_sim": n_sim, "suspicious": []}


def team_keepers(players, team_id: str, photo_of: dict | None = None, n: int = 4) -> list[dict]:
    """Arqueros que usó el equipo últimamente (para cambiar el arquero en Modo DT)."""
    g = players[(players["team_id"].astype(str) == str(team_id)) & players["pos"].astype(str).str.upper().isin(["G", "GK"])]
    if g.empty:
        return []
    g = g.sort_values("date").groupby("player_id").tail(1).sort_values("date", ascending=False).head(n)
    return [{"player_id": str(x.player_id), "player": x.player, "team_id": str(team_id), "pos": "G",
             "photo": (photo_of or {}).get(str(x.player_id), {}).get("url")} for x in g.itertuples()]


# Betano Argentina: ?bt=N abre la pestaña N del partido. "Jugadores" (donde
# "Tiros al Arco" y "Tiros" son los dos primeros mercados) es la 2 en la LPF
# (no tiene "Formaciones") y la 3 en las otras ligas (medido 01/11/2026). Sólo
# existe cuando Betano ya publicó los mercados de jugador; si no, se abre el
# partido sin pestaña. Si el nombre del link no es exacto, Betano redirige y
# pierde la pestaña (abre "Populares").
BETANO_AR_URL = "https://caba.betano.bet.ar/cuotas-de-partido/{slug}/{id}/"
BETANO_PLAYERS_TAB = {"arg": 2}             # resto de las ligas: 3
# bet365 AR: /I101/ abre la pestaña "Rápidas de jugador" del partido (probado
# por el usuario en LPF, 01/11/2026).
BET365_AR_URL = "https://www.bet365.bet.ar/#/AC/B1/C1/D8/E{id}/F3/I101/"


def _slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


_BETANO_TEAMS: dict = {}


def _betano_team(pid: str, fallback: str | None) -> str:
    """Nombre del equipo en Betano (futbol/betano_teams.json, por n° de OddsPapi)."""
    if not _BETANO_TEAMS:
        try:
            _BETANO_TEAMS.update(json.loads((Path(__file__).parent / "betano_teams.json")
                                            .read_text(encoding="utf-8"))["teams"])
        except Exception:  # noqa: BLE001
            _BETANO_TEAMS["_"] = ""
    return _BETANO_TEAMS.get(pid) or fallback or ""


def _book_links(ev: dict, fx) -> dict:
    """Links al partido en las casas (por ahora Betano)."""
    ids = op.book_fixture_ids(fx)
    out = {}
    if ids.get("betano"):
        p1, p2, n1, n2 = ids.get("_teams") or ("", "", None, None)
        home, away = _betano_team(p1, n1 or ev["home"]), _betano_team(p2, n2 or ev["away"])
        url = BETANO_AR_URL.format(slug=f"{_slug(home)}-{_slug(away)}", id=ids["betano"])
        if "betano" in (ids.get("_props") or []):
            url += f"?bt={BETANO_PLAYERS_TAB.get(ev.get('league'), 3)}"
        out["betano"] = url
    if ids.get("bet365.bet.ar"):
        # Página del partido en bet365 AR (mismo formato en todos sus sitios).
        out["bet365.bet.ar"] = BET365_AR_URL.format(id=ids["bet365.bet.ar"])
    return out


def simulate_event(ev: dict, ctx: dict, squad: list[dict] | None) -> tuple[dict, list[dict]]:
    """Un partido → (bloque del partido para el JSON, picks)."""
    lg = ev["league"]
    if lg not in ctx["tms"]:
        if lg in cfg.CUPS and cfg.CUP_TEAM_MODEL != "copa":
            ctx["tms"][lg] = fm.fit_team_model_cup(ctx["teams"], ctx["asof"], lg,
                                                   kappa_fixed=1.0 if cfg.CUP_TEAM_MODEL == "liga" else None)
        else:
            ctx["tms"][lg] = fm.fit_team_model(ctx["teams"][ctx["teams"]["league"] == lg], ctx["asof"])
    tm = ctx["tms"][lg]
    squad = lu.fill_positions(lu.complete_only(squad), ctx["players"])
    by_team, lineup = {}, {}
    for side in ("home", "away"):
        tid = ev[f"{side}_id"]
        confirmed = lu.has_xi(squad, tid)
        sq = [s for s in squad if str(s["team_id"]) == str(tid)] if confirmed else None
        by_team[tid] = fm.build_profiles(ctx["hist"], tid, ctx["asof"], ctx["pri"], sq)
        lineup[side] = ((lu.confirmed_xi(squad, tid) if confirmed else None)
                        or lu.expected_xi(ctx["players"], ctx["teams"], tid, ctx["asof"]))
    odds = ctx["odds"]
    fx = op.match_fixture(ev, odds) if not odds.empty else None
    market = _market_anchor(ev, ctx, fx)
    sim = fm.simulate_match(tm, ev["home_id"], ev["away_id"], by_team[ev["home_id"]],
                            by_team[ev["away_id"]], n=ctx["n_sim"], key=ev["event_id"],
                            shot_mult=tuple(market["shot_mult"]) if market else (1.0, 1.0))
    mo = odds[odds["fixture_id"] == fx] if fx else pd.DataFrame()
    cands = {espn.norm_name(p["player"]): p["player_id"] for p in sim["players"]}
    if not mo.empty:
        mo = mo.assign(player_id=mo["player_key"].map(_player_ids(mo["player_key"], cands)))

    teams_out = {}
    for side in ("home", "away"):
        t = sim["teams"][side]
        teams_out[side] = {
            "team_id": t["team_id"], "name": ev[side], "abbr": ev[f"{side}_abbr"],
            "shots": round(t["shots_mean"], 2), "sot": round(t["sot_mean"], 2),
            "shots_range": t["shots_q"][::2], "sot_range": t["sot_q"][::2],
            "shots_over": {str(l): round(fm.p_over(t["shots_dist"], l), 3) for l in cfg.TEAM_SHOT_LINES},
            "sot_over": {str(l): round(fm.p_over(t["sot_dist"], l), 3) for l in cfg.TEAM_SOT_LINES},
        }
    picks, players_out = [], []
    meta = {"lg": lg, "ev": ev, "confirmed": confirmed}
    for p in sorted(sim["players"], key=lambda x: -x["shots_mean"]):
        if p["exp_minutes"] < 1 or p["pos_group"] == "G":
            continue
        starter_like = confirmed and p["p_start"] >= 1.0 or (not confirmed and p["p_start"] >= SUBPLUS_MIN_START)
        entries = []
        for stat, ls in (("shots", cfg.SHOT_LINES), ("sot", cfg.SOT_LINES)):
            for line in ls:
                pm = fm.p_over(p[f"{stat}_dist"], line)
                if pm < 0.02:
                    continue
                psp = fm.p_over(p[f"{stat}_sp_dist"], line) if starter_like else pm
                entries.append((stat, line, pm, psp))
        info = {"player_id": p["player_id"], "player": p["player"], "team_id": p["team_id"],
                "pos": p["pos_group"], "role": p.get("role"), "dm": bool(p.get("dm")), "exp_minutes": round(p["exp_minutes"])}
        lines, pk = _price_player(meta, info, entries, starter_like, mo, ctx["suspicious"])
        picks += pk
        players_out.append({
            "player_id": p["player_id"], "player": p["player"], "team_id": p["team_id"],
            "side": p["side"], "pos": p["pos_group"], "role": p.get("role"), "dm": bool(p.get("dm")), "phi": round(p.get("phi", 0.0), 3),
            "p_play": round(p.get("p_play", 1.0), 3), "p_start": round(p["p_start"], 2),
            "exp_minutes": round(p["exp_minutes"]), "shots": round(p["shots_mean"], 2),
            "sot": round(p["sot_mean"], 2), "share90": round(p["share90"], 3),
            "acc": round(p["acc"], 3), "minutes_history": round(p["n_min"]),
            "photo": ctx["photo_of"].get(str(p["player_id"]), {}).get("url"), "lines": lines})
    picks = _cap_picks(picks)
    match = {
        "event_id": ev["event_id"], "league": lg, "league_name": cfg.LEAGUES[lg]["name"],
        "date": ev["date"], "venue": ev.get("venue"), "lineup_confirmed": confirmed,
        "lineup": lineup, "oddspapi_fixture": fx, "book_links": _book_links(ev, fx),
        # Titulares en un puesto (o lado) donde casi no juegan: aviso para revisar en Modo DT.
        "pos_alerts": lu.pos_alerts(squad, ctx["players"], ev["date"], ctx["teams"]),
        "teams": teams_out, "market": market,
        "total": {"shots": round(sim["total"]["shots_mean"], 2), "sot": round(sim["total"]["sot_mean"], 2)},
        "players": players_out,
        "keepers": {s_: team_keepers(ctx["players"], ev[f"{s_}_id"], ctx["photo_of"]) for s_ in ("home", "away")},
        "name_ids": cands,                    # para cruzar nombres de las casas al repreciar
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ev": ev,
    }
    if lg in cfg.SAVES_LEAGUES:            # atajadas de los dos arqueros (no pasa por el modelo)
        try:
            from futbol import saves as fsaves
            match["saves"] = fsaves.for_match(sim, ev, ctx["teams"], lg, ctx["asof"], ctx["photo_of"])
        except Exception as exc:  # noqa: BLE001 - sin atajadas el partido sale igual
            print(f"  atajadas {ev['home']} - {ev['away']}: {exc}")
    return match, picks


def _price_player(meta: dict, info: dict, entries, starter_like: bool, mo: pd.DataFrame,
                  suspicious: list) -> tuple[list[dict], list[dict]]:
    """Cuotas de las casas contra las probabilidades del modelo → líneas y jugadas
    de un jugador. Sirve tanto al simular como al repreciar con cuotas nuevas."""
    lg, ev, confirmed = meta["lg"], meta["ev"], meta["confirmed"]
    lines, picks = [], []
    p = info
    for stat, line, pm, psp in entries:
        prices = {}
        if not mo.empty:
            q = mo[(mo["player_id"] == p["player_id"]) & (mo["stat"] == stat)
                   & (np.isclose(mo["line"], line))]
            for r in q.itertuples():
                # Cordura: una cuota 3× la justa del modelo es casi
                # siempre un cruce de jugador equivocado.
                if r.price > SANITY_X / pm:
                    suspicious.append((p["player"], stat, line, r.book, r.price))
                    continue
                prices[r.book] = {"price": r.price, "p_book": round(float(r.p_book), 3)}
                if getattr(r, "susp", False) is True:
                    prices[r.book]["susp"] = True      # último precio antes de suspender
        fair = 1.0 / pm
        best = max(prices.items(), key=lambda kv: kv[1]["price"]) if prices else None
        row = {"stat": stat, "line": line, "p_model": round(pm, 3),
               "fair_odds": round(fair, 2), "min_odds": round(fair * (1 + MIN_EDGE), 2),
               "p_sub": round(psp, 3), "fair_sub": round(1.0 / psp, 2),
               "min_sub": round(1.0 / psp * (1 + MIN_EDGE), 2),
               "books": prices,
               "best_book": best[0] if best else None,
               "best_price": best[1]["price"] if best else None}
        if best:
            row["edge"] = round(pm * best[1]["price"] - 1.0, 3)
        lines.append(row)
        base = {"league": lg, "event_id": ev["event_id"], "date": ev["date"],
                "match": f"{ev['home']} vs {ev['away']}", "player": p["player"],
                "player_id": p["player_id"], "team_id": p["team_id"],
                "pos": p["pos"], "role": p.get("role"), "dm": bool(p.get("dm")), "exp_minutes": p["exp_minutes"],
                "lineup": confirmed, **row}
        # Sólo titulares (confirmados, o probables si no hay formación): la
        # probabilidad es "si juega", y para un suplente eso infla mucho.
        if not starter_like:
            continue
        if best and PICK_P_MIN <= pm <= PICK_P_MAX and best[1]["price"] >= row["min_odds"]:
            picks.append({**base, "mode": "normal"})
        elif starter_like and psp > pm + 0.005:
            # Con suplente: mejor casa que ofrece la promo y su precio.
            sp = {b: v for b, v in prices.items() if b in SUBPLUS_BOOKS}
            bsp = max(sp.items(), key=lambda kv: kv[1]["price"]) if sp else None
            if bsp and PICK_P_MIN <= psp <= PICK_P_MAX and bsp[1]["price"] >= row["min_sub"]:
                picks.append({**base, "mode": "sub", "p_model": row["p_sub"],
                              "fair_odds": row["fair_sub"], "min_odds": row["min_sub"],
                              "best_book": bsp[0], "best_price": bsp[1]["price"],
                              "edge": round(psp * bsp[1]["price"] - 1.0, 3)})
    return lines, picks


def _name_ids(m: dict, players: pd.DataFrame | None = None) -> dict:
    """Nombres del plantel → id, para partidos guardados antes de tener name_ids:
    el mismo grupo que usa la simulación (lista oficial, o los que jugaron los
    últimos partidos de cada equipo)."""
    out = {}
    if lu.complete_only(m.get("official_squad")):
        for s_ in lu.complete_only(m["official_squad"]):
            out[espn.norm_name(s_["player"])] = s_["player_id"]
    elif players is not None and not players.empty:
        for sd in ("home", "away"):
            tid = str(m["ev"][f"{sd}_id"])
            t = players[players["team_id"].astype(str) == tid]
            recent = t.drop_duplicates("event_id").sort_values("date")["event_id"].tolist()[-cfg.LINEUP_LOOKBACK:]
            for r in t[t["event_id"].isin(recent)].itertuples():
                out[espn.norm_name(r.player)] = str(r.player_id)
    for p in m.get("players") or []:
        out[espn.norm_name(p["player"])] = p["player_id"]
    for sd in ("home", "away"):
        for p in ((m.get("lineup") or {}).get(sd) or {}).get("players") or []:
            out.setdefault(espn.norm_name(p["player"]), p["player_id"])
        for p in (m.get("keepers") or {}).get(sd) or []:
            out.setdefault(espn.norm_name(p["player"]), p["player_id"])
    return out


def _reprice(result: dict, odds: pd.DataFrame, skip: str | None = None,
             players: pd.DataFrame | None = None) -> list[dict]:
    """Con cuotas nuevas, recalcula precios y jugadas de toda la jornada usando
    las probabilidades que ya calculó el modelo (no vuelve a simular)."""
    if odds.empty:
        return []
    new_picks = []
    # Partido ya empezado: las casas sacan la cuota previa, así que repreciarlo
    # borraría sus jugadas. Quedan congeladas como estaban al inicio.
    now = pd.Timestamp.now(tz="UTC")
    started = {m["event_id"] for m in result["matches"]
               if m.get("ev") and pd.Timestamp(m["ev"]["date"]) <= now}
    keep = [k for k in result["picks"] if k["event_id"] == skip or k["event_id"] in started]
    for m in result["matches"]:
        if m["event_id"] == skip or not m.get("ev") or m["event_id"] in started:
            continue
        ev = m["ev"]
        fx = op.match_fixture(ev, odds)
        mo = odds[odds["fixture_id"] == fx] if fx else pd.DataFrame()
        if not mo.empty:
            cands = m.get("name_ids") or _name_ids(m, players)
            mo = mo.assign(player_id=mo["player_key"].map(_player_ids(mo["player_key"], cands)))
        confirmed = bool(m.get("lineup_confirmed"))
        meta = {"lg": m["league"], "ev": ev, "confirmed": confirmed}
        pk_m = []
        for p in m["players"]:
            starter_like = confirmed and p["p_start"] >= 1.0 or (not confirmed and p["p_start"] >= SUBPLUS_MIN_START)
            entries = [(l["stat"], l["line"], l["p_model"], l.get("p_sub", l["p_model"])) for l in p["lines"]]
            p["lines"], pk = _price_player(meta, p, entries, starter_like, mo, [])
            pk_m += pk
        m["oddspapi_fixture"] = fx
        m["book_links"] = _book_links(ev, fx)
        m["priced_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new_picks += _cap_picks(pk_m)
    result["picks"] = keep + new_picks
    return new_picks


def _market_anchor(ev: dict, ctx: dict, fx) -> dict | None:
    """Goles esperados que da el mercado del partido (1X2 + goles) y cuánto
    mueven el volumen de remates de cada equipo. None si no hay precios."""
    lg = ev["league"]
    mm = op.match_markets(fx) if fx else None
    g = mkt.implied_goals(mm["p1x2"], mm["totals"]) if mm else None
    if not g:
        return None
    if lg not in ctx["gms"]:
        G = ctx["goals"]
        ctx["gms"][lg] = mkt.fit_goal_model(G[G["league"] == lg], ctx["asof"])
    gm = ctx["gms"][lg]
    gh = mkt.goal_expected(gm, ev["home_id"], ev["away_id"], 1)
    ga = mkt.goal_expected(gm, ev["away_id"], ev["home_id"], 0)
    beta = ctx["anchor"].get(lg)
    mh = mkt.shot_multiplier(beta, g[0], gh, g[1], ga)
    ma = mkt.shot_multiplier(beta, g[1], ga, g[0], gh)
    return {"goals": [round(g[0], 2), round(g[1], 2)], "goals_model": [round(gh, 2), round(ga, 2)],
            "shot_mult": [round(mh, 3), round(ma, 3)], "active": bool(beta)}


def _save(result: dict) -> None:
    result["picks"] = sorted(result["picks"], key=lambda x: -x.get("edge", 0))
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    JORNADA.write_text(json.dumps(result, ensure_ascii=False, default=float), encoding="utf-8")
    # Lo que la app ya no muestra no se liquida (ver picklog.withdraw_missing).
    try:
        picklog.withdraw_missing(result)
    except Exception as exc:  # noqa: BLE001 - el registro nunca corta la jornada
        print(f"  registro de picks: {exc}")
    # La probabilidad de cada jugador, para la pestaña Rendimiento.
    try:
        picklog.log_preds(result.get("matches") or [])
    except Exception as exc:  # noqa: BLE001
        print(f"  registro de la fecha: {exc}")


CARRY_STARTED_H = 4   # un partido empezado sigue en la jornada hasta 4 h después del inicio


def _carry_started(new_matches: list[dict]) -> tuple[list[dict], list[dict]]:
    """Partidos de la jornada guardada que ya empezaron (hace menos de
    CARRY_STARTED_H) y no vienen en la nueva, con sus jugadas tal cual."""
    try:
        old = json.loads(JORNADA.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - sin jornada previa no hay nada que copiar
        return [], []
    now = pd.Timestamp.now(tz="UTC")
    fresh = {m["event_id"] for m in new_matches}
    keep = []
    for m in old.get("matches") or []:
        if m.get("event_id") in fresh or not m.get("date"):
            continue
        h = (now - pd.Timestamp(m["date"])).total_seconds() / 3600
        if 0 <= h < CARRY_STARTED_H:
            keep.append(m)
    ids = {m["event_id"] for m in keep}
    return keep, [k for k in old.get("picks") or [] if k.get("event_id") in ids]


LINEUP_KEYS = ("lineup_src", "lineup_seen", "lineup_sig", "lineup_sources", "lineup_conflicts",
               "lineup_changes", "official_squad", "lineup_official", "lineup_source", "lineup_official_at",
               "pos_alerts", "lineup_notified", "lineup_probable")


def _has_photo(m: dict | None) -> bool:
    return bool(m) and any(v in ("foto", "dt") for v in (m.get("lineup_sources") or {}).values())


def _keep_lineup_state(old: dict | None, new: dict) -> dict:
    """Al re-simular, el partido nuevo conserva lo que se sabe de las fuentes
    (sobre todo la alineación cargada desde una foto): si no, la próxima
    vuelta del vigía no sabría que manda la foto y la pisaría ESPN."""
    for k in LINEUP_KEYS:
        if old and old.get(k) is not None and (k not in new or k in ("lineup_src", "lineup_seen", "lineup_sig",
                                                                    "lineup_sources", "lineup_conflicts",
                                                                    "lineup_changes", "lineup_notified",
                                                                    "lineup_probable")):
            new[k] = old[k]
    return new


def run(leagues=None, days: int = 4, refresh_odds: bool = False, use_lineups: bool = True,
        n_sim: int = cfg.N_SIM, verbose: bool = True, progress=None) -> dict:
    """`progress(pct, texto)`: avance para la barra de la app (0-100)."""
    step = progress or (lambda pct, text: None)
    leagues = leagues or list(cfg.LEAGUES)
    step(2, "Cargando historial y cuotas")
    ctx = _context(leagues, refresh_odds, n_sim)
    step(15, "Buscando los partidos en ESPN")
    events = espn.upcoming(leagues, days=days)
    if verbose:
        print(f"{len(events)} partidos en los próximos {days} días · historia: "
              f"{ctx['teams']['event_id'].nunique()} partidos")
    out_matches, picks = [], []
    try:
        prev = {m["event_id"]: m for m in json.loads(JORNADA.read_text(encoding="utf-8")).get("matches") or []}
    except Exception:  # noqa: BLE001 - sin jornada previa
        prev = {}
    for i, ev in enumerate(events):
        step(18 + 80 * i / max(len(events), 1), f"Simulando {ev['home']} vs {ev['away']} ({i + 1}/{len(events)})")
        old = prev.get(str(ev["event_id"]))
        if _has_photo(old):                              # la foto cargada sigue mandando
            squad = lu.complete_only(old.get("official_squad"))
        else:
            squad = official_lineup(ev)[0] if use_lineups else []
        match, mp = simulate_event(ev, ctx, squad)
        if _has_photo(old):
            _keep_lineup_state(old, match)
            match["lineup_official"] = True
        out_matches.append(match)
        picks += mp
        if verbose:
            _print_match(match)
    # ESPN solo trae los partidos que no empezaron. Los que están en juego se
    # copian de la jornada anterior, con sus jugadas congeladas: si no, al
    # recalcular desaparecían del seguimiento en vivo y no se liquidaban.
    live_m, live_k = _carry_started(out_matches)
    result = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "leagues": leagues, "matches": live_m + out_matches, "picks": live_k + picks,
              "min_edge": MIN_EDGE}
    step(99, "Guardando")
    _save(result)
    _log_picks(picks)
    if verbose:
        if ctx["suspicious"]:
            print(f"\n{len(ctx['suspicious'])} cuotas descartadas por sospecha de cruce equivocado "
                  f"(> {SANITY_X:g}× la cuota justa), p. ej.: {ctx['suspicious'][:3]}")
        _print_picks(result["picks"])
    return result


def _teams_with_xi(m: dict, squad: list[dict]) -> set:
    """Equipos del partido que ya tienen titulares en `squad`."""
    ids = {str(m["ev"]["home_id"]), str(m["ev"]["away_id"])}
    return {t for t in ids if lu.has_xi(squad, t)}


# ESPN es gratis: cerca del partido se le pregunta seguido.
ESPN_EVERY_NEAR_S, ESPN_EVERY_FAR_S, NEAR_MIN = 40, 240, 75
_espn_last: dict[str, float] = {}


def _espn_due(m: dict, now) -> bool:
    import time as _time
    mins = (pd.Timestamp(m["date"]) - now).total_seconds() / 60
    every = ESPN_EVERY_NEAR_S if mins <= NEAR_MIN else ESPN_EVERY_FAR_S
    if _time.time() - _espn_last.get(m["event_id"], 0) < every:
        return False
    _espn_last[m["event_id"]] = _time.time()
    return True


def _lineup_complete(m: dict) -> bool:
    """Ya están los titulares de LOS DOS equipos (ESPN a veces publica uno primero)."""
    if len(_teams_with_xi(m, m.get("official_squad") or [])) == 2:
        return True
    lu_ = m.get("lineup") or {}
    return bool(m.get("lineup_confirmed")) and all(
        (lu_.get(sd) or {}).get("confirmed") and len((lu_.get(sd) or {}).get("players") or []) >= lu.MIN_XI
        for sd in ("home", "away"))


_CANDS: dict = {"t": 0.0, "v": None}


def _name_cands() -> dict:
    """Por team_id de ESPN: nombre normalizado → player_id (último club de cada
    jugador en la historia). Sirve para cruzar los nombres de las otras fuentes."""
    import time as _time
    if _CANDS["v"] is None or _time.time() - _CANDS["t"] > 3600:
        _, hp = espn.build_history(list(cfg.LEAGUES), verbose=False)
        hp = hp.sort_values("date").groupby("player_id").tail(1)
        cands = {}
        for r in hp.itertuples():
            cands.setdefault(str(r.team_id), {})[espn.norm_name(r.player)] = str(r.player_id)
        _CANDS.update(t=_time.time(), v=cands)
    return _CANDS["v"]


def official_lineup(ev: dict) -> tuple[list[dict], str | None]:
    """Formación oficial de la primera fuente de cfg.LINEUP_SOURCES que la tenga
    (con los dos equipos, o la que traiga más). → (squad, fuente)."""
    best, src = [], None
    for name in cfg.LINEUP_SOURCES:
        try:
            if name == "espn":
                sq = espn.lineup(ev)
            elif name == "pitchapi":
                sq = pa.lineup(ev, _name_cands())
            elif name == "api-football":
                sq = af.lineup(ev, _name_cands())
            elif name == "highlightly":
                sq = hl.lineup(ev, _name_cands())
            else:
                continue
        except Exception as exc:  # noqa: BLE001 - una fuente caída no corta las demás
            print(f"  formación {name}: {exc}")
            continue
        n = len({str(s["team_id"]) for s in sq if s.get("starter")})
        if n > len({str(s["team_id"]) for s in best if s.get("starter")}):
            best, src = sq, name
        if n >= 2:
            break
    return best, src


# Hasta cuántos minutos DESPUÉS del inicio se siguen mirando las fuentes: a
# veces corrigen el dibujo sobre la hora (Internacional–Coritiba, 03/11/2026:
# ESPN publicó 4-2-3-1 con Bruno Gomes de enganche y después lo pasó al
# 3-4-2-1 real, el que PitchAPI ya tenía).
LINEUP_FOLLOW_MIN = 10


def _form(f) -> str:
    return str(f or "").replace(" ", "").strip()


def _team_rows(squad: list[dict], tid: str) -> list[dict]:
    return [s for s in squad or [] if str(s["team_id"]) == str(tid)]


def _xi_sig(rows: list[dict], src: str | None) -> str:
    """Huella del XI de un equipo: fuente, dibujo y cada titular con su lugar."""
    st = [s for s in rows if s.get("starter")]
    f = next((_form(s.get("formation")) for s in st if s.get("formation")), "")
    return f"{src}|{f}|" + ",".join(sorted(f"{s['player_id']}@{s.get('formation_place') or ''}" for s in st))


def _choose_team_xi(snaps: dict, tid: str) -> tuple[list[dict], str | None, dict | None]:
    """XI de un equipo según las fuentes que lo tienen → (filas, fuente, desacuerdo).
    PitchAPI es la fuente elegida (cfg.LINEUP_SOURCES). ESPN se usa mientras
    PitchAPI no publicó, o si coinciden en el dibujo y en los titulares (los
    lugares de ESPN dan puestos más finos). Si no coinciden, manda PitchAPI."""
    have = {src: _team_rows(sq, tid) for src, sq in snaps.items() if lu.has_xi(sq, tid)}
    if not have:
        return [], None, None
    msrc = next((x for x in ("foto", "dt") if x in have), None)   # cargadas por el usuario
    f = have.get(msrc) if msrc else None
    if f:
        # La que cargó el usuario desde una foto manda. Si ESPN o PitchAPI traen
        # otros titulares u otro dibujo, se avisa (puede que la foto esté mal).
        ff = next((_form(s.get("formation")) for s in f if s.get("starter") and s.get("formation")), "")
        xf = {str(s["player_id"]) for s in f if s.get("starter")}
        diff = {}
        for src in ("espn", "pitchapi"):
            rows = have.get(src)
            if not rows:
                continue
            fo = next((_form(s.get("formation")) for s in rows if s.get("starter") and s.get("formation")), "")
            xo = {str(s["player_id"]) for s in rows if s.get("starter") and not str(s["player_id"]).startswith("pa:")}
            known = {x for x in xf if not x.startswith("foto:")}
            if (ff and fo and ff != fo) or (xo - known and len(xo - known) > len(xf - known)):
                nm = {str(s["player_id"]): s.get("player") for s in rows + f}
                diff[src] = {"formation": fo or None,
                             "in": sorted(nm.get(x) or x for x in xo - known),
                             "out": sorted(nm.get(x) or x for x in known - xo)}
        return f, msrc, ({"foto": ff or None, **diff} if diff else None)
    e, p = have.get("espn"), have.get("pitchapi")
    if e and p:
        fe = next((_form(s.get("formation")) for s in e if s.get("starter") and s.get("formation")), "")
        fp = next((_form(s.get("formation")) for s in p if s.get("starter") and s.get("formation")), "")
        xe = {str(s["player_id"]) for s in e if s.get("starter")}
        # Los de PitchAPI que no se pudieron cruzar ("pa:…") no cuentan como desacuerdo.
        xp = {str(s["player_id"]) for s in p if s.get("starter") and not str(s["player_id"]).startswith("pa:")}
        # Si a ESPN le queda un titular sin puesto ("M" a secas) aun con la tabla
        # de lugares, sus puestos no son "más finos": van los de PitchAPI. ("F"
        # no cuenta: es el 9 solo en las tablas de ESPN.)
        e_vague = any(s.get("starter") and str(s.get("pos") or "").upper() in ("", "D", "M", "SUB", "NONE")
                      for s in lu.place_positions(e))
        if fe and fe == fp and xp <= xe and not e_vague:
            return e, "espn", None
        if fe and fe == fp and xp <= xe:
            return p, "pitchapi", None
        return p, "pitchapi", {"espn": fe or None, "pitchapi": fp or None,
                               "players": sorted(xe ^ xp) if xp - xe else []}
    for src in ("pitchapi", "espn", "api-football", "highlightly"):
        if src in have:
            return have[src], src, None
    src = next(iter(have))
    return have[src], src, None


def check_lineups(window_h: float = LINEUP_WINDOW_H, verbose: bool = False,
                  only_event: str | None = None) -> list[dict]:
    """Partidos de la jornada con formación oficial nueva o CAMBIADA desde la
    última vez. Las fuentes se siguen mirando hasta LINEUP_FOLLOW_MIN después
    del inicio: si una corrige el dibujo, los titulares o los puestos, se
    actualiza y se avisa. No re-simula ni gasta cuotas."""
    if not JORNADA.exists():
        return []
    result = json.loads(JORNADA.read_text(encoding="utf-8"))
    now = pd.Timestamp.now(tz="UTC")
    if only_event:
        # Un solo partido (la alineación cargada desde una foto): sin pedir a
        # las fuentes, se aplica lo que ya hay guardado, sea la hora que sea.
        window = [m for m in result["matches"] if m.get("ev") and m["event_id"] == only_event]
    else:
        window = [m for m in result["matches"]
                  if m.get("ev") and -0.5 <= (pd.Timestamp(m["date"]) - now).total_seconds() / 3600 <= window_h]
    # Prioritarios para el cupo de API-Football / Highlightly: los que seguís y los que tienen jugadas.
    try:
        fav = set(json.loads((cfg.DATA_DIR / "prefs.json").read_text(encoding="utf-8")).get("fav", []))
    except Exception:  # noqa: BLE001
        fav = set()
    with_picks = {k["event_id"] for k in result.get("picks", [])}
    cands = None
    newly, dirty = [], False
    for m in window:
        seen = m.setdefault("lineup_seen", {})          # cuándo trajo cada fuente los dos XI (1.ª vez)
        snaps = m.setdefault("lineup_src", {})          # último XI de cada fuente, por equipo
        prio = m["event_id"] in fav or m["event_id"] in with_picks
        mins = (pd.Timestamp(m["date"]) - now).total_seconds() / 60
        found = []                                       # [(squad, fuente)]
        if only_event:
            pass
        elif "espn" in cfg.LINEUP_SOURCES and mins >= -LINEUP_FOLLOW_MIN and _espn_due(m, now):
            sq = espn.lineup(m["ev"])
            if any(s["starter"] for s in sq):
                found.append((sq, "espn"))
        for src, mod in () if only_event else (("pitchapi", pa), ("api-football", af), ("highlightly", hl)):
            # API-Football y Highlightly gastan cupo: una sola vez. PitchAPI es gratis.
            if src not in cfg.LINEUP_SOURCES or (src != "pitchapi" and src in seen) \
                    or not mod.should_ask(m["ev"], prio):
                continue
            if cands is None:
                cands = _name_cands()
            try:
                sq = mod.lineup(m["ev"], cands)
                if any(s["starter"] for s in sq):
                    found.append((sq, src))
            except Exception as exc:  # noqa: BLE001 - sin cupo o sin red: siguen las otras
                if verbose:
                    print(f"  {src}: {exc}")
        for sq, src in found:
            teams = _teams_with_xi(m, sq)
            if len(teams) == 2 and src not in seen:
                seen[src] = now.isoformat(timespec="seconds")
                _log_lineup_time(m, src, now)
                dirty = True
            if teams:
                # Se reemplazan los equipos que trae; si trae uno solo, el otro queda como estaba.
                new = [s for s in sq if str(s["team_id"]) in teams] + \
                      [s for s in snaps.get(src) or [] if str(s["team_id"]) not in teams]
                if new != snaps.get(src):
                    snaps[src] = new
                    dirty = True
        if not snaps:
            continue
        # Huella de lo que hay guardado (partidos de antes de este cambio: se calcula).
        old_sig = m.get("lineup_sig")
        if old_sig is None:
            old_sq, old_src = m.get("official_squad") or [], m.get("lineup_source")
            old_sig = {t: _xi_sig(_team_rows(old_sq, t), old_src) for t in _teams_with_xi(m, old_sq)}
        squad, sig, srcs, conflicts = [], {}, {}, {}
        for side in ("home", "away"):
            tid = str(m["ev"][f"{side}_id"])
            rows, src, conflict = _choose_team_xi(snaps, tid)
            if rows:
                sig[tid] = _xi_sig(rows, src)
            elif tid in old_sig:                         # ninguna fuente lo trae ahora: queda el que había
                rows = _team_rows(m.get("official_squad") or [], tid)
                src = (m.get("lineup_sources") or {}).get(tid) or m.get("lineup_source")
                sig[tid] = old_sig[tid]
            else:
                continue
            squad += rows
            srcs[tid] = src
            if conflict:
                conflicts[tid] = conflict
        changed = [t for t in sig if sig[t] != old_sig.get(t)]
        # Una fuente que no coincide con la foto cargada también se guarda y se avisa.
        new_conf = {t: c for t, c in conflicts.items()
                    if srcs.get(t) in ("foto", "dt") and c != (m.get("lineup_conflicts") or {}).get(t)}
        if not changed and not new_conf:
            continue
        for t, c in new_conf.items():
            _notify_photo_conflict(m, t, c, fav, with_picks)
        newly.append((m, squad, srcs, changed, conflicts, sig))
    if not newly:
        if dirty:
            _save(result)
        return []
    # NO se re-simula solo: se guarda la alineación oficial y se avisa. La
    # simulación con ese XI corre cuando el usuario toca "Simular partido".
    updated, changes = [], []
    hist_teams, hist_players = espn.build_history(list(cfg.LEAGUES), verbose=False)
    for m, squad, srcs, changed, conflicts, sig in newly:
        squad = lu.place_positions(squad)
        if any(s["starter"] and lu._no_pos(s.get("pos")) for s in squad):
            squad = lu.fill_positions(squad, hist_players)
        before_lu = m.get("lineup") or {}
        was_official = bool(m.get("lineup_official"))
        had = {t for t in (m.get("lineup_sig") or {})} | _teams_with_xi(m, m.get("official_squad") or [])
        m["official_squad"] = squad
        m["pos_alerts"] = lu.pos_alerts(squad, hist_players, m["date"], hist_teams)
        m["lineup_official"] = True
        uniq = sorted(set(srcs.values()))
        m["lineup_source"] = uniq[0] if len(uniq) == 1 else "+".join(uniq)
        m["lineup_sources"] = srcs
        m["lineup_sig"] = sig
        m["lineup_conflicts"] = conflicts
        if not was_official:
            m["lineup_official_at"] = now.isoformat(timespec="seconds")
        m["lineup"] = {side: (lu.confirmed_xi(squad, m["ev"][f"{side}_id"]) or before_lu.get(side))
                       for side in ("home", "away")}
        # Equipos que YA tenían XI oficial y cambió: se anota y se avisa aparte.
        for tid in changed:
            if tid not in had:
                continue
            side = "home" if str(m["ev"]["home_id"]) == tid else "away"
            old_l, new_l = before_lu.get(side) or {}, m["lineup"].get(side) or {}
            fo, fn = old_l.get("formation"), new_l.get("formation")
            # Función en la cancha (central, lateral, enganche…) y lado: si sólo
            # cambia la etiqueta entre fuentes ("AM-R" / "CF-R"), no es un cambio.
            role = lambda pos, f: (lu.alert_role(pos, f), (lu.side_of(pos) > 0) - (lu.side_of(pos) < 0))
            old_pos = {str(p["player_id"]): p.get("pos") for p in old_l.get("players") or []}
            moved = [f"{p['player']} {old_pos[str(p['player_id'])]}→{p.get('pos')}"
                     for p in new_l.get("players") or []
                     if str(p["player_id"]) in old_pos
                     and role(old_pos[str(p["player_id"])], fo) != role(p.get("pos"), fn)]
            new_ids = {str(p["player_id"]) for p in new_l.get("players") or []}
            ins = [p["player"] for p in new_l.get("players") or [] if str(p["player_id"]) not in old_pos]
            outs = [p["player"] for p in old_l.get("players") or [] if str(p["player_id"]) not in new_ids]
            ch = {"at": now.isoformat(timespec="seconds"), "team_id": tid, "team": m["ev"][side],
                  "source": srcs.get(tid), "before": fo, "after": fn, "moved": moved, "in": ins, "out": outs}
            m.setdefault("lineup_changes", []).append(ch)
            if _form(fo) != _form(fn) or ins or outs or moved:
                changes.append((m, ch))
        # Sin re-simular: se retiran ya las jugadas de quien quedó en el banco
        # (o fuera de la lista) en los equipos que tienen el XI confirmado.
        xi = {str(s["player_id"]) for s in squad if s.get("starter")}
        teams_xi = _teams_with_xi(m, squad)
        before = len(result["picks"])
        result["picks"] = [k for k in result["picks"]
                           if not (k["event_id"] == m["event_id"] and str(k["team_id"]) in teams_xi
                                   and str(k["player_id"]) not in xi)]
        updated.append({"event_id": m["event_id"], "match": f"{m['ev']['home']} vs {m['ev']['away']}",
                        "picks": 0, "source": m["lineup_source"], "simulated": False,
                        "changed": bool(had & set(changed)),
                        "bench_removed": before - len(result["picks"])})
    result["lineups_checked_at"] = now.isoformat(timespec="seconds")
    _save(result)
    if not only_event:                                   # la foto la cargó el usuario: no hace falta avisarle
        _notify_lineups([(m, sq, srcs) for m, sq, srcs, *_ in newly], fav, with_picks, now)
        _notify_changes(changes, fav, with_picks)
    return updated


PARLAY_LOOKBACK = 10   # titularidades que se miran para ver si remata seguido


def recent_starts(event_id: str, n: int = PARLAY_LOOKBACK) -> dict:
    """Modo parlay: de cada jugador del partido, remates y remates al arco en
    sus últimas `n` titularidades (antes del partido). Sirve para ver quién
    remata de forma reiterada, no sólo en promedio."""
    if not JORNADA.exists():
        raise ValueError("Todavía no hay jornada calculada")
    result = json.loads(JORNADA.read_text(encoding="utf-8"))
    m = next((x for x in result["matches"] if x["event_id"] == event_id), None)
    if m is None:
        raise ValueError("Partido no encontrado en la jornada")
    ids = {str(p["player_id"]) for p in m.get("players") or []}
    _, hp = espn.build_history(list(cfg.LEAGUES), verbose=False)
    asof = pd.Timestamp(m["date"]).tz_convert(None)
    h = hp[hp["player_id"].astype(str).isin(ids) & (hp["starter"] == 1) & (hp["date"] < asof)].sort_values("date")
    out = {}
    for pid, g in h.groupby("player_id"):
        g = g.tail(n)
        out[str(pid)] = {"shots": g["shots"].fillna(0).astype(int).tolist(),
                         "sot": g["sot"].fillna(0).astype(int).tolist(),
                         "min": g["minutes"].fillna(0).astype(int).tolist()}
    return {"event_id": event_id, "n": n, "players": out}


LINEUP_TIMES = cfg.DATA_DIR / "lineup_times.csv"


def _log_lineup_time(m: dict, source: str, now) -> None:
    """Una fila por partido y fuente: cuándo trajo los dos titulares."""
    import csv
    new = not LINEUP_TIMES.exists()
    mins = round((pd.Timestamp(m["date"]) - now).total_seconds() / 60, 1)
    with LINEUP_TIMES.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["event_id", "league", "match", "kickoff", "source", "seen_at", "min_before"])
        w.writerow([m["event_id"], m.get("league") or m["ev"].get("league"),
                    f"{m['ev']['home']} vs {m['ev']['away']}", m["date"], source,
                    now.isoformat(timespec="seconds"), mins])


def lineup_speed() -> pd.DataFrame:
    """Por partido: minutos antes del inicio en que cada fuente trajo los titulares."""
    if not LINEUP_TIMES.exists():
        return pd.DataFrame()
    d = pd.read_csv(LINEUP_TIMES).drop_duplicates(["event_id", "source"])
    return d.pivot_table(index=["league", "match"], columns="source", values="min_before", aggfunc="first")


def _notify_changes(changes: list, fav: set, with_picks: set) -> None:
    """Aviso al celular cuando una formación YA confirmada cambia (dibujo,
    titulares o puestos), en los partidos que seguís o que tienen jugadas."""
    from futbol import notify
    for m, ch in changes:
        if m["event_id"] not in fav and m["event_id"] not in with_picks:
            continue
        name = {"pitchapi": "PitchAPI", "espn": "ESPN"}.get(ch.get("source"), ch.get("source") or "")
        lines = []
        if ch.get("before") != ch.get("after"):
            lines.append(f"{ch['team']}: {ch.get('before') or '?'} → {ch.get('after') or '?'} ({name})")
        else:
            lines.append(f"{ch['team']}: cambió la formación ({name})")
        if ch.get("in"):
            lines.append("Entran: " + ", ".join(ch["in"]))
        if ch.get("out"):
            lines.append("Salen: " + ", ".join(ch["out"]))
        if ch.get("moved"):
            lines.append("Cambian de puesto: " + ", ".join(ch["moved"][:6]))
        lines.append("Tocá Simular para recalcular.")
        try:
            notify.send(f"Cambió la formación: {m['ev']['home']} vs {m['ev']['away']}", "\n".join(lines),
                        tags=["warning"])
        except Exception:  # noqa: BLE001 - el aviso nunca corta el vigía
            pass


def _notify_photo_conflict(m: dict, tid: str, c: dict, fav: set, with_picks: set) -> None:
    """Aviso cuando ESPN o PitchAPI publican algo distinto a la foto cargada."""
    from futbol import notify
    if m["event_id"] not in fav and m["event_id"] not in with_picks:
        return
    side = "home" if str(m["ev"]["home_id"]) == str(tid) else "away"
    lines = []
    for src, d in c.items():
        if src == "foto":
            continue
        name = {"pitchapi": "PitchAPI", "espn": "ESPN"}.get(src, src)
        bits = []
        if d.get("formation") and d["formation"] != c.get("foto"):
            bits.append(f"{d['formation']} (foto: {c.get('foto') or '?'})")
        if d.get("in"):
            bits.append("con " + ", ".join(d["in"]))
        if d.get("out"):
            bits.append("sin " + ", ".join(d["out"]))
        lines.append(f"{name}: " + "; ".join(bits))
    lines.append("Revisá la foto o cargá otra.")
    try:
        notify.send(f"La foto no coincide: {m['ev'][side]}", "\n".join(lines), tags=["warning"])
    except Exception:  # noqa: BLE001 - el aviso nunca corta el vigía
        pass


def _notify_lineups(newly, fav: set, with_picks: set, now) -> None:
    """Aviso al celular cuando están los titulares de los dos equipos, en los
    partidos que seguís o que tienen jugadas."""
    from futbol import notify
    for m, _sq, source in newly:
        if not _lineup_complete(m) or m.get("lineup_notified"):
            continue
        if m["event_id"] not in fav and m["event_id"] not in with_picks:
            continue
        mins = int((pd.Timestamp(m["date"]) - now).total_seconds() // 60)
        lu_ = m.get("lineup") or {}
        forms = " · ".join(f"{m['ev'][sd]} {(lu_.get(sd) or {}).get('formation') or ''}".strip() for sd in ("home", "away"))
        n = sum(1 for k in _jornada_picks() if k["event_id"] == m["event_id"])
        msg = (f"{forms}\nEmpieza en {mins} min." if mins > 0 else f"{forms}\nYa empezó.") + \
              (f" Tiene {n} jugada{'s' if n != 1 else ''}: tocá Simular para recalcularlas." if n else " Tocá Simular para recalcular.")
        if notify.send(f"Titulares: {m['ev']['home']} vs {m['ev']['away']}", msg, tags=["soccer"]):
            m["lineup_notified"] = True
    if any(m.get("lineup_notified") for m, _s, _src in newly):
        res = json.loads(JORNADA.read_text(encoding="utf-8"))
        done = {m["event_id"] for m, _s, _src in newly if m.get("lineup_notified")}
        for x in res["matches"]:
            if x["event_id"] in done:
                x["lineup_notified"] = True
        _save(res)


def _jornada_picks() -> list[dict]:
    try:
        return json.loads(JORNADA.read_text(encoding="utf-8")).get("picks", [])
    except Exception:  # noqa: BLE001
        return []


def _log_picks(picks: list[dict]) -> None:
    try:
        picklog.log(picks)
    except Exception as exc:  # noqa: BLE001 - el registro nunca corta la jornada
        print(f"  registro de picks: {exc}")


def _recent_callups(hist, team_id: str, asof, xi_ids: set) -> list[str]:
    """Banco probable cuando todavía no hay formación oficial: de los que
    estuvieron en las últimas LINEUP_LOOKBACK listas del equipo, los más
    convocados (y más recientes), hasta el tamaño de banco que usó el equipo
    (la mediana de esos partidos). Sin el tope entraban 16-18 suplentes, el
    banco sumaba minutos de más y le sacaba remates a los titulares."""
    te = hist.team_events.get(str(team_id))
    if te is None:
        return []
    recent = te[te["date"] < pd.Timestamp(asof)]["event_id"].tolist()[-cfg.LINEUP_LOOKBACK:]
    rec = hist.p[(hist.p["team_id"] == str(team_id)) & hist.p["event_id"].isin(recent)]
    if rec.empty:
        return []
    size = int(rec[rec["starter"] == 0].groupby("event_id").size().median())
    order = {e: i for i, e in enumerate(recent)}
    rank = (rec.assign(pid=rec["player_id"].astype(str), w=rec["event_id"].map(order))
               .groupby("pid").agg(n=("event_id", "nunique"), last=("w", "max"))
               .sort_values(["n", "last"], ascending=False))
    return [p for p in rank.index if p not in xi_ids][:size]


def _custom_squad(old: dict, custom: dict, hist=None, asof=None) -> list[dict]:
    """Modo DT: arma el plantel con el XI que eligió el usuario (titulares).
    Suplentes: los oficiales si ya salió la formación; si no, los convocados de
    los últimos partidos (no el plantel entero: eso le daba minutos de más al
    banco y se los sacaba a los titulares). El equipo que no tocó usa la
    oficial o la probable."""
    ev = old["ev"]
    names = {str(p["player_id"]): p.get("player") for p in old.get("players") or []}
    squad = []
    for side in ("home", "away"):
        tid = str(ev[f"{side}_id"])
        mine = (custom or {}).get(side) or {}
        if mine.get("players"):
            xi = [{"team_id": tid, "formation": mine.get("formation"), "formation_place": i + 1,
                   "player_id": str(p["player_id"]), "player": p.get("player"), "starter": 1,
                   "pos": p.get("pos"), "pos_group": espn.position_group(p.get("pos"))}
                  for i, p in enumerate(mine["players"][:11])]
        else:
            off = [s_ for s_ in (old.get("official_squad") or []) if s_["team_id"] == tid and s_.get("starter")]
            if off:
                xi = off
            else:
                lu_ = (old.get("lineup") or {}).get(side) or {}
                xi = [{"team_id": tid, "formation": lu_.get("formation"), "formation_place": p.get("place"),
                       "player_id": str(p["player_id"]), "player": p.get("player"), "starter": 1,
                       "pos": p.get("pos"), "pos_group": espn.position_group(p.get("pos"))}
                      for p in lu_.get("players") or []]
        ids = {str(x["player_id"]) for x in xi}
        official = [s_ for s_ in lu.complete_only(old.get("official_squad")) if str(s_["team_id"]) == tid]
        if official:
            pool = [str(s_["player_id"]) for s_ in official]                 # banco oficial (+ titulares que el DT sacó)
            names.update({str(s_["player_id"]): s_.get("player") for s_ in official})
        elif hist is not None:
            pool = _recent_callups(hist, tid, asof, ids)
        else:
            pool = [str(p["player_id"]) for p in old.get("players") or [] if str(p.get("team_id")) == tid]
        bench = [{"team_id": tid, "formation": None, "formation_place": None, "player_id": pid,
                  "player": names.get(pid) or pid, "starter": 0, "pos": "SUB", "pos_group": "?"}
                 for pid in dict.fromkeys(pool) if pid not in ids]
        squad += xi + bench
    return squad


# Cambiá esto cuando cambie el modelo: obliga a volver a simular todo.
MODEL_VERSION = "2026-11-03-cups-leaguelevel"  # Champions y Europa League; puesto del plantel para los "SUB"
MANUAL_MIN_AGE_S = 10 * 60          # "Cuotas nuevas" no vuelve a pedir si tienen menos de 10'


def _lineup_fp(m: dict, custom: dict | None) -> str:
    if custom:
        return "custom:" + hashlib.sha1(json.dumps(custom, sort_keys=True).encode()).hexdigest()[:12]
    # Con el puesto: si sólo cambian las posiciones también hay que volver a simular.
    st = sorted(f"{s['player_id']}@{s.get('pos') or ''}"
                for s in lu.complete_only(m.get("official_squad")) if s.get("starter"))
    return ("official:" + ",".join(st)) if st else "probable"


def _model_fp() -> str:
    return f"{MODEL_VERSION}|{datetime.now().strftime('%Y-%m-%d')}"   # día nuevo = historia nueva


def _lineup_label(m: dict, custom: bool = False) -> str:
    if custom or m.get("custom_lineup"):
        return "tu alineación"
    if m.get("lineup_confirmed"):
        src = m.get("lineup_source")
        names = {"api-football": "API-Football", "pitchapi": "PitchAPI", "highlightly": "Highlightly"}
        return "oficial" + (f" ({' + '.join(names.get(s, 'ESPN') for s in str(src).split('+'))})" if src else "")
    return "probable"


def _pick_key(k: dict) -> tuple:
    return (k["player_id"], k["stat"], k["line"], k.get("mode", "normal"))


def _pick_brief(k: dict) -> dict:
    return {"player": k["player"], "stat": k["stat"], "line": k["line"], "mode": k.get("mode", "normal"),
            "p": k.get("p_model"), "book": k.get("best_book"), "price": k.get("best_price")}


def _last_sim(mode: str, old: dict, new: dict, old_picks: list, new_picks: list, odds_info: dict,
              custom: bool = False) -> dict:
    """Lo que cambió con este "Simular", para mostrarlo debajo del botón."""
    ok, nk = {_pick_key(k): k for k in old_picks}, {_pick_key(k): k for k in new_picks}
    return {
        "mode": mode, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lineup": {"before": _lineup_label(old), "after": _lineup_label(new, custom)},
        "odds": {k: odds_info.get(k) for k in ("refreshed", "reason", "age_s", "today", "cap")},
        "teams": {sd: {st: {"before": old["teams"][sd][st], "after": new["teams"][sd][st]} for st in ("shots", "sot")}
                  for sd in ("home", "away")},
        "picks_before": len(old_picks), "picks_after": len(new_picks),
        "added": [_pick_brief(nk[k]) for k in nk if k not in ok][:6],
        "removed": [_pick_brief(ok[k]) for k in ok if k not in nk][:6],
        "changed": [{**_pick_brief(nk[k]), "price_before": ok[k].get("best_price")}
                    for k in nk if k in ok and nk[k].get("best_price") != ok[k].get("best_price")][:6],
    }


def refresh_and_reprice(force: bool = False) -> dict:
    """Cuotas nuevas (si hace falta, o siempre con force) y repreciar toda la
    jornada con las probabilidades guardadas. No simula."""
    info = {"refreshed": False}
    age = op.cache_age_s()
    if force and age is not None and age < MANUAL_MIN_AGE_S:
        return {"odds": {"refreshed": False, "reason": "recent", "age_s": round(age),
                         "today": op.refreshes_today(), "cap": cfg.ODDS_REFRESH_DAY_CAP}, "picks": None}
    if force:
        op.fetch_all(refresh=True)
        info = {"refreshed": op._FETCHED[0], "reason": "forced", "age_s": op.cache_age_s(),
                "today": op.refreshes_today(), "cap": cfg.ODDS_REFRESH_DAY_CAP}
    else:
        info = op.maybe_refresh()
    if not info.get("refreshed"):
        return {"odds": info, "picks": None}
    result = json.loads(JORNADA.read_text(encoding="utf-8"))
    odds = _book_table(op.fetch_all(allow_fetch=False))
    _, players = espn.build_history(result.get("leagues") or list(cfg.LEAGUES), verbose=False)
    before = len(result["picks"])
    new = _reprice(result, odds, players=players)
    _save(result)
    _log_picks(new)
    return {"odds": info, "picks": {"before": before, "after": len(result["picks"])}}


def refresh_scope(event_id: str | None = None, league: str | None = None) -> dict:
    """Cuotas nuevas de UN partido (event_id) o de TODA una liga (league) y
    repreciar la jornada con las probabilidades guardadas. 1 pedido por partido."""
    if not JORNADA.exists():
        raise ValueError("Todavía no hay jornada calculada")
    result = json.loads(JORNADA.read_text(encoding="utf-8"))
    if event_id:
        m = next((x for x in result["matches"] if x["event_id"] == event_id), None)
        if m is None or not m.get("ev"):
            raise ValueError("Partido no encontrado en la jornada")
        fx = op.fixture_for_event(m["ev"])
        if fx is None:
            return {"refreshed": False, "reason": "nofixture", "requests": 0, "usage": op.usage()}
        fxs = [fx]
    else:
        if league not in cfg.LEAGUES:
            raise ValueError("Liga desconocida")
        fxs = op.league_fixtures(league)
    plan = op.plan_refresh()
    left = plan.get("remaining") or 0
    if left - len(fxs) < 2:
        return {"refreshed": False, "reason": "month", "requests": 0, "usage": op.usage()}
    n = op.refresh_fixtures(fxs)
    odds = _book_table(op.fetch_all(allow_fetch=False))
    _, players = espn.build_history(result.get("leagues") or list(cfg.LEAGUES), verbose=False)
    before = len(result["picks"])
    new = _reprice(result, odds, players=players)
    _save(result)
    _log_picks(new)
    scope = {event_id} if event_id else {x["event_id"] for x in result["matches"] if x.get("league") == league}
    return {"refreshed": True, "requests": n, "matches": len(fxs), "usage": op.usage(),
            "picks": {"before": before, "after": len(result["picks"]),
                      "scope": sum(1 for k in result["picks"] if k["event_id"] in scope)}}


def simulate_one(event_id: str, custom: dict | None = None) -> dict:
    """Vuelve a simular UN partido de la jornada: le pregunta a ESPN si ya
    salió la formación y usa las cuotas guardadas (no gasta OddsPapi).
    Devuelve el partido antes y después para mostrar qué cambió."""
    if not JORNADA.exists():
        raise ValueError("Todavía no hay jornada calculada")
    result = json.loads(JORNADA.read_text(encoding="utf-8"))
    old = next((m for m in result["matches"] if m["event_id"] == event_id), None)
    if old is None or not old.get("ev"):
        raise ValueError("Partido no encontrado en la jornada")
    ev = old["ev"]
    old_picks = sum(1 for k in result["picks"] if k["event_id"] == event_id)
    # La alineación oficial que ya encontró el vigía (ESPN o API-Football); si no, se pregunta a ESPN.
    old_pk = [k for k in result["picks"] if k["event_id"] == event_id]
    # Cuotas al día para comparar: si tienen más de 90' se piden de nuevo (3
    # consultas, todas las ligas), con tope diario. Si no, se usan las guardadas.
    try:
        odds_info = op.maybe_refresh()
    except Exception as exc:  # noqa: BLE001 - sin red o sin cupo: se simula con las guardadas
        odds_info = {"refreshed": False, "reason": f"error: {exc}", "age_s": op.cache_age_s()}
    if not custom and len(_teams_with_xi(old, old.get("official_squad") or [])) < 2:
        # ¿Ya salió la formación? (fuentes de cfg.LINEUP_SOURCES) — si salió, es algo nuevo.
        sq_now, src_now = official_lineup(ev)
        if len(_teams_with_xi(old, sq_now)) == 2:
            old = {**old, "official_squad": sq_now, "lineup_official": True, "lineup_source": src_now}
    fp = {"lineup": _lineup_fp(old, custom), "model": _model_fp(), "odds": op.cache_stamp()}
    prev = old.get("sim_inputs") or {}
    same_sim = prev.get("lineup") == fp["lineup"] and prev.get("model") == fp["model"]
    if same_sim:
        if prev.get("odds") == fp["odds"]:
            # Nada nuevo: misma formación, mismo modelo y mismas cuotas → el
            # resultado sería idéntico (la simulación es reproducible).
            ls = _last_sim("skipped", old, old, old_pk, old_pk, odds_info, bool(custom))
            for x in result["matches"]:
                if x["event_id"] == event_id:
                    x["last_sim"] = ls
            _save(result)
            return {"mode": "skipped", "custom": bool(custom), "odds": odds_info, "last_sim": ls,
                    "before": {"teams": {s_: {k: old["teams"][s_][k] for k in ("shots", "sot")} for s_ in ("home", "away")},
                               "lineup_confirmed": old.get("lineup_confirmed"), "picks": old_picks},
                    "after": {"teams": {s_: {k: old["teams"][s_][k] for k in ("shots", "sot")} for s_ in ("home", "away")},
                              "lineup_confirmed": old.get("lineup_confirmed"), "picks": old_picks},
                    "n_sim": 0}
        # Sólo cambiaron las cuotas: se reprecia (toda la jornada) sin simular.
        odds = _book_table(op.fetch_all(allow_fetch=False))
        _, players = espn.build_history(result.get("leagues") or list(cfg.LEAGUES), verbose=False)
        new_all = _reprice(result, odds, players=players)
        new_pk = [k for k in new_all if k["event_id"] == event_id]
        m_now = next(x for x in result["matches"] if x["event_id"] == event_id)
        m_now["sim_inputs"] = {**prev, "odds": fp["odds"]}
        m_now["last_sim"] = ls = _last_sim("repriced", old, m_now, old_pk, new_pk, odds_info, bool(custom))
        _save(result)
        _log_picks(new_all)
        return {"mode": "repriced", "custom": bool(custom), "odds": odds_info, "last_sim": ls,
                "before": {"teams": {s_: {k: old["teams"][s_][k] for k in ("shots", "sot")} for s_ in ("home", "away")},
                           "lineup_confirmed": old.get("lineup_confirmed"), "picks": old_picks},
                "after": {"teams": {s_: {k: m_now["teams"][s_][k] for k in ("shots", "sot")} for s_ in ("home", "away")},
                          "lineup_confirmed": m_now.get("lineup_confirmed"), "picks": len(new_pk)},
                "n_sim": 0}
    ctx = _context(list(cfg.LEAGUES), False, cfg.N_SIM, allow_fetch=False)
    squad = (_custom_squad(old, custom, ctx["hist"], ctx["asof"]) if custom
             else (lu.complete_only(old.get("official_squad")) or official_lineup(ev)[0]))
    match, mp = simulate_event(ev, ctx, squad)
    if custom:
        # Simulado con la alineación del usuario: la probable/oficial queda guardada
        # para poder volver, y estas jugadas no van al registro.
        match.update(lineup=old.get("lineup"), lineup_confirmed=old.get("lineup_confirmed"), custom_lineup=True,
                     pos_alerts=old.get("pos_alerts") or [])
        for k in ("official_squad", "lineup_official", "lineup_source", "lineup_official_at"):
            if old.get(k) is not None:
                match[k] = old[k]
    elif lu.complete_only(old.get("official_squad")):
        match.update(official_squad=old["official_squad"], lineup_official=True,
                     lineup_source=old.get("lineup_source"), lineup_official_at=old.get("lineup_official_at"))
    _keep_lineup_state(old, match)
    match["sim_inputs"] = fp
    match["last_sim"] = _last_sim("simulated", old, match, old_pk, mp, odds_info, bool(custom))
    result["matches"] = [match if x["event_id"] == event_id else x for x in result["matches"]]
    result["picks"] = [k for k in result["picks"] if k["event_id"] != event_id] + mp
    repriced = []
    if odds_info.get("refreshed"):
        # Cuotas nuevas: el resto de la jornada se reprecia sin volver a simular.
        repriced = _reprice(result, ctx["odds"], skip=event_id, players=ctx["players"])
    _save(result)
    if not custom:
        _log_picks(mp)
    if repriced:
        _log_picks(repriced)
    return {"mode": "simulated", "custom": bool(custom), "odds": odds_info, "last_sim": match["last_sim"], "before": {"teams": {s_: {k: old["teams"][s_][k] for k in ("shots", "sot")} for s_ in ("home", "away")},
                       "lineup_confirmed": old.get("lineup_confirmed"),
                       "picks": old_picks},
            "after": {"teams": {s_: {k: match["teams"][s_][k] for k in ("shots", "sot")} for s_ in ("home", "away")},
                      "lineup_confirmed": match["lineup_confirmed"], "picks": len(mp)},
            "n_sim": cfg.N_SIM}


def _print_match(m: dict) -> None:
    h, a = m["teams"]["home"], m["teams"]["away"]
    fh = (m.get("lineup") or {}).get("home") or {}
    fa = (m.get("lineup") or {}).get("away") or {}
    print(f"\n{m['league_name']} · {m['date'][:16].replace('T', ' ')} · {h['name']} vs {a['name']}  "
          f"{'[formación confirmada]' if m['lineup_confirmed'] else '[XI esperado]'} "
          f"{fh.get('formation') or ''} / {fa.get('formation') or ''}"
          f"{'' if m['oddspapi_fixture'] else '  (sin cuotas)'}")
    print(f"   remates  {h['abbr']} {h['shots']:.1f} ({h['shots_range'][0]:.0f}-{h['shots_range'][1]:.0f})"
          f"  ·  {a['abbr']} {a['shots']:.1f} ({a['shots_range'][0]:.0f}-{a['shots_range'][1]:.0f})")
    print(f"   al arco  {h['abbr']} {h['sot']:.1f}  ·  {a['abbr']} {a['sot']:.1f}")
    for p in m["players"][:6]:
        l1 = next((x for x in p["lines"] if x["stat"] == "shots" and x["line"] == 0.5), None)
        l2 = next((x for x in p["lines"] if x["stat"] == "sot" and x["line"] == 0.5), None)
        print(f"     {p['player'][:22]:22} {p['pos']} {p['exp_minutes']:>3}'  remates {p['shots']:.2f}"
              f"  P(1+) {l1['p_model'] if l1 else 0:.0%}  al arco P(1+) {l2['p_model'] if l2 else 0:.0%}")


def _print_picks(picks: list[dict]) -> None:
    if not picks:
        return
    print(f"\nPICKS ({len(picks)}): probabilidad {PICK_P_MIN:.0%}-{PICK_P_MAX:.0%} y la mejor cuota ≥ cuota mínima")
    for k in picks[:25]:
        st = "remates" if k["stat"] == "shots" else "al arco"
        print(f"   {k['player'][:22]:22} {st:7} {int(k['line'] + 0.5)}+  modelo {k['p_model']:.0%}  "
              f"justa {k['fair_odds']:.2f}  mín {k['min_odds']:.2f}  {k['best_book']} {k['best_price']:.2f}  "
              f"({k['match'][:34]})")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Panorama de remates de la jornada")
    ap.add_argument("--leagues", default=",".join(cfg.LEAGUES))
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--refresh-odds", action="store_true", help="volver a pedir cuotas (gasta 3 consultas)")
    ap.add_argument("--no-lineups", action="store_true")
    ap.add_argument("--check-lineups", action="store_true",
                    help="re-simular sólo los partidos que confirmaron formación")
    ap.add_argument("--lineup-speed", action="store_true",
                    help="qué fuente trajo antes los titulares (minutos antes del inicio)")
    a = ap.parse_args(argv)
    if a.lineup_speed:
        print(lineup_speed().to_string())
        return
    if a.check_lineups:
        up = check_lineups(verbose=True)
        print(f"{len(up)} partido(s) con formación nueva" + (f": {[u['match'] for u in up]}" if up else ""))
        return
    run(a.leagues.split(","), a.days, a.refresh_odds, not a.no_lineups)


if __name__ == "__main__":
    main()
