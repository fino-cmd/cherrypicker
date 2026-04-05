# CherryPick — MLB Analytics Backend

FastAPI backend powering the CherryPick MLB betting analytics platform.

## Setup

```bash
cd backend

# 1. Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and add your ODDS_API_KEY if you have one

# 4. Start the server
uvicorn main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/games/today` | Today's MLB schedule with probable pitchers |
| GET | `/api/simulate/{game_id}` | Monte Carlo win probability for a game |
| GET | `/api/pitchers/today` | Pitcher props analysis (K projections, CSW%, etc.) |
| GET | `/api/tracker` | All tracked bets + ROI summary |
| POST | `/api/tracker` | Record a new bet |

### POST /api/tracker — request body

```json
{
  "date": "2024-04-01",
  "game": "NYY vs BOS",
  "market": "K Over",
  "odds_decimal": 1.91,
  "result": "W",
  "clv": 0.03,
  "units": 1.0,
  "notes": "Cole vs Sale, strong CSW edge"
}
```

`market` options: `"K Over"`, `"K Under"`, `"Moneyline"`, `"Total Over"`, `"Total Under"`
`result` options: `"W"` (win), `"L"` (loss), `"P"` (push), `null` (pending)

---

## Data Sources

- **MLB Stats API** — schedule, probable pitchers, pitcher game logs
- **pybaseball** — FanGraphs pitching/batting stats (xFIP, SIERA, CSW%, xwOBA, K%)
- **The Odds API** *(optional)* — live K-prop lines

## Caching

pybaseball responses are cached in memory for `CACHE_TTL_HOURS` (default 6h) to avoid repeated network calls on every request.

## Notes

- If a pitcher is not found in pybaseball (new call-up, short season), advanced stats return `null` and `data_incomplete: true` is set.
- The odds API is entirely optional — all endpoints return valid JSON without it; `k_line` and `edge_pct` will be `null`.
- All probability and monetary values are rounded to 2 decimal places.
