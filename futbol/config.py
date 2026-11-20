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
    "arg": {"name": "Liga Profesional", "espn": "arg.1", "oddspapi": 155, "polymarket": "102561"},
    "bra": {"name": "Brasileirão", "espn": "bra.1", "oddspapi": 325, "polymarket": "102648"},
    "eng": {"name": "Premier League", "espn": "eng.1", "oddspapi": 17, "polymarket": "306"},
    "ita": {"name": "Serie A", "espn": "ita.1", "oddspapi": 23, "polymarket": "101962"},
    "esp": {"name": "LaLiga", "espn": "esp.1", "oddspapi": 8},   # 08/11/2026
    # Liga MX (16/11/2026). Además de remates, lleva atajadas del arquero (futbol.saves).
    "mex": {"name": "Liga MX", "espn": "mex.1", "oddspapi": 27464},
    # Copas europeas (02/11/2026). Mezclan equipos de muchas ligas: el modelo de
    # equipos se ajusta con los partidos de la copa y cada jugador suma también
    # su historia en las 4 ligas (mismo id de ESPN).
    "ucl": {"name": "Champions League", "espn": "uefa.champions", "oddspapi": 7, "cup": True},
    "uel": {"name": "Europa League", "espn": "uefa.europa", "oddspapi": 679, "cup": True},
    # Copa do Brasil (08/11/2026): casi todos son equipos del Brasileirão; los de
    # Série B/C suman sus partidos de liga por la misma vía que en las copas europeas.
    "cdb": {"name": "Copa do Brasil", "espn": "bra.copa_do_brazil", "oddspapi": 373, "cup": True},
    # UEFA Nations League (10/11/2026): selecciones. Su historia sale de todos sus
    # partidos (Nations, Eliminatorias, Euro, Mundial, amistosos) por la vía de las
    # copas; cada jugador suma además lo de su club si juega en nuestras ligas.
    "unl": {"name": "Nations League", "espn": "uefa.nations", "oddspapi": 23755, "cup": True, "national": True},
}
NATIONAL = {k for k, v in LEAGUES.items() if v.get("national")}
# Ligas con atajadas del arquero: las que se bajaron de ESPN guardando las atajadas.
SAVES_LEAGUES = {"mex"}
SAVE_LINES = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]
# Selecciones: cuánto pesa lo que el jugador hace con su selección frente a lo
# del club. Son los minutos de selección que valen lo mismo que toda su historia
# de club (menos = más peso a la selección). None = todo mezclado por fecha, sin
# distinguir. Se elige con futbol/backtest_nt.py.
# Backtest 10/11/2026 (186 partidos, 13.685 filas de jugadores con club y selección):
# log-loss 0,3828 con 1080 o 540 · 0,3833 mezclado · 0,3872 sólo club · 0,3911 con 90.
NT_PRIOR_MIN = 1080.0
CUPS = {k for k, v in LEAGUES.items() if v.get("cup")}
# Modelo de equipos en las copas: "copa" (sólo partidos de la copa), "liga" (todas
# las ligas con el nivel de cada una) o "liga+k" (además, la compresión κ aprendida).
CUP_TEAM_MODEL = "liga+k"
# Ligas que se bajan SÓLO como historia de jugadores (no son pestaña de la app):
# donde juegan muchos seleccionados de la Nations League. Código de ESPN.
# Se bajan cuando la corrida incluye una competencia de selecciones.
HISTORY_ONLY = ("ger.1", "fra.1", "por.1", "ned.1", "tur.1", "bel.1")
# Meses de historia a bajar (YYYYMM). Dos temporadas de cada liga.
HISTORY_FROM = "202501"

# ── OddsPapi ─────────────────────────────────────────────────────────────────
ODDSPAPI_BASE = "https://api.oddspapi.io/v4"
# Casas: se configuran en futbol/books.json (ver futbol/books.py). OddsPapi
# NO acepta varias casas por consulta en odds-by-tournaments ("exactly one
# bookmaker"), así que cada casa activa cuesta 1 consulta por actualización.
from futbol import books as _books  # noqa: E402
BOOKS = _books.oddspapi_ids()
# Presupuesto del plan de OddsPapi (250/mes, compartido con la pestaña NBA).
ODDSPAPI_MONTH_LIMIT = 250
ODDSPAPI_RESERVE = 30          # se guardan para NBA y para imprevistos
ODDS_TTL_S = 3 * 3600          # una jornada no se vuelve a pedir en 3 h
ODDS_MAX_AGE_SIM_S = 90 * 60   # al tocar Simular, cuotas con más de 90' se piden de nuevo
ODDS_REFRESH_DAY_CAP = 3       # como mucho 3 actualizaciones por día


def odds_api() -> str:
    """De dónde salen las cuotas: 'direct' (api.oddspapi.io v4, ODDSPAPI_KEY)
    o 'rapid' (OddsPapi por RapidAPI v5, RAPIDAPI_KEY). Se elige con ODDS_API
    en backend/.env; sin nada, RapidAPI."""
    return (os.getenv("ODDS_API") or "rapid").strip().lower()


def futbol_odds() -> str:
    """Fuente de cuotas de la pestaña Fútbol: 'oddspapi' (por defecto) o 'net'
    (odds-api.net, futbol.oddsnet). Se elige con FUTBOL_ODDS en backend/.env.
    La NBA sigue siempre con OddsPapi."""
    v = (os.getenv("FUTBOL_ODDS") or "").strip().lower()
    return v if v in ("net", "optic") else "oddspapi"      # optic: OpticOdds (futbol.oddsoptic)


def oddspapi_key() -> str | None:
    return os.getenv("ODDSPAPI_KEY") if odds_api() == "direct" else os.getenv("RAPIDAPI_KEY")


# OddsPapi por RapidAPI (v5): las cuotas de jugadores se piden de a un partido
# (las 3 casas juntas). Plan y pedidos restantes: los informa RapidAPI.
RAPIDAPI_HOST = "odds-api1.p.rapidapi.com"
# Fuentes de la formación OFICIAL, en orden. "pitchapi" (gratis, sin cupo; desde
# 02/11/2026 la única, pedido del usuario). Las otras siguen disponibles:
# "espn", "api-football", "highlightly".
LINEUP_SOURCES = ("pitchapi", "espn")   # ESPN de respaldo: PitchAPI puede no confirmar ni a 15' del inicio
ODDS_WINDOW_H = 96             # partidos que se cotizan: los que empiezan en las próximas 96 h
FIXTURES_TTL_S = 6 * 3600      # la lista de partidos de cada liga se pide cada 6 h
RAPIDAPI_RESERVE = 20          # pedidos que siempre quedan libres (NBA, nombres, imprevistos)


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
SUB_K_POWER = {"arg": 0.3, "eng": 1.0, "bra": 0.0, "ita": 1.0, "esp": 1.0, "cdb": 0.0}
# Serie A: ×1,67 (backtest) · LaLiga: ×1,67 como las otras europeas (sin backtest propio) · Copa do Brasil = Brasileirão
SUB_K_PRIOR_POWER = 0.3        # en los promedios por puesto (comunes a las tres ligas)
if _os.environ.get("FB_SUB_POW"):
    SUB_K_POWER = {k: float(_os.environ["FB_SUB_POW"]) for k in SUB_K_POWER}
PHI_PRIOR_N = 12.0

# Líneas que se reportan
SHOT_LINES = [0.5, 1.5, 2.5, 3.5]
SOT_LINES = [0.5, 1.5, 2.5]
TEAM_SHOT_LINES = [6.5, 8.5, 10.5, 12.5, 14.5, 16.5]
TEAM_SOT_LINES = [2.5, 3.5, 4.5, 5.5, 6.5]
