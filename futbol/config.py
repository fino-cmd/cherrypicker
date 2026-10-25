"""
Futbol — configuración.

Un modelo de REMATES y REMATES AL ARCO, no de resultados. Ligas: Liga
Profesional (Argentina), Brasileirão Série A y Premier League.

Fuentes:
  • ESPN (gratis, sin clave): partidos, estadísticas por equipo y por jugador
    (remates, al arco, titular/suplente, minuto de cada cambio, posición).
  • OddsPapi (clave ODDSPAPI_KEY en backend/.env): cuotas de remates por
    jugador de bet365 AR, Betano y Betsson. Plan gratis = 250 consultas/mes:
    UNA consulta trae todas las ligas de una casa, y todo se cachea en disco.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "futbol"
CACHE_DIR = DATA_DIR / "cache"

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend" / ".env", override=False)
except Exception:  # noqa: BLE001 - python-dotenv es opcional
    pass

# ── Ligas ─────────────────────────────────────────────────────────────────────
# espn: código de liga en site.api.espn.com · oddspapi: tournamentId
LEAGUES = {
    "arg": {"name": "Liga Profesional", "espn": "arg.1", "oddspapi": 155},
    "bra": {"name": "Brasileirão", "espn": "bra.1", "oddspapi": 325},
    "eng": {"name": "Premier League", "espn": "eng.1", "oddspapi": 17},
}
# Meses de historia a bajar (YYYYMM). Dos temporadas de cada liga.
HISTORY_FROM = "202501"

# ── OddsPapi ─────────────────────────────────────────────────────────────────
ODDSPAPI_BASE = "https://api.oddspapi.io/v4"
# Casas: bet365 AR trae remates y al arco en las 3 ligas; Betano sólo remates;
# Betsson remates y al arco (Brasil y Premier).
BOOKS = ["bet365.bet.ar", "betano", "betsson"]
ODDS_TTL_S = 3 * 3600          # una jornada no se vuelve a pedir en 3 h


def oddspapi_key() -> str | None:
    return os.getenv("ODDSPAPI_KEY")


# ── Modelo ────────────────────────────────────────────────────────────────────
N_SIM = 20_000
SEED = 20261024

# Fuerza de equipo (remates a favor / en contra), en escala log:
TEAM_HALFLIFE = 12.0       # partidos: semivida del peso por recencia
TEAM_PRIOR_K = 8.0         # partidos "virtuales" en 1.0 (shrinkage)
PREV_SEASON_W = 0.6        # la temporada anterior pesa menos
# Nivel general de la liga (remates y puntería promedio): ventana CORTA. El
# volumen de toda la liga se mueve durante el año (en 2026 subió 10-25 %).
LEAGUE_HALFLIFE_DAYS = 21.0
# Puntería (al arco / remates) del que remata y la que permite el rival:
ACC_PRIOR_K = 60.0         # remates virtuales en la puntería de la liga

# Jugadores:
PLAYER_HALFLIFE = 10.0     # partidos
SHARE_PRIOR_MIN = 450.0    # minutos virtuales en el promedio de su posición
PLAYER_ACC_PRIOR = 12.0    # remates virtuales en la puntería de su posición
MIN_MINUTES_FOR_HISTORY = 1

# Minutos esperados cuando no hay alineación confirmada se estiman del
# historial reciente (fracción de titularidades y minutos promedio).
LINEUP_LOOKBACK = 6

# Perfil de cada jugador:
#  · ROL según el puesto real que le da ESPN (centrodelantero, extremo,
#    enganche, volante, lateral, central, arquero): el prior de porción y de
#    puntería sale de su rol, no del grupo G/D/M/F (Haaland ≠ Doku).
#  · CONSTANCIA propia: cuánto varía de un partido a otro más allá del azar
#    (sobre-dispersión φ); se encoge hacia la de su rol con PHI_PRIOR_N partidos.
PLAYER_ROLES = True
PLAYER_DISPERSION = True
# Los suplentes rematan ~1,67x más por minuto que el mismo jugador de titular
# (partido abierto, piernas frescas). Sus minutos de banco cuentan con ese peso
# para no inflar al suplente que pasa a ser titular.
SUB_ADJUST = True
import os as _os
# Parte del factor que se aplica en cada liga (elegida con el backtest walk-forward:
# LPF 0,3 → ×1,17 · Premier 1 → ×1,67 · Brasileirão 0 → sin ajuste).
SUB_K_POWER = {"arg": 0.3, "eng": 1.0, "bra": 0.0}
SUB_K_PRIOR_POWER = 0.3        # en los promedios por puesto (comunes a las tres ligas)
if _os.environ.get("FB_SUB_POW"):
    SUB_K_POWER = {k: float(_os.environ["FB_SUB_POW"]) for k in SUB_K_POWER}
PHI_PRIOR_N = 12.0

# Líneas que se reportan
SHOT_LINES = [0.5, 1.5, 2.5, 3.5]
SOT_LINES = [0.5, 1.5, 2.5]
TEAM_SHOT_LINES = [6.5, 8.5, 10.5, 12.5, 14.5, 16.5]
TEAM_SOT_LINES = [2.5, 3.5, 4.5, 5.5, 6.5]
