"""
AltLines — Module 4: Output / dashboard.

Two outputs, both fed the same `value table` from ev.py:

  • render_console()  — a colour-coded table for the terminal (top slice).
  • write_html_report() — a self-contained, interactive HTML panel with live
                          search + filters (EV, market, profile, side, line).
  • streamlit_dashboard() — optional, only used if `streamlit` is installed.

The whole point of this module is NOISE REDUCTION: the pipeline can price
thousands of lines; you want to look at the handful where our simulated
probability and the book's price disagree the most.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

# ANSI helpers (no external dependency). Guarded for non-TTY output.
_USE_COLOR = True


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def _fmt(x, width: int, dec: int = 2) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return " " * (width - 2) + "--"
    if isinstance(x, (int, np.integer)):
        return f"{x:>{width}}"
    if isinstance(x, float):
        return f"{x:>{width}.{dec}f}"
    return f"{str(x):>{width}}"


# ══════════════════════════════════════════════════════════════════════════════
# 4a. Console table
# ══════════════════════════════════════════════════════════════════════════════

def render_console(df: pd.DataFrame, title: str = "ALTLINES — TOP EDGES") -> None:
    """Print the shortlist as a fixed-width, colour-coded table."""
    if df.empty:
        print(f"\n{title}\n  (nothing to show — no +EV lines in the slice)\n")
        return

    cols = [
        ("player",       16, "s"),
        ("market_label", 20, "s"),
        ("side",          5, "s"),
        ("point",         7, "f"),
        ("book",         11, "s"),
        ("decimal_odds",  7, "f"),
        ("p_model",       7, "f"),
        ("p_fair",        7, "f"),
        ("discrepancy",   8, "f"),
        ("ev_pct",        7, "f"),
        ("curve_ratio",   8, "f"),
        ("stake_units",   6, "f"),
        ("profile",      10, "s"),
    ]
    header = "  ".join(f"{name[:w]:>{w}}" for name, w, _ in cols)
    print(f"\n{title}")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    for _, r in df.iterrows():
        cells = []
        for name, w, kind in cols:
            val = r.get(name)
            if kind == "s":
                cells.append(f"{str(val)[:w]:>{w}}")
            else:
                cells.append(_fmt(val, w, 2))
        line = "  ".join(cells)
        # Colour the EV cell by strength (green = strong, grey = marginal).
        ev = r.get("ev_pct", 0)
        if isinstance(ev, (int, float)) and np.isfinite(ev) and ev > 0:
            line = _c(line, "92") if ev >= 15 else _c(line, "32")
        print(line)

    print("-" * len(header))
    print(f"  {len(df)} lines  |  EV is per 1 unit staked  |  "
          f"discrepancy = P_model − P_fair  |  curve_ratio = P_model / book's Normal curve")


# ══════════════════════════════════════════════════════════════════════════════
# 4b. Interactive HTML report
# ══════════════════════════════════════════════════════════════════════════════

def _records(df: pd.DataFrame) -> list[dict]:
    """NaN/numpy-safe records for JSON embedding."""
    safe = df.replace({np.nan: None})
    out = []
    for rec in safe.to_dict(orient="records"):
        clean = {}
        for k, v in rec.items():
            if isinstance(v, (np.integer,)):
                v = int(v)
            elif isinstance(v, (np.floating,)):
                v = float(v)
            elif isinstance(v, (np.bool_,)):
                v = bool(v)
            clean[k] = v
        out.append(clean)
    return out


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --bg:#0d1117; --panel:#161b22; --line:#30363d; --text:#e6edf3;
    --muted:#8b949e; --green:#3fb950; --amber:#d29922; --red:#f85149;
    --accent:#58a6ff;
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
  header { padding:20px 24px 8px; }
  h1 { margin:0 0 4px; font-size:20px; letter-spacing:.5px; }
  .sub { color:var(--muted); font-size:12px; }
  .controls { display:flex; flex-wrap:wrap; gap:10px; padding:14px 24px;
              background:var(--panel); border-bottom:1px solid var(--line);
              position:sticky; top:0; z-index:5; }
  .controls label { color:var(--muted); font-size:11px; text-transform:uppercase;
                    letter-spacing:.5px; display:flex; flex-direction:column; gap:3px; }
  input, select { background:#0d1117; color:var(--text); border:1px solid var(--line);
                  border-radius:6px; padding:5px 8px; font-size:13px; min-width:90px; }
  input:focus, select:focus { outline:1px solid var(--accent); }
  .stats { display:flex; gap:24px; padding:12px 24px; color:var(--muted); font-size:12px; }
  .stats b { color:var(--text); }
  table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
  th, td { padding:8px 12px; text-align:right; white-space:nowrap;
           border-bottom:1px solid var(--line); }
  th:first-child, td:first-child, th:nth-child(2), td:nth-child(2),
  th:last-child, td:last-child { text-align:left; }
  thead th { position:sticky; top:0; background:#11161d; cursor:pointer;
             color:var(--muted); font-size:11px; text-transform:uppercase;
             letter-spacing:.5px; user-select:none; }
  thead th:hover { color:var(--text); }
  tbody tr:hover { background:#1b2230; }
  td.ev-strong { color:var(--green); font-weight:600; }
  td.ev-ok { color:var(--amber); }
  .pill { font-size:10px; padding:2px 7px; border-radius:10px; border:1px solid var(--line); }
  .p-BOOM-BUST { color:var(--red); border-color:#5a2222; }
  .p-VOLATILE { color:var(--amber); border-color:#4a3a12; }
  .p-STEADY { color:var(--muted); }
  .side-OVER { color:var(--green); } .side-UNDER { color:var(--accent); }
  .wrap { overflow-x:auto; }
  footer { padding:16px 24px; color:var(--muted); font-size:11px; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="sub">__SUBTITLE__</div>
</header>
<div class="controls">
  <label>Search<input id="q" placeholder="player / team"></label>
  <label>Market<select id="market"><option value="">all</option></select></label>
  <label>Profile<select id="profile"><option value="">all</option></select></label>
  <label>Side<select id="side"><option value="">all</option>
    <option value="OVER">OVER</option><option value="UNDER">UNDER</option></select></label>
  <label>Min EV %<input id="minev" type="number" value="0" step="0.5" style="width:80px"></label>
  <label>Min P_model<select id="minp">
    <option value="0">0%</option><option value="0.05">5%</option>
    <option value="0.10">10%</option><option value="0.20">20%</option>
  </select></label>
</div>
<div class="stats" id="stats"></div>
<div class="wrap">
<table>
  <thead><tr id="head"></tr></thead>
  <tbody id="body"></tbody>
</table>
</div>
<footer>AltLines · simulated P vs de-vigged market P · EV per 1 unit staked ·
click headers to sort. Educational analytics, not betting advice.</footer>

<script>
const DATA = __DATA__;
const COLS = [
  ["player","Player","s"], ["team","Team","s"], ["market_label","Market","s"],
  ["side","Side","s"], ["point","Line","f"], ["book","Book","s"],
  ["decimal_odds","Odds","f"], ["p_model","P_model","p"],
  ["p_fair","P_fair","p"], ["discrepancy","Edge","p"], ["ev_pct","EV%","f"],
  ["curve_ratio","Curve×","f"], ["stake_units","Stake","f"],
  ["profile","Profile","pill"]
];
let sortCol = "ev_pct", sortDir = -1;

function fillSelect(id, key) {
  const vals = [...new Set(DATA.map(r => r[key]).filter(v => v !== null && v !== ""))].sort();
  const el = document.getElementById(id);
  vals.forEach(v => { const o = document.createElement("option"); o.value = v; o.textContent = v; el.appendChild(o); });
}

function fmt(v, kind) {
  if (v === null || v === undefined) return "–";
  if (kind === "p") return (v * 100).toFixed(1) + "%";
  if (kind === "f") return (typeof v === "number") ? v.toFixed(2) : v;
  return v;
}

function render() {
  const q = document.getElementById("q").value.toLowerCase();
  const mk = document.getElementById("market").value;
  const pf = document.getElementById("profile").value;
  const sd = document.getElementById("side").value;
  const minev = parseFloat(document.getElementById("minev").value || "0");
  const minp = parseFloat(document.getElementById("minp").value || "0");

  let rows = DATA.filter(r =>
    (!q || (r.player + " " + (r.team||"")).toLowerCase().includes(q)) &&
    (!mk || r.market === mk) && (!pf || r.profile === pf) && (!sd || r.side === sd) &&
    (r.ev_pct ?? -999) >= minev && (r.p_model ?? 0) >= minp
  );
  rows.sort((a, b) => {
    const x = a[sortCol], y = b[sortCol];
    if (x === null) return 1; if (y === null) return -1;
    return (x > y ? 1 : x < y ? -1 : 0) * sortDir;
  });

  document.getElementById("head").innerHTML = COLS
    .map(([k, label]) => `<th data-k="${k}">${label}${sortCol===k ? (sortDir<0?" ▾":" ▴"):""}</th>`).join("");
  document.querySelectorAll("#head th").forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    if (sortCol === k) sortDir *= -1; else { sortCol = k; sortDir = (th.dataset.k === "ev_pct" ? -1 : 1); }
    render();
  });

  document.getElementById("body").innerHTML = rows.map(r => {
    const cls = (r.ev_pct >= 15) ? "ev-strong" : (r.ev_pct > 0 ? "ev-ok" : "");
    return "<tr>" + COLS.map(([k, , kind]) => {
      if (kind === "pill") return `<td><span class="pill p-${r[k]}">${r[k]}</span></td>`;
      if (k === "side") return `<td class="side-${r[k]}">${r[k]}</td>`;
      if (k === "ev_pct") return `<td class="${cls}">${fmt(r[k], kind)}</td>`;
      return `<td>${fmt(r[k], kind)}</td>`;
    }).join("") + "</tr>";
  }).join("");

  const totalEv = rows.reduce((s, r) => s + (r.ev_pct || 0), 0);
  document.getElementById("stats").innerHTML =
    `Showing <b>${rows.length}</b> of ${DATA.length} priced lines · ` +
    `mean EV <b>${rows.length ? (totalEv/rows.length).toFixed(1) : "0.0"}%</b> · ` +
    `cut: min EV ${minev}% · min P_model ${(minp*100).toFixed(0)}%`;
}

["q","market","profile","side","minev","minp"].forEach(id =>
  document.getElementById(id).addEventListener("input", render));
["market","profile"].forEach(id => fillSelect(id, id === "market" ? "market" : "profile"));
render();
</script>
</body>
</html>
"""


def write_html_report(df: pd.DataFrame, path: str | Path,
                      title: str = "AltLines — Top Edge Finder",
                      subtitle: str | None = None) -> Path:
    """Write a self-contained interactive HTML panel (no server, no deps)."""
    path = Path(path)
    if df.empty:
        subtitle = subtitle or "No +EV lines found in this slice."
    else:
        n = len(df)
        best = df["ev_pct"].max()
        subtitle = subtitle or (
            f"{n} priced opportunities · best EV {best:.1f}% · "
            f"simulated P vs de-vigged market P"
        )
    html = (_HTML_TEMPLATE
            .replace("__TITLE__", title)
            .replace("__SUBTITLE__", subtitle)
            .replace("__DATA__", json.dumps(_records(df))))
    path.write_text(html, encoding="utf-8")
    return path


# ══════════════════════════════════════════════════════════════════════════════
# 4c. Optional Streamlit panel
# ══════════════════════════════════════════════════════════════════════════════

def streamlit_dashboard(df: pd.DataFrame) -> None:
    """Lightweight interactive panel. Requires `pip install streamlit`.

    Run with:  streamlit run altlines/dashboard.py
    (the __main__ guard below calls this with a demo table if run directly)
    """
    import streamlit as st  # imported lazily so the package doesn't require it

    st.set_page_config(page_title="AltLines", layout="wide")
    st.title("AltLines — Top Edge Finder")
    if df.empty:
        st.warning("No priced lines in this slice.")
        return

    c1, c2, c3, c4 = st.columns(4)
    min_ev = c1.slider("Min EV %", -10.0, 50.0, 0.0, 0.5)
    markets = ["(all)"] + sorted(df["market"].unique())
    market = c2.selectbox("Market", markets)
    profiles = ["(all)"] + sorted(df["profile"].dropna().unique())
    profile = c3.selectbox("Profile", profiles)
    sides = ["(all)", "OVER", "UNDER"]
    side = c4.selectbox("Side", sides)

    view = df[df["ev_pct"] >= min_ev]
    if market != "(all)":
        view = view[view["market"] == market]
    if profile != "(all)":
        view = view[view["profile"] == profile]
    if side != "(all)":
        view = view[view["side"] == side]

    st.caption(f"{len(view)} lines shown")
    st.dataframe(
        view[["player", "market_label", "side", "point", "book", "decimal_odds",
              "p_model", "p_fair", "discrepancy", "ev_pct", "curve_ratio",
              "stake_units", "profile"]],
        use_container_width=True, hide_index=True,
    )


if __name__ == "__main__":
    # Best-effort direct run: load a cached/demo table and launch Streamlit.
    import sys
    if Path(sys.argv[0]).name == "dashboard.py":
        try:
            from altlines.ingest import load_odds
            from altlines.run_pipeline import run  # noqa: F401
            print("Launch via:  python -m altlines.run_pipeline --demo --panel streamlit")
        except Exception:
            print("Import the module and call streamlit_dashboard(df) instead.")
