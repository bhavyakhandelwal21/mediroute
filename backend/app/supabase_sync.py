import os
import math
from datetime import datetime, timedelta
import httpx
from dotenv import load_dotenv
from app import db

load_dotenv()

def normalize_base_url(raw_url: str) -> str:
    """Cleans trailing slashes or accidental '/rest/v1' suffixes from Supabase URLs."""
    url = raw_url.strip().rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[:-8]
    return url

def get_configured_hospitals():
    """Loads configured hospital Supabase nodes from environment variables (with active defaults)."""
    hospitals = []
    defaults = {
        1: (
            "Sunrise Hospital",
            "https://elgzqwrnjkuqexriqwtw.supabase.co",
            "sb_publishable_GM-sDmqneHqMvPnuyleXQg_nL5neOcO",
        ),
        2: (
            "Green Valley Clinic",
            "https://ghusmdzyhwqykpxzghhc.supabase.co",
            "sb_publishable_emOKrBxBkeESy7oKry4Gaw_j023rHL7",
        ),
        3: (
            "Lotus Health Centre",
            "https://apyxsiqjrhvzqrpxxmvh.supabase.co/rest/v1/",
            "sb_publishable_CX7KveCk5sRi76vtU8FIlg_fVu2YdBB",
        ),
    }

    for i in range(1, 11):
        default_name, default_url, default_key = defaults.get(i, (f"Hospital Node {i}", "", ""))
        url = os.getenv(f"HOSPITAL_{i}_URL", default_url).strip()
        key = os.getenv(f"HOSPITAL_{i}_KEY", default_key).strip()
        label = os.getenv(f"HOSPITAL_{i}_NAME", default_name).strip()

        if url and key:
            hospitals.append({
                "id": i,
                "name": label,
                "url": normalize_base_url(url),
                "key": key,
            })
    return hospitals

def fetch_node(node):
    """Fetches facility, stock_register, and daily_usage from one independent Supabase database."""
    headers = {"apikey": node["key"]}
    base = node["url"]

    with httpx.Client(timeout=10.0) as client:
        fac = client.get(f"{base}/rest/v1/facility?select=*", headers=headers).json()
        stk = client.get(f"{base}/rest/v1/stock_register?select=*", headers=headers).json()
        usg = client.get(f"{base}/rest/v1/daily_usage?select=*", headers=headers).json()

    if not isinstance(fac, list) or not isinstance(stk, list) or not isinstance(usg, list):
        raise ValueError(f"Unexpected response from {node['name']}: {fac}")

    return fac, stk, usg

def _haversine_transit_days(lat1, lon1, lat2, lon2):
    """Calculates inter-hospital transit days from coordinates (minimum 1 day)."""
    if None in (lat1, lon1, lat2, lon2):
        return 1
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    km = 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return max(1, math.ceil(km / 150.0))

def apply_sync_to_sqlite(sync_payload):
    """Writes synced Supabase facilities, stock, and 60-day usage into mediflow.db."""
    facilities = sync_payload.get("facilities", [])
    stock_rows = sync_payload.get("stock_register", [])
    usage_rows = sync_payload.get("daily_usage", [])

    if not facilities or not stock_rows:
        return {"updated": False, "reason": "No facility or stock rows returned"}

    fac_meta = {f["facility_id"]: f for f in facilities}
    lead_by_fac = {
        s["facility_id"]: int(s["lead_days"])
        for s in stock_rows
        if s.get("lead_days") is not None
    }

    parsed_dates = [
        datetime.strptime(str(u["usage_date"])[:10], "%Y-%m-%d").date()
        for u in usage_rows
        if u.get("usage_date")
    ]
    max_date = max(parsed_dates) if parsed_dates else datetime.utcnow().date()
    start_date = max_date - timedelta(days=59)

    c = db.conn()
    try:
        db.clear(c)
        sqlite_fac_ids = {}

        for fid, f in fac_meta.items():
            sqlite_fac_ids[fid] = db.get_id(
                c,
                "facilities",
                f["name"],
                lead_days=lead_by_fac.get(fid, 4),
                locality=f.get("locality"),
                latitude=f.get("latitude"),
                longitude=f.get("longitude"),
            )

        for s in stock_rows:
            fid = s["facility_id"]
            if fid not in sqlite_fac_ids:
                continue
            med_id = db.get_id(c, "medicines", s["medicine"])
            c.execute(
                "INSERT INTO batches(facility_id, medicine_id, quantity, expiry_days, incoming_qty) VALUES(?,?,?,?,?)",
                (sqlite_fac_ids[fid], med_id, s.get("stock"), s.get("expiry_days"), s.get("incoming", 0)),
            )

        for u in usage_rows:
            fid = u["facility_id"]
            if fid not in sqlite_fac_ids or not u.get("usage_date"):
                continue
            u_date = datetime.strptime(str(u["usage_date"])[:10], "%Y-%m-%d").date()
            day_idx = (u_date - start_date).days
            if 0 <= day_idx < 60:
                med_id = db.get_id(c, "medicines", u["medicine"])
                c.execute(
                    "INSERT OR REPLACE INTO usage(facility_id, medicine_id, day, used) VALUES(?,?,?,?)",
                    (sqlite_fac_ids[fid], med_id, day_idx, u.get("used")),
                )

        fac_keys = list(sqlite_fac_ids.keys())
        for fid_a in fac_keys:
            for fid_b in fac_keys:
                if fid_a != fid_b:
                    fa, fb = fac_meta[fid_a], fac_meta[fid_b]
                    days = _haversine_transit_days(
                        fa.get("latitude"), fa.get("longitude"),
                        fb.get("latitude"), fb.get("longitude"),
                    )
                    c.execute(
                        "INSERT OR REPLACE INTO transit(from_id, to_id, days) VALUES(?,?,?)",
                        (sqlite_fac_ids[fid_a], sqlite_fac_ids[fid_b], days),
                    )

        c.commit()
        return {"updated": True, "facilities_loaded": len(sqlite_fac_ids)}
    finally:
        c.close()

def sync_all_hospitals(extra_nodes=None, write_to_db=True):
    """Syncs all configured hospitals + runtime-registered nodes and updates mediflow.db."""
    nodes = get_configured_hospitals()
    if extra_nodes:
        for n in extra_nodes:
            nodes.append({
                "id": n.get("id", len(nodes) + 1),
                "name": n["name"],
                "url": normalize_base_url(n["url"]),
                "key": n["key"].strip(),
            })

    all_facilities, all_stock, all_usage, statuses = [], [], [], []

    for node in nodes:
        try:
            fac, stk, usg = fetch_node(node)
            all_facilities.extend(fac)
            all_stock.extend(stk)
            all_usage.extend(usg)
            statuses.append({
                "node": node["name"],
                "status": "synced",
                "facilities": len(fac),
                "stock_items": len(stk),
                "usage_rows": len(usg),
            })
        except Exception as e:
            statuses.append({"node": node["name"], "status": "failed", "error": str(e)})

    payload = {
        "sync_report": statuses,
        "facilities": all_facilities,
        "stock_register": all_stock,
        "daily_usage": all_usage,
    }
    if write_to_db:
        payload["database_updated"] = apply_sync_to_sqlite(payload)
    return payload
