"""Before/after replay of the last 30 days + forecast error. Works on whatever data is in the database."""
import math
from .forecast import mean, fill, weekday_index

def sim(pr, with_mediflow, p):
    h = fill(pr["usage"]); lead = pr["lead"]
    stock = mean(h[:30]) * 12; pipe = []; stockout_days = 0; total = 0.0
    for t in range(30, 60):
        stock += sum(q for tt, q in pipe if tt == t); pipe = [x for x in pipe if x[0] > t]
        stock -= h[t]
        if stock < 0: stockout_days += 1; stock = 0
        total += stock
        on_order = sum(q for _, q in pipe)
        a14, a7, a30 = mean(h[t-13:t+1]), mean(h[t-6:t+1]), mean(h[t-29:t+1])
        if not with_mediflow:                      # basic rule: reorder when stock <= 7 days of average use
            a = mean(h[:30])
            if stock + on_order <= 7 * a: pipe.append((t + lead, round(14 * a)))
        else:                                      # MediFlow rule: forecast x (lead + buffer + safety)
            fc = a7 if a7 > p["spike"] * a30 else a14
            target = fc * (lead + p["buffer"] + p["safety"])
            if stock + on_order < target: pipe.append((t + lead, math.ceil(target + fc * 7 - stock - on_order)))
    return stockout_days, total / 30

def evaluate(pairs, rows, transfers, saved, p):
    ok = [i for i, r in enumerate(rows) if not r["uncertain"]]
    if not ok: return None
    a = [sim(pairs[i], False, p) for i in ok]; b = [sim(pairs[i], True, p) for i in ok]
    e1 = e2 = n = 0
    for i in ok:
        h = pairs[i]["usage"]; train, test = h[:46], h[46:]
        lv = mean(train[-14:]); ix = weekday_index(train)
        for k, v in enumerate(test):
            if v is not None:
                e1 += abs(v - lv); e2 += abs(v - lv * ix[(46 + k) % 7]); n += 1
    ex0 = sum(r["excess"] for r in rows)
    return dict(pairs=len(ok),
                stockout_days=[sum(x[0] for x in a), sum(x[0] for x in b)],
                avg_shelf_units=[round(sum(x[1] for x in a) / len(ok)), round(sum(x[1] for x in b) / len(ok))],
                mae=[round(e1 / n, 2) if n else None, round(e2 / n, 2) if n else None],
                expiry_risk_units=[ex0, max(0, ex0 - saved)],
                short_units=[sum(r["short0"] for r in rows), sum(r["order"] for r in rows)],
                transfer_use=[sum(t["qty"] for t in transfers), sum(r["donor0"] for r in rows)])
