"""Creates made-up practice data (synthetic). Same seed = same data."""
import math
import numpy as np
from .db import clear, get_id

# name: (delivery days, locality, latitude, longitude). Positions are ILLUSTRATIVE, names are made up.
FACILITIES = {"City Hospital": (4, "Indirapuram", 28.6430, 77.3690), "District Store": (4, "Indirapuram", 28.6395, 77.3640),
              "East Community Clinic": (6, "Vaishali", 28.6460, 77.3400), "Rural PHC Nandgaon": (9, "Loni", 28.7510, 77.2870),
              "North Health Centre": (5, "Raj Nagar", 28.6700, 77.4380)}
MEDICINES = ["Paracetamol 500mg", "Amoxicillin 250mg", "ORS sachets", "Metformin 500mg",
             "Salbutamol inhaler", "Iron-folic acid"]
WEEK = [1, 1.05, 1, 1.1, 1.15, .7, .6]   # weekday pattern: busier midweek, quieter weekend

def km(a, b):
    p = math.pi / 180
    h = math.sin((b[0]-a[0])*p/2)**2 + math.cos(a[0]*p)*math.cos(b[0]*p)*math.sin((b[1]-a[1])*p/2)**2
    return 12742 * math.asin(math.sqrt(h))

def seed(c, seed_value=7):
    rng = np.random.default_rng(seed_value)
    clear(c)
    fid = {n: get_id(c, "facilities", n, lead_days=v[0], locality=v[1], latitude=v[2], longitude=v[3]) for n, v in FACILITIES.items()}
    mid = {n: get_id(c, "medicines", n) for n in MEDICINES}
    names = list(FACILITIES)
    for i, a in enumerate(names):                      # travel days between facilities
        for b in names[i + 1:]:
            d = 1 + int(km(FACILITIES[a][2:], FACILITIES[b][2:]) // 15)   # travel days grow with distance
            c.execute("INSERT INTO transit VALUES(?,?,?)", (fid[a], fid[b], d))
            c.execute("INSERT INTO transit VALUES(?,?,?)", (fid[b], fid[a], d))
    for f in names:
        for m in MEDICINES:
            rate = rng.uniform(4, 40)
            spike = rng.random() < .14                 # some pairs get a demand jump in the last week
            for d in range(60):
                v = max(0, round(rate * rng.uniform(.7, 1.3) * (2.1 if spike and d >= 53 else 1) * WEEK[d % 7]))
                used = None if rng.random() < .08 else int(v)   # ~8% missing days
                c.execute("INSERT INTO usage VALUES(?,?,?,?)", (fid[f], mid[m], d, used))
            total = round(rate * rng.uniform(2, 47))
            nb = 2 if rng.random() < .5 else 1
            stock_missing = rng.random() < .04
            for b in range(nb):
                qty = None if (stock_missing and b == 0) else int(round(total / nb))
                exp = None if rng.random() < .07 else int(rng.integers(8, 118))
                inc = int(round(rate * 10)) if b == 0 and rng.random() < .35 else 0
                c.execute("INSERT INTO batches(facility_id,medicine_id,quantity,expiry_days,incoming_qty) VALUES(?,?,?,?,?)",
                          (fid[f], mid[m], qty, exp, inc))
    c.commit()
