"""
AltLines — a long-tail player-prop edge finder for CherryPick.

Public surface (see the individual modules):

    ingest      → load/clean game logs + odds, convert to fair (vig-free) prob
    simulation  → Monte Carlo distribution engine (the edge)
    ev          → expected value / edge / Kelly, and the value table
    dashboard   → top-5% console table + lightweight interactive HTML panel

Quick start (no API keys required — uses synthetic demo data):

    python -m altlines.run_pipeline --demo
"""

from __future__ import annotations

__all__ = ["config", "ingest", "simulation", "ev", "dashboard", "run_pipeline"]
__version__ = "0.1.0"
