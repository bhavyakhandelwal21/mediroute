"""Local blood-bank inventory and exact-product redistribution proposals.

Targets and handling readiness are entered by blood-bank staff. This planner
does not select patient-compatible products or authorize transfusion/dispatch.
"""
from datetime import date, datetime, time, timedelta, timezone
import io
import re
from typing import Literal, Optional
import pdfplumber
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator
from . import db

GROUPS = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']
COMPONENTS = ['Red cells', 'Whole blood', 'Platelets', 'Frozen plasma']
Group = Literal['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']
Component = Literal['Red cells', 'Whole blood', 'Platelets', 'Frozen plasma']
router = APIRouter(prefix='/api/blood', tags=['Blood bank'])


def connect():
    c = db.conn()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS blood_positions(
            id INTEGER PRIMARY KEY, facility TEXT NOT NULL COLLATE NOCASE,
            blood_group TEXT NOT NULL, component TEXT NOT NULL,
            target_units INTEGER NOT NULL, reported_units INTEGER,
            storage_verified INTEGER NOT NULL,
            transport_verified INTEGER NOT NULL,
            UNIQUE(facility, blood_group, component));
        CREATE TABLE IF NOT EXISTS blood_batches(
            id INTEGER PRIMARY KEY, position_id INTEGER NOT NULL,
            batch_code TEXT NOT NULL, units INTEGER NOT NULL,
            expires_at TEXT NOT NULL, released INTEGER NOT NULL,
            UNIQUE(position_id, batch_code));
    ''')
    have = {row[1] for row in c.execute('PRAGMA table_info(blood_positions)')}
    for col, kind in [('locality', 'TEXT'), ('latitude', 'REAL'), ('longitude', 'REAL'), ('reported_units', 'INTEGER')]:
        if col not in have:
            c.execute(f'ALTER TABLE blood_positions ADD COLUMN {col} {kind}')
    return c


class Position(BaseModel):
    facility: str = Field(min_length=1, max_length=120)
    blood_group: Group
    component: Component
    target_units: int = Field(ge=0, le=100000, strict=True)
    reported_units: Optional[int] = Field(default=None, ge=0, le=100000, strict=True)
    locality: Optional[str] = None
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    storage_verified: bool = False
    transport_verified: bool = False

    @field_validator('facility')
    @classmethod
    def clean_facility(cls, value):
        value = ' '.join(value.split())
        if not value:
            raise ValueError('Hospital name is required')
        return value


class Batch(BaseModel):
    position_id: int = Field(gt=0)
    batch_code: str = Field(min_length=1, max_length=120)
    units: int = Field(ge=0, le=100000, strict=True)
    expires_at: datetime
    released: bool = False

    @field_validator('batch_code')
    @classmethod
    def clean_code(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Batch reference is required')
        return value

    @field_validator('expires_at')
    @classmethod
    def aware_expiry(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Expiry must include a time zone')
        return value.astimezone(timezone.utc)


@router.get('/catalog')
def catalog():
    return dict(groups=GROUPS, components=COMPONENTS)


@router.post('/positions')
def save_position(p: Position):
    c = connect()
    try:
        with c:
            c.execute(
                "INSERT INTO blood_positions(facility,blood_group,component,target_units,reported_units,locality,latitude,longitude,storage_verified,transport_verified) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(facility,blood_group,component) DO UPDATE SET "
                "target_units=excluded.target_units,reported_units=COALESCE(excluded.reported_units,blood_positions.reported_units),locality=excluded.locality,latitude=excluded.latitude,longitude=excluded.longitude,storage_verified=excluded.storage_verified,transport_verified=excluded.transport_verified",
                (p.facility,p.blood_group,p.component,p.target_units,p.reported_units,p.locality,p.latitude,p.longitude,int(p.storage_verified),int(p.transport_verified)))
            row = c.execute('SELECT * FROM blood_positions WHERE facility=? AND blood_group=? AND component=?',
                            (p.facility, p.blood_group, p.component)).fetchone()
            return dict(row)
    finally:
        c.close()


@router.post('/batches')
def save_batch(b: Batch):
    c = connect()
    try:
        with c:
            if not c.execute('SELECT id FROM blood_positions WHERE id=?', (b.position_id,)).fetchone():
                raise HTTPException(404, 'Save the hospital and blood product first.')
            # Same reference updates the current count; repeated saves never double stock.
            c.execute('''INSERT INTO blood_batches(position_id,batch_code,units,expires_at,released)
                VALUES(?,?,?,?,?) ON CONFLICT(position_id,batch_code) DO UPDATE SET
                units=excluded.units, expires_at=excluded.expires_at, released=excluded.released''',
                (b.position_id, b.batch_code, b.units, b.expires_at.isoformat(), int(b.released)))
            c.execute('UPDATE blood_positions SET reported_units=NULL WHERE id=?', (b.position_id,))
        return {'ok': True}
    finally:
        c.close()


@router.delete('/batches/{batch_id}')
def delete_batch(batch_id: int):
    c = connect()
    try:
        with c:
            if not c.execute('DELETE FROM blood_batches WHERE id=?', (batch_id,)).rowcount:
                raise HTTPException(404, 'Batch not found')
        return {'ok': True}
    finally:
        c.close()


def analyse(positions, batches, transit_hours=2, now=None):
    now = now or datetime.now(timezone.utc)
    arrival = now + timedelta(hours=transit_hours)
    rows, available = [], {}
    for p in positions:
        all_lots = [dict(b) for b in batches if b['position_id'] == p['id']]
        # A current PDF stock count supersedes older batch snapshots without
        # deleting them. Re-enter current batch details to make stock transferable.
        lots = [] if p.get('reported_units') is not None else all_lots
        for b in all_lots:
            b['superseded'] = p.get('reported_units') is not None
        valid = []
        for b in all_lots:
            expiry = datetime.fromisoformat(b['expires_at'])
            b['expired'] = expiry <= now
            b['eligible'] = bool(not b.get('superseded') and b['released'] and not b['expired'] and p['storage_verified'])
            if b in lots and b['eligible'] and expiry > arrival:
                valid.append(dict(b))
        usable = sum(b['units'] for b in lots if b['eligible'])
        # A PDF may report a current count without batch expiry/release data.
        # Keep it visible, but never make that unverified count transfer-eligible.
        stock = p.get('reported_units') if p.get('reported_units') is not None else sum(b['units'] for b in lots) if lots else None
        shortage_basis = usable if lots else stock
        known_shortage = max(0, p['target_units'] - shortage_basis) if shortage_basis is not None else 0
        # Reserve is protected using only stock still valid at estimated arrival.
        arrival_stock = sum(b['units'] for b in valid)
        short = known_shortage
        warnings = []
        if not p['storage_verified']:
            warnings.append('Component-specific storage readiness is not verified; stock excluded and receipt blocked.')
        if arrival_stock > p['target_units'] and not p['transport_verified']:
            warnings.append('Transport readiness is not verified; surplus is not transfer-eligible.')
        if stock is None:
            warnings.append('Current stock is unknown. Add an available-units count or blood batch before calculating a shortage.')
        elif p.get('reported_units') is not None and stock:
            warnings.append('Stock count was reported without batch, expiry, and release details; it is visible but cannot be transferred until verified.')
        if any(b['expired'] and b['units'] for b in lots):
            warnings.append('Expired units excluded.')
        if any(not b['released'] and b['units'] for b in lots):
            warnings.append('Unreleased or quarantined units excluded.')
        if any(b['eligible'] and datetime.fromisoformat(b['expires_at']) <= arrival and b['units'] for b in lots):
            warnings.append('Some stock expires before estimated arrival and cannot be transferred.')
        row = dict(p, batches=all_lots, stock_units=stock, stock_known=stock is not None,
                   unverified_units=max(0, stock-usable) if stock is not None else 0,
                   usable_units=usable, shortage=short,
                   surplus=max(0, arrival_stock-p['target_units']) if p['transport_verified'] else 0,
                   received=0, proposed_out=0, unmet=short, warnings=warnings,
                   status='Verify storage' if not p['storage_verified'] else 'Verify stock' if stock is None else 'Shortage' if short else 'Verify transport' if arrival_stock > p['target_units'] and not p['transport_verified'] else 'Verify batches' if stock > usable else 'Sufficient')
        row['surplus_before_transfers'] = row['surplus']
        rows.append(row)
        available[p['id']] = sorted(valid, key=lambda b: (b['expires_at'], b['id']))
    transfers = []
    for receiver in sorted(rows, key=lambda r: (-r['shortage'], r['id'])):
        if not receiver['shortage'] or not receiver['storage_verified']:
            continue
        donors = [d for d in rows if d['facility'].casefold() != receiver['facility'].casefold()
                  and d['blood_group'] == receiver['blood_group'] and d['component'] == receiver['component']
                  and d['storage_verified'] and d['transport_verified'] and d['surplus'] > 0]
        donors.sort(key=lambda d: (available[d['id']][0]['expires_at'], d['id']))
        for donor in donors:
            for lot in available[donor['id']]:
                qty = min(lot['units'], donor['surplus'], receiver['unmet'])
                if qty <= 0:
                    continue
                lot['units'] -= qty
                donor['surplus'] -= qty
                donor['proposed_out'] += qty
                receiver['unmet'] -= qty
                receiver['received'] += qty
                transfers.append(dict(from_=donor['facility'], to=receiver['facility'],
                    blood_group=donor['blood_group'], component=donor['component'], units=qty,
                    batch_id=lot['id'], batch_code=lot['batch_code'], expires_at=lot['expires_at'],
                    transit_hours=transit_hours,
                    why=f"Exact group and component match; {donor['facility']} retains at least its "
                        f"{donor['target_units']}-unit target in stock valid at estimated arrival. "
                        "Recorded release, storage and transport checks passed."))
        if receiver['unmet']:
            receiver['warnings'].append(f"{receiver['unmet']} units still needed: no further eligible exact-match surplus. Contact the blood bank.")
    return dict(rows=rows, transfers=transfers, generated_at=now.isoformat(),
                summary=dict(shortage_positions=sum(r['shortage'] > 0 for r in rows),
                             usable_units=sum(r['usable_units'] for r in rows),
                             reported_units=sum(r['stock_units'] or 0 for r in rows),
                             unknown_stock_positions=sum(not r['stock_known'] for r in rows),
                             surplus_units=sum(r['surplus_before_transfers'] for r in rows),
                             transfer_units=sum(t['units'] for t in transfers),
                             unmet_units=sum(r['unmet'] for r in rows)))




def _norm_header(value):
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())

_BLOOD_COLUMNS = {
    'facility': {'facility', 'hospital', 'clinic', 'hospitalname'},
    'blood_group': {'bloodgroup', 'group', 'abogroup'},
    'component': {'component', 'bloodcomponent', 'product'},
    'target_units': {'targetunits', 'minimumstocktarget', 'minimumstocktargetunits', 'minimumunits', 'stocktarget', 'targetquantity'},
    'locality': {'locality', 'area', 'location'}, 'latitude': {'latitude', 'lat'}, 'longitude': {'longitude', 'lon', 'lng'},
    'storage_verified': {'storageverified', 'verifiedstorage'}, 'transport_verified': {'transportverified', 'verifiedtransport'},
    'batch_code': {'batchcode', 'batchreference', 'batch'}, 'units': {'units', 'stock', 'quantity', 'availableunits', 'currentstock', 'stockunits', 'unitsavailable'},
    'expires_at': {'expiresat', 'expiry', 'expirydate', 'labelledexpiry'}, 'released': {'released', 'releasedfordistribution'},
}
_BLOOD_LOOKUP = {alias: key for key, aliases in _BLOOD_COLUMNS.items() for alias in aliases}

def _truth(value):
    text = str(value or '').strip().casefold()
    if not text: return False
    if text in {'yes', 'y', 'true', '1', 'ready', 'verified', 'released'}: return True
    if text in {'no', 'n', 'false', '0', 'not ready', 'unverified', 'not released'}: return False
    raise ValueError(f'Expected yes or no, got "{value}"')

def _expiry(value):
    text = str(value or '').strip()
    if not text: return None
    try: parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        try: parsed = datetime.combine(date.fromisoformat(text), time(23, 59), tzinfo=timezone.utc)
        except ValueError: raise ValueError(f'Expiry "{text}" must use YYYY-MM-DD or ISO date-time format')
    if parsed.tzinfo is None or parsed.utcoffset() is None: parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def parse_blood_pdf(data: bytes):
    'Read a text-based PDF table with one hospital/product/batch per row.'
    try: pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc: raise ValueError('Could not open this file as a PDF.') from exc
    records, chars = [], 0
    with pdf:
        for page in pdf.pages:
            chars += len(page.extract_text() or '')
            for table in page.extract_tables() or []:
                if not table or not table[0]: continue
                header = [_BLOOD_LOOKUP.get(_norm_header(x)) for x in table[0]]
                if not {'facility','blood_group','component','target_units'} <= {x for x in header if x}: continue
                for row in table[1:]:
                    item = {key:(str(value).strip() if value is not None else '') for key,value in zip(header,row) if key}
                    if item.get('facility'): records.append(item)
    if chars == 0: raise ValueError('No selectable text found. Export the table from Excel or Word as a PDF; scanned images are not supported.')
    if not records: raise ValueError('No blood inventory table found. Required columns: Facility, Blood group, Component, Target units. Optional: Batch reference, Units, Expiry, Released, Locality, Latitude, Longitude, Storage verified, Transport verified.')
    return records

def _position_from_row(row):
    def number(key):
        text = str(row.get(key) or '').strip().replace(',', '')
        return None if not text else float(text)
    raw_units = str(row.get('units') or '').strip().replace(',', '')
    return Position(facility=row.get('facility','').strip(), blood_group=row.get('blood_group','').strip(),
        component=row.get('component','').strip(), target_units=int(float(row.get('target_units',''))),
        reported_units=None if not raw_units else int(float(raw_units)),
        locality=row.get('locality') or None, latitude=number('latitude'), longitude=number('longitude'),
        storage_verified=_truth(row.get('storage_verified')), transport_verified=_truth(row.get('transport_verified')))

@router.post('/upload-pdf')
async def upload_pdf(file: UploadFile = File(...), replace: bool = Form(False)):
    'Import blood targets and optional batch details from a text-based PDF.'
    try: records = parse_blood_pdf(await file.read())
    except ValueError as exc: raise HTTPException(422, str(exc))
    prepared=[]
    for line,row in enumerate(records,start=2):
        try:
            pos=_position_from_row(row)
            code=str(row.get('batch_code') or '').strip(); units=str(row.get('units') or '').strip(); expiry=_expiry(row.get('expires_at'))
            has_batch=bool(code or expiry)
            lot=None
            if has_batch:
                if not code or not units or not expiry: raise ValueError('Batch rows need Batch reference, Units, and Expiry together')
                lot=Batch(position_id=1,batch_code=code,units=int(float(units)),expires_at=expiry,released=_truth(row.get('released')))
                pos.reported_units = None
            prepared.append((pos,lot))
        except Exception as exc: raise HTTPException(422,f'Blood PDF row {line}: {exc}') from exc
    c=connect()
    try:
        with c:
            if replace:
                c.execute('DELETE FROM blood_batches'); c.execute('DELETE FROM blood_positions')
            count=0
            for pos,lot in prepared:
                c.execute("INSERT INTO blood_positions(facility,blood_group,component,target_units,reported_units,locality,latitude,longitude,storage_verified,transport_verified) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(facility,blood_group,component) DO UPDATE SET target_units=excluded.target_units,reported_units=excluded.reported_units,locality=excluded.locality,latitude=excluded.latitude,longitude=excluded.longitude,storage_verified=excluded.storage_verified,transport_verified=excluded.transport_verified",
                    (pos.facility,pos.blood_group,pos.component,pos.target_units,pos.reported_units,pos.locality,pos.latitude,pos.longitude,int(pos.storage_verified),int(pos.transport_verified)))
                item=c.execute('SELECT id FROM blood_positions WHERE facility=? AND blood_group=? AND component=?',(pos.facility,pos.blood_group,pos.component)).fetchone()
                if lot:
                    c.execute('INSERT INTO blood_batches(position_id,batch_code,units,expires_at,released) VALUES(?,?,?,?,?) ON CONFLICT(position_id,batch_code) DO UPDATE SET units=excluded.units,expires_at=excluded.expires_at,released=excluded.released',(item['id'],lot.batch_code,lot.units,lot.expires_at.isoformat(),int(lot.released)))
                    c.execute('UPDATE blood_positions SET reported_units=NULL WHERE id=?',(item['id'],))
                    count+=1
        return {'rows':len(prepared),'positions':len({(p.facility,p.blood_group,p.component) for p,_ in prepared}),'batches':count,'replace':replace}
    finally: c.close()

@router.delete('/positions/{position_id}')
def delete_position(position_id: int):
    c=connect()
    try:
        with c:
            c.execute('DELETE FROM blood_batches WHERE position_id=?',(position_id,))
            if not c.execute('DELETE FROM blood_positions WHERE id=?',(position_id,)).rowcount: raise HTTPException(404,'Hospital product not found')
        return {'ok':True}
    finally: c.close()

@router.get('/analysis')
def analysis(transit_hours: float = Query(2, gt=0, le=72, allow_inf_nan=False)):
    c = connect()
    try:
        positions = [dict(r) for r in c.execute('SELECT * FROM blood_positions ORDER BY facility,blood_group,component')]
        batches = [dict(r) for r in c.execute('SELECT * FROM blood_batches ORDER BY expires_at,id')]
        return analyse(positions, batches, transit_hours)
    finally:
        c.close()
