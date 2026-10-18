"""Rules for one facility+medicine pair: data check, stockout risk, expiry risk, spare stock."""
import math
from .forecast import level
from .ai_engine import outbreak_multiplier_for_pair

def analyse_pair(pr, p):
    h = pr["usage"]
    o = dict(facility=pr["facility"], medicine=pr["medicine"], locality=pr.get("locality"),
             latitude=pr.get("latitude"), longitude=pr.get("longitude"), status="OK", tags=[], why=[], uncertain=False,
             cold_chain_required=pr.get("cold_chain_required", False),
             cold_chain_storage=pr.get("cold_chain_storage", False),
             cold_chain_transport=pr.get("cold_chain_transport", False),
             cover=None, level="unknown", target=None, have=None, short=0, short0=0, excess=0, donor=0, donor0=0, order=0, lead=pr["lead"], inc=0, stock=None, fc=0)
    observed_usage = pr.get("observed_usage", h)
    recorded = sum(x is not None for x in observed_usage)
    if recorded < 60:
        o["why"].append(f"{60 - recorded} of 60 consumption days missing; filled with the average of recorded days.")
    base, spike, ratio, a30, a7 = level(h, p["spike"], observed_usage)
    if spike:
        o["tags"].append("Demand spike")
        o["why"].append(f"Demand spike: last 7 days average {a7:.1f}/day is {ratio:.1f}x the 30-day average ({a30:.1f}). Forecast raised to {a7:.1f}/day.")
    surge = outbreak_multiplier_for_pair(
        pr["medicine"], pr["facility"], pr.get("locality"),
        p.get("outbreak_category", ""), p.get("outbreak_region", ""), p.get("outbreak_multiplier", 1.0))
    fc = base * p["season"] * surge; o["fc"] = round(fc, 2)
    if surge > 1:
        o["tags"].append("Outbreak demand")
        o["why"].append(f"Outbreak adjustment: forecast raised by {surge:.2f}x for the matching medicine category and locality.")

    qtys = [b["qty"] for b in pr["batches"]]; exps = [b["exp"] for b in pr["batches"]]
    miss_stock = any(q is None for q in qtys); miss_exp = any(e is None for e in exps)
    stock = sum(q or 0 for q in qtys); inc = sum(b["inc"] for b in pr["batches"])
    o["inc"] = inc; o["stock"] = None if miss_stock else stock

    # RULE 1: missing critical data -> flag, do not guess
    if miss_stock or miss_exp or recorded < 7:
        o["status"] = "Uncertain"; o["uncertain"] = True
        o["why"].insert(0, "Current stock is missing, so no action is recommended until it is verified." if miss_stock else
                        "Expiry date missing for a batch, so expiry risk and transfers are skipped. Please verify." if miss_exp else
                        "Fewer than 7 days of usage recorded, so a forecast would be a guess. Add more usage data.")
        return o

    lead = pr["lead"]; need = fc * (lead + p["buffer"]); safety = fc * p["safety"]; threshold = lead + p["buffer"]
    cover = (stock + inc) / fc if fc > 0 else 999.0
    o.update(cover=round(cover, 1), threshold=threshold)
    o["short"] = o["short0"] = max(0, math.ceil(need + safety - (stock + inc)))

    # RULE 3: stockout risk = days of cover below delivery time + buffer
    if cover < threshold:
        o["status"] = "Critical" if cover < lead / 2 else "High" if cover < lead else "Medium"
        o["tags"].append("Stockout risk")
        o["why"].append(f"Stockout risk: {cover:.1f} days of cover (stock {stock}{f' + {inc} arriving' if inc else ''} / {fc:.1f} per day) "
                        f"but replenishment takes {lead} days (+{p['buffer']} buffer).")

    # RULE 4: expiry risk, first-expiry-first-out (earliest batch is used first)
    used = 0; exc = 0; ex_batches = []; min_shelf = None
    for i, b in sorted(enumerate(pr["batches"]), key=lambda x: x[1]["exp"]):
        expected = max(0.0, fc * b["exp"] - used)
        e = max(0, math.floor(b["qty"] - expected)); used += b["qty"]
        if e > 0:
            exc += e; min_shelf = b["exp"] if min_shelf is None else min(min_shelf, b["exp"])
            ex_batches.append(dict(batch=f"B{i+1}", qty=b["qty"], exp=b["exp"], excess=e))
            o["why"].append(f"Expiry risk: batch B{i+1} has {b['qty']} units expiring in {b['exp']} days; expected use is "
                            f"{round(min(b['qty'], expected))}, so {e} may expire.")
    o["excess"] = exc; o["expiring"] = ex_batches
    # map colour: below the needed level = short (red); far above it, or units that will expire = excess (yellow); else ok (green)
    target = fc * (lead + p["buffer"] + p["safety"]); have = stock + inc
    o.update(target=round(target, 1), have=have)
    o["level"] = "short" if have < target else "excess" if (have > p["excess_x"] * target and have - target >= p["min_qty"]) or exc > 0 else "ok"
    if exc > 0:
        o["tags"].append("Expiry risk")
        if o["status"] == "OK": o["status"] = "Medium"

    # Spare stock must remain after delivery needs and the 21-day emergency reserve.
    donor_reserve = fc * max(21, p["safety"])
    spare = max(0, math.floor(stock + inc - need - donor_reserve))
    # Transfers never breach the 21-day emergency reserve; expiry risk is surfaced
    # separately and does not override the reserve constraint.
    o["donor"] = o["donor0"] = min(stock, spare)
    # Use the earliest known expiry as a conservative transfer constraint.
    o["shelf"] = min(exps)
    o["donor_reserve_days"] = max(0.0, (stock + inc - o["donor"]) / fc) if fc > 0 else 999.0
    if o["status"] == "OK":
        o["why"].append(f"{cover:.1f} days of cover, above the {threshold}-day requirement. No action needed.")
    return o
