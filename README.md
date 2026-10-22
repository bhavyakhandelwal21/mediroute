# MediFlow: Medicine Inventory Optimization System

Rule-based and statistical (no AI/ML). It forecasts demand, flags stockout and expiry risks, recommends transfers between
facilities and reorder quantities, and explains every recommendation.

**Stack:** Python, Pandas, NumPy, SQLite, FastAPI, React, Recharts (Vite).
**Data:** synthetic sample data is created on first run. Do not present its results as real-world proof.

## Features

- **PDF upload** (Add my data tab): reads tables from a text-based PDF, stores them in SQLite, then runs the normal analysis.
- **Map tab:** registered hospitals as coloured dots for a chosen medicine, a locality filter, and the shortest road route between two hospitals.
  Red = shortage, green = right amount, yellow = excess, grey = missing/too little data.
- Forecasts, stockout and expiry alerts, transfers, reorders, before/after replay, data checks, and a typed-entry form.

## Offline use on this computer

Double-click `Start-MediRoute-Offline.cmd` in the project folder. It opens http://127.0.0.1:8000 once the local server is ready. Keep the launcher window open while using the app; press Ctrl+C to stop it. If port 8000 is already in use, stop the existing backend first.

The installed Python environment and built interface are already present on this computer. No internet or Node.js server is needed to run the built prototype. Inventory edits, PDF import, forecasts, transfers, drug matching, outbreak parsing and local transfer explanations work offline. Data persists in `backend/mediflow.db`.

Offline mode disables external AI calls and map tile/road-routing requests. Hospital markers still work on a plain map background; distances are explicitly straight-line estimates, with no road travel time. Street maps and road directions are not bundled.

For a fresh computer, install the dependencies using the setup steps below while online, then run `npm run build` in `frontend`. Python virtual environments are machine-specific; copying `.venv` is not a portable installation. Rebuild after frontend changes. The normal two-terminal development setup remains available.

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

## Blood bank (offline)

Open **Blood bank** to record hospital targets and blood batches independently of medicine inventory. Supported groups: A+, A-, B+, B-, AB+, AB-, O+, O-. Products: red cells, whole blood, platelets and frozen plasma. Targets are staff-entered units, not the medicine forecast or its 21-day reserve.

Try the complete synthetic upload file: [MediRoute Blood Bank Test Data](output/pdf/MediRoute_Blood_Bank_Test_Data.pdf). It includes transfer-eligible stock, exact shortages/surpluses, expiry and release exclusions, handling checks, a stock-only row, unknown stock, and map locations (including one missing route location).

1. Save each hospital/product's minimum stock target and component-specific storage/transport readiness. If no stock count or batches are entered, stock is shown as unknown rather than assumed to be zero.
2. Add unreserved batch units, batch reference, labelled expiry date/time and verified release status. Reusing the same reference at the same hospital/product updates its count rather than adding it again.
3. Set an assumed transit time in hours and refresh. The planner matches exact group and component, uses earlier-expiring eligible donor batches first, and protects each donor's target using stock still valid at arrival. Expired, unreleased and unverified-storage stock is excluded. Recipient storage and donor transport must be verified.
4. Review proposed transfers and any remaining shortage. Proposals do not execute shipments or deduct saved stock. After an actual movement, update the donor and receiver batch counts. Refresh before acting; this is a snapshot, not a reservation system.

Example with synthetic data: Hospital North has an A+ red-cell target of 10 and a released 25-unit batch with future expiry; Hospital South has the same target and no stock. With both storage checks and North's transport verified, the planner proposes 10 units North to South, leaving North with 15. An O+ or A- batch is not substituted.

Blood PDF import accepts text-based inventory tables with Facility, Blood group, Component and Target units; Available units can be entered without batch detail for review, but those counts are not transfer-eligible until current batch, expiry, release and handling information is verified. Locality and coordinates allow the blood map to show hospitals and routes. Stock alerts compare each hospital's target with its verified usable stock or reported count and show exact shortages, eligible surplus, proposed quantities and remaining gaps.

Handling checks follow the blood bank's component-specific procedures, not the medicine cold-chain checkbox. This is inventory redistribution only: blood-bank staff must verify product suitability, patient compatibility/crossmatching where applicable, labelled expiry and shipment readiness. No automatic compatibility substitutions or clinical approvals are provided.

Blood tables share the local SQLite file but are separate from medicine tables. Medicine sample/reset/PDF replacement actions preserve blood inventory. API: GET `/api/blood/catalog`, GET `/api/blood/analysis?transit_hours=2`, POST `/api/blood/upload-pdf`, POST `/api/blood/positions`, POST `/api/blood/batches`, DELETE `/api/blood/positions/{id}`, and DELETE `/api/blood/batches/{id}`.

Validation: from `backend`, run `.venv/Scripts/python.exe -m unittest test_blood -v`. Set `MEDIROUTE_HTTP_TESTS=1` to also run the HTTP integration test on a host with working loopback sockets. Rebuild the interface with `npm run build` in `frontend` before restarting the offline launcher.

Reference for component-specific transport procedures: [JPAC transport guidance](https://www.jpac.org.uk/guidelines/red-book/chapters/6/6-11/).
