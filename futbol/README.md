# Futbol — modelo de remates y remates al arco

Liga Profesional (Argentina), Brasileirão Série A y Premier League. No
pronostica resultados: pronostica **cuánto se remata y quién remata**.

## Fuentes

| Qué | De dónde | Costo |
|---|---|---|
| Partidos, remates y al arco por equipo y jugador, titulares, minuto de cada cambio, posición | ESPN (`site.api.espn.com`) | gratis |
| Alineación confirmada (~1 h antes) | ESPN | gratis |
| Cuotas de remates por jugador | OddsPapi: **bet365 AR**, **Betano**, **Betsson** | 1 consulta por casa por refresco (plan gratis: 250/mes) |

La clave de OddsPapi va en `backend/.env` como `ODDSPAPI_KEY` (fuera de git).

Cobertura de cuotas (octubre 2026):

| Casa | Liga Profesional | Brasileirão | Premier |
|---|---|---|---|
| bet365 AR | remates + al arco (escalera 1+, 2+…) | ídem | ídem |
| Betano | remates | remates | remates |
| Betsson | — | remates + al arco (O/U) | ídem |

Betwarrior no está en ningún proveedor: para esa casa sirve la **cuota mínima**.

## El modelo (`futbol/model.py`)

1. **Equipos** (por liga, sólo con partidos anteriores a la fecha):
   - remates ~ binomial negativa con `log λ = μ + local + ataque(equipo) + defensa(rival)`,
     Poisson con ridge (= `TEAM_PRIOR_K` partidos virtuales en el promedio) y
     peso por recencia (semivida `TEAM_HALFLIFE` partidos ≈ semanas);
   - puntería (al arco / remates): `logit p = ν + puntería(equipo) + permite(rival)`;
   - la sobre-dispersión (r) y la variación de puntería "del día" se miden en cada liga.
2. **Jugadores**:
   - porción de los remates del equipo **por minuto en cancha**, con prior de su
     posición (G/D/M/F) equivalente a `SHARE_PRIOR_MIN` minutos;
   - puntería propia con prior de su posición;
   - minutos: de la formación confirmada, o de su historial (titularidades,
     minutos cuando es titular, chance de entrar desde el banco).
3. **Simulación del partido** (20.000 iteraciones): remates de cada equipo →
   reparto multinomial entre los jugadores según porción × minutos → cada
   remate va al arco con la puntería del jugador × el factor del día. Todo
   suma coherente: los remates de los jugadores son los del equipo.

## ¿Funciona? (backtest walk-forward)

`python -m futbol.backtest`: cada partido se pronostica sólo con datos
anteriores a ese día y con la alineación real (la que se conoce ~1 h antes).
Historia: 2.300 partidos de ESPN desde enero de 2025.

**Jugadores** (~36.000 actuaciones de titulares, 3 ligas): calibrado.

| El modelo dice | Pasó |
|---|---|
| 10–20 % | 15 % |
| 20–35 % | 26 % |
| 35–50 % | 42 % |
| 50–65 % | 55 % |
| 65–80 % | 71 % |
| 80–100 % | 86 % |

Le gana a la referencia ingenua (promedio del jugador por 90' × minutos) en
las 3 ligas y en todas las líneas (log-loss 0,392 vs 0,447); donde más aporta
es en **1+ al arco**.

**Equipos**: también le gana a la referencia (log-loss 0,569 vs 0,639), pero
fuera de muestra queda **un poco conservador** en Argentina (−3 %) y Brasil
(−5 %): en 2026 se remata bastante más que en 2025 en las tres ligas
(Argentina 11,8 → 13,6 remates por equipo, Brasil 12 → 15–16) y el nivel nuevo
todavía se está asentando.

Correcciones que ya tiene:
- nivel general de la liga con ventana corta (`LEAGUE_HALFLIFE_DAYS`), aparte
  de la fuerza de cada equipo;
- `python -m futbol.calibrate`: sesgo de nivel y dispersión por liga medidos
  **fuera de muestra** (con partidos anteriores al 1/4/2026; el backtest se
  evalúa después de esa fecha) → `data/futbol/team_calibration.json`.

## Cada jugador según su rol y su constancia

No todos los delanteros juegan igual. El prior de cada jugador sale de su
**rol** según el puesto real que le da ESPN en la formación:
centrodelantero (F, CF), extremo (LF/RF, AM-L/AM-R), enganche (AM), volante
(CM, DM y los dobles cinco LM/RM del 4-2-3-1), lateral, central, arquero.
Además se mide su **constancia** (sobre-dispersión φ): cuánto se aparta
partido a partido de lo esperado por los remates de su equipo y sus minutos.
Un jugador muy constante tiene una distribución angosta; uno irregular, colas
más anchas (más 0 y más 4+). φ se encoge hacia el de su rol (`PHI_PRIOR_N`).

Las probabilidades son **condicionales a que juegue**: si no entra, la casa
devuelve la apuesta.

## Con suplente (Suplente+, Suplente de Oro, Suplente)

bet365 (Suplente+), Betano (Suplente de Oro) y Betsson (Suplente): si el
titular sale, la apuesta pasa al que entra y **se suman los dos** (90' +
descuento). Reglas de bet365 confirmadas; las de Betano y Betsson se asumen
iguales. La simulación calcula esa probabilidad para cada titular (el que
entra juega lo que falta con la porción promedio de los suplentes de su
grupo). Un pick "con suplente" es el que sólo paga si se activa la promo; en
la app se pueden ocultar con el interruptor. La liquidación busca al que
entró en el minuto en que salió el titular (con dos cambios simultáneos puede
quedar ambiguo).

## Ancla del mercado del partido (`futbol/market.py`)

El 1X2 y las líneas de goles resumen lo que el mercado sabe (lesiones,
rotación, motivación) en los goles esperados de cada equipo. Si el mercado ve
a un equipo más fuerte que nuestro modelo de goles, va a rematar más:

    log λ_remates' = log λ_remates + β1·log(goles_mercado / goles_modelo) + β2·(lo mismo del rival)

β se mide con cierres históricos gratis de football-data.co.uk (antes del
1/4/2026) y se evalúa después. Fuera de muestra:

| Liga | β1 | β2 | Log-lik remates | Se usa |
|---|---|---|---|---|
| Premier | +0,21 | −0,01 | 2,906 → 2,894 (mejora) | sí |
| Brasileirão | +0,07 | −0,04 | 3,085 → 3,083 (mejora leve) | sí |
| Liga Profesional | +0,15 | −0,07 | 2,924 → 2,927 (empeora) | no |

En vivo los precios del partido salen de la misma consulta de OddsPapi que
trae los remates (no gasta cuotas extra). `python -m futbol.market` vuelve a
medir β y sólo activa las ligas donde mejora.

## Registro de picks y resultados (`futbol/picklog.py`)

Cada corrida guarda sus picks en `data/futbol/picks.db`: la primera cuota que
se vio y la última antes del partido (para medir si el precio tomado le gana
al cierre). Cuando ESPN da el partido por terminado se liquida solo: ganada,
perdida o nula (el jugador no entró). La app lo pide a `/api/futbol/results`
y muestra el récord y las unidades (1 u por pick) en la vista simple.

```bash
python -m futbol.picklog     # liquida lo pendiente y muestra el récord
```

## Cruce con las casas

- Partidos: misma liga, ±30 h y nombres de equipo parecidos (o uno idéntico y
  el mismo horario).
- Jugadores: nombre normalizado; si no coincide exacto, por palabras
  contenidas ("Alberto Campo" ⊂ "Driden Alberto Campo Deusa") sólo con 2+
  palabras — los apodos de una palabra (Pedro, Matheus) tienen que coincidir
  exacto.
- **Control de cordura**: una cuota de más de 3× la cuota justa del modelo se
  descarta (casi siempre es un jugador cruzado mal).

## Fotos

`python -m futbol.photos` busca la foto de cada jugador de la jornada en
TheSportsDB (recortadas, sin fondo) una sola vez y la guarda en
`data/futbol/cache/photos.json`. La clave pública admite pocas consultas por
minuto: la primera vez tarda (~2 s por jugador), después es instantáneo.

## Uso

```bash
python -m futbol.backtest            # ¿el modelo acierta? (walk-forward, sin cuotas)
python -m futbol.run                 # la jornada: panorama + picks
python -m futbol.run --refresh-odds  # cuotas nuevas (3 consultas de OddsPapi)
```

## Formaciones: XI esperado → confirmada

Las formaciones se publican ~1 h antes. Hasta entonces cada partido muestra un
**XI esperado** (`futbol/lineups.py`, al estilo Sofascore pero con nuestra
historia): el esquema del último partido y, en cada puesto, el jugador que más
lo ocupó en las últimas 5 fechas (más peso a las recientes). Cada uno lleva su
chance de ser titular; debajo de 60 % se marca como **duda**.

La pestaña Fútbol consulta `/api/futbol/lineups/check` cada 3 min mientras
está abierta y hay partidos en las próximas 2 h. Cuando ESPN publica la
formación, **se re-simula sólo ese partido** con la alineación real (sin gastar
cuotas), la cancha pasa a "Formación confirmada" y aparece un aviso. A mano:

```bash
python -m futbol.run --check-lineups
```

`futbol.run` deja `data/futbol/jornada.json` y muestra por partido los remates
esperados de cada equipo, la probabilidad de cada jugador de 1+, 2+, 3+
remates y 1+, 2+ al arco, la **cuota justa** y la **cuota mínima**
(justa × 1,05): si tu casa paga eso o más, la apuesta tiene sentido.
