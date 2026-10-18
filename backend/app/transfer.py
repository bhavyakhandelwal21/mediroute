"""Transfers (greedy: most urgent receiver first, nearest donor first) and reorder quantities."""
import math

def plan(rows, transit, p):
    T, saved = [], 0
    for med in dict.fromkeys(r["medicine"] for r in rows):
        g = [r for r in rows if r["medicine"] == med and not r["uncertain"]]
        for rc in sorted([r for r in g if r["short"] > 0], key=lambda r: r["cover"]):
            cold_required = bool(rc.get("cold_chain_required"))
            if cold_required and not rc.get("cold_chain_storage"):
                rc["why"].append("Cold-chain transfer blocked: the receiving facility is not marked as having verified 2–8°C storage.")
                continue
            donor_pool = [r for r in g if r["donor"] >= p["min_qty"] and r is not rc]
            if cold_required and not any(d.get("cold_chain_transport") for d in donor_pool):
                rc["why"].append("Cold-chain transfer blocked: no eligible donor is marked as having 2–8°C transport available.")
            donors = sorted([r for r in donor_pool if not cold_required or r.get("cold_chain_transport")],
                            key=lambda d: transit.get((d["facility"], rc["facility"]), p["transit"]))
            for dn in donors:
                if rc["short"] <= 0:
                    break
                tr = transit.get((dn["facility"], rc["facility"]), p["transit"])
                qty = min(dn["donor"], rc["short"], int(rc["fc"] * dn["shelf"]))   # receiver must be able to use it before expiry
                if qty < p["min_qty"]:
                    continue
                if tr >= rc["cover"] or tr >= dn["shelf"]:      # must arrive before receiver runs out and before expiry
                    continue
                sv = min(qty, dn.setdefault("exl", dn["excess"])); dn["exl"] -= sv; saved += sv
                def distance_km(a, b):
                    if None in (a.get("latitude"), a.get("longitude"), b.get("latitude"), b.get("longitude")):
                        return None
                    lat1, lon1, lat2, lon2 = map(math.radians, (a["latitude"], a["longitude"], b["latitude"], b["longitude"]))
                    dlat, dlon = lat2-lat1, lon2-lon1
                    x = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
                    return round(6371 * 2 * math.atan2(math.sqrt(x), math.sqrt(1-x)), 1)
                T.append(dict(medicine=med, from_=dn["facility"], to=rc["facility"], qty=qty, transit=tr,
                              expiry_saved_units=sv,
                              source_expiry_days=int(dn["shelf"]), target_stockout_days=float(rc["cover"]),
                              donor_reserve_days=float((dn["stock"] + dn["inc"] - (dn["donor0"] - dn["donor"] + qty)) / dn["fc"]) if dn["fc"] > 0 else 999.0,
                              transit_distance_km=distance_km(dn, rc), is_cold_chain=cold_required,
                              why=f"{rc['facility']} has {rc['cover']} days of cover and needs {rc['short']} more units. "
                                  f"{dn['facility']} can spare {dn['donor']} after keeping a 21-day minimum reserve"
                                  f"{f' ({dn['excess']} would otherwise expire)' if dn['excess'] else ''}. Transit takes {tr} day(s), "
                                  f"within the receiver's cover and the {dn['shelf']}-day shelf life."))
                dn["donor"] -= qty; rc["short"] -= qty; rc["recv"] = rc.get("recv", 0) + qty
                rc["why"].append(f"Transfer: receive {qty} from {dn['facility']} ({tr} d).")
                dn["why"].append(f"Transfer: send {qty} to {rc['facility']}.")
    for r in rows:        # what is still missing after transfers must be ordered
        if not r["uncertain"] and r["short"] > 0:
            r["order"] = r["short"]
            r["why"].append(f"Reorder: {r['short']} units still short after incoming stock{' and transfers' if r.get('recv') else ''}. "
                            "Formula: forecast x (lead + buffer) + safety - stock - incoming - transfers.")
    return T, saved
