"""Reads a TEXT-based PDF (exported from Excel/Word, not a scan) that contains tables, and loads it into the database.

Stock table needs:  Facility, Medicine, Stock   (optional: Expiry, Lead days, Incoming, Locality, Latitude, Longitude, Usage, Cold chain required, Cold-chain storage, Cold-chain transport)
Usage table needs:  Facility, Medicine, Date, Used
Column names are matched loosely (e.g. 'Hospital' = Facility, 'Qty' = Stock).
"""
import io, re, datetime
import pandas as pd
import pdfplumber
from .db import clear, get_id

ALIASES = {
    "facility": {"facility", "hospital", "clinic", "centre", "center", "facilityname", "hospitalname", "name"},
    "medicine": {"medicine", "drug", "item", "medication", "medicinename", "drugname"},
    "stock": {"stock", "quantity", "qty", "currentstock", "onhand", "stockqty", "balance"},
    "expiry_days": {"expirydays", "expiry", "daystoexpiry", "expiryindays", "expirydate"},
    "lead_days": {"leaddays", "lead", "leadtime", "deliverytime", "deliverydays", "delivery"},
    "incoming_qty": {"incoming", "incomingqty", "onorder", "incomingunits"},
    "locality": {"locality", "area", "location", "address"},
    "cold_chain_required": {"coldchainrequired", "requirescoldchain"},
    "cold_chain_storage": {"coldchainstorage", "coldstorageready"},
    "cold_chain_transport": {"coldchaintransport", "coldtransportavailable"},
    "latitude": {"latitude", "lat"}, "longitude": {"longitude", "lon", "lng", "long"},
    "usage": {"usage", "dailyusage", "consumption"},
    "date": {"date", "day"}, "used": {"used", "consumed", "dispensed", "unitsused", "quantityused"},
}
LOOKUP = {n: k for k, names in ALIASES.items() for n in names}
norm = lambda s: re.sub(r"[^a-z0-9]", "", str(s or "").lower())

def clean(v):
    if v is None: return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s or None

def to_num(v):
    s = clean(v)
    try: return float(s.replace(",", "")) if s else None
    except ValueError: return None

def to_bool(v):
    """Parse common yes/no values; return None when the PDF cell is blank/unknown."""
    s = clean(v)
    if not s: return None
    s = s.casefold()
    if s in {"yes", "y", "true", "1", "required", "available", "ready"}: return True
    if s in {"no", "n", "false", "0", "not required", "unavailable", "not ready"}: return False
    return None

def to_date(v):
    s = clean(v)
    if not s: return None
    iso = re.match(r"\d{4}-\d{1,2}-\d{1,2}", s)
    d = pd.to_datetime(s, errors="coerce") if iso else pd.to_datetime(s, dayfirst=True, errors="coerce")   # other formats: DD/MM/YYYY
    return None if pd.isna(d) else d.date()

def parse_pdf(data):
    try: pdf = pdfplumber.open(io.BytesIO(data))
    except Exception: raise ValueError("Could not open this file as a PDF.")
    stock, usage, prev, chars, skipped = [], [], None, 0, 0
    with pdf:
        for page in pdf.pages:
            chars += len(page.extract_text() or "")
            for t in page.extract_tables():
                header, rows = [LOOKUP.get(norm(h)) for h in t[0]], t[1:]
                if sum(h is not None for h in header) < 2 and prev and len(prev) == len(t[0]):
                    header, rows = prev, t                      # table continues from the previous page
                keys = {h for h in header if h}
                if {"facility", "medicine", "date", "used"} <= keys: target = usage
                elif {"facility", "medicine", "stock"} <= keys: target = stock
                else: skipped += 1; continue
                prev = header
                for r in rows:
                    d = {h: clean(v) for h, v in zip(header, r) if h}
                    if d.get("facility") and d.get("medicine"): target.append(d)
    if chars == 0:
        raise ValueError("No text found. This looks like a scanned or image-only PDF, which is not supported. Use a PDF exported from Excel or Word.")
    if not stock and not usage:
        raise ValueError("No tables with the expected columns were found. A stock table needs Facility, Medicine, Stock "
                         "(optional: Expiry, Lead days, Incoming, Locality, Latitude, Longitude, Usage). A usage table needs Facility, Medicine, Date, Used.")
    return dict(stock=stock, usage=usage, warnings=[f"{skipped} table(s) had unrecognised columns and were skipped."] if skipped else [])

def ingest(c, parsed, replace=False):
    stock, usage, warn = parsed["stock"], parsed["usage"], list(parsed["warnings"])
    dates = [d for d in (to_date(r.get("date")) for r in usage) if d]
    last = max(dates) if dates else None
    ref = last or datetime.date.today()                       # expiry dates are turned into 'days from this date'
    if replace: clear(c)
    hist, bad = {}, 0
    for r in usage:
        d, u = to_date(r.get("date")), to_num(r.get("used"))
        if not d or u is None: bad += 1; continue
        i = 59 - (last - d).days                              # day 59 = the latest date in the PDF
        if 0 <= i < 60:
            h = hist.setdefault((r["facility"], r["medicine"]), [None] * 60)
            h[i] = (h[i] or 0) + u
    if bad: warn.append(f"{bad} usage row(s) had an unreadable date or number and were ignored.")
    pairs = set()
    for r in stock:
        lead = to_num(r.get("lead_days"))
        f = get_id(c, "facilities", r["facility"], lead_days=int(lead or 5))
        if lead: c.execute("UPDATE facilities SET lead_days=? WHERE id=?", (int(lead), f))
        if r.get("locality"): c.execute("UPDATE facilities SET locality=? WHERE id=?", (r["locality"], f))
        storage, transport = to_bool(r.get("cold_chain_storage")), to_bool(r.get("cold_chain_transport"))
        if storage is not None: c.execute("UPDATE facilities SET cold_chain_storage=? WHERE id=?", (int(storage), f))
        if transport is not None: c.execute("UPDATE facilities SET cold_chain_transport=? WHERE id=?", (int(transport), f))
        lat, lon = to_num(r.get("latitude")), to_num(r.get("longitude"))
        if lat is not None and lon is not None: c.execute("UPDATE facilities SET latitude=?, longitude=? WHERE id=?", (lat, lon, f))
        m = get_id(c, "medicines", r["medicine"])
        required = to_bool(r.get("cold_chain_required"))
        if required is not None: c.execute("UPDATE medicines SET cold_chain_required=? WHERE id=?", (int(required), m))
        q, ex = to_num(r.get("stock")), to_num(r.get("expiry_days"))
        if ex is None and (d := to_date(r.get("expiry_days"))): ex = (d - ref).days
        c.execute("INSERT INTO batches(facility_id,medicine_id,quantity,expiry_days,incoming_qty) VALUES(?,?,?,?,?)",
                  (f, m, None if q is None else int(q), None if ex is None else int(ex), int(to_num(r.get("incoming_qty")) or 0)))
        if r.get("usage"):                                    # usage given inside the stock table as "20 22 19 - 25 ..."
            nums = [None if x == "-" else to_num(x) for x in re.split(r"[\s,;]+", r["usage"]) if x]
            hist[(r["facility"], r["medicine"])] = ([None] * 60 + nums)[-60:]
        pairs.add((r["facility"], r["medicine"]))
    for (fn, mn), incoming in hist.items():
        f = get_id(c, "facilities", fn, lead_days=5); m = get_id(c, "medicines", mn)
        h, anchor = incoming, last
        if not replace:
            old = c.execute("SELECT day, used FROM usage WHERE facility_id=? AND medicine_id=? ORDER BY day", (f, m)).fetchall()
            meta = c.execute("SELECT last_date FROM usage_meta WHERE facility_id=? AND medicine_id=?", (f, m)).fetchone()
            old_anchor = to_date(meta["last_date"]) if meta and meta["last_date"] else None
            if old:
                h = [None] * 60
                if old_anchor and last:
                    anchor = max(old_anchor, last)
                    for row in old:
                        if row["used"] is None: continue
                        day = old_anchor - datetime.timedelta(days=59 - int(row["day"]))
                        idx = 59 - (anchor - day).days
                        if 0 <= idx < 60: h[idx] = row["used"]
                    for idx, value in enumerate(incoming):
                        if value is None: continue
                        day = last - datetime.timedelta(days=59 - idx)
                        target = 59 - (anchor - day).days
                        if 0 <= target < 60: h[target] = value
                else:
                    h = [value if value is not None else (old[i]["used"] if i < len(old) else None) for i, value in enumerate(incoming)]
                    anchor = old_anchor or last
        c.execute("DELETE FROM usage WHERE facility_id=? AND medicine_id=?", (f, m))
        c.executemany("INSERT INTO usage VALUES(?,?,?,?)", [(f, m, i, v) for i, v in enumerate(h)])
        if anchor:
            c.execute("INSERT INTO usage_meta VALUES(?,?,?) ON CONFLICT(facility_id,medicine_id) DO UPDATE SET last_date=excluded.last_date",
                      (f, m, anchor.isoformat()))
        if not c.execute("SELECT 1 FROM batches WHERE facility_id=? AND medicine_id=?", (f, m)).fetchone():
            c.execute("INSERT INTO batches(facility_id,medicine_id,quantity,expiry_days,incoming_qty) VALUES(?,?,NULL,NULL,0)", (f, m))
            warn.append(f"Usage found for {fn} / {mn} but no stock row: marked Uncertain.")
    for fn, mn in sorted(pairs - set(hist))[:5]:
        warn.append(f"No usage data for {fn} / {mn}: marked Uncertain.")
    c.commit()
    return dict(stock_rows=len(stock), usage_rows=len(usage), pairs=len(pairs | set(hist)), warnings=warn)
