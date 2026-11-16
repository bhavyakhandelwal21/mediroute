
"""FastAPI server. Run:  uvicorn app.main:app --reload"""
from typing import Optional
from datetime import date
import pathlib
import os
import secrets
from email.message import EmailMessage
from urllib.parse import urlencode
from fastapi.staticfiles import StaticFiles
import pandas as pd
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .supabase_sync import sync_all_hospitals
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

class TransferOrderRequest(BaseModel):
    medicine: str
    source_facility: str
    target_facility: str
    quantity: int
    donor_email: str
    requester_email: str
    unit_price: float = 20
    transit_days: int = 1

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

@app.get("/api/transfer-orders")
def transfer_orders():
    c = db.conn()
    rows = c.execute("SELECT id, medicine, source_facility, target_facility, quantity, status, donor_email, requester_email, unit_price, transit_days, created_at FROM transfer_orders ORDER BY id DESC").fetchall()
    c.close()
    return [dict(row) for row in rows]

@app.post("/api/transfer-orders")
def create_transfer_order(order: TransferOrderRequest):
    if order.quantity <= 0 or not all(x.strip() for x in (order.medicine, order.source_facility, order.target_facility)):
        raise HTTPException(422, "Choose a medicine, donor and receiving hospital, and a positive quantity.")
    import re
    email_re = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    if not email_re.match(order.donor_email.strip()) or not email_re.match(order.requester_email.strip()):
        raise HTTPException(422, "Enter valid email addresses for both the donor and requesting hospital.")
    if order.unit_price < 0 or order.transit_days < 0: raise HTTPException(422, "Price and delivery estimate cannot be negative.")
    token = secrets.token_urlsafe(32)
    c = db.conn()
    try:
        pending = c.execute("SELECT 1 FROM transfer_orders WHERE medicine=? AND source_facility=? AND target_facility=? AND status='Requested' LIMIT 1",
                            (order.medicine, order.source_facility, order.target_facility)).fetchone()
        if pending:
            raise HTTPException(409, "A request for this medicine and route is already pending.")
        c.execute("INSERT INTO transfer_orders(medicine, source_facility, target_facility, quantity, donor_email, requester_email, unit_price, transit_days, response_token) VALUES(?,?,?,?,?,?,?,?,?)",
                  (order.medicine, order.source_facility, order.target_facility, order.quantity, order.donor_email.strip(), order.requester_email.strip(), order.unit_price, order.transit_days, token))
        c.commit()
        saved = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        c.close()
    _send_order_email(order, saved, token)
    return {"ok": True, "id": saved}

def _send_email(to, subject, text_body, html_body):
    message = EmailMessage(); message["Subject"] = subject; message["To"] = to
    message.set_content(text_body); message.add_alternative(html_body, subtype="html")
    try:
        smtp_user = os.getenv("SMTP_USER", "").strip()
        smtp_password = os.getenv("SMTP_PASSWORD", "").replace(" ", "")
        if smtp_user or smtp_password:
            if not smtp_user or not smtp_password:
                raise HTTPException(503, "Set both SMTP_USER and SMTP_PASSWORD in backend/.env, then restart the backend.")
            from .smtp_mailer import send_message
            send_message(message, smtp_user=smtp_user, password=smtp_password)
            return
        from .gmail_mailer import send_message
        send_message(message)
    except FileNotFoundError:
        raise HTTPException(503, "Email is not configured. Set SMTP_USER and SMTP_PASSWORD in backend/.env, or connect Gmail OAuth.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Gmail could not send this message: {exc}")

def _send_order_email(order, order_id, token):
    base = os.getenv("MEDIROUTE_PUBLIC_URL", "http://localhost:5173").rstrip("/")
    accept = f"{base}/api/transfer-orders/{order_id}/respond?" + urlencode({"token": token, "status": "Accepted"})
    decline = f"{base}/api/transfer-orders/{order_id}/respond?" + urlencode({"token": token, "status": "Declined"})
    total = order.quantity * order.unit_price
    subject = f"Medicine transfer request: {order.medicine} ({order.quantity} units)"
    text_body = (f"{order.target_facility} requests {order.quantity} units of {order.medicine} from {order.source_facility}. "
                 f"Indicative total: INR {total:.2f} (INR {order.unit_price:.2f}/unit). Estimated delivery: {order.transit_days} day(s).\n\n"
                 f"Accept: {accept}\nDecline: {decline}")
    html = f"<p><b>{order.target_facility}</b> requests {order.quantity} units of {order.medicine} from {order.source_facility}.</p><p>Indicative total: <b>₹{total:.2f}</b> (₹{order.unit_price:.2f}/unit). Estimated delivery: {order.transit_days} day(s).</p><p><a href='{accept}'>Accept request</a> &nbsp; <a href='{decline}'>Decline request</a></p><p>Price is an estimate and should be confirmed by the donor.</p>"
    _send_email(order.donor_email, subject, text_body, html)

def _send_acceptance_email(row):
    total = row["quantity"] * row["unit_price"]
    subject = f"Transfer accepted: {row['medicine']}"
    body = f"{row['source_facility']} accepted your request for {row['quantity']} units of {row['medicine']}. Indicative total: INR {total:.2f} (INR {row['unit_price']:.2f}/unit). Estimated delivery: {row['transit_days']} day(s). Confirm final price and delivery with the donor."
    _send_email(row["requester_email"], subject, body, f"<p>{row['source_facility']} accepted your request for {row['quantity']} units of {row['medicine']}.</p><p>Indicative total: <b>₹{total:.2f}</b> (₹{row['unit_price']:.2f}/unit). Estimated delivery: <b>{row['transit_days']} day(s)</b>.</p><p>Confirm final price and delivery with the donor.</p>")

@app.get("/api/transfer-orders/{order_id}/respond")
def respond_to_transfer_order(order_id: int, token: str, status: str):
    if status not in ("Accepted", "Declined"): raise HTTPException(422, "Invalid response.")
    c = db.conn(); row = c.execute("SELECT * FROM transfer_orders WHERE id=? AND response_token=? AND status='Requested'", (order_id, token)).fetchone()
    if not row: c.close(); raise HTTPException(404, "This request is already handled or the link is invalid.")
    c.execute("UPDATE transfer_orders SET status=? WHERE id=?", (status, order_id)); c.commit(); c.close()
    if status == "Accepted": _send_acceptance_email(row)
    return {"ok": True, "status": status}

@app.patch("/api/transfer-orders/{order_id}")
def update_transfer_order(order_id: int, status: str):
    if status not in ("Accepted", "Declined", "Cancelled"):
        raise HTTPException(422, "Status must be Accepted, Declined or Cancelled.")
    c = db.conn()
    row = c.execute("SELECT * FROM transfer_orders WHERE id=? AND status='Requested'", (order_id,)).fetchone()
    cur = c.execute("UPDATE transfer_orders SET status=? WHERE id=? AND status='Requested'", (status, order_id))
    c.commit(); c.close()
    if not cur.rowcount:
        raise HTTPException(404, "Pending transfer request not found.")
    if status == "Accepted": _send_acceptance_email(row)
    return {"ok": True}

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

# --- Multi-Hospital Decentralized Supabase Sync ---
DYNAMIC_NODES = []

class NewHospitalNode(BaseModel):
    name: str
    url: str
    key: str

@app.post("/api/register-node")
def register_hospital_node(node: NewHospitalNode):
    """Registers a 3rd+ hospital (e.g., Lotus Health Centre) dynamically."""
    DYNAMIC_NODES.append({
        "id": len(DYNAMIC_NODES) + 3,
        "name": node.name,
        "url": node.url,
        "key": node.key,
    })
    return {"status": "registered", "node": node.name}

@app.post("/api/sync-network")
def sync_network():
    """Syncs Sunrise Hospital, Green Valley Clinic, and any 3rd+ hospital node."""
    data = sync_all_hospitals(extra_nodes=DYNAMIC_NODES)
    return {
        "status": "complete",
        "nodes": data["sync_report"],
        "total_facilities": len(data["facilities"]),
        "total_stock_rows": len(data["stock_register"]),
        "total_usage_rows": len(data["daily_usage"]),
        "facilities": data["facilities"],
        "stock_register": data["stock_register"],
        "daily_usage": data["daily_usage"],
    }
