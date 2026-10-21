"""
AltLines — orchestrator / CLI.

Runs the full pipeline end to end:

    game logs ─┐
               ├─► simulate_universe ─► value table ─► top-5% ─► console + HTML
    alt odds ──┘

Examples
--------
    # Zero-setup: build synthetic data, simulate, and report (default).
    python -m altlines.run_pipeline --demo

    # Real data you exported/wrote yourself:
    python -m altlines.run_pipeline --logs data/altlines/player_game_logs.csv \
                                     --odds data/altlines/alt_lines_odds.csv

    # Only books you can actually bet, tighter slice, power de-vig:
    python -m altlines.run_pipeline --demo --books betano,pinnacle \
                                    --top-pct 5 --min-ev 3 --devig power

    # Also launch the Streamlit panel (needs `pip install streamlit`):
    python -m altlines.run_pipeline --demo --panel streamlit
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python altlines/run_pipeline.py` as well as `python -m altlines.run_pipeline`.
if __package__ in (None, ""):  # pragma: no cover - CLI convenience
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which cannot encode box/arrow glyphs.
# Force UTF-8 and never crash on an un-encodable character.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - older/captured streams have no reconfigure
        pass

import pandas as pd

from altlines import config as cfg
from altlines import ev as ev_mod
from altlines import slate as slate_mod
from altlines.dashboard import render_console, write_html_report
from altlines.ev import build_value_table, select_board, top_n
from altlines.ingest import (
    fetch_alt_lines_api,
    generate_demo_dataset,
    load_game_logs,
    load_odds,
    write_demo_dataset,
)
from altlines.simulation import simulate_universe
from altlines.tiers import add_tiers


def _print_distribution_report(summary: pd.DataFrame, n: int = 12) -> None:
    """Showcase the core insight: mean vs median, skew, and the profile tag."""
    if summary.empty:
        return
    print("\nDISTRIBUTION PROFILES  (mean > median => the book's median-anchored "
          "curve understates the right tail)")
    print("-" * 92)
    print(f"{'Player':<16} {'Market':<20} {'Mean':>7} {'Median':>7} "
          f"{'m-med':>7} {'Skew':>7} {'CV':>6} {'Profile':<11}")
    print("-" * 92)
    show = summary.sort_values("mean_minus_median", ascending=False).head(n)
    for _, r in show.iterrows():
        print(f"{str(r['player_name'])[:16]:<16} {str(r['market_label'])[:20]:<20} "
              f"{r['sim_mean']:>7.2f} {r['sim_median']:>7.2f} "
              f"{r['mean_minus_median']:>7.2f} {r['sim_skew']:>7.2f} "
              f"{r['sim_cv']:>6.2f} {r['profile']:<11}")
    print("-" * 92)


def run(demo: bool = False,
        logs_path=None, odds_path=None,
        n_iter: int = cfg.N_SIM_DEFAULT,
        top_pct: float = 5.0,
        metric: str = "ev",
        min_ev: float = 0.0,
        min_prob: float = 0.0,
        max_odds: float = ev_mod.MAX_DECIMAL_ODDS,
        devig_method: str = "multiplicative",
        books: list[str] | None = None,
        halflife: float = 6.0,
        halflife_var: float | None = None,
        seed: int = cfg.RANDOM_SEED,
        out_html=cfg.REPORT_HTML,
        write_html: bool = True,
        quiet: bool = False,
        slate: dict | None = None,
        game: str | None = None,
        offline: bool = False,
        write_data: bool = False,
        live: bool = False,
        date: str | None = None,
        refresh_odds: bool = False,
        anchor: bool | None = None,
        log_picks: bool = True,
        log_origin: str = "cli",
        calibrate: bool | None = None) -> dict:
    """Execute the pipeline and return a dict of artifacts (frames + paths).

    `game` restricts the whole run to a single game of the slate (the "por
    partido" mode). Because `simulate_universe` seeds per (player, market), a
    single-game run returns **exactly** the same numbers for that game as the
    league-wide run — that is what makes the per-game view trustworthy.

    `write_data` persists the synthetic dataset to CSV. Off by default: the web
    API regenerates it in memory on every request and must not churn files (or
    race with the CLI doing the same).
    """
    # ── 1. Data ───────────────────────────────────────────────────────────────
    resolved_slate = slate
    live_meta = None
    if live:
        # Real data: ESPN game logs + The Odds API alt trays. Resolve the game
        # FIRST so a single-game run only pays credits for that game.
        from altlines.live import build_live_dataset
        if resolved_slate is None:
            resolved_slate = slate_mod.get_slate(date, offline=False)
        if game:
            hit = slate_mod.find_game(resolved_slate, str(game).strip())
            if hit is not None:
                game = str(hit["game_id"])
        print(f"[1/4] Calendario NBA: {len(resolved_slate['games'])} partidos "
              f"({resolved_slate['date']}). Datos REALES: ESPN + The Odds API...")
        logs_df, odds_df, live_meta = build_live_dataset(
            resolved_slate, game=game, refresh_odds=refresh_odds)
        q = (live_meta or {}).get("quota") or {}
        if q.get("remaining") is not None:
            print(f"      Odds API: {q.get('last_cost')} créditos la última llamada, "
                  f"{q.get('remaining')} restantes.")
        for miss in (live_meta or {}).get("no_event", []):
            print(f"      sin cuotas para {miss['game_id']}: {miss['reason']}")
        if live_meta and live_meta["unmatched_names"]:
            names = ", ".join(m["name"] for m in live_meta["unmatched_names"][:8])
            print(f"      {len(live_meta['unmatched_names'])} nombres sin casar con el roster: {names}")
    elif demo or (logs_path is None and odds_path is None):
        if resolved_slate is None:
            resolved_slate = slate_mod.get_slate(date, offline=offline)
        print(f"[1/4] Calendario NBA: {len(resolved_slate['games'])} partidos "
              f"({resolved_slate['date']}, fuente: {resolved_slate['source']}).")
        print("      Generating synthetic dataset over that slate "
              "(no API keys needed)...")
        maker = write_demo_dataset if write_data else generate_demo_dataset
        logs_df, odds_df = maker(slate=resolved_slate, seed=seed)
        # Uncomment to pull live alt lines instead (needs THE_ODDS_API_KEY):
        # odds_df = fetch_alt_lines_api("receiving_yards")
    else:
        print("[1/4] Loading data...")
        logs_df = load_game_logs(logs_path or cfg.GAME_LOGS_CSV)
        odds_df = load_odds(odds_path or cfg.ALT_LINES_CSV)
        if resolved_slate is None and game:
            resolved_slate = slate_mod.get_slate(offline=offline)

    if live and odds_df.empty:
        reasons = "; ".join(f"{m['game_id']}: {m['reason']}"
                            for m in (live_meta or {}).get("no_event", [])) or "sin props publicados"
        raise SystemExit(f"No hay Alt Lines reales para este partido ({reasons}).")
    if logs_df.empty:
        raise SystemExit("No game logs available. Pass --logs or run with --demo.")
    if odds_df.empty:
        raise SystemExit("No odds available (no key/CSV). Pass --odds or run with --demo.")

    # Optional: restrict to the books you can actually bet at.
    if books:
        allow = {b.strip().lower() for b in books}
        odds_df = odds_df[odds_df["book"].astype(str).str.lower().isin(allow)]
        if odds_df.empty:
            raise SystemExit(f"No odds left after --books filter {sorted(allow)}.")
        print(f"      books restricted to: {', '.join(sorted(allow))}")

    # ── 1b. Player → game mapping, and the single-game filter ─────────────────
    # Only needed when we are looking at a slate at all; a pure CSV run with no
    # game filter never pays for this.
    mapping = None
    if resolved_slate is not None:
        player_cols = [c for c in ("player_id", "team") if c in logs_df.columns]
        mapping = slate_mod.assign_players_to_games(
            resolved_slate["games"], logs_df[player_cols])
        matched = int(mapping["matched"].sum()) if not mapping.empty else 0
        print(f"      {matched}/{len(mapping)} jugadores casan por tricode real "
              f"(el resto se asigna al slate).")

    if game:
        if resolved_slate is None:
            raise SystemExit("--game needs a slate: run with --demo, or let the "
                             "API fetch today's calendar.")
        gid = str(game).strip()
        # A person types `--game BOS@DET`; the data carries `401909088`. Resolve
        # the handle against tonight's calendar first.
        hit = slate_mod.find_game(resolved_slate, gid)
        if hit is not None:
            gid = str(hit["game_id"])
        pids: set[str] = set()
        # Prefer the data's own game_id when the CSV carries it — no network and
        # it works even for a dataset built against a different slate.
        if "game_id" in logs_df.columns:
            hits = logs_df.loc[logs_df["game_id"].astype(str) == gid, "player_id"]
            pids |= {str(p) for p in hits}
        if not pids and mapping is not None and not mapping.empty:
            hits = mapping.loc[mapping["game_id"].astype(str) == gid, "player_id"]
            pids |= {str(p) for p in hits}
        if not pids:
            raise SystemExit(f"Game {gid} has no props in the loaded data "
                             f"(use --list-games to see tonight's ids).")
        logs_df = logs_df[logs_df["player_id"].astype(str).isin(pids)].reset_index(drop=True)
        odds_df = odds_df[odds_df["player_id"].astype(str).isin(pids)].reset_index(drop=True)
        if logs_df.empty or odds_df.empty:
            raise SystemExit(f"Game {gid} has no props in the loaded data.")
        print(f"      filtro por partido -> {gid}: "
              f"{logs_df['player_id'].nunique()} jugadores, "
              f"{len(odds_df)} líneas cotizadas.")

    # ── 1c. Stale history (offseason) ─────────────────────────────────────────
    # If the newest game is weeks old, "recent form" is last spring's minutes
    # restrictions and load management, not the player we will see tonight.
    # Lean on the whole season for the level until new games arrive.
    if live and "date" in logs_df.columns and resolved_slate is not None:
        last = pd.to_datetime(logs_df["date"]).max()
        ref = pd.to_datetime(resolved_slate.get("date"))
        if pd.notna(last) and pd.notna(ref) and (ref - last).days > cfg.LIVE_STALE_DAYS:
            halflife = max(halflife, cfg.LIVE_STALE_HALFLIFE)
            print(f"      historial de hace {(ref - last).days} días: nivel con "
                  f"semivida larga ({halflife:g} partidos).")

    # ── 2. Simulate ───────────────────────────────────────────────────────────
    print(f"[2/4] Simulating {n_iter:,} iterations per player-market...")
    summary_df, sims_store = simulate_universe(
        logs_df, n_iter=n_iter, seed=seed, halflife=halflife,
        halflife_var=halflife_var)
    print(f"      {len(summary_df)} player-markets, "
          f"{int(summary_df['n_games'].sum())} games of history.")

    # ── 3. Price & EV ─────────────────────────────────────────────────────────
    print(f"[3/4] Pricing alt lines against de-vigged market ({devig_method})...")
    table = build_value_table(summary_df, odds_df, sims_store,
                              devig_method=devig_method, min_prob=min_prob,
                              max_odds=max_odds,
                              anchor=live if anchor is None else anchor,
                              seed=seed, calibrate=calibrate)
    if table.empty:
        raise SystemExit("No lines could be priced (player ids must match between logs and odds).")
    # Pickstachio Score y categoría (Dorado / Abierto / Cáscara): se calculan
    # acá una sola vez y viajan al tablero, al registro de picks y a la web.
    table = add_tiers(table, anchored=live if anchor is None else bool(anchor))

    table = table[table["ev_pct"] >= min_ev].reset_index(drop=True)
    if "anchor_scale" in table.columns:
        drift = cfg.LIVE_MAX_ANCHOR_DRIFT
        table["level_ok"] = table["anchor_scale"].between(1.0 / (1.0 + drift), 1.0 + drift)
        n_bad = int((~table["level_ok"]).sum())
        if n_bad:
            print(f"      {n_bad} líneas fuera del tablero: el modelo y el mercado "
                  f"discrepan > ±{drift:.0%} en el nivel del jugador.")
    else:
        table["level_ok"] = True
    # Tablero recomendado: filtros de probabilidad y cuota, y sin correlación
    # (una línea por jugador-mercado, máx. 2 por jugador). Ver ev.select_board.
    shortlist = select_board(table, pct=top_pct, metric=metric)

    # ── 3b. Pick log (real mode only) ────────────────────────────────────────
    # Every real run is kept, whole board, so it can be settled later
    # (altlines.tools.settle) and measured (altlines.tools.report_live).
    run_id = None
    if live and log_picks:
        from altlines import picklog
        run_id = picklog.log_run(
            {"value_table": table, "odds": odds_df, "shortlist": shortlist,
             "slate": resolved_slate, "game": game},
            {"n_iter": n_iter, "seed": seed, "devig": devig_method,
             "top_pct": top_pct, "metric": metric},
            origin=log_origin)
        if run_id is not None:
            print(f"      pick log -> run {run_id} en {picklog.PICKS_DB}")

    # ── 4. Output ─────────────────────────────────────────────────────────────
    print(f"[4/4] Reporting...  {len(table)} priced lines -> "
          f"top {top_pct:g}% by {metric} = {len(shortlist)} picks.")

    if not quiet:
        _print_distribution_report(summary_df)
        render_console(shortlist, title=f"ALTLINES — TOP {top_pct:g}% BY {metric.upper()}")

    html_path = None
    if write_html:
        html_path = write_html_report(shortlist, out_html)
        print(f"      HTML panel -> {html_path}")

    return {
        "logs": logs_df,
        "odds": odds_df,
        "summary": summary_df,
        "sims_store": sims_store,
        "value_table": table,
        "shortlist": shortlist,
        "html_path": html_path,
        "slate": resolved_slate,
        "game": game,
        "mapping": mapping,
        "live_meta": live_meta,
        "pick_run_id": run_id,
    }


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AltLines — long-tail player-prop edge finder")
    p.add_argument("--demo", action="store_true",
                   help="use the synthetic dataset (default when no paths given)")
    p.add_argument("--live", action="store_true",
                   help="REAL data: ESPN game logs + The Odds API alt trays "
                        "(costs ~5 credits per game; use with --game)")
    p.add_argument("--refresh-odds", dest="refresh_odds", action="store_true",
                   help="with --live, ignore the 20-min odds cache (spends credits)")
    p.add_argument("--odds-cache-only", dest="odds_cache_only", action="store_true",
                   help="with --live, use ONLY the odds already on disk (any age); "
                        "never spends credits, fails if the game is not cached")
    p.add_argument("--no-log", dest="log_picks", action="store_false",
                   help="with --live, do not write the run to data/altlines/picks.db")
    p.add_argument("--no-anchor", dest="anchor", action="store_false", default=None,
                   help="with --live, do NOT put the model median on the market's "
                        "main line (edges then mix level and shape)")
    p.add_argument("--logs", default=None, help="path to player game logs CSV")
    p.add_argument("--odds", default=None, help="path to alt-line odds CSV")
    p.add_argument("--n-iter", type=int, default=cfg.N_SIM_DEFAULT,
                   help=f"Monte Carlo iterations per player (default {cfg.N_SIM_DEFAULT:,})")
    p.add_argument("--top-pct", type=float, default=5.0,
                   help="report only the top X%% of opportunities (default 5)")
    p.add_argument("--top-n", type=int, default=None,
                   help="alternative to --top-pct: report a fixed number of picks")
    p.add_argument("--metric", default="ev", choices=["ev", "discrepancy", "ev_pct"],
                   help="ranking metric for the shortlist (default ev)")
    p.add_argument("--min-ev", type=float, default=0.0,
                   help="drop lines below this EV %% before ranking")
    p.add_argument("--min-prob", type=float, default=0.0,
                   help="drop lines our model gives less than this win prob (e.g. 0.05)")
    p.add_argument("--max-odds", dest="max_odds", type=float,
                   default=ev_mod.MAX_DECIMAL_ODDS,
                   help="drop un-bettable longshots above this decimal price "
                        f"(default {ev_mod.MAX_DECIMAL_ODDS:g}; 0 disables)")
    p.add_argument("--devig", default="multiplicative",
                   choices=["multiplicative", "power", "shin"],
                   help="vig-removal method")
    p.add_argument("--books", default=None,
                   help="comma-separated books to keep, e.g. betano,pinnacle")
    p.add_argument("--halflife", type=float, default=6.0,
                   help="recency half-life in games for the MEAN (default 6)")
    p.add_argument("--halflife-var", dest="halflife_var", type=float, default=None,
                   help="recency half-life for the VARIANCE (default ~3x --halflife); "
                        "keep it long so volatility is not read off a hot streak")
    p.add_argument("--seed", type=int, default=cfg.RANDOM_SEED)
    p.add_argument("--date", default=None,
                   help="slate date (YYYY-MM-DD); default: tonight, else the "
                        "nearest night with games")
    p.add_argument("--game", default=None,
                   help="simulate ONLY this game_id (list them with --list-games)")
    p.add_argument("--list-games", action="store_true",
                   help="print the slate (matchups, props, lines) and exit")
    p.add_argument("--offline", action="store_true",
                   help="never touch the network: use the deterministic local slate")
    p.add_argument("--no-write-data", dest="write_data", action="store_false",
                   default=True,
                   help="with --demo, do not persist the synthetic CSVs to "
                        "data/altlines/ (the web API never writes them)")
    p.add_argument("--out", default=str(cfg.REPORT_HTML), help="HTML report path")
    p.add_argument("--panel", default="console", choices=["console", "html", "streamlit"],
                   help="which panel to show (streamlit needs the extra dep)")
    return p.parse_args(argv)


def _print_slate(view: dict) -> None:
    """The `--list-games` board: what is on tonight and what we can price."""
    shift = view.get("search_offset_days") or 0
    when = view.get("date")
    if shift:
        when = f"{when}  (pediste {view.get('requested_date')}, "
        when += f"corrido {shift:+d} día(s) al más cercano con partidos)"
    print(f"\nSLATE {when}   fuente: {view.get('source')}")
    print("-" * 96)
    print(f"{'game_id':<24} {'Away':<6} {'Home':<6} {'jug':>4} {'pmkt':>5} "
          f"{'líneas':>7}  {'estado':<12} books")
    print("-" * 96)
    for g in view["games"]:
        pr = g["props"]
        print(f"{str(g['game_id'])[:24]:<24} {g['away']['tricode']:<6} "
              f"{g['home']['tricode']:<6} {pr['players']:>4} "
              f"{pr['player_markets']:>5} {pr['lines']:>7}  "
              f"{str(g['status'])[:12]:<12} {','.join(pr['books'])[:28]}")
    print("-" * 96)
    print(f"{view['n_games']} partidos · {view['games_with_props']} con props · "
          f"{view['matched_players']} jugadores casados por tricode · "
          f"{view['assigned_players']} asignados")
    print("Simulá uno solo con:  python -m altlines.run_pipeline --demo "
          f"--game {view['games'][0]['game_id'] if view['games'] else '<id>'}")


def main(argv=None) -> None:
    args = parse_args(argv)
    books = [b for b in (args.books.split(",") if args.books else []) if b.strip()]
    is_demo = not args.live and (args.demo or (args.logs is None and args.odds is None))

    if args.list_games:
        slate = slate_mod.get_slate(args.date, offline=args.offline)
        if is_demo:
            logs_df, odds_df = generate_demo_dataset(slate=slate, seed=args.seed)
        else:
            logs_df = load_game_logs(args.logs or cfg.GAME_LOGS_CSV)
            odds_df = load_odds(args.odds or cfg.ALT_LINES_CSV)
        cols = [c for c in ("player_id", "team") if c in logs_df.columns]
        mapping = slate_mod.assign_players_to_games(slate["games"], logs_df[cols])
        _print_slate(slate_mod.build_slate_view(slate, logs_df, odds_df, mapping))
        return

    if args.odds_cache_only:
        cfg.LIVE_ODDS_CACHE_ONLY = True
    artifacts = run(
        demo=is_demo,
        logs_path=args.logs, odds_path=args.odds,
        n_iter=args.n_iter, top_pct=args.top_pct, metric=args.metric,
        min_ev=args.min_ev, min_prob=args.min_prob, max_odds=args.max_odds,
        devig_method=args.devig,
        books=books or None, halflife=args.halflife,
        halflife_var=args.halflife_var, seed=args.seed,
        out_html=args.out, write_html=args.panel in ("html", "console", "streamlit"),
        game=args.game, offline=args.offline, write_data=args.write_data,
        live=args.live, date=args.date, refresh_odds=args.refresh_odds,
        anchor=args.anchor, log_picks=args.log_picks,
    )

    if args.top_n:
        top = top_n(artifacts["value_table"], n=args.top_n, metric=args.metric)
        render_console(top, title=f"ALTLINES — TOP {args.top_n} BY {args.metric.upper()}")

    if args.panel == "streamlit":
        from altlines.dashboard import streamlit_dashboard
        print("Launching Streamlit panel...")
        streamlit_dashboard(artifacts["shortlist"])


if __name__ == "__main__":
    main()
