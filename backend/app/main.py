"""FastAPI server. Run:  uvicorn app.main:app --reload"""
from typing import Optional
from datetime import date
import pathlib
import os
from fastapi.staticfiles import StaticFiles
import pandas as pd
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from . import db
from .seed import seed
from .forecast import level, curve, fill
from .risk import analyse_pair
from .transfer import plan
from .evaluate import evaluate
from .pdf_import import parse_pdf, ingest
from .ai_engine import (
    extract_outbreak_multipliers,
    generate_clinical_justification,
    impute_missing_consumption,
    load_nlem_catalog,
    normalize_drug_name,
)
from .temperature import router as temperature_router

SAMPLE_PDF = pathlib.Path(__file__).resolve().parent.parent / "samples" / "sample_hospital_report.pdf"

app = FastAPI(title="MediRoute API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def start():
    c = db.conn()
    if db.is_empty(c): seed(c)          # first run: fill with synthetic sample data
    c.close()

def params(spike: float = 1.5, safety: int = 3, buffer: int = 2, season: float = 1.0, min_qty: int = 10,
           transit: int = 1, excess_x: float = 1.5, outbreak_multiplier: float = 1.0,
           outbreak_category: str = "", outbreak_region: str = ""):
    return dict(spike=spike, safety=safety, buffer=buffer, season=season, min_qty=min_qty, transit=transit,
                excess_x=excess_x, outbreak_multiplier=outbreak_multiplier,
                outbreak_category=outbreak_category, outbreak_region=outbreak_region)

def compute(p):
    c = db.conn(); pairs, transit = db.load_pairs(c); c.close()
    for pair in pairs:
        raw_usage = list(pair["usage"])
        observed = sum(value is not None for value in raw_usage)
        pair["observed_usage"] = raw_usage
        pair["observed_usage_days"] = observed
        pair["imputed_usage_days"] = 0
        if observed >= 7:
            anchor = pair.get("usage_anchor") or date.today()
            if isinstance(anchor, str):
                try: anchor = date.fromisoformat(anchor)
                except ValueError: anchor = date.today()
            logs = pd.DataFrame({
                "facility_id": [pair["facility"]] * len(raw_usage),
                "item_id": [pair["medicine"]] * len(raw_usage),
                "date": pd.date_range(end=anchor, periods=len(raw_usage), freq="D"),
                "units_dispensed": raw_usage,
            })
            imputed = impute_missing_consumption(logs)
            pair["usage"] = imputed["units_dispensed"].tolist()
            pair["imputed_usage_days"] = int(imputed["was_imputed"].sum())
    rows = [analyse_pair(pr, p) for pr in pairs]
    transfers, saved = plan(rows, transit, p)
    return pairs, rows, transfers, saved

@app.get("/api/analysis")
def analysis(p: dict = Depends(params)):
    pairs, rows, transfers, saved = compute(p)
    sev = ("Critical", "High")
    return dict(
        summary=dict(high_risk=sum(r["status"] in sev for r in rows), expiry_units=sum(r["excess"] for r in rows),
                     transfers=len(transfers), transfer_units=sum(t["qty"] for t in transfers),
                     reorder_units=sum(r["order"] for r in rows), to_verify=sum(r["uncertain"] for r in rows), pairs=len(rows)),
        rows=rows, transfers=transfers, reorders=[r for r in rows if r["order"] > 0])

@app.get("/api/forecast")
def forecast(facility: str, medicine: str, p: dict = Depends(params)):
    pairs, rows, _, _ = compute(p)
    for pr, r in zip(pairs, rows):
        if pr["facility"] == facility and pr["medicine"] == medicine:
            if r["uncertain"]: raise HTTPException(400, "Not enough reliable data for a forecast")
            return dict(history=[round(x, 1) for x in fill(pr["usage"])[-28:]], forecast=curve(pr["usage"], r["fc"]), status=r["status"])
    raise HTTPException(404, "Pair not found")

@app.get("/api/evaluation")
def evaluation(p: dict = Depends(params)):
    pairs, rows, transfers, saved = compute(p)
    return evaluate(pairs, rows, transfers, saved, p) or {}

class Record(BaseModel):
    facility: str
    medicine: str
    stock: Optional[int] = None
    expiry_days: Optional[int] = None
    lead_days: int = 5
    incoming_qty: int = 0
    locality: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    cold_chain_required: bool = False
    cold_chain_storage: bool = False
    cold_chain_transport: bool = False
    usage: list[Optional[float]] = []      # daily usage, oldest first (null = missing day); last 60 are kept

class ColdChainSettings(BaseModel):
    facility: str
    medicine: str
    cold_chain_required: bool = False
    cold_chain_storage: bool = False
    cold_chain_transport: bool = False

class DrugMatchRequest(BaseModel):
    raw_name: str

class OutbreakRequest(BaseModel):
    raw_text_report: str

class TransferJustificationRequest(BaseModel):
    source_name: str
    target_name: str
    item_name: str
    qty: int
    source_expiry_days: int
    target_stockout_days: float
    donor_reserve_days: float
    transit_distance_km: float
    is_cold_chain: bool = False

@app.post("/api/ai/normalize-drug")
def normalize_drug(request: DrugMatchRequest):
    """Suggest a canonical NLEM 2022 starter-catalog name for a raw label."""
    catalog = load_nlem_catalog()
    matched, confidence = normalize_drug_name(request.raw_name, catalog)
    return dict(raw_name=request.raw_name, matched_name=matched, confidence=confidence,
                accepted=confidence >= 0.78, catalog_version="NLEM 2022 starter catalog")

@app.post("/api/ai/outbreak")
def parse_outbreak(request: OutbreakRequest):
    """Parse a field bulletin into validated demand-surge fields."""
    try:
        return extract_outbreak_multipliers(request.raw_text_report)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

@app.post("/api/ai/transfer-justification")
def transfer_justification(request: TransferJustificationRequest):
    """Create a two-sentence, auditable transfer rationale."""
    try:
        return {"justification": generate_clinical_justification(request.model_dump() if hasattr(request, "model_dump") else request.dict())}
    except ValueError as exc:
        raise HTTPException(422, str(exc))

@app.get("/api/records")
def records():
    c = db.conn()
    r = c.execute("""SELECT b.id, f.name facility, m.name medicine, m.cold_chain_required,
        f.cold_chain_storage, f.cold_chain_transport, b.quantity stock, b.expiry_days, f.lead_days, b.incoming_qty,
        (SELECT COUNT(*) FROM usage u WHERE u.facility_id=b.facility_id AND u.medicine_id=b.medicine_id AND u.used IS NOT NULL) usage_days
        FROM batches b JOIN facilities f ON f.id=b.facility_id JOIN medicines m ON m.id=b.medicine_id ORDER BY b.id""").fetchall()
    c.close(); return [dict(x) for x in r]

@app.post("/api/records")
def add_record(r: Record):
    c = db.conn()
    f = db.get_id(c, "facilities", r.facility, lead_days=r.lead_days)
    c.execute("UPDATE facilities SET lead_days=? WHERE id=?", (r.lead_days, f))
    if r.locality: c.execute("UPDATE facilities SET locality=? WHERE id=?", (r.locality, f))
    if r.latitude is not None and r.longitude is not None:
        c.execute("UPDATE facilities SET latitude=?, longitude=? WHERE id=?", (r.latitude, r.longitude, f))
    c.execute("UPDATE facilities SET cold_chain_storage=?, cold_chain_transport=? WHERE id=?",
              (int(r.cold_chain_storage), int(r.cold_chain_transport), f))
    m = db.get_id(c, "medicines", r.medicine)
    c.execute("UPDATE medicines SET cold_chain_required=? WHERE id=?", (int(r.cold_chain_required), m))
    c.execute("INSERT INTO batches(facility_id,medicine_id,quantity,expiry_days,incoming_qty) VALUES(?,?,?,?,?)",
              (f, m, r.stock, r.expiry_days, r.incoming_qty))
    if r.usage:
        old = [None] * 60
        for row in c.execute("SELECT day, used FROM usage WHERE facility_id=? AND medicine_id=? ORDER BY day", (f, m)):
            if 0 <= row["day"] < 60: old[row["day"]] = row["used"]
        u = (old + r.usage)[-60:]
        u = [None] * (60 - len(u)) + u
        c.execute("DELETE FROM usage WHERE facility_id=? AND medicine_id=?", (f, m))
        c.executemany("INSERT INTO usage VALUES(?,?,?,?)", [(f, m, i, v) for i, v in enumerate(u)])
        c.execute("DELETE FROM usage_meta WHERE facility_id=? AND medicine_id=?", (f, m))
    c.commit(); c.close(); return {"ok": True}

@app.put("/api/cold-chain")
def update_cold_chain(settings: ColdChainSettings):
    """Update cold-chain metadata without creating or changing a stock batch."""
    c = db.conn()
    facility = c.execute("SELECT id FROM facilities WHERE name=?", (settings.facility,)).fetchone()
    medicine = c.execute("SELECT id FROM medicines WHERE name=?", (settings.medicine,)).fetchone()
    if not facility or not medicine:
        c.close()
        raise HTTPException(404, "Add a stock record for this facility and medicine before saving its cold-chain settings.")
    c.execute("UPDATE facilities SET cold_chain_storage=?, cold_chain_transport=? WHERE id=?",
              (int(settings.cold_chain_storage), int(settings.cold_chain_transport), facility["id"]))
    c.execute("UPDATE medicines SET cold_chain_required=? WHERE id=?",
              (int(settings.cold_chain_required), medicine["id"]))
    c.commit(); c.close()
    return {"ok": True}

@app.delete("/api/records/{batch_id}")
def delete_record(batch_id: int):
    c = db.conn(); c.execute("DELETE FROM batches WHERE id=?", (batch_id,)); c.commit(); c.close(); return {"ok": True}

@app.post("/api/sample")
def sample(seed_value: int = 7):
    c = db.conn(); seed(c, seed_value); c.close(); return {"ok": True}

@app.post("/api/clear")
def clear_all():
    c = db.conn(); db.clear(c); c.close(); return {"ok": True}

@app.get("/api/facilities")
def facilities():
    c = db.conn()
    r = c.execute("SELECT name, locality, latitude, longitude, lead_days, cold_chain_storage, cold_chain_transport FROM facilities ORDER BY name").fetchall()
    c.close(); return [dict(x) for x in r]

@app.post("/api/upload-pdf")
async def upload_pdf(file: UploadFile = File(...), replace: bool = Form(False)):
    """Read tables from a text-based PDF, store them in SQLite, and the normal analysis then runs on them."""
    data = await file.read()
    try: parsed = parse_pdf(data)
    except ValueError as e: raise HTTPException(422, str(e))
    catalog = load_nlem_catalog()
    threshold = 0.78
    drug_matches: dict[str, dict] = {}
    for table in (parsed["stock"], parsed["usage"]):
        for row in table:
            raw = row.get("medicine", "")
            matched, confidence = normalize_drug_name(raw, catalog)
            if raw and raw != matched:
                drug_matches[raw] = dict(raw_name=raw, matched_name=matched, confidence=confidence,
                                         applied=confidence >= threshold)
            if raw and confidence >= threshold:
                row["medicine"] = matched
    for match in drug_matches.values():
        if not match["applied"]:
            parsed["warnings"].append(
                f"Medicine label '{match['raw_name']}' was not renamed; closest catalog match was "
                f"'{match['matched_name']}' ({match['confidence']:.2f} similarity). Please verify."
            )
    c = db.conn()
    try:
        result = ingest(c, parsed, replace)
        result["drug_matches"] = list(drug_matches.values())
        return result
    finally: c.close()

@app.get("/api/sample-pdf")
def sample_pdf():
    return FileResponse(SAMPLE_PDF, media_type="application/pdf", filename="sample_hospital_report.pdf")


@app.get("/api/runtime")
def runtime():
    return {"offline": os.getenv("MEDIROUTE_OFFLINE") == "1"}


from .blood import router as blood_router
app.include_router(blood_router)
app.include_router(temperature_router)


# Register after API routes so the packaged UI and API share one local origin.
FRONTEND_DIST = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "dist"
if (FRONTEND_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
