# MediRoute: Medicine Inventory Optimization System

Rule-based and statistical (no AI/ML). It forecasts demand, flags stockout and expiry risks, recommends transfers between
facilities and reorder quantities, and explains every recommendation.

**Stack:** Python, Pandas, NumPy, SQLite, FastAPI, React, Recharts (Vite).
**Data:** synthetic sample data is created on first run. Do not present its results as real-world proof.

## Features

- **PDF upload** (Add my data tab): reads tables from a text-based PDF, stores them in SQLite, then runs the normal analysis.
- **Map tab:** registered hospitals as coloured dots for a chosen medicine, a locality filter, and the shortest road route between two hospitals.
  Red = shortage, green = right amount, yellow = excess, grey = missing/too little data.
- Forecasts, stockout and expiry alerts, transfers, reorders, before/after replay, data checks, and a typed-entry form.

## Run it (two terminals)

You need Python 3.10+ and Node.js 18+.

**Terminal 1: backend**
```
cd backend
python -m venv .venv
# Windows:  .venv\Scripts\activate        Mac/Linux:  source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
Check http://127.0.0.1:8000/docs (interactive API page). The first start creates `backend/mediflow.db` with sample data.

**Terminal 2: frontend**
```
cd frontend
npm install
npm run dev
```
Open http://localhost:5173.

## Folder map (read in this order)

| File | Job |
|---|---|
| `backend/app/pdf_import.py` | Reads tables from a PDF and loads them into the database |
| `backend/app/db.py` | SQLite tables, and loading data into Pandas |
| `backend/app/seed.py` | Makes the synthetic sample data |
| `backend/app/forecast.py` | Recent average x weekday pattern, spike check, likely range |
| `backend/app/risk.py` | Missing-data flag, stockout rule, expiry rule (first-expiry-first-out), spare stock |
| `backend/app/transfer.py` | Greedy transfers, then reorder quantities |
| `backend/app/evaluate.py` | 30-day before/after replay and forecast error |
| `backend/app/main.py` | FastAPI endpoints that tie it together |
| `frontend/src/App.jsx` | Page shell, sliders, tabs, summary cards |
| `frontend/src/pages.jsx` | Alerts, Transfers, Reorders, Data checks, How it works |
| `frontend/src/MapPage.jsx` | Leaflet map, colours, locality filter, routes |
| `frontend/src/Forecast.jsx`, `Evaluation.jsx`, `DataEntry.jsx` | Chart, comparison tables, PDF upload and entry form |

## API

`GET /api/analysis` (all results), `GET /api/forecast?facility=&medicine=`, `GET /api/evaluation`,
`GET /api/facilities`, `POST /api/upload-pdf`, `GET /api/sample-pdf`, `GET/POST /api/records`, `DELETE /api/records/{id}`, `POST /api/sample`, `POST /api/clear`.
Settings (spike, safety, buffer, season, min_qty, transit) are query parameters on the first three.

## PDF format

Export your sheet to PDF from Excel or Word (text-based, not a scan or photo). Include one or two tables with a header row:

- **Stock table:** `Facility, Medicine, Stock` (optional: `Expiry` as days or a date, `Lead days`, `Incoming`, `Locality`, `Latitude`, `Longitude`, `Usage`)
- **Usage table:** `Facility, Medicine, Date, Used` (one row per day; the latest 60 days are used)

Column names are matched loosely (Hospital = Facility, Qty = Stock). Tables that continue over several pages work. Dates like 31/03/2026 are read day first.
Try `backend/samples/sample_hospital_report.pdf` first (made-up data; regenerate with `make_sample_pdf.py`).
Missing stock, expiry or usage gives an "Uncertain" record, never a guess. Scanned PDFs need OCR, which is not included.

## Map notes

- Hospitals need latitude and longitude (type them in the form or put them in the PDF). The sample positions are illustrative.
- Colours: red when stock + incoming is below forecast x (delivery time + buffer + safety days); yellow when above "Excess above" times that level
  (and at least the minimum transfer size above it) or when units may expire; green otherwise.
- Map tiles and road routes need internet. Routes use the free OSRM demo server (fine for a prototype, not for heavy use); it is asked for alternatives
  and the shortest by distance is drawn. Without internet a straight line is shown.

## Decision logic (in `risk.py` and `transfer.py`)

1. Stock or expiry missing, or under 7 days of usage: flag "Uncertain", no action.
2. Spike: 7-day average > 1.5 x 30-day average: use the 7-day level.
3. Stockout: days of cover = (stock + incoming) / forecast; flag if below delivery time + buffer.
4. Expiry: units of a batch that expected use cannot cover before expiry (earliest batch first).
5. Transfer: receiver short, donor keeps safety stock, transit shorter than receiver's cover and the batch's shelf life.
6. Reorder = forecast x (lead + buffer) + safety stock - stock - incoming - transfers received.

## What is and is not done

- Backend logic, PDF upload (on a generated sample PDF) and the dev-server proxy were run and gave sensible results. The frontend compiles, but I did not view it in a browser, so check the layout and the map.
- PDF reading was tested on one made-up PDF only. Real-world PDFs vary, so expect to adjust column names in `ALIASES` in `pdf_import.py`.
- Not included: Statsmodels, OR-Tools, any pretrained model or Claude API. Transfers use a greedy matcher.
  Seasonality is a weekday pattern plus a manual factor (yearly seasonality needs a year of history).
- Only the last 60 days of usage are used. Facility-to-facility travel days are random in the sample data; for typed data a default (slider) is used.

## Ideas to extend

- Replace the greedy matcher with Google OR-Tools (add it in `transfer.py`).
- Add Statsmodels Holt-Winters as a second forecast and compare MAE in `evaluate.py`.
- Optional AI: a pretrained forecaster (Chronos-Bolt) in `forecast.py`, and the Claude API to reword explanations only.
