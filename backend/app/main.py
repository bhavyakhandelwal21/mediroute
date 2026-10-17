"""FastAPI server. Run:  uvicorn app.main:app --reload"""
from typing import Optional
import pathlib
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

SAMPLE_PDF = pathlib.Path(__file__).resolve().parent.parent / "samples" / "sample_hospital_report.pdf"

app = FastAPI(title="MediFlow API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def start():
    c = db.conn()
    if db.is_empty(c): seed(c)          # first run: fill with synthetic sample data
    c.close()

def params(spike: float = 1.5, safety: int = 3, buffer: int = 2, season: float = 1.0, min_qty: int = 10, transit: int = 1, excess_x: float = 1.5):
    return dict(spike=spike, safety=safety, buffer=buffer, season=season, min_qty=min_qty, transit=transit, excess_x=excess_x)

def compute(p):
    c = db.conn(); pairs, transit = db.load_pairs(c); c.close()
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
    usage: list[Optional[float]] = []      # daily usage, oldest first (null = missing day); last 60 are kept

@app.get("/api/records")
def records():
    c = db.conn()
    r = c.execute("""SELECT b.id, f.name facility, m.name medicine, b.quantity stock, b.expiry_days, f.lead_days, b.incoming_qty,
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
    m = db.get_id(c, "medicines", r.medicine)
    c.execute("INSERT INTO batches(facility_id,medicine_id,quantity,expiry_days,incoming_qty) VALUES(?,?,?,?,?)",
              (f, m, r.stock, r.expiry_days, r.incoming_qty))
    if r.usage:
        u = r.usage[-60:]; u = [None] * (60 - len(u)) + u
        c.execute("DELETE FROM usage WHERE facility_id=? AND medicine_id=?", (f, m))
        c.executemany("INSERT INTO usage VALUES(?,?,?,?)", [(f, m, i, v) for i, v in enumerate(u)])
    c.commit(); c.close(); return {"ok": True}

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
    r = c.execute("SELECT name, locality, latitude, longitude, lead_days FROM facilities ORDER BY name").fetchall()
    c.close(); return [dict(x) for x in r]

@app.post("/api/upload-pdf")
async def upload_pdf(file: UploadFile = File(...), replace: bool = Form(True)):
    """Read tables from a text-based PDF, store them in SQLite, and the normal analysis then runs on them."""
    data = await file.read()
    try: parsed = parse_pdf(data)
    except ValueError as e: raise HTTPException(422, str(e))
    c = db.conn()
    try: return ingest(c, parsed, replace)
    finally: c.close()

@app.get("/api/sample-pdf")
def sample_pdf():
    return FileResponse(SAMPLE_PDF, media_type="application/pdf", filename="sample_hospital_report.pdf")
