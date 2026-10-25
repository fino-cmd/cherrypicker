"""
Futbol — XI esperado y formación confirmada.

XI esperado (al estilo Sofascore, pero con nuestra propia historia):
  • esquema: el del último partido del equipo (ESPN, p. ej. "4-2-3-1");
  • para cada puesto del esquema (1–11), el jugador que más lo ocupó en los
    últimos LOOKBACK partidos, con más peso a los recientes; si nadie lo
    ocupó, el que más jugó en la misma línea (arquero/defensa/medio/ataque);
  • probabilidad de ser titular = peso de sus titularidades recientes;
    debajo de DOUBT se marca como DUDA.

Formación confirmada: la que publica ESPN ~1 h antes del partido.

Para dibujarla en una cancha, cada jugador lleva su línea (0 arquero … 5
delanteros) y su lado (−1 izquierda, 0 centro, +1 derecha), sacados de su
posición en ese puesto (LB, CD-L, AM-R, CF…).
"""

from __future__ import annotations

import pandas as pd

LOOKBACK = 5
DECAY = 0.75
DOUBT = 0.6


def line_of(pos: str | None) -> int:
    """0 arquero · 1 defensa · 2 volante central · 3 medio · 4 enganche/extremo · 5 delantero."""
    a = str(pos or "").upper()
    if a in ("G", "GK"):
        return 0
    if a.startswith(("DM", "CDM", "SW")):          # ESPN usa "SW" para el tapón del 4-1-3-2
        return 2
    if a.startswith(("CD", "LB", "RB", "CB", "D", "LWB", "RWB", "WB")):
        return 1
    if a.startswith(("AM", "LW", "RW", "CAM")):
        return 4
    if a.startswith(("F", "CF", "ST", "S", "LF", "RF")) and a != "SUB":
        return 5
    return 3


def side_of(pos: str | None) -> int:
    a = str(pos or "").upper()
    if a.startswith(("L",)) or a.endswith("-L"):
        return -1
    if a.startswith(("R",)) or a.endswith("-R"):
        return 1
    return 0


def _group(line: int) -> str:
    return {0: "G", 1: "D", 2: "M", 3: "M", 4: "M", 5: "F"}[line]


def expected_xi(players: pd.DataFrame, teams: pd.DataFrame, team_id: str, asof) -> dict | None:
    """XI esperado de `team_id` para un partido en `asof` (sólo con historia anterior)."""
    asof = pd.Timestamp(asof)
    t = teams[(teams["team_id"] == team_id) & (teams["date"] < asof)].sort_values("date")
    if t.empty:
        return None
    recent = t.tail(LOOKBACK)
    events = recent["event_id"].tolist()[::-1]                      # más reciente primero
    weights = {e: DECAY ** i for i, e in enumerate(events)}
    wsum = sum(weights.values())
    formation = next((f for f in recent["formation"].tolist()[::-1] if isinstance(f, str) and f), None) \
        if "formation" in recent.columns else None

    st = players[(players["team_id"] == team_id) & players["event_id"].isin(events) & (players["starter"] == 1)]
    if st.empty:
        return None
    last = st[st["event_id"] == events[0]]
    if "formation_place" not in last.columns or last["formation_place"].isna().all():
        slots = {i + 1: r.pos for i, r in enumerate(last.itertuples())}
    else:
        slots = {int(r.formation_place): r.pos for r in last.itertuples() if pd.notna(r.formation_place)}

    # p(titular) de cada jugador en las últimas fechas
    w_start = st.groupby("player_id")["event_id"].apply(lambda s: sum(weights[e] for e in s) / wsum)
    names = st.groupby("player_id")["player"].last()

    # Puntaje (jugador, puesto): mismo puesto pesa 1; misma línea, 0,4.
    scores = []
    for pid, g in st.groupby("player_id"):
        for place, pos in slots.items():
            same = sum(weights[r.event_id] for r in g.itertuples()
                       if "formation_place" in g.columns and pd.notna(r.formation_place)
                       and int(r.formation_place) == place)
            line = line_of(pos)
            near = sum(weights[r.event_id] for r in g.itertuples()
                       if _group(line_of(r.pos)) == _group(line))
            sc = same + 0.4 * near
            if sc > 0:
                scores.append((sc, pid, place))
    scores.sort(reverse=True)
    used_p, used_s, xi = set(), set(), []
    for sc, pid, place in scores:
        if pid in used_p or place in used_s:
            continue
        used_p.add(pid)
        used_s.add(place)
        pos = slots[place]
        p = float(w_start.get(pid, 0.0))
        xi.append({"player_id": str(pid), "player": names.get(pid), "place": place, "pos": pos,
                   "line": line_of(pos), "side": side_of(pos), "p_start": round(p, 2),
                   "doubt": p < DOUBT})
        if len(xi) == 11:
            break
    xi.sort(key=lambda x: (x["line"], x["side"], x["place"]))
    return {"formation": formation, "confirmed": False, "players": xi}


def _no_pos(pos) -> bool:
    return str(pos or "").upper() in ("", "SUB", "NONE")


def fill_positions(squad: list[dict], players) -> list[dict]:
    """A veces ESPN publica los titulares sin puesto (todos "SUB", sin lugar en la
    cancha). A esos les ponemos el puesto que más jugaron de titulares últimamente,
    así la cancha se dibuja bien y el modelo sabe qué rol cumplen."""
    miss = {s["player_id"] for s in squad if s.get("starter") and _no_pos(s.get("pos"))}
    if not miss or players is None or getattr(players, "empty", True):
        return squad
    st = players[players["player_id"].astype(str).isin(miss) & (players["starter"] == 1)]
    st = st[~st["pos"].map(_no_pos)].sort_values("date")
    usual = {str(pid): g["pos"].tail(8).mode().iloc[-1] for pid, g in st.groupby("player_id")}
    from .espn import position_group
    out = []
    for s_ in squad:
        if s_["player_id"] in miss and s_["player_id"] in usual:
            s_ = {**s_, "pos": usual[s_["player_id"]], "pos_group": position_group(usual[s_["player_id"]])}
        out.append(s_)
    return out


def confirmed_xi(squad: list[dict], team_id: str) -> dict | None:
    """Formación confirmada desde espn.lineup()."""
    st = [s for s in squad if s["team_id"] == team_id and s.get("starter")]
    if not st:
        return None
    formation = next((s.get("formation") for s in st if s.get("formation")), None)
    xi = [{"player_id": s["player_id"], "player": s["player"], "place": s.get("formation_place"),
           "pos": s.get("pos"), "line": line_of(s.get("pos")), "side": side_of(s.get("pos")),
           "p_start": 1.0, "doubt": False} for s in st]
    xi.sort(key=lambda x: (x["line"], x["side"], x["place"] or 0))
    return {"formation": formation, "confirmed": True, "players": xi}
