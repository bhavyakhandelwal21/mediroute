"""SQLite database: tables + helpers. The file mediflow.db is created next to this folder."""
import sqlite3, pathlib
import pandas as pd

DB = pathlib.Path(__file__).resolve().parent.parent / "mediflow.db"
TABLES = ["usage_meta", "usage", "batches", "transit", "medicines", "facilities"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS facilities(id INTEGER PRIMARY KEY, name TEXT UNIQUE, lead_days INTEGER NOT NULL, locality TEXT, latitude REAL, longitude REAL,
    cold_chain_storage INTEGER NOT NULL DEFAULT 0, cold_chain_transport INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS medicines(id INTEGER PRIMARY KEY, name TEXT UNIQUE, cold_chain_required INTEGER NOT NULL DEFAULT 0);
-- one row per batch. quantity / expiry_days may be NULL (= missing, will be flagged)
CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY, facility_id INT, medicine_id INT,
    quantity INT, expiry_days INT, incoming_qty INT DEFAULT 0);
-- day 0..59, oldest first. used may be NULL (= missing day)
CREATE TABLE IF NOT EXISTS usage(facility_id INT, medicine_id INT, day INT, used INT,
    PRIMARY KEY(facility_id, medicine_id, day));
CREATE TABLE IF NOT EXISTS usage_meta(facility_id INT, medicine_id INT, last_date TEXT,
    PRIMARY KEY(facility_id, medicine_id));
CREATE TABLE IF NOT EXISTS transit(from_id INT, to_id INT, days INT, PRIMARY KEY(from_id, to_id));
"""

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    have = {r[1] for r in c.execute("PRAGMA table_info(facilities)")}      # add location columns to older databases
    for col, typ in [("locality", "TEXT"), ("latitude", "REAL"), ("longitude", "REAL")]:
        if col not in have:
            c.execute(f"ALTER TABLE facilities ADD COLUMN {col} {typ}")
    for col in ("cold_chain_storage", "cold_chain_transport"):
        if col not in have:
            c.execute(f"ALTER TABLE facilities ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
    medicine_cols = {r[1] for r in c.execute("PRAGMA table_info(medicines)")}
    if "cold_chain_required" not in medicine_cols:
        c.execute("ALTER TABLE medicines ADD COLUMN cold_chain_required INTEGER NOT NULL DEFAULT 0")
    return c

def clear(c):
    for t in TABLES:
        c.execute(f"DELETE FROM {t}")
    c.commit()

def is_empty(c):
    return c.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0

def get_id(c, table, name, **extra):
    row = c.execute(f"SELECT id FROM {table} WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    cols = ["name"] + list(extra)
    cur = c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?'*len(cols))})", [name, *extra.values()])
    return cur.lastrowid

def load_pairs(c):
    """Read the database with Pandas and return one dict per (facility, medicine) pair."""
    b = pd.read_sql("""SELECT b.id, f.name facility, f.locality, f.latitude, f.longitude,
        f.cold_chain_storage, f.cold_chain_transport, f.lead_days lead,
        m.name medicine, m.cold_chain_required, b.quantity, b.expiry_days, b.incoming_qty
        FROM batches b JOIN facilities f ON f.id=b.facility_id JOIN medicines m ON m.id=b.medicine_id ORDER BY b.id""", c)
    u = pd.read_sql("""SELECT f.name facility, m.name medicine, u.day, u.used FROM usage u
        JOIN facilities f ON f.id=u.facility_id JOIN medicines m ON m.id=u.medicine_id""", c)
    t = pd.read_sql("""SELECT f1.name a, f2.name b, t.days FROM transit t
        JOIN facilities f1 ON f1.id=t.from_id JOIN facilities f2 ON f2.id=t.to_id""", c)
    pairs = []
    for (f, m), g in b.groupby(["facility", "medicine"], sort=False):
        hist = [None] * 60
        for r in u[(u.facility == f) & (u.medicine == m)].itertuples():
            if pd.notna(r.used) and 0 <= int(r.day) < 60:
                hist[int(r.day)] = float(r.used)
        batches = [dict(id=int(r.id), qty=None if pd.isna(r.quantity) else int(r.quantity),
                        exp=None if pd.isna(r.expiry_days) else int(r.expiry_days),
                        inc=0 if pd.isna(r.incoming_qty) else int(r.incoming_qty)) for r in g.itertuples()]
        first = g.iloc[0]
        meta = c.execute("""SELECT last_date FROM usage_meta WHERE facility_id=(SELECT id FROM facilities WHERE name=?)
            AND medicine_id=(SELECT id FROM medicines WHERE name=?)""", (f, m)).fetchone()
        pairs.append(dict(facility=f, medicine=m, locality=first.locality,
                          latitude=None if pd.isna(first.latitude) else float(first.latitude),
                          longitude=None if pd.isna(first.longitude) else float(first.longitude),
                          usage_anchor=meta["last_date"] if meta else None,
                          cold_chain_storage=bool(first.cold_chain_storage),
                          cold_chain_transport=bool(first.cold_chain_transport),
                          cold_chain_required=bool(first.cold_chain_required),
                          lead=int(g.lead.iloc[0]), usage=hist, batches=batches))
    transit = {(r.a, r.b): int(r.days) for r in t.itertuples()}
    return pairs, transit
