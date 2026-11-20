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
# Un equipo cuenta como "con formación" recién con un XI casi completo: ESPN a
# veces publica primero sólo los arqueros (pasó en Sassuolo–Lazio).
MIN_XI = 10


def has_xi(squad: list[dict] | None, team_id) -> bool:
    """¿`squad` trae el XI de ese equipo (al menos MIN_XI titulares)?"""
    tid = str(team_id)
    return sum(1 for s in squad or [] if s.get("starter") and str(s["team_id"]) == tid) >= MIN_XI


def complete_only(squad: list[dict] | None) -> list[dict]:
    """Deja sólo los equipos con el XI completo; los demás se descartan enteros."""
    ok = {str(s["team_id"]) for s in squad or [] if has_xi(squad, s["team_id"])}
    return [s for s in squad or [] if str(s["team_id"]) in ok]


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


# Puesto de ESPN según formación + lugar en la formación (formationPlace). Sale
# del historial: en ESPN cada lugar es siempre el mismo puesto (100% de los casos).
PLACE_POS = {
    "3-1-4-2": {2: "RM", 3: "LM", 4: "CD", 5: "CD-R", 6: "CD-L", 7: "CM-R", 8: "SW", 9: "CF-R", 10: "CF-L", 11: "CM-L"},
    "3-2-4-1": {2: "CM-R", 3: "CM-L", 4: "LB", 5: "CD", 6: "RB", 7: "AM-R", 8: "AM-L", 9: "F", 10: "RM", 11: "LM"},
    "3-3-3-1": {2: "CM-R", 3: "CM-L", 4: "CD-L", 5: "CD", 6: "CD-R", 7: "AM", 8: "RCM", 9: "F", 10: "RM", 11: "LM"},
    "3-4-3-d": {2: "RM", 3: "LM", 4: "CD-L", 5: "CD", 6: "DM", 7: "AM", 8: "SW", 9: "F", 10: "RF", 11: "LF"},
    "3-4-1-2": {2: "RM", 3: "LM", 4: "CD-L", 5: "CD", 6: "CD-R", 7: "CM-R", 8: "CM-L", 9: "AM", 10: "CF-R", 11: "CF-L"},
    "3-4-2-1": {2: "RM", 3: "LM", 4: "CD-L", 5: "CD", 6: "CD-R", 7: "CM-R", 8: "CM-L", 9: "F", 10: "CF-R", 11: "CF-L"},
    "3-4-3": {2: "RM", 3: "LM", 4: "CD-L", 5: "CD", 6: "CD-R", 7: "CM-R", 8: "CM-L", 9: "F", 10: "CF-R", 11: "CF-L"},
    "3-5-1-1": {2: "RM", 3: "LM", 4: "CD-L", 5: "CD", 6: "CD-R", 7: "CM-R", 8: "CM-L", 9: "F", 10: "RCF", 11: "AM"},
    "3-5-2": {2: "RM", 3: "LM", 4: "CD-L", 5: "CD", 6: "CD-R", 7: "CM-R", 8: "CM-L", 9: "CF-L", 10: "CF-R", 11: "AM"},
    "4-1-2-1-2": {2: "RB", 3: "LB", 4: "DM", 5: "CD-R", 6: "CD-L", 7: "RM", 8: "AM", 9: "CF-L", 10: "CF-R", 11: "LM"},
    "4-1-3-2": {2: "RB", 3: "LB", 4: "SW", 5: "CD-R", 6: "CD-L", 7: "RM", 8: "CM", 9: "CF-R", 10: "CF-L", 11: "LM"},
    "4-1-4-1": {2: "RB", 3: "LB", 4: "DM", 5: "CD-R", 6: "CD-L", 7: "RM", 8: "CM-R", 9: "F", 10: "CM-L", 11: "LM"},
    # ESPN pone "M" en el lugar 10 (el segundo delantero) en los 76 partidos con
    # 4-3-1-2 del historial: es el CF-L (Gimnasia–Belgrano, 05/11/2026: Torres).
    "4-3-1-2": {2: "RB", 3: "LB", 4: "CM", 5: "CD-R", 6: "CD-L", 7: "RM", 8: "AM", 9: "CF-R", 10: "CF-L", 11: "LM"},
    "4-2-2-2": {2: "RB", 3: "LB", 4: "CM-R", 5: "CD-R", 6: "CD-L", 7: "AM-R", 8: "CM-L", 9: "CF-L", 10: "CF-R", 11: "AM-L"},
    "4-2-3-1": {2: "RB", 3: "LB", 4: "LM", 5: "CD-R", 6: "CD-L", 7: "AM-R", 8: "RM", 9: "F", 10: "AM", 11: "AM-L"},
    "4-3-2-1": {2: "RB", 3: "LB", 4: "CM", 5: "CD-R", 6: "CD-L", 7: "CM-L", 8: "CM-R", 9: "F", 10: "CF-R", 11: "CF-L"},
    "4-3-3": {2: "RB", 3: "LB", 4: "CM", 5: "CD-R", 6: "CD-L", 7: "RM", 8: "LM", 9: "F", 10: "RF", 11: "LF"},
    "4-4-1-1": {2: "RB", 3: "LB", 4: "CM-R", 5: "CD-R", 6: "CD-L", 7: "RM", 8: "CM-L", 9: "F", 10: "RCF", 11: "LM"},
    "4-4-2": {2: "RB", 3: "LB", 4: "CM-R", 5: "CD-R", 6: "CD-L", 7: "RM", 8: "CM-L", 9: "CF-L", 10: "CF-R", 11: "LM"},
    "4-5-1": {2: "RB", 3: "LB", 4: "CM-R", 5: "CD-R", 6: "CD-L", 7: "RM", 8: "CM-L", 9: "F", 10: "AM", 11: "LM"},
    "5-3-2": {2: "RB", 3: "LB", 4: "CD-L", 5: "CD", 6: "CD-R", 7: "CM-R", 8: "CM", 9: "CF-L", 10: "CF-R", 11: "CM-L"},
    "5-4-1": {2: "RB", 3: "LB", 4: "CD-L", 5: "CD", 6: "CD-R", 7: "RM", 8: "CM-R", 9: "F", 10: "CM-L", 11: "LM"},
}


def _depth(lab: str) -> float:
    """Qué tan adelantado es un puesto de ESPN (para ordenarlos por líneas)."""
    if lab in ("SW", "DM"):
        return 1.5
    if lab.startswith(("CD", "LB", "RB")):
        return 1.0
    if lab.startswith("AM"):
        return 3.0
    if lab == "F":
        return 4.5                     # el 9 solo va siempre en la última línea
    if lab in ("LF", "RF", "RCF") or lab.startswith("CF"):
        return 4.0
    return 2.0                         # LM / RM / CM*


def _lateral(lab: str) -> int:
    if lab in ("LB", "LM", "LF", "AM-L"):
        return -2
    if lab.endswith("-L"):
        return -1
    if lab in ("RB", "RM", "RF", "AM-R"):
        return 2
    if lab.endswith("-R") or lab == "RCF":
        return 1
    return 0


def grid_labels(formation: str | None, row_sizes: list[int]) -> list[list[str]] | None:
    """Etiquetas de ESPN para una grilla por líneas (sin el arquero), cada línea
    de izquierda a derecha. Sale de PLACE_POS: así una formación de otra fuente
    (Highlightly) usa los mismos puestos que aprendió el modelo con ESPN.
    None si la formación no está en la tabla o la grilla no coincide."""
    labs = list((PLACE_POS.get(str(formation or "")) or {}).values())
    try:
        lines = [int(x) for x in str(formation).split("-")]
    except ValueError:
        return None
    if not labs or lines != row_sizes or sum(lines) != len(labs):
        return None
    labs.sort(key=lambda s: (_depth(s), _lateral(s)))
    out, i = [], 0
    for n in lines:
        out.append(sorted(labs[i:i + n], key=_lateral))
        i += n
    return out


def place_positions(squad: list[dict]) -> list[dict]:
    """El puesto sale del lugar en la formación (formationPlace), no de la etiqueta
    de ESPN: antes del partido ESPN a veces manda puestos genéricos ("D", "M") o
    etiquetas que no corresponden al lugar (ej. un central puesto como volante),
    mientras que el lugar sí coincide con la formación real."""
    from .espn import position_group
    out = []
    for s_ in squad:
        pos = str(s_.get("pos") or "").upper()
        fp = s_.get("formation_place")
        exact = PLACE_POS.get(str(s_.get("formation") or ""), {}).get(int(fp)) if fp else None
        # Sólo para ESPN: API-Football numera el lugar a su manera.
        if s_.get("starter") and exact and pos not in ("G", "GK") and s_.get("src") in (None, "espn"):
            s_ = {**s_, "pos": exact, "pos_group": position_group(exact)}
        out.append(s_)
    return out


def fill_positions(squad: list[dict], players) -> list[dict]:
    """A veces ESPN publica los titulares sin puesto (todos "SUB", sin lugar en la
    cancha). A esos les ponemos el puesto que más jugaron de titulares últimamente,
    así la cancha se dibuja bien y el modelo sabe qué rol cumplen."""
    squad = place_positions(squad)
    miss = {s["player_id"] for s in squad if s.get("starter") and _no_pos(s.get("pos"))}
    if not miss or players is None or getattr(players, "empty", True):
        return squad
    st = players[players["player_id"].astype(str).isin(miss) & (players["starter"] == 1)]
    if st.empty:                       # nadie con historia de titular: queda como vino
        return squad
    st = st.loc[~st["pos"].map(_no_pos).astype(bool)].sort_values("date")
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


# ── Puesto raro: el jugador hoy está en un lugar donde casi nunca juega ──────
# Con la formación oficial, se compara el puesto de hoy de cada titular con el
# de sus últimas titularidades. Sirve de aviso para revisar en Modo DT: a veces
# las fuentes publican un dibujo y el equipo juega con otro (Juventus–Napoli,
# 01/11/2026: salió 3-4-1-2 con Cabal de carrilero derecho y jugó de lateral).

ALERT_LOOKBACK = 15        # últimas titularidades que se miran
ALERT_MIN_ROLE = 3         # titularidades mínimas para opinar del puesto
ALERT_MIN_SIDE = 2         # … y del lado
ALERT_SHARE = 0.2          # el puesto de hoy aparece en menos de esta fracción → aviso
ROLE_LABEL = {"GK": "arquero", "CB": "central", "FB": "lateral", "DM": "5", "CM": "volante",
              "WM": "volante por afuera", "AM": "enganche", "WG": "extremo", "ST": "centrodelantero"}
# Puestos vecinos: pasar de uno al otro es normal y no se avisa.
_NEAR = {frozenset(x) for x in (("CM", "DM"), ("CM", "AM"), ("WG", "ST"), ("AM", "WG"), ("CM", "WM"))}


def _wide_mids(formation: str | None) -> bool:
    """¿El dibujo tiene volantes/carrileros abiertos? Sí con línea de 3 o de 5
    atrás y en el 4-4-x; en el 4-2-3-1 o el 4-3-3, "LM/RM" de ESPN es el doble 5."""
    f = str(formation or "").strip()
    return f.startswith(("3", "5", "4-4"))


def alert_role(pos: str | None, formation: str | None = None) -> str | None:
    """Puesto de la cancha (más fino que el rol del modelo: separa el 5, el
    volante por afuera / carrilero y el lateral). None si el puesto es genérico
    ("D", "M", "F": API-Football a veces no dice dónde juega)."""
    if _no_pos(pos):
        return None
    # Dibujo desconocido (None, "" o NaN de la historia de ESPN) = no se sabe:
    # no hay que leer "LM/RM" como doble 5. Eso tapó el aviso de Bruno Gomes
    # de enganche (Internacional–Coritiba, 03/11/2026).
    if not isinstance(formation, str) or not formation.strip():
        formation = None
    a = str(pos).upper()
    if a in ("D", "M", "F"):
        return None
    ln, sd = line_of(pos), side_of(pos)
    if ln == 0:
        return "GK"
    if ln == 1:
        return "CB" if a.startswith(("CD", "CB")) or sd == 0 else "FB"
    if ln == 2:
        return "DM"
    if ln == 3:
        return "WM" if a in ("LM", "RM", "LWB", "RWB") and (formation is None or _wide_mids(formation)) else "CM"
    if ln == 4:
        return "WG" if sd else "AM"
    return "WG" if sd else "ST"


def _label(role: str, formation: str | None = None) -> str:
    f = str(formation or "")
    return "carrilero" if role == "WM" and f.startswith(("3", "5")) else ROLE_LABEL[role]


def _side_word(sd: int) -> str:
    return "por izquierda" if sd < 0 else "por derecha"


def pos_alerts(squad: list[dict], players: pd.DataFrame, asof, teams: pd.DataFrame | None = None) -> list[dict]:
    """Titulares de la formación oficial que hoy están en un puesto (o un lado)
    donde casi no jugaron en sus últimas titularidades.
    → [{team_id, player_id, player, pos, kind, today, usual, n, of, text}]"""
    if not squad or players is None or getattr(players, "empty", True):
        return []
    starters = [s for s in squad if s.get("starter") and not _no_pos(s.get("pos"))]
    ids = {str(s["player_id"]) for s in starters}
    cut = pd.Timestamp(asof)
    cut = cut.tz_convert(None) if cut.tzinfo else cut
    d = pd.to_datetime(players["date"])
    d = d.dt.tz_convert(None) if d.dt.tz is not None else d
    h = players[players["player_id"].astype(str).isin(ids) & (players["starter"] == 1) & (d < cut.normalize())]
    h = h.loc[~h["pos"].map(_no_pos).astype(bool)].sort_values("date")
    # El dibujo de cada partido sale de la historia de los equipos (`teams`).
    fmap = {}
    if teams is not None and not teams.empty and "formation" in teams:
        fmap = {(str(e), str(t)): f for e, t, f in zip(teams["event_id"], teams["team_id"], teams["formation"])}
    h = h.assign(_f=[fmap.get((str(e), str(t))) for e, t in zip(h["event_id"], h["team_id"])])
    hist = {str(pid): list(zip(g["pos"].tail(ALERT_LOOKBACK), g["_f"].tail(ALERT_LOOKBACK)))
            for pid, g in h.groupby("player_id")}
    out = []
    for s in starters:
        past = hist.get(str(s["player_id"])) or []
        today = alert_role(s.get("pos"), s.get("formation") or "")
        if not past or today is None:
            continue
        roles = [alert_role(p, f) for p, f in past]
        roles = [r for r in roles if r]
        base = {"team_id": str(s["team_id"]), "player_id": str(s["player_id"]), "player": s.get("player"),
                "pos": s.get("pos"), "today": today}
        if len(roles) >= ALERT_MIN_ROLE:
            same = sum(1 for r in roles if r == today or frozenset((r, today)) in _NEAR)
            if same / len(roles) < ALERT_SHARE:
                usual = pd.Series(roles).value_counts()
                u = usual.index[0]
                out.append({**base, "kind": "role", "usual": u, "n": int(usual.iloc[0]), "of": len(roles),
                            "text": f"hoy {_label(today, s.get('formation'))} · suele jugar de {_label(u)} ({int(usual.iloc[0])} de {len(roles)})"})
                continue
        sd = side_of(s.get("pos"))
        if sd and today in ("FB", "WM", "WG"):
            sides = [side_of(p) for p, f in past if side_of(p) and alert_role(p, f) in ("FB", "WM", "WG")]
            if len(sides) >= ALERT_MIN_SIDE:
                same = sum(1 for x in sides if x == sd)
                if same / len(sides) < ALERT_SHARE:
                    out.append({**base, "kind": "side", "usual": "L" if sd > 0 else "R", "n": len(sides) - same, "of": len(sides),
                                "text": f"hoy {_label(today, s.get('formation'))} {_side_word(sd)} · suele jugar {_side_word(-sd)} ({len(sides) - same} de {len(sides)})"})
    return out
