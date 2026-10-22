# AltLines — buscador de ineficiencias en Alt Lines NBA

Motor de **probabilidad de colas largas** para props de jugador NBA. No es un
simulador de partidos: reconstruye la **distribución completa** del stat de un
jugador y la compara contra cómo el libro cotiza las líneas alternativas
extremas.

---

## 1. La tesis (por qué existe un edge)

Los libros no calculan cada Alt Line de cero. Anclan en la **mediana** del
jugador y cotizan las colas con **una sola curva simétrica genérica por
mercado**. Para un jugador *boom-or-bust* la verdad tiene **sesgo a la derecha**
y cola gruesa, así que esa curva infravalora sistemáticamente los OVER extremos.

El tablero lo mide explícitamente con la columna `Curva`:

> `Curva = P_modelo ÷ P_curva_del_libro` (una Normal centrada en la línea
> principal con el sigma genérico del mercado)

`Curva > 1×` significa que el libro está infravalorando esa cola. En el demo los
mejores edges están entre 2× y 4×.

> ⚠️ **Advertencia importante**: esto NO es una máquina de dinero. Las Alt Lines
> extremas son donde el error del modelo también es mayor, y los libros ponen
> **límites de apuesta muy bajos** justo ahí. Un EV del 150 % en una línea
> profunda significa "el precio está mal", no "vas a ganar 1,5× por unidad".

---

## 2. Estructura (los 4 módulos que pediste)

| Módulo | Archivo | Qué hace |
|---|---|---|
| **1 · Ingesta y limpieza de cuotas** | `altlines/ingest.py` | Conversión de cuotas, **remoción de vig** (multiplicativa, power, Shin), emparejado over/under, mejor precio por línea, hooks de API y el generador del dataset demo |
| **2 · Motor de simulación de distribución** | `altlines/simulation.py` | **Monte Carlo (20.000 iteraciones por jugador-mercado)**. Proyecta media/varianza/sesgo desde los logs y reconstruye la distribución entera |
| **3 · Comparación y EV** | `altlines/ev.py` | Probabilidad del modelo vs. probabilidad justa del libro, **discrepancia**, **EV por unidad**, **Kelly fraccionado**, cuota justa |
| **4 · Interfaz de salida (top 5 %)** | `altlines/dashboard.py` | Panel de consola y panel HTML interactivo, filtrado al top X % de discrepancias |

Extras de integración:

| Archivo | Qué hace |
|---|---|
| `altlines/run_pipeline.py` | Orquestador por CLI (los 4 módulos en secuencia) |
| `altlines/slate.py` | **Calendario**: ESPN, calendario de demo offline, logos y mapeo jugador → partido |
| `altlines/api.py` | Puente JSON para el backend FastAPI (caché en proceso + candado por clave) |
| `altlines/tools/make_sim_video.py` | Genera el mini video de las tarjetas (`assets/altlines/`) |
| `altlines/tools/check_calibration.py` | Re-mide la tabla de calibración de la sección 5 |
| `altlines/picklog.py` | Log de cada corrida real en `data/altlines/picks.db` (validación, §4b) |
| `altlines/tools/settle.py` | Liquida las líneas logueadas contra el box score ESPN |
| `altlines/tools/report_live.py` | Reliability `p_model`/`p_fair`, ROI top vs resto, CLV |
| `altlines/config.py` | Rutas, **claves de API**, registro de mercados y todos los parámetros numéricos |

**Todo el paquete es autónomo**: no importa nada del modelo MLB de CherryPick
(`predict.py`, `features.py`, …). Si AltLines falla, la app MLB sigue igual.

---

## 3. Cómo correrlo

```bash
# Demo (sin claves de API: genera datos NBA sintéticos)
python -m altlines.run_pipeline --demo

# Con tus propios CSV
python -m altlines.run_pipeline --logs data/altlines/player_game_logs.csv \
                                --odds data/altlines/alt_lines_odds.csv
```

Flags útiles:

| Flag | Default | Para qué |
|---|---|---|
| `--n-iter` | `20000` | Iteraciones de Monte Carlo por jugador (el brief pide ≥ 10.000) |
| `--top-pct` | `5` | Qué porcentaje de líneas reportar |
| `--metric` | `ev` | Ranking: `ev`, `ev_pct` o `discrepancy` |
| `--devig` | `multiplicative` | `multiplicative`, `power` o `shin` |
| `--max-odds` | `50` | Descarta longshots impagables (0 = sin tope) |
| `--min-ev` | `0` | EV mínimo para entrar al tablero |
| `--min-prob` | `0` | Descarta líneas con probabilidad del modelo muy baja |
| `--halflife` | `6` | Semivida (en partidos) del **nivel** |
| `--halflife-var` | `~3×` | Semivida de la **volatilidad** |
| `--books` | todos | Limitar a las casas donde realmente podés apostar |
| `--panel` | `console` | `console`, `html` o `streamlit` |
| `--date` | hoy | Fecha del calendario a analizar (`YYYY-MM-DD`) |
| `--game` | todos | Analizar **un solo partido** (id de ESPN o tricode, ej. `BOS@DET`) |
| `--list-games` | — | Mostrar el calendario del día y salir (no simula) |
| `--offline` | — | No tocar la red: usa un calendario de demo determinístico |
| `--no-write-data` | — | No escribir los CSV de demo en disco |

Salidas: el tablero se imprime en consola y se escribe
`altlines_report.html` (panel interactivo autónomo).

### El calendario y el modo "por partido"

El motor no vive en el aire: primero resuelve **qué se juega**. Ese trabajo lo
hace el módulo de calendario (`altlines/slate.py`):

```bash
# ¿Qué hay hoy? (no simula nada, responde en ~2 s)
python -m altlines.run_pipeline --demo --list-games

# Simular un solo partido (el que te interesa)
python -m altlines.run_pipeline --demo --game BOS@DET

# Un día puntual, sin internet
python -m altlines.run_pipeline --demo --date 2026-03-14 --offline
```

De dónde salen los partidos:

| Fuente | Cuándo se usa |
|---|---|
| **ESPN** (`site.api.espn.com`, sin clave) | Por defecto. Trae equipos, tricodes, horario, sede y estado reales |
| **Calendario de demo** determinístico | Con `--offline`, o si ESPN no responde |

Si la fecha pedida no tiene partidos, el calendario **busca hasta ±7 días** y te
avisa que se movió (`auto_shifted`). Eso evita el clásico "0 partidos" de abrir
la herramienta un lunes a la mañana en temporada muerta.

Al navegar con las flechas la búsqueda **respeta el sentido** (`direction`): `‹`
agota primero el pasado y `›` el futuro. Sin eso, un hueco en el calendario
rebotaba la fecha hacia adelante y el botón "anterior" parecía roto.

El demo se genera **sobre ese calendario**: cada partido aporta 2 equipos reales
× 4 jugadores × 3 mercados = 24 jugador-mercado, y **cada fila lleva su
`game_id`**. Por eso "simular sólo un partido" es un filtro, no una corrida
distinta.

> **Los jugadores son sintéticos, los equipos no.** El calendario y los tricodes
> son reales; los nombres y los stats son del generador de demo (los apellidos
> `Kai …` / `Ruben …` lo delatan). Cada fila trae el flag `matched` para que la
> app diga honestamente si el jugador se pudo colgar de un partido real o si sus
> props son sintéticos.

---

## 4. Dónde poner tus claves de API

Abrí `altlines/config.py` y buscá el bloque `_HARDCODED` (está arriba, bien
comentado, con las instrucciones de cada proveedor). Ahí van:

```python
_HARDCODED = {
    # "THE_ODDS_API_KEY":    "pega-tu-clave-aca",   # https://the-odds-api.com → cuotas y Alt Lines
    # "SPORTS_DATA_API_KEY": "pega-tu-clave-aca",   # tu proveedor de logs/uso por jugador
}
```

También se leen del entorno (y tienen prioridad si están seteadas):
`THE_ODDS_API_KEY`, `ODDS_API_KEY`, `SPORTS_DATA_API_KEY`.

Con clave puesta, el ingest consulta **The Odds API v4** para el deporte
`SPORT_KEY` (`basketball_nba` por defecto) y pide el ladder alternativo con el
sufijo `ODDS_API_ALT_SUFFIX`. El mapeo mercado interno → clave de la API está en
`ODDS_API_MARKET_KEYS`; ajustá ahí si tu plan expone otras claves.

Los dos hooks donde enchufás tu propio proveedor están en `ingest.py`, cada uno
con su bloque de instrucciones en el docstring:

- `fetch_player_logs_api()` — ingesta de logs y minutos por jugador
- `fetch_alt_lines_api()` — ingesta del ladder de Alt Lines

---

## 4b. Modo REAL (ESPN + The Odds API) — `altlines/live.py`

```bash
python -m altlines.run_pipeline --live --game BOS@DET     # un partido, ~5 créditos
```

En la app: pestaña NBA → selector **Demo | Real**.

| Pieza | Fuente | Costo |
|---|---|---|
| Calendario | ESPN scoreboard | gratis |
| Partidos con cuotas | The Odds API `/events` | gratis |
| Bandeja Alt de cada casa (FanDuel, DraftKings, BetRivers, Bovada, BetOnline) | The Odds API `/events/{id}/odds`, 5 mercados `*_alternate`, región `us` | **≤ 5 créditos por partido** |
| Logs por jugador (temporada actual + anterior) | ESPN athlete gamelog | gratis |

`stats.nba.com` no se usa: bloquea clientes que no son navegador.

**Cuota.** The Odds API cobra `mercados × regiones` por partido. El plan gratis
son 500 créditos/mes **compartidos con la pestaña MLB**: una noche de 10
partidos son ~50 créditos. Todo se cachea en disco (`data/altlines/cache/`,
fuera de git): reabrir un partido en 20 min no cuesta nada. "Simular todos" y
"Actualizar" piden confirmación en modo real.

**Identidad.** El feed de cuotas sólo trae el nombre; se casa contra el roster
ESPN de los dos equipos del partido con un nombre normalizado (acentos, Jr./II).
Lo que no casa se descarta y se informa — nunca se adivina.

### Lo que cambia en modo real (y por qué)

Con datos reales aparecieron tres formas de **edge falso** que el demo no podía
mostrar. Cada una tiene su defensa:

1. **Partidos parciales.** Una salida por lesión (6 pts en 9 min) o un regreso
   con minutos restringidos baja el nivel y ensancha la dispersión a la vez.
   → se descartan los partidos con menos del 50 % de los minutos habituales del
   jugador (`LIVE_MIN_MINUTES_FRAC`): la simulación queda condicionada a su rol
   normal ("minutos proyectados"). Los playoffs también se excluyen.
2. **Historial viejo.** En el debut de temporada "lo reciente" es abril pasado.
   → si el último partido tiene más de 45 días, el nivel usa semivida de 30
   partidos (`LIVE_STALE_*`).
3. **Discrepar en el nivel, no en la cola.** Sin corrección, el tablero se
   llenaba de UNDERs con +100 % de EV: el modelo veía al jugador más bajo que el
   mercado, que sabe de roles y lesiones. Eso no es la tesis.
   → **la mediana simulada se ancla a la línea principal del mercado**
   (reconstruida desde la propia bandeja Alt: el punto donde cada casa está
   50/50, mediana entre casas). Se conserva la *forma* del jugador:
   - continuos: `x' = k·m + √k·(x − m)` (la dispersión crece con √k, no con k);
   - conteos: adelgazamiento binomial / engrosamiento Poisson (se agregan o
     quitan eventos, como con más o menos intentos). Multiplicar un conteo por
     1,25 convertía 4 triples en 5 y triplicaba la probabilidad de la alt.

   Si para anclar hay que mover el nivel más de ±25 % (`LIVE_MAX_ANCHOR_DRIFT`),
   modelo y mercado no coinciden ni en el nivel y esa línea **no entra al top**
   (queda en la tabla con `level_ok = false`). La columna `level_gap` muestra
   la diferencia cruda (mediana del modelo − línea principal).

Además, el prior de varianza y la curva del libro ahora escalan con el nivel
del jugador (`league_ref_mean`, varianza ∝ media): un suplente de 7 puntos ya
no recibe el sigma de 9 de una estrella, que le inventaba colas gordas.

### Validación contra resultados reales (log → settle → reporte)

En la primera corrida real el modelo pone las colas ~1,4× por encima del precio
justo del mercado. Si eso es edge o sesgo sólo se decide liquidando picks.

**1. Log automático.** Cada corrida en modo real (CLI `--live` o
`/api/altlines/edges` con source=live) guarda el **tablero completo** en
`data/altlines/picks.db` (SQLite, fuera de git, separado del tracker MLB):
tabla `runs` (parámetros de la corrida) y tabla `lines` (una fila por línea:
`game_id`, `player_id`, mercado, lado, punto, casa, cuota, `p_model`, `p_fair`,
`p_curve`, `ev`, `main_line`, `anchor_scale`, `level_ok`, `in_top`,
`snapshot_ts`, `tip_off`). Dentro de una corrida `(game, player, market, side,
point, book)` es único; la misma línea vista en otra corrida es otra fila (un
snapshot más para CLV). `--no-log` lo apaga. Si el log falla, la corrida sigue.

```bash
# Re-loguear un partido con las cuotas YA cacheadas (cero créditos; falla si no hay caché)
python -m altlines.run_pipeline --live --game 401909088 --date 2026-10-20 --odds-cache-only
```

**2. Liquidar** (sólo ESPN, sin créditos). Pregunta a ESPN si el partido
terminó, re-baja el gamelog de cada jugador (`live.fetch_athlete_games`) y marca
cada línea `win` / `loss` / `push` con `pnl` por unidad (`cuota − 1` / `−1` /
`0`). Un jugador sin fila en un partido terminado (DNP) queda `void`, pero recién
36 h después del tip (ESPN tarda en publicar el log). Lo pendiente se reintenta
en la próxima corrida.

```bash
python -m altlines.tools.settle                    # todo lo pendiente
python -m altlines.tools.settle --game 401909088   # un partido (--dry-run: no escribe)
```

**3. Reporte.**

```bash
python -m altlines.tools.report_live [--game 401909088] [--all-levels]
```

Un *pick* = `(game, player, market, side, point)` tomado de la **primera**
corrida pre-tip (lo priceado después del tip se ignora). Por default sólo
líneas `level_ok` (las que pueden entrar al tablero).

| Sección | Qué mide | Cómo leerlo |
|---|---|---|
| 1a · Reliability de `p_model` | buckets 20-35, 10-20, 5-10, 2-5 %: `p_model` medio vs frecuencia real (IC 95 % de Wilson) | `real_vs_prob` ≈ 1 calibrado; < 1 el modelo sobreestima la cola |
| 1b · Reliability de `p_fair` | lo mismo bucketeando por la probabilidad sin vig del mercado | si `p_fair` calibra y `p_model` da ~0,7, el "edge" es sesgo |
| 2 · ROI | top X % vs resto (y resto con EV > 0), pnl por unidad | push = 0, void fuera |
| 3 · CLV | si la línea tiene > 1 snapshot pre-tip: `cuota_pick / cuota_cierre − 1` y deriva de `p_fair` hacia nuestro lado | CLV > 0 sostenido es la señal más rápida de edge real |

Para CLV hay que correr el modo real al menos dos veces antes del tip (p. ej.
al publicarse las líneas y ~1 h antes): cada corrida cuesta los ~5 créditos del
partido salvo que caiga dentro del caché de 20 min, en cuyo caso el snapshot es
el mismo y no aporta CLV.

Con pocos partidos los IC son anchísimos (15 picks de ~8 % ⇒ ~1 acierto
esperado): hacen falta cientos de líneas liquidadas por bucket antes de
concluir algo.

---

## 5. Cómo funciona el motor (lo que realmente importa)

### De logs a distribución

Para cada `(jugador, mercado)`:

```
oportunidades (uso)   ~ BinomialNegativa(mu, var)        # riesgo de volumen
valor por unidad      ~ mezcla de 3 regímenes (bust/base/boom) × ruido lognormal
valor                 = oportunidades × valor por unidad

mercados de conteo    → Beta-Binomial (tasa de conversión Beta)
```

### El presupuesto de varianza (el punto clave)

El error clásico —y la forma más común de que estas herramientas engañen a su
autor— es **contar la varianza dos veces**: multiplicar una mezcla de regímenes
por un lognormal a plena potencia infla la cola hasta que la media queda
dominada por cuatro tiradas, el grueso de la distribución se colapsa cerca de
cero y *todas* las Alt Lines parecen un chollo. Es un edge falso.

Por eso la varianza observada se reparte en tres cubetas:

| Cubeta | Origen |
|---|---|
| (a) varianza de volumen | los conteos de oportunidad |
| (b) varianza entre regímenes | la estructura boom/bust |
| (c) varianza dentro del régimen | ruido lognormal residual |

…y **(c) se calibra numéricamente** hasta que la varianza simulada iguala la del
jugador. El nivel y la dispersión quedan honestos; del sesgo se conserva la parte
que sobrevive al reparto — medido: **~0,6×** (ver la tabla de calibración más
abajo, que es donde se paga este trato).

En los mercados de conteo hay además un término **binomial irreducible**
`E[u·p(1−p)]` que los productos de variables continuas no tienen. Omitirlo
también infla las colas:

```
Var(valor) = vol_mu·rate(1−rate) + rate_var·(vol_mu² − vol_mu + vol_var) + vol_var·rate²
```

### Las cuatro defensas contra el edge falso

1. **Shrinkage empírico-bayesiano de la varianza** (`SHRINKAGE_K0 = 25`):
   `var = w·var_observada + (1−w)·league_sd²`. Una racha caliente de 10 partidos
   no debería reescribir el sigma de un jugador.
2. **Semividas separadas**: el nivel usa una semivida corta (6 partidos) porque
   el rol cambia; la volatilidad usa una larga (18) porque es una propiedad de
   la temporada. Estimar la varianza con ~9 partidos efectivos la hace oscilar
   ±50 % por azar.
3. **Mezcla del nivel** (`LEVEL_BLEND_FAST = 0.65`): 65 % EWMA reciente + 35 %
   media de la muestra, para no apostar la mala semana de alguien que no cambió.
4. **`p_inactive` solo en mercados continuos**: los mercados de conteo ya
   producen ceros de forma natural (0 triples, 0 asistencias es un partido
   normal). Sumarles un 3 % de "no jugó" contaba la cola izquierda dos veces.

### Calibración medida

Medido **contra la historia sintética del demo** (`P_modelo` vs la frecuencia
empírica del mismo evento en los propios logs del jugador). Ojo: esto es
**auto-consistencia del pipeline, no validación contra resultados reales** — el
generador del demo usa la misma familia (BinomialNegativa + Beta-Binomial) que el
motor. Para validar de verdad hace falta una corrida con tus datos y el
settlement de las líneas.

Nivel y dispersión: prácticamente exactos.

| Cantidad | Ratio `modelo / empírico` |
|---|---|
| mediana (`P50`) | 1,03 |
| `P90` | 1,03 |
| `P95` | 1,04 |
| desvío estándar | 1,03 |
| coeficiente de variación | 1,04 |
| **sesgo (skewness)** | **0,63** |

El único número que no cierra es el **sesgo**: el motor reproduce sólo ~0,6× del
sesgo empírico. La causa es el presupuesto de varianza (sección anterior): la
varianza observada se reparte para que el **nivel y la dispersión** queden
honestos, y lo que se paga es parte de la asimetría.

La consecuencia es la dirección del error en la cola:

| Bucket de `P_empírica` | Ratio `P_modelo / P_empírica` |
|---|---|
| 20 – 35 % | 0,86 |
| 10 – 20 % | 0,77 |
| 5 – 10 % | 0,69 |
| 2 – 5 % | 0,60 |
| **global** | **0,90** |

En esta medición el motor es **conservador, no optimista**: subestima la
probabilidad en las líneas extremas, así que los edges que reporta son un piso,
no un techo.

Tres advertencias, porque acá es donde uno se miente solo:

1. Que el modelo sea conservador **en el demo** no dice nada sobre datos reales.
   El sesgo de un jugador real (rol cambiante, lesiones, defensa rival) es otra
   cosa.
2. El sesgo es el momento **menos confiable** de todos: el que peor se estima con
   ~30 partidos, y el que más depende del shrinkage (`SHRINKAGE_K0`).
3. El `--max-odds` sigue teniendo sentido — en cuotas muy altas cualquier error
   de la cola se multiplica — pero **no** porque hayamos medido sobreestimación.

### Backtest walk-forward con logs reales (`altlines/tools/backtest.py`)

La medición de arriba es sintética. Esta es la real: logs de ESPN de la
temporada 2025-26, **sin mirar el futuro** y sin gastar un crédito de The Odds API.

```bash
python -m altlines.tools.backtest                 # rosters cacheados (rápido)
python -m altlines.tools.backtest --teams ALL     # los 30 equipos (~9 min con 4 procesos)
#   --season 2026  --min-games 15  --min-minutes 20  --n-iter 10000  --workers 4
#   --conditional  (excluye partidos con minutos bajo el piso: salidas por lesión)
#   --no-save      (no escribe el JSON ni el CSV de predicciones)
```

Qué hace, por jugador de rotación (≥ 20 PJ y mediana ≥ 20 min en 2025-26) y por
cada partido a partir de su 15º de la temporada:

1. **historia = sólo los partidos anteriores** (2024-25 + 2025-26 hasta la
   víspera), con el mismo tratamiento que el modo real: sin playoffs, filtro de
   minutos (`LIVE_MIN_MINUTES_FRAC`) calculado **con esa historia**, y la
   semivida larga si el jugador vuelve de > 45 días parado;
2. `project_player` + `calibrate_player` → 10.000 simulaciones;
3. `P(valor > umbral)` en una escalera fija por mercado (`LADDERS`: puntos
   10.5…45.5 de a 5, rebotes 3.5…17.5 de a 2, asistencias 2.5…14.5 de a 2,
   triples 0.5…7.5 de a 1, PRA 15.5…65.5 de a 5);
4. lo que pasó esa noche (`hit` = 1 si superó el umbral).

Las mismas líneas se tarifan con dos referencias: **Normal ingenua** (media y sd
de la misma historia) y **curva del libro** (`casino_curve_prob`, un σ por
mercado, centrada en la mediana del modelo: mismo nivel, sólo cambia la forma).

**Resultado (corrida `--teams ALL`, 2026-10-19):** 260 jugadores, 12.727
partidos-jugador, 537.978 predicciones (364 partidos con minutos bajo el piso,
incluidos: una apuesta en un partido así se liquida igual).

| Bucket `P` predicha | n | Motor: pred → obs | ratio | Normal ingenua | Curva libro |
|---|---:|---|---:|---:|---:|
| 35 – 50 % | 28.585 | 42,2 % → 44,1 % | 0,96× | 1,07× | 1,02× |
| 20 – 35 % | 41.011 | 26,9 % → 28,9 % | 0,93× | 1,03× | 0,91× |
| 10 – 20 % | 44.403 | 14,5 % → 15,9 % | 0,92× | 0,92× | 0,82× |
| 5 – 10 % | 40.189 | 7,3 % → 7,9 % | 0,92× | 0,77× | 0,60× |
| 2 – 5 % | 50.218 | 3,3 % → 3,4 % | 0,97× | 0,59× | 0,48× |
| < 2 % | 257.651 | 0,36 % → 0,36 % | 1,00× | 0,27× | 0,18× |

`ratio` = p media predicha ÷ frecuencia observada (> 1 = cola demasiado gorda;
< 1 = demasiado flaca). Las columnas de las referencias son su propio ratio,
agrupando por **su** probabilidad.

| | Brier motor | Brier naive | Brier libro | LL motor | LL naive | LL libro |
|---|---:|---:|---:|---:|---:|---:|
| TOTAL | **0,0748** | 0,0773 | 0,0772 | **0,2374** | 0,2481 | 0,2539 |

El motor gana a las dos referencias en Brier y log-loss **en los 5 mercados y
los 3 perfiles** (`--teams ALL` imprime el desglose completo).

Ratio del motor en la cola, por mercado:

| Mercado | 10–20 % | 5–10 % | 2–5 % | < 2 % |
|---|---:|---:|---:|---:|
| asistencias | 0,99× | 1,02× | 1,05× | 0,86× |
| puntos | 0,89× | 0,88× | 0,97× | **1,38×** |
| PRA | **0,78×** | **0,81×** | 0,88× | 1,14× |
| rebotes | 1,00× | 1,03× | 1,04× | 0,89× |
| triples | 1,00× | 0,99× | 0,98× | 0,80× |

Por perfil, BOOM-BUST va de 0,85× (10–20 %) a 1,09× (< 2 %); STEADY ≈ 0,92×;
VOLATILE ≈ 0,94–1,00×.

**Cómo leerlo**

- **El motor está calibrado en la cola** dentro de ±10 % hasta el bucket de
  2–5 %, que es justo donde viven las alt lines. Sin ningún ajuste.
- **La tesis se sostiene:** una curva simétrica con un σ genérico (la del libro)
  subestima la cola profunda 2× en 2–5 % y 5× en < 2 %. La Normal del propio
  jugador también falla (0,59× / 0,27×). La asimetría del motor es la que
  acierta.
- **El sesgo es leve y conservador** (0,92–0,96× entre 5 y 50 %): el motor da un
  poco *menos* probabilidad de la que pasa, sobre todo en PRA. Esto contradice
  la lectura in-sample de BOS@DET (1,6× en 5–10 %): ese partido era un solo
  tablero, y con colas *ancladas* a la línea del mercado.
- **La excepción son las colas extremas de puntos** (< 2 %: 1,38×, y PRA
  1,14×): en líneas de +5000 el motor sí sobreestima. `MAX_DECIMAL_ODDS = 50`
  ya corta casi todo eso; no lo subas.

**El mapa de calibración** (`data/altlines/tail_calibration.json`). Por mercado
se ajustan una isotónica y una Platt (logística sobre `logit(p)`) con el 60 %
más viejo de los partidos, se comparan en el 40 % más nuevo (hold-out temporal,
sin mirar el futuro) y se guarda la que tiene menor log-loss, reajustada con
todo. Ganó Platt en los cinco mercados, con pendientes de 0,92–0,98, es decir,
un retoque. La mejora en el hold-out es mínima (log-loss de puntos: 0,2387 →
0,2381), y en el propio ajuste, Platt infla un poco las colas < 5 % (1,18× /
1,25×) a cambio de arreglar el medio.

`APPLY_TAIL_CALIBRATION` (en `config.py`) queda en **`False`** y la
recomendación es dejarlo así: el motor crudo ya está calibrado, el mapa casi no
mejora el hold-out y empeora justo las colas que apostamos. Si lo activás,
`ev.build_value_table` pasa cada `P(over)` por el mapa (el under queda como
`1 − p_over_cal − p_push`) y guarda el número crudo en `p_model_raw`.

Tres advertencias:

1. **Sin anclaje.** El tablero real mueve la mediana del motor a la línea
   principal (`anchor_to_line`); acá no hay líneas históricas, así que se mide
   el motor sin anclar. El anclaje conserva la forma, que es lo que se mide,
   pero el mapa se ajustó sobre probabilidades sin anclar.
2. **No es un backtest de EV.** Mide si las probabilidades son verdaderas, no si
   le ganan al precio. Para eso hace falta liquidar picks (`tools/settle.py`).
3. **Sesgo de supervivencia leve:** los jugadores salen de los rosters
   *actuales* (2026-27), así que faltan los que dejaron la liga.

Las predicciones crudas quedan en `data/altlines/backtest_predictions.csv.gz`
(gitignored, ~16 MB), para reagrupar sin volver a simular.

### Contexto del partido: rival, lesiones, descanso, localía (`altlines/context.py`)

El motor proyecta a cada jugador sólo con su historial. `context.py` le suma el
partido de esta noche como **un multiplicador por (jugador, mercado)** que
`project_player(level_mult=…)` aplica al volumen (`vol_mu`) y al nivel, con las
varianzas escaladas igual (var ∝ media). Escalar sólo `vol_mu` no alcanza: la
simulación fija la media al nivel proyectado y lo desharía.

Cada efecto se mide igual, con los logs de ESPN cacheados (cero créditos): por
partido-jugador, `valor ÷ media de sus 20 partidos anteriores`, agrupado y
siempre **con datos anteriores a la fecha**:

| Flag (`config.py`) | Qué hace | Shrinkage |
|---|---|---|
| `CONTEXT_OPP` | ratio del rival por mercado (ritmo + defensa juntos) | hacia 1,0 con `CTX_K_OPP = 200` partidos-jugador; la temporada anterior pesa 0,5 |
| `CONTEXT_INJURIES` | compañeros de rotación OUT (≥ 24 min en sus últimos 10) → sus minutos se reparten 60 % dentro de la posición y 40 % entre todos, ∝ minutos × margen (1 − min/42); sólo `INJ_REDIST` llega a la rotación | tope 1,5× |
| `CONTEXT_REST` | back-to-back / 1 día / 2+ | efecto de liga + el del jugador, `CTX_K_PLAYER = 40` |
| `CONTEXT_HOME` | local / visitante | ídem |

Un OUT cuenta sólo si jugó para **ese** equipo y sigue en él (en el backtest:
vuelve a jugar para él más adelante; en vivo: figura en el reporte de lesiones),
y si estuvo en alguno de los 3 últimos partidos del jugador. Si lleva semanas
afuera, la EWMA ya absorbió su ausencia y no es noticia. En el backtest, el OUT
es "jugador de rotación que no jugó ese partido", que es la aproximación pedida.

**Medición** (`python -m altlines.tools.backtest --teams ALL --variants opp,inj@0.2,…`,
2026-10-21). Las mismas 537.978 predicciones de arriba y las mismas
simulaciones (números aleatorios comunes por jugador-mercado-fecha), así que la
diferencia es sólo el contexto. El motor base reproduce el log-loss 0,23738.
`ΔLL` < 0 = mejor; `±SE` agrupado por jugador-partido-mercado. Las fechas se
parten 60/40 para ver si el efecto se sostiene en el tramo final.

| Variante | ΔLL total (±SE) | ΔLL 1º 60 % | ΔLL últ. 40 % | ΔBrier | Veredicto |
|---|---:|---:|---:|---:|---|
| descanso | +0,00038 (0,00008) | +0,00030 | +0,00051 | +0,00012 | **fuera** |
| local/visitante | +0,00039 (0,00007) | +0,00047 | +0,00027 | +0,00012 | **fuera** |
| rival | −0,00093 (0,00009) | −0,00081 | −0,00111 | −0,00029 | **activo** |
| descanso+local+rival | −0,00020 (0,00013) | −0,00008 | −0,00037 | −0,00006 | — |
| lesiones, redist 0,2 | −0,00086 (0,00008) | −0,00092 | −0,00078 | −0,00027 | **activo** |
| lesiones, redist 0,3 | −0,00093 (0,00012) | −0,00099 | −0,00085 | −0,00029 | empata con 0,2 |
| lesiones, redist 0,6 | −0,00001 (0,00021) | −0,00008 | +0,00008 | −0,00000 | demasiado |
| lesiones, redist 1,0 | +0,00264 (0,00030) | +0,00259 | +0,00272 | +0,00086 | demasiado |
| placebo: nivel ×1,02 a todos | +0,00015 (0,00006) | +0,00018 | +0,00011 | +0,00002 | control |
| **rival + lesiones 0,2 (config)** | **−0,00177 (0,00012)** | **−0,00174** | **−0,00180** | **−0,00054** | **activo** |

Por mercado, con rival + lesiones 0,2 (ΔLL): puntos −0,00243, PRA −0,00307,
asistencias −0,00140, rebotes −0,00069, triples −0,00071. Mejora en los cinco.
En el 43 % de filas con algún compañero OUT, ΔLL = −0,00317.

Calibración por bucket (pred/obs), motor base → rival + lesiones 0,2:

| Bucket | 35–50 % | 20–35 % | 10–20 % | 5–10 % | 2–5 % | < 2 % |
|---|---:|---:|---:|---:|---:|---:|
| base | 0,96× | 0,93× | 0,92× | 0,92× | 0,97× | 1,00× |
| rival + lesiones | 0,98× | 0,97× | 0,97× | 1,00× | 1,09× | 1,09× |

Cómo leerlo:

- **El rival es el único ajuste "de calendario" que sirve**: mejora en las dos
  mitades y en los cinco mercados. Descanso y localía empeoran incluso con
  shrinkage: el efecto real es más chico que el ruido con que se estima por
  jugador.
- **Lesiones: la tesis 3 se sostiene.** Cuando falta un titular, el suplente
  rinde por encima de su historial. El placebo descarta que sea sólo "subir el
  nivel": un ×1,02 parejo empeora el total, mientras que el ajuste por
  lesiones, con un multiplicador medio parecido (1,016), lo mejora. En las filas
  con OUT gana 2,3× más que el placebo (−0,00202 vs −0,00087).
- **Pero redistribuir de más rompe la calibración** (0,6 y 1,0 empeoran: los
  minutos liberados se van más a la rotación profunda de lo que parece).
  `INJ_REDIST = 0,2` es la pendiente de minutos reales sobre el boost predicho
  en 2024-25, fijada **antes** de mirar el backtest. 0,3 queda a 0,00005 (un
  tercio de SE) e infla más la cola < 2 % (1,16× vs 1,09×), así que queda 0,2.
- **Costo:** el sesgo conservador del motor (0,92×) desaparece, y las colas
  2–5 % y < 2 % pasan de 1,0× a 1,09×. `MAX_DECIMAL_ODDS` sigue cortando la
  zona donde eso pesa.

**En modo real** (`live.build_live_dataset`): para cada partido se calcula el
multiplicador de cada jugador con el reporte de lesiones de ESPN
(`site.api.espn.com/…/nba/injuries`, público, caché 30 min; sólo cuenta
`status = Out`). La rotación activa de un equipo con un OUT sale de sus
gamelogs de ESPN (caché). Las tablas de rival se arman con todos los gamelogs
cacheados (temporada actual + la anterior a 0,5). El resultado queda en
`live_meta["context"]` (factores, compañeros OUT y `mult`) y `run_pipeline` lo
pasa a `simulate_universe(level_mults=…)`; la tabla resumen trae `level_mult`.
Si algo falla, el tablero sale igual, sin contexto.

**Advertencia importante: el anclaje.** El tablero real mueve la mediana del
motor a la línea principal del mercado (`anchor_to_line`). Así, en vivo, el
ajuste de **nivel** se pierde casi entero: lo que sobrevive es el cambio de
dispersión, el filtro `level_ok` (ahora el modelo y el mercado discrepan menos
cuando el libro ya movió la principal por una baja) y la mezcla motor-mercado.
El backtest mide el motor **sin anclar**, porque no hay líneas principales
históricas. Para aprovechar la tesis 3 de lleno (el libro mueve la principal
del suplente pero no re-tarifa su escalera alternativa), el próximo paso es
medir con picks liquidados si conviene anclar menos cuando hay un OUT.

**Repaso sobre las noches reales liquidadas**
(`python -m altlines.tools.context_replay`): reconstruye el tablero de cada
partido de `picks.db` con las cuotas **cacheadas** (0 créditos), historial
cortado el día anterior y el reporte de lesiones que ESPN adjunta al resumen del
partido. Compara las variantes sobre las mismas líneas:

| Variante | líneas | LL motor | LL mezcla | Brier motor | tablero |
|---|---:|---:|---:|---:|---|
| base | 180 | 0,4748 | 0,4608 | 0,1521 | 0-11 |
| rival | 180 | 0,4761 | 0,4611 | 0,1524 | 0-10 |
| lesiones | 180 | 0,4748 | 0,4608 | 0,1521 | 0-11 |
| config (rival + lesiones) | 180 | 0,4761 | 0,4611 | 0,1524 | 0-10 |

BOS@DET y PHI@NYK, noche inaugural. Ningún OUT calificó: Carter y Prince
jugaron 2025-26 en otros equipos, y los de PHI no eran de rotación. Por eso
"lesiones" = base, y el rival mueve el LL en el cuarto decimal. Dos partidos no
deciden nada; el 0-11 del tablero no viene del contexto.

### El libro, reproducido

`casino_sigma` (en `config.py`) es el sigma genérico del libro por mercado —
**uno solo para todos los jugadores**, que es exactamente la ineficiencia que
explotamos. `casino_curve_prob()` reproduce esa curva Normal centrada en la
línea principal y alimenta la columna `Curva`.

La quita de vig es real: emparejamos over/under de la misma línea para medir el
overround, y usamos margen asumido (`ASSUMED_MARGIN_SINGLE_SIDED`) cuando la
línea es de un solo lado, como pasa en las Alt Lines.

---

## 6. Datos de entrada

**Game logs** (`player_game_logs.csv`):

```
date, season, week, game_id, player_id, player_name, team, opponent, position,
market, value, usage, snap_share, home_away, rest_days, opp_def_rating
```

- `value` = el stat realizado (puntos, rebotes, asistencias, triples, PRA)
- `usage` = el conductor de oportunidad: **FGA** para puntos/PRA, **minutos**
  para rebotes/asistencias/PRA, **3PA** para triples
- `game_id` = partido al que pertenece la fila (el mismo id del calendario).
  Es lo que permite correr "sólo un partido" sin recalcular nada

**Cuotas** (`alt_lines_odds.csv`):

```
snapshot_ts, event_date, game_id, player_id, player_name, team, market, book,
side, point, decimal_odds, main_line
```

El `player_id` tiene que coincidir entre los dos archivos (es la clave del join),
y el `game_id` con el calendario (es la clave del filtro por partido).

---

## 7. La pestaña NBA en la app

El motor está enganchado al frontend existente como una **pestaña de deporte**,
sin tocar el modelo MLB:

- **Backend** — dos endpoints al final de `backend/main.py`:
  - `GET /api/altlines/slate` → el **calendario** (no simula): equipos, logos,
    horario, estado, cuántos props hay por partido y cuánto tardaría. ~2 s.
  - `GET /api/altlines/edges` → el tablero. Con `?game=<id>` simula **sólo ese
    partido**; sin `game`, la liga entera.

  Los dos importan `altlines.api` **de forma perezosa** dentro del handler, así
  que un problema en el paquete no impide que arranque la API MLB. Son `def`
  normales (no `async`) para que FastAPI los corra en su threadpool: es CPU pura
  (20.000 iteraciones por jugador-mercado) y no puede bloquear los endpoints MLB.
  Cada respuesta se cachea 5 minutos, con **candado por clave**: si dos pestañas
  piden el mismo partido, corre una sola vez. `?refresh=true` ignora la caché.
  La API **no escribe los CSV de demo** (`write_data=False`): leer un tablero no
  debe ensuciar `data/altlines/`.

- **Frontend** — pestañas `MLB` / `NBA` en la topbar; la sección NBA se
  auto-construye desde `app.js` (`showSport`, `loadSlate`, `simulateGame`).
  El flujo es **pizarra primero**:

  1. al abrir la pestaña se pide **sólo el calendario** y se dibuja la grilla de
     partidos (cada tarjeta con sus logos, horario local y una estimación);
  2. al hacer clic en una tarjeta se simula **ese** partido y aparece su tablero;
  3. **"Simular todos"** recorre los partidos de a uno, con progreso real en cada
     tarjeta;
  4. **"Liga (N simulados)"** no cuesta nada: es la **fusión en el navegador** de
     los `all_edges` de cada partido ya simulado, re-rankeada con la misma regla
     del top 5 % de EV > 0. Da **exactamente** el mismo tablero que una corrida
     de liga completa, sin pagar el cómputo.

  Al abrir un partido quedás **exactamente en el top 5 %** que reporta el motor,
  con filtros por mercado, lado, EV mínimo y jugador, más el panel de perfiles de
  distribución.

### Calidad del tablero (después de los primeros partidos liquidados)

Los dos primeros partidos reales (20/10) dieron **0 de 27** en los recomendados:
el tablero elegía cuotas largas (media 19) donde el motor se equivocaba para
arriba, contra un precio justo inflado. Tres cambios (`config.py`, bloque
"Board quality"):

1. **Margen real de las bandejas alt** (`ingest.ladder_gamma`). Las alt lines
   vienen de un solo lado, así que se suponía un margen de 5,5 %. Ahora cada
   escalera (casa × jugador × mercado) se desvigoriza con
   `p_justa = p_implícita ** γ`, con γ tal que la línea principal quede en 50 %:
   le saca más a las cuotas largas, como hacen las casas. Sobre 4.794 escalones
   liquidados: log-loss 0,4429 → 0,4409 y la sobreestimación en 2–5 % pasa de
   0,20× a 0,41× (en 5–10 %, de 0,41× a 0,61×).
2. **Mezcla con el mercado** (`ev.blend_with_market`, `MODEL_WEIGHT = 0.40`):
   `p = σ(0,4·logit p_motor + 0,6·logit p_justa)`. Elegir el mayor EV entre
   cientos de líneas elige justo donde el motor se pasó (maldición del ganador).
   La fila guarda `p_engine` (motor solo) y `p_model` (la mezcla, con la que se
   calcula el EV).
3. **Selección sin correlación** (`ev.select_board`): el tamaño sigue siendo el
   5 % de las líneas con EV > 0, pero sólo con `p ≥ 15 %`, cuota `≤ 10`, una
   línea por jugador y mercado y como mucho 2 por jugador.

Repetición de esos dos partidos con el mismo motor (cuotas de la caché e
historial cortado el día anterior): viejo **3-24, −2,1u** (27 picks de 9
jugadores, cuota media 21,9); nuevo **3-12, +5,9u** (15 picks de 11 jugadores,
cuota media 6,1; el modelo esperaba 2,9 ganadas). Dos partidos no prueban
rentabilidad; `MODEL_WEIGHT` y los filtros se ajustan con los picks liquidados.

### Pickstachio: la cara de la pestaña NBA

La pestaña NBA es otra app dentro de la app: **Pickstachio**, en verde
pistacho y negro (variables del tema bajo `body[data-sport="nba"]` en
`styles.css`). Al pasar de MLB a NBA, un círculo crece desde la pestaña, aparece
el logo y el velo se funde revelando la página convertida (`_sportMorph` en
`app.js`; con `prefers-reduced-motion` es un fundido corto).

Navegación (sólo en NBA; MLB conserva la suya):

- **Barra lateral = riel de partidos.** La fecha (‹ MAR 20 OCT ›; tocarla vuelve a
  hoy), una fila por partido con logos, horario y estado (Simular / simulando /
  cantidad de pistachos), y al pie **Toda la liga** y **Simular todos**. En el
  teléfono el riel es una tira deslizable arriba del contenido.
- **Barra superior:** el logo (pistacho abierto + wordmark en Big Shoulders),
  el cambio MLB/NBA y los ajustes: Demo/Real, créditos de The Odds API y sonido.
- **Cabecera del partido:** la cancha de la simulación en grande; al terminar
  dibuja la distribución real del mejor pick contra la curva del libro.

El tablero tiene dos vistas (selector **Easy | Nerd**, se recuerda por navegador):

- **Easy** (`pickstachio.js`): un panel de picks. Cada línea con valor es un
  **Pistacho**, según su *Pickstachio Score* (0–100: ventaja 40 %, cola gorda
  25 %, probabilidad 20 %, nivel alineado 15 %):
  **Pistacho Dorado** (≥ 70), **Pistacho Abierto** (50–69), **Cáscara Dura** (< 50).
  Cada pick es un **cromo** cuadrado: color del equipo, logo de marca de
  agua, foto del jugador (ESPN, sólo en modo Real), marco del color de su
  categoría (dorado holográfico para los Dorados) y la apuesta en una franja
  negra; con el mouse se inclina en 3D con un reflejo. Al tocarlo, el cromo
  viaja al centro, se agranda y a su lado se despliega su **ficha**
  (Escape o clic afuera la cierra y el cromo vuelve a su lugar). La ficha
  muestra la apuesta, la cuota americana y la casa, una frase
  ("lo ve llegar 1 de cada N; el libro paga como 1 de cada M"), alertas
  (Cola gorda, Boom-or-bust, Hito lejano, Nivel alineado —sólo en modo
  real—, Longshot) y cuatro métricas con su definición (Ventaja, Cola gorda,
  Hito, Explosividad). Abajo, un glosario.
- **Nerd**: la tabla completa con filtros, probabilidades, curvas y perfiles.

### La animación de la simulación: la cancha

`altlines-galton.js` (raíz del repo, canvas + WebAudio, sin assets). Corre en
la cabecera del partido y tiene tres fases:

1. **Mientras simula**: media cancha NBA vista desde arriba, con proporciones
   reales (línea de 3 a 23,75 pies, pintura de 16 × 19 con el color del local)
   encuadrada como una toma de TV. Salen tiros en parábola hacia el aro (la
   sombra marca el piso; los del régimen "boom", un 12 %, van en pistacho),
   la red se sacude con los que entran, y cada tiro suma su resultado a un
   **marcador de LEDs** al pie que arma la distribución. Encima del marcador,
   la Normal del libro centrada en la mediana; las columnas de la cola que la
   superan se encienden. Un **reloj de posesión** cuenta los segundos reales
   de la simulación. Se muestra al menos 3,5 s aunque el motor termine antes.
2. **Sonido**: un pad cálido que respira (sube ~1 s, se desvanece ~1,5 s y
   vuelve) con un latido grave suave en cada pico. Cuando un tiro cae en la
   cola por encima del libro, el filtro del pad se abre y brilla un instante.
   Botón de sonido en la barra superior (se recuerda por navegador).
3. **Al terminar**: la cancha se disuelve en el **resultado real** del mejor
   pick del partido —su distribución (media, desvío y sesgo del motor, como
   Gamma desplazada) contra la Normal del libro (σ recuperado de `p_curve`)—,
   con la línea alt marcada, la región ganadora en verde y
   `modelo X % · libro Y %`. El pad da una última respiración larga y se apaga.

Los tiros son ilustrativos (el cómputo real corre en el servidor); el reloj y
el cuadro final no. Con `prefers-reduced-motion` no hay animación: la cabecera
pasa directo al cuadro final. `assets/altlines/sim-loop.*` y
`tools/make_sim_video.py` quedan sin uso.

### Por qué un partido == la liga (determinismo)

El motor ya **no comparte un generador aleatorio global**. La semilla se deriva
por `(jugador, mercado)`:

```
SeedSequence([RANDOM_SEED, blake2b(player_id, market)])   # simulation.py::player_rng
```

Así el resultado de un jugador **no depende del orden** ni de cuántos partidos
entraron: correr un solo partido, un subconjunto o la liga entera da valores
**bit a bit idénticos** (es lo que permite que la fusión del navegador cierre
contra una corrida de liga).

Parámetros de `/api/altlines/edges`: `game`, `date`, `offline`, `n_iter`,
`top_pct`, `metric`, `devig`, `max_odds`, `min_ev`, `halflife`, `seed`, `refresh`.
Y de `/api/altlines/slate`: `date`, `direction` (`‹` = -1), `offline`, `n_games`,
`seed`, `refresh`.

### Cómo abrir la app (la parte que se rompe fácil)

Son **dos servidores** los que tienen que estar arriba:

1. el backend FastAPI (la API MLB **y** el endpoint de AltLines) en el puerto **8002**;
2. un servidor estático que sirva el frontend (o sea, `index.html`).

**La forma fácil:** doble clic en `start_cherrypick.bat` (raíz del repo). Levanta
los dos en `127.0.0.1`, espera a que cargue el modelo y abre el navegador solo.

**La forma manual:**

```bash
# terminal 1 — backend
cd backend
python -m uvicorn main:app --host 127.0.0.1 --port 8002

# terminal 2 — frontend (desde la raíz del repo)
python -m http.server 5500 --bind 127.0.0.1
```

y después entrá a **`http://localhost:5500/index.html`**.

> **El doble clic en `index.html` (`file://`) funciona, pero es el camino
> frágil.** Con `file://` el `hostname` queda vacío y el `app.js` tiene que caer
> a `localhost` para armar la URL del backend; algunos navegadores bloquean los
> pedidos al backend desde ese origen (el video sí carga, porque es una ruta
> relativa). Si ves *Failed to parse URL* o *Failed to fetch*, serví la carpeta
> en vez de doble clic.

---

## 8. Tests rápidos de sanidad

```bash
# 1) Calendario: no simula, sólo dice qué se juega
python -m altlines.run_pipeline --demo --list-games

# 2) Un partido suelto (anotá un id o un tricode de la lista anterior)
python -m altlines.run_pipeline --demo --game BOS@DET

# 3) Consistencia: un partido, un subconjunto y la liga deben dar lo mismo
python -m altlines.run_pipeline --demo                      # liga entera
python -m altlines.run_pipeline --demo --game BOS@DET       # un partido
#    → mismo EV y mismo P_modelo para el mismo (jugador, mercado)

# 4) Calibración de colas (reproduce las tablas de la sección 5)
python -m altlines.tools.check_calibration --offline
#    → reliability por bucket de P_empírica, cuantiles P10…P99 y sesgo
```

Los números de la sección 5 salen de ese comando. Si tocás el motor, volvé a
correrlo: es la forma barata de detectar que "mejoraste" el modelo inflando la
cola.

---

## 9. Lo que este motor NO es

- **No es una regresión.** Una regresión predice un número cerca de la mediana;
  acá reconstruimos la distribución entera (media, sesgo, curtosis, colas).
- **No es un simulador de partidos.** No juega partidos: simula el stat.
- **No conoce el contexto.** No sabe de lesiones, faltas, blowouts ni rotaciones.
  `p_inactive` es un parche grueso (3 % en continuos), no un modelo de
  disponibilidad. En modo real el ancla al mercado absorbe el *nivel* (el
  mercado sí sabe de lesiones), pero la *forma* sigue saliendo del rol que el
  jugador tuvo en su historial: si un suplente hereda los minutos de un titular
  lesionado, su dispersión real cambia y el motor no lo ve.
- **No está backtesteado contra resultados reales.** En la primera corrida real
  (BOS@DET, 20/10/2026) el modelo pone las colas ~1,4× por encima del precio justo
  del mercado de forma bastante pareja. Eso es exactamente lo que dice la tesis…
  o un sesgo del modelo. Sólo el settlement lo decide. La calibración de la
  sección 5 es contra la historia **sintética** del demo, no contra el settlement
  de las líneas. Antes de poner dinero de verdad, hay que loguear cada pick y
  medir CLV y ROI.
