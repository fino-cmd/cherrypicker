"""
Futbol — modelo de remates y remates al arco.

No pronostica el resultado: pronostica CUÁNTO se remata y QUIÉN remata.

1. Equipo (por liga, sólo con partidos anteriores a la fecha):
     remates(i vs j) ~ BinNeg(λ, r)
     log λ = μ + h·local + ataque_i + defensa_j          (Poisson con ridge)
     puntería(i vs j): logit p = ν + punt_i + permite_j  (binomial con ridge)
   Peso por recencia (semivida en días) y shrinkage hacia el promedio de la
   liga (el ridge equivale a TEAM_PRIOR_K partidos "virtuales" en 1.0).
   r (sobre-dispersión) se mide en la propia liga.

2. Jugador:
     porción = remates del jugador / remates del equipo mientras él está en
               cancha (por minuto), con prior de su posición (G/D/M/F);
     puntería = al arco / remates, con prior de su posición;
     minutos   = de la alineación confirmada o, si no la hay, de su historial
               reciente (titularidades, minutos al ser titular, chance de
               entrar desde el banco).

3. Simulación del partido entero: por cada iteración se sortean los remates
   de cada equipo, los minutos de cada jugador y un factor de puntería del
   día; los remates del equipo se REPARTEN entre los jugadores (multinomial)
   según porción × minutos, y cada remate va al arco con la puntería del
   jugador. Así todo es coherente: si el equipo remata más, todos rematan
   más, y los remates de los jugadores suman los del equipo.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from futbol import config as cfg

POS = ("G", "D", "M", "F")
DEFAULT_START_MIN = {"G": 90.0, "D": 86.0, "M": 78.0, "F": 72.0}
DEFAULT_SUB_MIN = 22.0

# Rol según el puesto de ESPN en la formación (LM/RM son los dobles cinco del
# 4-2-3-1; LF/RF y AM-L/AM-R, los extremos).
ROLES = ("GK", "CB", "FB", "CM", "AM", "WG", "ST")
ROLE_NAME = {"GK": "Arquero", "CB": "Central", "FB": "Lateral", "CM": "Volante",
             "AM": "Enganche", "WG": "Extremo", "ST": "Centrodelantero"}
ROLE_OF_GROUP = {"G": "GK", "D": "CB", "M": "CM", "F": "ST"}
GROUP_OF_ROLE = {"GK": "G", "CB": "D", "FB": "D", "CM": "M", "AM": "M", "WG": "F", "ST": "F"}


DM_POS = ("DM", "CDM", "SW")          # ESPN/API-Football: el 5 (SW = tapón del 4-1-3-2)


def _dm_pos(a: str) -> bool:
    # "CM" a secas es el del medio de una línea de tres (LM-CM-RM): el que tapa.
    return a.startswith(DM_POS) or a == "CM"


def is_dm(today, hist_pos=None) -> bool:
    """¿Juega de mediocampista defensivo? El puesto de hoy manda; si no hay,
    el más común de sus últimos 10 partidos. Sólo para mostrar."""
    t = str(today or "").upper()
    if t and t not in ("SUB", "?"):
        return _dm_pos(t)
    if hist_pos is None:
        return False
    hp = [str(x).upper() for x in hist_pos.dropna() if str(x).upper() not in ("SUB", "?", "")][-10:]
    return bool(hp) and _dm_pos(pd.Series(hp).value_counts().index[0])


def role_of(pos) -> str | None:
    a = str(pos or "").upper()
    if not a or a == "SUB" or a == "?":
        return None
    if a in ("G", "GK"):
        return "GK"
    if a.startswith(("CD", "CB", "SW")) or a == "D":
        return "CB"
    if a in ("LB", "RB", "LWB", "RWB", "WB"):
        return "FB"
    if a in ("AM", "CAM"):
        return "AM"
    if a in ("AM-L", "AM-R", "LF", "RF", "LW", "RW"):
        return "WG"
    if a in ("F", "CF", "ST", "S") or a.startswith(("CF", "RCF", "LCF")):
        return "ST"
    return "CM"


def _rng(*parts) -> np.random.Generator:
    h = hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=8).digest()
    return np.random.default_rng(np.random.SeedSequence([cfg.SEED, int.from_bytes(h, "little")]))


def _weights(dates: pd.Series, asof: pd.Timestamp, halflife_days: float) -> np.ndarray:
    age = (asof - pd.to_datetime(dates)).dt.days.clip(lower=0).to_numpy(float)
    return 0.5 ** (age / halflife_days)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Equipos
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TeamModel:
    mu: float
    home: float
    att: dict
    dfn: dict
    nu: float
    acc: dict
    acc_allow: dict
    disp: float               # r de la binomial negativa (remates)
    acc_disp: float           # k del factor de puntería del día (Gamma(k, 1/k))
    league_shots: float
    league_acc: float

    def expected(self, team: str, opp: str, home: int) -> tuple[float, float]:
        lam = math.exp(self.mu + self.home * (1 if home else -1) + self.att.get(team, 0.0)
                       + self.dfn.get(opp, 0.0))
        z = self.nu + self.acc.get(team, 0.0) + self.acc_allow.get(opp, 0.0)
        return lam, 1.0 / (1.0 + math.exp(-z))


def _team_calibration() -> dict:
    """Sesgo de nivel y dispersión por liga medidos fuera de muestra (futbol.calibrate)."""
    try:
        import json as _json
        return _json.loads((cfg.DATA_DIR / "team_calibration.json").read_text(encoding="utf-8"))["leagues"]
    except Exception:  # noqa: BLE001 - sin archivo: sin corrección
        return {}


def fit_team_model(teams: pd.DataFrame, asof, halflife_matches: float = cfg.TEAM_HALFLIFE,
                   prior_k: float = cfg.TEAM_PRIOR_K, calibrated: bool = True) -> TeamModel:
    """Ajusta la liga con los partidos ANTERIORES a `asof`.

    Con `calibrated`, aplica el sesgo de nivel y la dispersión fuera de
    muestra de la liga (data/futbol/team_calibration.json).
    """
    asof = pd.Timestamp(asof)
    d = teams[(teams["date"] < asof) & teams["shots"].notna()].copy()
    if d.empty:
        raise ValueError("sin historia para ajustar el modelo de equipos")
    w = _weights(d["date"], asof, halflife_matches * 7.0)
    ids = sorted(set(d["team_id"]) | set(d["opp_id"].dropna()))
    ix = {t: i for i, t in enumerate(ids)}
    n = len(ids)
    ti = d["team_id"].map(ix).to_numpy()
    oi = d["opp_id"].map(ix).to_numpy()
    hm = np.where(d["home"].to_numpy() == 1, 1.0, -1.0)
    y = d["shots"].to_numpy(float)
    # ridge: prior_k partidos virtuales ≈ varianza del prior ~ 1/(k·λ̄)
    lam_bar = float(np.average(y, weights=w))
    ridge = prior_k * lam_bar * 0.5

    def nll(theta):
        mu, h = theta[0], theta[1]
        a, b = theta[2:2 + n], theta[2 + n:]
        eta = mu + h * hm + a[ti] + b[oi]
        lam = np.exp(eta)
        f = np.sum(w * (lam - y * eta)) + ridge * (a @ a + b @ b) / 2
        r = w * (lam - y)
        g = np.empty_like(theta)
        g[0] = r.sum()
        g[1] = (r * hm).sum()
        g[2:2 + n] = np.bincount(ti, weights=r, minlength=n) + ridge * a
        g[2 + n:] = np.bincount(oi, weights=r, minlength=n) + ridge * b
        return f, g

    th0 = np.zeros(2 + 2 * n)
    th0[0] = math.log(max(lam_bar, 0.5))
    res = minimize(nll, th0, jac=True, method="L-BFGS-B")
    th = res.x
    mu, h = th[0], th[1]
    att = {t: th[2 + ix[t]] for t in ids}
    dfn = {t: th[2 + n + ix[t]] for t in ids}

    # Nivel de la LIGA con ventana corta: el volumen de remates de toda la
    # liga se mueve en el año (2026 remata bastante más que 2025) y la fuerza
    # relativa de cada equipo no. Se re-estima μ con semivida corta, dejando
    # fijos local/ataque/defensa.
    ws = _weights(d["date"], asof, cfg.LEAGUE_HALFLIFE_DAYS)
    base = np.exp(h * hm + th[2:2 + n][ti] + th[2 + n:][oi])
    mu = float(np.log(np.sum(ws * y) / np.sum(ws * base)))

    # Sobre-dispersión: Var(y) ≈ λ + λ²/r  →  r por momentos (ponderado).
    lam_fit = np.exp(mu + h * hm + th[2:2 + n][ti] + th[2 + n:][oi])
    excess = np.average((y - lam_fit) ** 2 - lam_fit, weights=w)
    disp = float(np.clip(np.average(lam_fit ** 2, weights=w) / max(excess, 1e-6), 3.0, 200.0))

    # Puntería: binomial (al arco de remates) con ridge.
    m = d["sot"].notna() & (d["shots"] > 0)
    s = d.loc[m, "shots"].to_numpy(float)
    k = d.loc[m, "sot"].to_numpy(float).clip(0, None)
    k = np.minimum(k, s)
    wa = w[m.to_numpy()]
    ta, oa = ti[m.to_numpy()], oi[m.to_numpy()]
    p_bar = float(np.sum(wa * k) / np.sum(wa * s))
    ridge_a = cfg.ACC_PRIOR_K * p_bar * (1 - p_bar)

    def nll_a(theta):
        nu = theta[0]
        a, b = theta[1:1 + n], theta[1 + n:]
        z = nu + a[ta] + b[oa]
        p = 1 / (1 + np.exp(-z))
        f = -np.sum(wa * (k * np.log(p + 1e-12) + (s - k) * np.log(1 - p + 1e-12))) \
            + ridge_a * (a @ a + b @ b) / 2
        r = wa * (s * p - k)
        g = np.empty_like(theta)
        g[0] = r.sum()
        g[1:1 + n] = np.bincount(ta, weights=r, minlength=n) + ridge_a * a
        g[1 + n:] = np.bincount(oa, weights=r, minlength=n) + ridge_a * b
        return f, g

    tha0 = np.zeros(1 + 2 * n)
    tha0[0] = math.log(p_bar / (1 - p_bar))
    ra = minimize(nll_a, tha0, jac=True, method="L-BFGS-B").x
    # Puntería promedio de la liga, también con ventana corta (ν tal que los
    # al arco esperados igualen a los reales en las últimas semanas).
    wsa = ws[m.to_numpy()]
    off = ra[1:1 + n][ta] + ra[1 + n:][oa]
    lo_, hi_ = -4.0, 4.0
    for _ in range(50):
        mid = (lo_ + hi_) / 2
        pm_ = 1 / (1 + np.exp(-(mid + off)))
        if np.sum(wsa * s * pm_) < np.sum(wsa * k):
            lo_ = mid
        else:
            hi_ = mid
    ra[0] = (lo_ + hi_) / 2
    acc = {t: ra[1 + ix[t]] for t in ids}
    allow = {t: ra[1 + n + ix[t]] for t in ids}

    # Factor de puntería del día: sobre-dispersión de al arco | remates.
    z = ra[0] + ra[1:1 + n][ta] + ra[1 + n:][oa]
    pf = 1 / (1 + np.exp(-z))
    var_bin = s * pf * (1 - pf)
    excess_a = np.average((k - s * pf) ** 2 - var_bin, weights=wa)
    # Var extra ≈ (s·p)² / k_gamma
    acc_disp = float(np.clip(np.average((s * pf) ** 2, weights=wa) / max(excess_a, 1e-6), 4.0, 400.0))

    if calibrated and "league" in d.columns:
        cal = _team_calibration().get(str(d["league"].iloc[0]))
        if cal:
            mu += math.log(cal["level"])
            disp = float(cal["r"])
    return TeamModel(mu=mu, home=h, att=att, dfn=dfn, nu=ra[0], acc=acc, acc_allow=allow,
                     disp=disp, acc_disp=acc_disp, league_shots=lam_bar, league_acc=p_bar)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Jugadores
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PlayerProfile:
    player_id: str
    player: str
    team_id: str
    pos_group: str
    share90: float           # porción de los remates del equipo por 90' en cancha
    acc: float               # al arco / remates
    start_min: np.ndarray    # minutos de sus partidos como titular (para sortear)
    sub_min: np.ndarray      # minutos de sus partidos al entrar desde el banco
    p_enter_bench: float     # chance de entrar cuando está en el banco
    p_start: float           # chance de ser titular (sin alineación confirmada)
    n_min: float             # minutos de historia (peso de la evidencia)
    role: str = "CM"         # centrodelantero, extremo, enganche…
    sub_k: float = 1.0       # cuánto más remata por minuto cuando entra desde el banco
    phi: float = 0.0         # sobre-dispersión propia (0 = muy constante)
    dm: bool = False         # mediocampista defensivo (sólo para mostrar; el modelo lo trata como CM)


class History:
    """Historia de jugadores indexada para consultas rápidas "hasta la fecha X".

    La posición habitual se toma de TODA la historia (sólo resuelve a los
    suplentes que ESPN marca 'SUB'; no usa resultados).
    """

    def __init__(self, players: pd.DataFrame, teams: pd.DataFrame):
        p = players.copy()
        p["pos_resolved"] = _resolve_positions(p)
        # sin ningún partido con puesto: build_profiles lo busca en el plantel
        p["pos_known"] = p["player_id"].isin(p.loc[p["pos_group"].isin(POS), "player_id"].unique())
        p["role"] = p["pos"].map(role_of)
        mode = p.dropna(subset=["role"]).groupby("player_id")["role"].agg(lambda s: s.value_counts().index[0])
        p["role_resolved"] = p["player_id"].map(mode).fillna(p["pos_resolved"].map(ROLE_OF_GROUP))
        tm = teams[["event_id", "team_id", "shots"]].rename(columns={"shots": "team_shots"})
        p = p.merge(tm, on=["event_id", "team_id"], how="left")
        p = p.sort_values("date").reset_index(drop=True)
        self.p = p
        self.by_player = {pid: g for pid, g in p.groupby("player_id", sort=False)}
        self.team_events = {tid: g.drop_duplicates("event_id")[["event_id", "date"]]
                            for tid, g in p.groupby("team_id", sort=False)}
        self.teams = teams
        tl = teams.assign(_cup=teams["league"].isin(cfg.CUPS)).sort_values("_cup", kind="stable")
        self.team_league = tl.drop_duplicates("team_id").set_index("team_id")["league"].to_dict()
        # Selecciones: los equipos que juegan las competencias de cfg.NATIONAL
        self.national_teams = set(teams.loc[teams["league"].isin(cfg.NATIONAL), "team_id"])
        core = teams[teams["league"].isin([k for k in cfg.LEAGUES if k not in cfg.CUPS])]
        self.mean_team_shots = float((core if len(core) else teams)["shots"].mean())

    def sub_intensity(self, q0: pd.DataFrame) -> float:
        """Porción de remates por minuto entrando desde el banco / como titular,
        comparando a cada jugador consigo mismo (sólo historia anterior)."""
        if not cfg.SUB_ADJUST:
            return 1.0
        q = q0[(q0["pos_resolved"] != "G") & q0["team_shots"].notna()]
        if q.empty:
            return 1.0
        e = q["team_shots"] * q["minutes"] / 90.0
        g = q.assign(e=e).groupby(["player_id", "starter"]).agg(s=("shots", "sum"), e=("e", "sum"), n=("shots", "size"))
        w = g.unstack("starter").dropna()
        if 0 not in w["n"].columns or 1 not in w["n"].columns:
            return 1.0
        w = w[(w[("n", 0)] >= 3) & (w[("n", 1)] >= 3)]
        if len(w) < 30:
            return 1.0
        k = (w[("s", 0)].sum() / w[("e", 0)].sum()) / (w[("s", 1)].sum() / w[("e", 1)].sum())
        return float(min(max(k, 1.0), 2.2))

    def priors(self, asof) -> dict:
        q0 = self.p[(self.p["date"] < pd.Timestamp(asof)) & (self.p["minutes"] > 0)]
        core = q0[q0["league"].isin([k for k in cfg.LEAGUES if k not in cfg.CUPS])]
        q0 = core if len(core) else q0              # promedios: sólo las ligas de la app
        k_sub = self.sub_intensity(q0)
        q0 = q0.assign(_ew=np.where(q0["starter"] == 1, 1.0, k_sub ** cfg.SUB_K_PRIOR_POWER))
        out = {"sub_k": k_sub}
        for g in POS:
            q = q0[q0["pos_resolved"] == g]
            exp_team = (q["team_shots"] * q["minutes"] / 90.0 * q["_ew"]).sum()
            out[g] = {
                "share90": float(q["shots"].sum() / exp_team) if exp_team > 0 else 0.05,
                "acc": float(q["sot"].sum() / q["shots"].sum()) if q["shots"].sum() > 0 else 0.3,
            }
        for r in ROLES:
            q = q0[q0["role_resolved"] == r]
            exp_team = (q["team_shots"] * q["minutes"] / 90.0 * q["_ew"]).sum()
            pr = {
                "share90": float(q["shots"].sum() / exp_team) if exp_team > 0 else 0.05,
                "acc": float(q["sot"].sum() / q["shots"].sum()) if q["shots"].sum() > 0 else 0.3,
                "phi": _role_phi(q),
            }
            out["role:" + r] = pr
        return out


def _role_phi(q: pd.DataFrame) -> float:
    """Sobre-dispersión típica de un rol: promedio de la de cada jugador
    (con su propia porción), así no se confunde "jugadores distintos" con
    "un jugador irregular"."""
    q = q[q["team_shots"].notna()]
    if q.empty:
        return 0.15
    et = q["team_shots"].to_numpy(float) * q["minutes"].to_numpy(float) / 90.0
    y = q["shots"].fillna(0).to_numpy(float)
    d = pd.DataFrame({"pid": q["player_id"].to_numpy(), "y": y, "et": et})
    g = d.groupby("pid").agg(sy=("y", "sum"), se=("et", "sum"), n=("y", "size"))
    g = g[(g["n"] >= 6) & (g["se"] > 0)]
    if g.empty:
        return 0.15
    d = d[d["pid"].isin(g.index)]
    d["mu"] = d["et"] * d["pid"].map(g["sy"] / g["se"])
    d["r2"] = (d["y"] - d["mu"]) ** 2
    a = d.groupby("pid").agg(r2=("r2", "sum"), mu=("mu", "sum"), mu2=("mu", lambda x: float((x ** 2).sum())), n=("y", "size"))
    a = a[a["mu2"] > 0]
    phi = ((a["r2"] - a["mu"]) / a["mu2"]).clip(0, 2.0)
    return float(np.average(phi, weights=a["n"])) if len(a) else 0.15


def _resolve_positions(players: pd.DataFrame) -> pd.Series:
    """Posición habitual de cada jugador (los suplentes vienen como '?')."""
    known = players[players["pos_group"].isin(POS)]
    mode = known.groupby("player_id")["pos_group"].agg(lambda s: s.value_counts().index[0])
    return players["player_id"].map(mode).fillna("M")


def _roster_pos(hist: "History", team_id: str, pid: str) -> str | None:
    """Puesto del plantel de ESPN (para quien la historia sólo tiene como 'SUB')."""
    from futbol import espn
    lg = hist.team_league.get(team_id)
    try:
        return espn.roster_positions(lg, team_id).get(str(pid)) if lg else None
    except Exception:  # noqa: BLE001
        return None


def build_profiles(hist: "History", team_id: str, asof, priors: dict,
                   squad: list[dict] | None = None) -> list[PlayerProfile]:
    """Perfiles de los jugadores de `team_id` para un partido en `asof`.

    `squad`: alineación confirmada [{player_id, starter, ...}] o None. Sin
    alineación, se usa el plantel de sus últimos partidos.
    """
    asof = pd.Timestamp(asof)
    te = hist.team_events.get(team_id)
    recent = []
    if te is not None:
        recent = te[te["date"] < asof]["event_id"].tolist()[-cfg.LINEUP_LOOKBACK:]
    if squad:
        ids = [str(s["player_id"]) for s in squad]
        starters = {str(s["player_id"]) for s in squad if s.get("starter")}
    else:
        rec = hist.p[(hist.p["team_id"] == team_id) & hist.p["event_id"].isin(recent)]
        ids = rec["player_id"].unique().tolist()
        starters = None
    prior_team = cfg.SHARE_PRIOR_MIN / 90.0 * max(hist.mean_team_shots, 1.0)
    # factor de suplente de esta liga
    k_sub = float(priors.get("sub_k", 1.0)) ** cfg.SUB_K_POWER.get(hist.team_league.get(team_id), 0.0)

    national = team_id in hist.national_teams
    out = []
    for pid in ids:
        g = hist.by_player.get(pid)
        h = g[g["date"] < asof] if g is not None else None
        if h is not None and not national and hist.national_teams:
            # partido de club: lo que hizo con su selección no cuenta (otro equipo, otro rol)
            h = h[~h["team_id"].isin(hist.national_teams)]
        sq = next((s for s in (squad or []) if str(s["player_id"]) == pid), {})
        if h is not None and not h.empty:
            pos = h["pos_resolved"].iloc[-1]
            name = h["player"].iloc[-1]
            if "pos_known" in h and not bool(h["pos_known"].iloc[-1]):
                # nunca jugó con puesto (siempre "SUB"): el de hoy o el del plantel
                pos = sq.get("pos_group") if sq.get("pos_group") in POS else _roster_pos(hist, team_id, pid) or pos
        else:
            h = None
            pos = sq.get("pos_group") if sq.get("pos_group") in POS else "M"
            name = sq.get("player") or pid
        if h is not None:
            rr = h["role"].dropna()
            role = rr.tail(10).value_counts().index[0] if len(rr) else ROLE_OF_GROUP.get(pos, "CM")
        else:
            role = ROLE_OF_GROUP.get(pos, "CM")
        if sq.get("pos") and role_of(sq.get("pos")):
            role = role_of(sq.get("pos"))          # el puesto de HOY en la formación confirmada
        dm = is_dm(sq.get("pos"), h["pos"] if h is not None and "pos" in h else None)
        pr = priors.get("role:" + role) if cfg.PLAYER_ROLES else None
        pr = pr or priors.get(pos, {"share90": 0.05, "acc": 0.3})
        if h is not None:
            played = h[h["minutes"] > 0]
            w = 0.5 ** (np.arange(len(played))[::-1] / cfg.PLAYER_HALFLIFE)
            exp_team = np.nan_to_num(played["team_shots"].to_numpy(float)) * played["minutes"].to_numpy(float) / 90.0
            # minutos entrando desde el banco: cuentan k veces (ahí se remata más por minuto)
            exp_team = exp_team * np.where(played["starter"].to_numpy() == 1, 1.0, k_sub)
            shots = played["shots"].fillna(0).to_numpy(float)
            sots = played["sot"].fillna(0).to_numpy(float)
            # Sin remates del equipo ese partido no cuenta (ligas chicas de ESPN:
            # a veces trae los del jugador y no los del equipo e inflaba su porción).
            has_team = played["team_shots"].notna().to_numpy()
            shots, sots = np.where(has_team, shots, 0.0), np.where(has_team, sots, 0.0)
            st = h[h["starter"] == 1]["minutes"].to_numpy(float)
            st = st[st > 0][-15:]
            bench = h[h["starter"] == 0].tail(15)
            sub = bench[bench["minutes"] > 0]["minutes"].to_numpy(float)
            p_enter = (float((bench["minutes"] > 0).sum()) + 1.0) / (len(bench) + 2.0)
            in_recent = h[h["event_id"].isin(recent)]
            p_start = (in_recent["starter"].sum() + 0.5) / (len(recent) + 1.0) if recent else 0.5
            n_min = float(played["minutes"].sum())
        else:
            w = shots = sots = exp_team = np.zeros(0)
            st, sub, p_enter, p_start, n_min = np.zeros(0), np.zeros(0), 0.4, 0.3, 0.0
        share = (np.sum(w * shots) + pr["share90"] * prior_team) / (np.sum(w * exp_team) + prior_team)
        if national and h is not None and cfg.NT_PRIOR_MIN is not None:
            # Selección: su porción con la selección, partiendo de la del club.
            # NT_PRIOR_MIN = minutos de selección que pesan lo mismo que todo el club.
            nt = played["team_id"].isin(hist.national_teams).to_numpy()
            if nt.any() and (~nt).any():
                wc = 0.5 ** (np.arange(int((~nt).sum()))[::-1] / cfg.PLAYER_HALFLIFE)
                wn = 0.5 ** (np.arange(int(nt.sum()))[::-1] / cfg.PLAYER_HALFLIFE)
                club = (np.sum(wc * shots[~nt]) + pr["share90"] * prior_team) / (np.sum(wc * exp_team[~nt]) + prior_team)
                kt = cfg.NT_PRIOR_MIN / 90.0 * max(hist.mean_team_shots, 1.0)
                share = (np.sum(wn * shots[nt]) + club * kt) / (np.sum(wn * exp_team[nt]) + kt)
        acc = (np.sum(w * sots) + pr["acc"] * cfg.PLAYER_ACC_PRIOR) / (np.sum(w * shots) + cfg.PLAYER_ACC_PRIOR)
        # Constancia: cuánto se aparta partido a partido de lo esperado dados
        # los remates de su equipo y sus minutos (más allá del azar).
        phi_role = (priors.get("role:" + role) or {}).get("phi", 0.15)
        phi = 0.0
        if cfg.PLAYER_DISPERSION:
            if h is not None and len(exp_team):
                mu = share * exp_team
                ww = w / w.max() if w.max() > 0 else w
                n_eff = float(ww.sum())
                den = float(np.sum(ww * mu ** 2))
                phi_i = float((np.sum(ww * (shots - mu) ** 2) - np.sum(ww * mu)) / den) if den > 0 else phi_role
                phi = (n_eff * max(phi_i, 0.0) + cfg.PHI_PRIOR_N * phi_role) / (n_eff + cfg.PHI_PRIOR_N)
            else:
                phi = phi_role
            phi = float(min(max(phi, 0.0), 1.5))
        if pos == "G":
            share = min(share, 0.002)
        if starters is not None:
            p_start = 1.0 if pid in starters else 0.0
        out.append(PlayerProfile(
            player_id=str(pid), player=name, team_id=team_id, pos_group=pos,
            share90=float(share), acc=float(min(max(acc, 0.05), 0.9)),
            start_min=st if len(st) else np.array([DEFAULT_START_MIN[pos]]),
            sub_min=sub if len(sub) else np.array([DEFAULT_SUB_MIN]),
            p_enter_bench=float(p_enter), p_start=float(p_start), n_min=n_min,
            role=role, phi=phi, sub_k=k_sub, dm=dm))
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 3. Simulación del partido
# ══════════════════════════════════════════════════════════════════════════════

def _draw_minutes(p: PlayerProfile, rng, n: int, with_start: bool = False):
    """Minutos por iteración: titular / entra desde el banco / no juega."""
    u = rng.random(n)
    start = u < p.p_start
    enter = (~start) & (rng.random(n) < p.p_enter_bench)
    mins = np.zeros(n)
    mins[start] = rng.choice(p.start_min, size=int(start.sum()))
    mins[enter] = rng.choice(p.sub_min, size=int(enter.sum()))
    return (mins, start) if with_start else mins


SUB_OFF_BEFORE = 88.0      # titular con menos minutos que esto = fue reemplazado


def _played_dist(x: np.ndarray, played: np.ndarray, size: int) -> np.ndarray:
    """Distribución SÓLO de las iteraciones en que juega: si no entra, la casa devuelve la apuesta."""
    return _hist(x[played], size) if played.any() else _hist(x, size)


def simulate_match(tm: TeamModel, home_id: str, away_id: str,
                   home_players: list[PlayerProfile], away_players: list[PlayerProfile],
                   n: int = cfg.N_SIM, key: str = "", shot_mult: tuple = (1.0, 1.0)) -> dict:
    """shot_mult: corrección del volumen de remates (local, visitante), p. ej.
    el ancla del mercado del partido (futbol.market)."""
    out = {"teams": {}, "players": []}
    for side, team, opp, roster, is_home in (("home", home_id, away_id, home_players, 1),
                                               ("away", away_id, home_id, away_players, 0)):
        rng = _rng(key, side)
        lam, p_acc = tm.expected(team, opp, is_home)
        lam *= shot_mult[0] if is_home else shot_mult[1]
        # Remates del equipo: Gamma-Poisson = binomial negativa.
        shots = rng.poisson(rng.gamma(tm.disp, lam / tm.disp, size=n))
        # Factor de puntería del día (común a todo el equipo).
        day = rng.gamma(tm.acc_disp, 1.0 / tm.acc_disp, size=n)

        if roster:
            drawn = [_draw_minutes(p, rng, n, with_start=True) for p in roster]
            mins = np.column_stack([d[0] for d in drawn])
            started = np.column_stack([d[1] for d in drawn])
            w = mins / 90.0 * np.array([p.share90 for p in roster])[None, :]
            w = w * np.where(started, 1.0, np.array([p.sub_k for p in roster])[None, :])
            # Constancia propia: un factor "del día" por jugador (Gamma con
            # media 1 y varianza φ). Haaland (φ chico) casi no se mueve; un
            # extremo irregular sí.
            phis = np.array([p.phi for p in roster])
            if (phis > 1e-3).any():
                k = 1.0 / np.maximum(phis, 1e-3)
                g = rng.gamma(k[None, :], 1.0 / k[None, :], size=(n, len(roster)))
                g[:, phis <= 1e-3] = 1.0
                w = w * g
            tot = w.sum(axis=1, keepdims=True)
            pv = np.where(tot > 0, w / np.where(tot > 0, tot, 1), 1.0 / len(roster))
            pshots = rng.multinomial(shots, pv)
            # Puntería del jugador, re-escalada para que el promedio del equipo
            # sea la del modelo de equipo para este rival.
            acc = np.array([p.acc for p in roster])
            mix = (pv * acc[None, :]).sum(axis=1)
            scale = np.where(mix > 0, p_acc / mix, 1.0)
            pacc = np.clip(acc[None, :] * scale[:, None] * day[:, None], 0.0, 0.97)
            psot = rng.binomial(pshots, pacc)
            sot = psot.sum(axis=1)
            # Suplente+ (bet365) / Suplente de Oro (Betano) / Suplente (Betsson):
            # si el titular sale, la apuesta sigue con el que entra y se SUMAN
            # los dos. El reemplazo juega lo que falta con la porción promedio
            # de los suplentes de su mismo grupo en el plantel.
            shares = np.array([p.share90 for p in roster])
            groups = np.array([p.pos_group for p in roster])
            bench_w = np.array([(1 - p.p_start) * p.p_enter_bench for p in roster])
            rate = shots / 90.0                       # remates del equipo por minuto, por iteración
            for j, p in enumerate(roster):
                played = mins[:, j] > 0
                off = started[:, j] & (mins[:, j] < SUB_OFF_BEFORE)
                same = (groups == p.pos_group) & (np.arange(len(roster)) != j) & (bench_w > 0)
                rep_share = float(np.average(shares[same], weights=bench_w[same])) if same.any() \
                    else p.share90 * 0.8
                rep_share *= p.sub_k                  # el que entra remata con ritmo de suplente
                rep_acc = float(np.average(acc[same], weights=bench_w[same])) if same.any() else p.acc
                rem = np.where(off, 90.0 - mins[:, j], 0.0)
                rs = rng.poisson(rate * rem * rep_share)
                ro = rng.binomial(rs, np.clip(rep_acc * scale * day, 0, 0.97))
                sp_shots, sp_sot = pshots[:, j] + rs, psot[:, j] + ro
                out["players"].append({
                    "player_id": p.player_id, "player": p.player, "team_id": team, "side": side,
                    "pos_group": p.pos_group, "role": p.role, "dm": p.dm, "phi": p.phi, "p_start": p.p_start,
                    "p_play": float(played.mean()),
                    "exp_minutes": float(mins[:, j].mean()),
                    "shots_mean": float(pshots[:, j].mean()), "sot_mean": float(psot[:, j].mean()),
                    "shots_dist": _played_dist(pshots[:, j], played, 10),
                    "sot_dist": _played_dist(psot[:, j], played, 8),
                    "shots_sp_dist": _played_dist(sp_shots, played, 10),
                    "sot_sp_dist": _played_dist(sp_sot, played, 8),
                    "share90": p.share90, "acc": p.acc, "n_min": p.n_min,
                })
        else:
            sot = rng.binomial(shots, np.clip(p_acc * day, 0, 0.97))
        out["teams"][side] = {
            "team_id": team, "lam_shots": lam, "p_acc": p_acc,
            "shots_mean": float(shots.mean()), "sot_mean": float(sot.mean()),
            "shots_dist": _hist(shots, 40),
            "sot_dist": _hist(sot, 20),
            "shots_q": np.percentile(shots, [10, 50, 90]).tolist(),
            "sot_q": np.percentile(sot, [10, 50, 90]).tolist(),
        }
        out["teams"][side]["_shots"] = shots
        out["teams"][side]["_sot"] = sot
    tot = out["teams"]["home"].pop("_shots") + out["teams"]["away"].pop("_shots")
    tsot = out["teams"]["home"].pop("_sot") + out["teams"]["away"].pop("_sot")
    out["total"] = {"shots_mean": float(tot.mean()), "sot_mean": float(tsot.mean()),
                    "shots_dist": _hist(tot, 60), "sot_dist": _hist(tsot, 30)}
    return out


def _hist(x: np.ndarray, size: int) -> np.ndarray:
    """Distribución discreta con la cola (≥ size−1) acumulada en el último casillero."""
    return np.bincount(np.minimum(x, size - 1), minlength=size)[:size] / len(x)


def p_over(dist: np.ndarray, line: float) -> float:
    """P(X > line) desde una distribución discreta (la cola truncada va al último bin)."""
    k = int(math.floor(line)) + 1
    return float(dist[k:].sum()) if k < len(dist) else 0.0


# ── Copas: un solo modelo con todas las ligas ────────────────────────────────
# En una copa cada equipo juega 3-8 partidos; con eso solo su ataque y su
# defensa salen muy ruidosos. Acá se ajusta TODO junto (liga local de cada
# equipo + copas europeas): ataque = nivel de su liga + lo suyo dentro de la
# liga. El nivel de cada liga sale de los cruces entre ligas (Champions,
# Europa, Conference, previas, Mundial de Clubes): así 3 remates por partido
# en Noruega no valen lo mismo que 3 en la Premier.
# κ ("compresión"): en los cruces entre ligas la diferencia de nivel se
# multiplica por κ. κ < 1 = el chico juega mejor de lo que dice su nivel (sin
# presión, partido de su vida); κ > 1 = el grande lo pasa por arriba. No se
# fija a mano: sale de los partidos de copa ya jugados.

def _is_cross(lg: str) -> bool:
    return lg in cfg.CUPS or str(lg).startswith("uefa.") or str(lg).startswith("fifa.")


def team_home_league(teams: pd.DataFrame) -> dict:
    """Liga local de cada equipo (la más jugada que no sea un cruce entre ligas)."""
    d = teams[~teams["league"].map(_is_cross)]
    return d.groupby("team_id")["league"].agg(lambda s: s.value_counts().index[0]).to_dict()


def fit_team_model_cup(teams: pd.DataFrame, asof, cup: str,
                       halflife_matches: float = cfg.TEAM_HALFLIFE,
                       prior_k: float = cfg.TEAM_PRIOR_K, kappa_fixed: float | None = None,
                       kappa_ridge: float = 40.0) -> TeamModel:
    asof = pd.Timestamp(asof)
    d = teams[(teams["date"] < asof) & teams["shots"].notna() & (teams["shots"] > 0)].copy()
    lg_mean = d.groupby("league")["shots"].mean()
    d = d[d["league"].map(lg_mean) >= 6.0]                       # ligas con datos rotos fuera
    d = d[d["opp_id"].notna()]
    if cfg.HISTORY_ONLY:
        # Las ligas que se bajan sólo por los jugadores (cfg.HISTORY_ONLY) entran
        # al modelo de equipos como antes: sólo los partidos de equipos de copa.
        cup_teams = set(d.loc[d["league"].isin(cfg.CUPS), "team_id"])
        extra = d["league"].isin(cfg.HISTORY_ONLY)
        d = d[~extra | d["team_id"].isin(cup_teams) | d["opp_id"].isin(cup_teams)]
    if d.empty or not (d["league"] == cup).any():
        raise ValueError("sin historia para ajustar el modelo de copa")
    w = _weights(d["date"], asof, halflife_matches * 7.0 * 2.0)  # más memoria: los cruces son pocos
    home_lg = team_home_league(d)
    ids = sorted(set(d["team_id"]) | set(d["opp_id"]))
    ix = {t: i for i, t in enumerate(ids)}
    grp = sorted({home_lg.get(t, "_") for t in ids})
    gx = {g: i for i, g in enumerate(grp)}
    tg = np.array([gx[home_lg.get(t, "_")] for t in ids])
    lgs = sorted(d["league"].unique())
    lx = {l: i for i, l in enumerate(lgs)}
    n, nG, nL = len(ids), len(grp), len(lgs)
    ti = d["team_id"].map(ix).to_numpy()
    oi = d["opp_id"].map(ix).to_numpy()
    li = d["league"].map(lx).to_numpy()
    cross = d["league"].map(_is_cross).to_numpy()
    hm = np.where(d["home"].to_numpy() == 1, 1.0, -1.0)
    # neutral (final, Mundial de Clubes): sin localía
    hm = np.where(d["league"].astype(str).str.startswith("fifa.").to_numpy(), 0.0, hm)
    y = d["shots"].to_numpy(float)
    lam_bar = float(np.average(y, weights=w))
    ridge = prior_k * lam_bar * 0.5
    ridge_g = ridge * 0.5
    ridge_k = ridge * kappa_ridge
    gt, go = tg[ti], tg[oi]
    # offsets de grupo: sólo en cruces (dentro de una liga se cancelan con μ de la liga)
    o_mu, o_h, o_A, o_D = 0, nL, nL + 1, nL + 1 + nG
    o_a, o_b, o_k = nL + 1 + 2 * nG, nL + 1 + 2 * nG + n, nL + 1 + 2 * nG + 2 * n

    def unpack(th):
        return th[o_mu:o_h], th[o_h], th[o_A:o_D], th[o_D:o_a], th[o_a:o_b], th[o_b:o_k], th[o_k]

    def nll(th):
        mu, h, A, D, a, b, k = unpack(th)
        if kappa_fixed is not None:
            k = kappa_fixed
        gap = np.where(cross, A[gt] + D[go], 0.0) + a[ti] + b[oi]
        c = np.where(cross, k, 1.0)
        eta = mu[li] + h * hm + c * gap
        lam = np.exp(eta)
        f = np.sum(w * (lam - y * eta)) + ridge * (a @ a + b @ b) / 2 \
            + ridge_g * (A @ A + D @ D) / 2 + ridge_k * (k - 1.0) ** 2 / 2
        r = w * (lam - y)
        rc = r * c
        g = np.zeros_like(th)
        g[o_mu:o_h] = np.bincount(li, weights=r, minlength=nL)
        g[o_h] = (r * hm).sum()
        g[o_A:o_D] = np.bincount(gt, weights=np.where(cross, rc, 0.0), minlength=nG) + ridge_g * A
        g[o_D:o_a] = np.bincount(go, weights=np.where(cross, rc, 0.0), minlength=nG) + ridge_g * D
        g[o_a:o_b] = np.bincount(ti, weights=rc, minlength=n) + ridge * a
        g[o_b:o_k] = np.bincount(oi, weights=rc, minlength=n) + ridge * b
        g[o_k] = 0.0 if kappa_fixed is not None else float(np.sum(np.where(cross, r * gap, 0.0))) + ridge_k * (k - 1.0)
        return f, g

    th0 = np.zeros(o_k + 1)
    th0[o_mu:o_h] = [math.log(max(float(np.average(y[li == j], weights=w[li == j])), 0.5)) for j in range(nL)]
    th0[o_k] = 1.0
    th = minimize(nll, th0, jac=True, method="L-BFGS-B").x
    mu, h, A, D, a, b, k = unpack(th)
    if kappa_fixed is not None:
        k = kappa_fixed
    att = {t: k * (A[tg[ix[t]]] + a[ix[t]]) for t in ids}
    dfn = {t: k * (D[tg[ix[t]]] + b[ix[t]]) for t in ids}
    mu_c = float(mu[lx[cup]])

    # Sobre-dispersión, con los partidos de cruces entre ligas
    gap = np.where(cross, A[gt] + D[go], 0.0) + a[ti] + b[oi]
    lam_fit = np.exp(mu[li] + h * hm + np.where(cross, k, 1.0) * gap)
    m = cross
    excess = np.average((y[m] - lam_fit[m]) ** 2 - lam_fit[m], weights=w[m])
    disp = float(np.clip(np.average(lam_fit[m] ** 2, weights=w[m]) / max(excess, 1e-6), 3.0, 200.0))

    # Puntería: binomial con ν por liga y equipo/rival en todos sus partidos
    ma = d["sot"].notna().to_numpy() & (y > 0)
    s = y[ma]
    kk = np.minimum(d["sot"].to_numpy(float)[ma].clip(0, None), s)
    wa, ta, oa, la = w[ma], ti[ma], oi[ma], li[ma]
    p_bar = float(np.sum(wa * kk) / np.sum(wa * s))
    ridge_a = cfg.ACC_PRIOR_K * p_bar * (1 - p_bar)

    def nll_a(th):
        nu, aa, bb = th[:nL], th[nL:nL + n], th[nL + n:]
        z = nu[la] + aa[ta] + bb[oa]
        p = 1 / (1 + np.exp(-z))
        f = -np.sum(wa * (kk * np.log(p + 1e-12) + (s - kk) * np.log(1 - p + 1e-12))) \
            + ridge_a * (aa @ aa + bb @ bb) / 2
        r = wa * (s * p - kk)
        g = np.empty_like(th)
        g[:nL] = np.bincount(la, weights=r, minlength=nL)
        g[nL:nL + n] = np.bincount(ta, weights=r, minlength=n) + ridge_a * aa
        g[nL + n:] = np.bincount(oa, weights=r, minlength=n) + ridge_a * bb
        return f, g

    tha = np.zeros(nL + 2 * n)
    tha[:nL] = math.log(p_bar / (1 - p_bar))
    ra = minimize(nll_a, tha, jac=True, method="L-BFGS-B").x
    nu_all, aa, bb = ra[:nL], ra[nL:nL + n], ra[nL + n:]
    acc = {t: aa[ix[t]] for t in ids}
    allow = {t: bb[ix[t]] for t in ids}
    pf = 1 / (1 + np.exp(-(nu_all[la] + aa[ta] + bb[oa])))
    mc = cross[ma]
    excess_a = np.average((kk[mc] - s[mc] * pf[mc]) ** 2 - s[mc] * pf[mc] * (1 - pf[mc]), weights=wa[mc])
    acc_disp = float(np.clip(np.average((s[mc] * pf[mc]) ** 2, weights=wa[mc]) / max(excess_a, 1e-6), 4.0, 400.0))
    tm = TeamModel(mu=mu_c, home=float(h), att=att, dfn=dfn, nu=float(nu_all[lx[cup]]), acc=acc,
                   acc_allow=allow, disp=disp, acc_disp=acc_disp,
                   league_shots=float(np.average(y[li == lx[cup]], weights=w[li == lx[cup]])), league_acc=p_bar)
    tm.kappa = float(k)                                          # para mostrar / auditar
    tm.league_level = {g: float(A[i]) for g, i in gx.items()}
    tm.league_def = {g: float(D[i]) for g, i in gx.items()}
    return tm
