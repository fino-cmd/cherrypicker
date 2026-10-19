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

### Pickstachio: la cara de la pestaña NBA

La pestaña NBA es otra app dentro de la app: **Pickstachio**, en verde
pistacho y negro (variables del tema bajo `body[data-sport="nba"]` en
`styles.css`). Al pasar de MLB a NBA, un círculo crece desde la pestaña, aparece
el logo y el velo se funde revelando la página convertida (`_sportMorph` en
`app.js`; con `prefers-reduced-motion` es un fundido corto).

El tablero tiene dos vistas (selector **Easy | Nerd**, se recuerda por navegador):

- **Easy** (`pickstachio.js`): un panel de picks. Cada línea con valor es un
  **Pistacho**, según su *Pickstachio Score* (0–100: ventaja 40 %, cola gorda
  25 %, probabilidad 20 %, nivel alineado 15 %):
  **Pistacho Dorado** (≥ 70), **Pistacho Abierto** (50–69), **Cáscara Dura** (< 50).
  Cada tarjeta muestra la apuesta, la cuota americana y la casa, una frase
  ("lo ve llegar 1 de cada N; el libro paga como 1 de cada M"), alertas
  (Cola gorda, Boom-or-bust, Hito lejano, Nivel alineado —sólo en modo
  real—, Longshot) y cuatro métricas con su definición (Ventaja, Cola gorda,
  Hito, Explosividad). Abajo, un glosario.
- **Nerd**: la tabla completa con filtros, probabilidades, curvas y perfiles.

### La animación de cada tarjeta: "La cola canta"

`altlines-galton.js` (raíz del repo, canvas + WebAudio, sin assets) reemplaza
al viejo video en loop. Tiene tres fases:

1. **Mientras simula**: un tablero de Galton con los colores de los dos equipos.
   Las tiradas caen por los clavos (un 12 % en régimen "boom" empuja a la
   derecha) y se apilan en una distribución sesgada. Encima, en fantasma, la
   Normal del libro centrada en la mediana de la pila; los compartimentos de la
   cola que la superan se encienden en verde. Se muestra al menos 3,5 s aunque
   el motor termine antes.
2. **Sonido**: un pad cálido que respira (sube ~1 s, se desvanece ~1,5 s y
   vuelve) con un latido grave suave en cada pico. Cuando una tirada cae en la
   cola por encima del libro, el filtro del pad se abre y brilla un instante.
   Botón **Sonido: sí/no** en la pizarra (se recuerda por navegador).
3. **Al terminar**: la pila se disuelve en el **resultado real** del mejor pick
   del partido —su distribución (media, desvío y sesgo del motor, como Gamma
   desplazada) contra la Normal del libro (σ recuperado de `p_curve`)—, con la
   línea alt marcada, la región ganadora en verde y `modelo X % · libro Y %`.
   El pad da una última respiración larga y se apaga.

Las fases 1 y 2 son ilustrativas (el cómputo real corre en el servidor); la 3
sale del payload. Con `prefers-reduced-motion` no hay animación: la tarjeta
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
