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
import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from futbol import config as cfg
from futbol import apifootball as af
from futbol import espn
from futbol import lineups as lu
from futbol import market as mkt
from futbol import model as fm
from futbol import oddspapi as op
from futbol import photos
from futbol import picklog

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
SUBPLUS_BOOKS = {"bet365.bet.ar", "betano", "betsson"}
SUBPLUS_MIN_START = 0.6
JORNADA = cfg.DATA_DIR / "jornada.json"
# Ventana en la que se le pregunta a ESPN si ya salió la formación.
LINEUP_WINDOW_H = 2.0


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


def simulate_event(ev: dict, ctx: dict, squad: list[dict] | None) -> tuple[dict, list[dict]]:
    """Un partido → (bloque del partido para el JSON, picks)."""
    lg = ev["league"]
    if lg not in ctx["tms"]:
        ctx["tms"][lg] = fm.fit_team_model(ctx["teams"][ctx["teams"]["league"] == lg], ctx["asof"])
    tm = ctx["tms"][lg]
    squad = lu.fill_positions(squad or [], ctx["players"])
    confirmed = any(s["starter"] for s in squad)
    by_team, lineup = {}, {}
    for side in ("home", "away"):
        tid = ev[f"{side}_id"]
        sq = [s for s in squad if s["team_id"] == tid] if confirmed else None
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
        mo = mo.assign(player_id=mo["player_key"].map(lambda k: op.match_player(k, cands)))

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
    for p in sorted(sim["players"], key=lambda x: -x["shots_mean"]):
        if p["exp_minutes"] < 15 or p["pos_group"] == "G":
            continue
        lines = []
        for stat, ls in (("shots", cfg.SHOT_LINES), ("sot", cfg.SOT_LINES)):
            for line in ls:
                pm = fm.p_over(p[f"{stat}_dist"], line)
                if pm < 0.02:
                    continue
                starter_like = confirmed and p["p_start"] >= 1.0 or (not confirmed and p["p_start"] >= SUBPLUS_MIN_START)
                psp = fm.p_over(p[f"{stat}_sp_dist"], line) if starter_like else pm
                prices = {}
                if not mo.empty:
                    q = mo[(mo["player_id"] == p["player_id"]) & (mo["stat"] == stat)
                           & (np.isclose(mo["line"], line))]
                    for r in q.itertuples():
                        # Cordura: una cuota 3× la justa del modelo es casi
                        # siempre un cruce de jugador equivocado.
                        if r.price > SANITY_X / pm:
                            ctx["suspicious"].append((p["player"], stat, line, r.book, r.price))
                            continue
                        prices[r.book] = {"price": r.price, "p_book": round(float(r.p_book), 3)}
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
                        "pos": p["pos_group"], "role": p.get("role"), "exp_minutes": round(p["exp_minutes"]),
                        "lineup": confirmed, **row}
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
        players_out.append({
            "player_id": p["player_id"], "player": p["player"], "team_id": p["team_id"],
            "side": p["side"], "pos": p["pos_group"], "role": p.get("role"), "phi": round(p.get("phi", 0.0), 3),
            "p_play": round(p.get("p_play", 1.0), 3), "p_start": round(p["p_start"], 2),
            "exp_minutes": round(p["exp_minutes"]), "shots": round(p["shots_mean"], 2),
            "sot": round(p["sot_mean"], 2), "share90": round(p["share90"], 3),
            "acc": round(p["acc"], 3), "minutes_history": round(p["n_min"]),
            "photo": ctx["photo_of"].get(str(p["player_id"]), {}).get("url"), "lines": lines})
    picks = _cap_picks(picks)
    match = {
        "event_id": ev["event_id"], "league": lg, "league_name": cfg.LEAGUES[lg]["name"],
        "date": ev["date"], "venue": ev.get("venue"), "lineup_confirmed": confirmed,
        "lineup": lineup, "oddspapi_fixture": fx, "teams": teams_out, "market": market,
        "total": {"shots": round(sim["total"]["shots_mean"], 2), "sot": round(sim["total"]["sot_mean"], 2)},
        "players": players_out,
        "keepers": {s_: team_keepers(ctx["players"], ev[f"{s_}_id"], ctx["photo_of"]) for s_ in ("home", "away")},
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ev": ev,
    }
    return match, picks


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


def run(leagues=None, days: int = 4, refresh_odds: bool = False, use_lineups: bool = True,
        n_sim: int = cfg.N_SIM, verbose: bool = True) -> dict:
    leagues = leagues or list(cfg.LEAGUES)
    ctx = _context(leagues, refresh_odds, n_sim)
    events = espn.upcoming(leagues, days=days)
    if verbose:
        print(f"{len(events)} partidos en los próximos {days} días · historia: "
              f"{ctx['teams']['event_id'].nunique()} partidos")
    out_matches, picks = [], []
    for ev in events:
        squad = espn.lineup(ev) if use_lineups else []
        match, mp = simulate_event(ev, ctx, squad)
        out_matches.append(match)
        picks += mp
        if verbose:
            _print_match(match)
    result = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "leagues": leagues, "matches": out_matches, "picks": picks, "min_edge": MIN_EDGE}
    _save(result)
    _log_picks(picks)
    if verbose:
        if ctx["suspicious"]:
            print(f"\n{len(ctx['suspicious'])} cuotas descartadas por sospecha de cruce equivocado "
                  f"(> {SANITY_X:g}× la cuota justa), p. ej.: {ctx['suspicious'][:3]}")
        _print_picks(result["picks"])
    return result


def check_lineups(window_h: float = LINEUP_WINDOW_H, verbose: bool = False) -> list[dict]:
    """Partidos de la jornada que confirmaron formación desde la última vez:
    se re-simulan SOLO esos, con la formación real. No gasta cuotas."""
    if not JORNADA.exists():
        return []
    result = json.loads(JORNADA.read_text(encoding="utf-8"))
    now = pd.Timestamp.now(tz="UTC")
    pending = [m for m in result["matches"]
               if not m.get("lineup_confirmed") and not m.get("lineup_official") and m.get("ev")
               and -0.5 <= (pd.Timestamp(m["date"]) - now).total_seconds() / 3600 <= window_h]
    # Prioritarios para el cupo de API-Football: los que seguís y los que tienen jugadas.
    try:
        fav = set(json.loads((cfg.DATA_DIR / "prefs.json").read_text(encoding="utf-8")).get("fav", []))
    except Exception:  # noqa: BLE001
        fav = set()
    with_picks = {k["event_id"] for k in result.get("picks", [])}
    cands = None
    newly = []
    for m in pending:
        squad, source = espn.lineup(m["ev"]), "espn"
        if not any(s["starter"] for s in squad) and af.should_ask(m["ev"], m["event_id"] in fav or m["event_id"] in with_picks):
            # ESPN todavía no la tiene: API-Football (la LPF sale ~40-60' antes ahí).
            if cands is None:
                _, hp = espn.build_history(list(af.AF_LEAGUES), verbose=False)
                hp = hp.sort_values("date").groupby("player_id").tail(1)
                cands = {}
                for r in hp.itertuples():
                    cands.setdefault(str(r.team_id), {})[espn.norm_name(r.player)] = str(r.player_id)
            try:
                squad, source = af.lineup(m["ev"], cands), "api-football"
            except Exception as exc:  # noqa: BLE001 - sin cupo o sin red: sigue con ESPN
                if verbose:
                    print(f"  API-Football: {exc}")
                squad = []
        if any(s["starter"] for s in squad):
            newly.append((m, squad, source))
    if not newly:
        return []
    # NO se re-simula solo: se guarda la alineación oficial y se avisa. La
    # simulación con ese XI corre cuando el usuario toca "Simular partido".
    updated = []
    hist_players = None
    for m, squad, source in newly:
        if any(s["starter"] and lu._no_pos(s.get("pos")) for s in squad):
            if hist_players is None:
                _, hist_players = espn.build_history(list(cfg.LEAGUES), verbose=False)
            squad = lu.fill_positions(squad, hist_players)
        m["official_squad"] = squad
        m["lineup_official"] = True
        m["lineup_source"] = source
        m["lineup_official_at"] = now.isoformat(timespec="seconds")
        m["lineup"] = {side: (lu.confirmed_xi(squad, m["ev"][f"{side}_id"]) or (m.get("lineup") or {}).get(side))
                       for side in ("home", "away")}
        updated.append({"event_id": m["event_id"], "match": f"{m['ev']['home']} vs {m['ev']['away']}",
                        "picks": 0, "source": source, "simulated": False})
    result["lineups_checked_at"] = now.isoformat(timespec="seconds")
    _save(result)
    return updated


def _log_picks(picks: list[dict]) -> None:
    try:
        picklog.log(picks)
    except Exception as exc:  # noqa: BLE001 - el registro nunca corta la jornada
        print(f"  registro de picks: {exc}")


def _custom_squad(old: dict, custom: dict) -> list[dict]:
    """Modo DT: arma el plantel con el XI que eligió el usuario (titulares) y el
    resto del plantel como suplentes. El equipo que no tocó usa la oficial o la probable."""
    ev = old["ev"]
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
        ids = {x["player_id"] for x in xi}
        bench = [{"team_id": tid, "formation": None, "formation_place": None, "player_id": str(p["player_id"]),
                  "player": p.get("player"), "starter": 0, "pos": "SUB", "pos_group": "?"}
                 for p in old.get("players") or [] if str(p.get("team_id")) == tid and str(p["player_id"]) not in ids]
        squad += xi + bench
    return squad


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
    squad = _custom_squad(old, custom) if custom else (old.get("official_squad") or espn.lineup(ev))
    ctx = _context([ev["league"]], False, cfg.N_SIM, allow_fetch=False)
    match, mp = simulate_event(ev, ctx, squad)
    if custom:
        # Simulado con la alineación del usuario: la probable/oficial queda guardada
        # para poder volver, y estas jugadas no van al registro.
        match.update(lineup=old.get("lineup"), lineup_confirmed=old.get("lineup_confirmed"), custom_lineup=True)
        for k in ("official_squad", "lineup_official", "lineup_source", "lineup_official_at"):
            if old.get(k) is not None:
                match[k] = old[k]
    elif old.get("official_squad"):
        match.update(official_squad=old["official_squad"], lineup_official=True,
                     lineup_source=old.get("lineup_source"), lineup_official_at=old.get("lineup_official_at"))
    result["matches"] = [match if x["event_id"] == event_id else x for x in result["matches"]]
    result["picks"] = [k for k in result["picks"] if k["event_id"] != event_id] + mp
    _save(result)
    if not custom:
        _log_picks(mp)
    return {"custom": bool(custom), "before": {"teams": {s_: {k: old["teams"][s_][k] for k in ("shots", "sot")} for s_ in ("home", "away")},
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
    ap.add_argument("--leagues", default="arg,bra,eng")
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--refresh-odds", action="store_true", help="volver a pedir cuotas (gasta 3 consultas)")
    ap.add_argument("--no-lineups", action="store_true")
    ap.add_argument("--check-lineups", action="store_true",
                    help="re-simular sólo los partidos que confirmaron formación")
    a = ap.parse_args(argv)
    if a.check_lineups:
        up = check_lineups(verbose=True)
        print(f"{len(up)} partido(s) con formación nueva" + (f": {[u['match'] for u in up]}" if up else ""))
        return
    run(a.leagues.split(","), a.days, a.refresh_odds, not a.no_lineups)


if __name__ == "__main__":
    main()
