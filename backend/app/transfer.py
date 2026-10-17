"""Transfers (greedy: most urgent receiver first, nearest donor first) and reorder quantities."""

def plan(rows, transit, p):
    T, saved = [], 0
    for med in dict.fromkeys(r["medicine"] for r in rows):
        g = [r for r in rows if r["medicine"] == med and not r["uncertain"]]
        for rc in sorted([r for r in g if r["short"] > 0], key=lambda r: r["cover"]):
            donors = sorted([r for r in g if r["donor"] >= p["min_qty"] and r is not rc],
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
                T.append(dict(medicine=med, from_=dn["facility"], to=rc["facility"], qty=qty, transit=tr,
                              why=f"{rc['facility']} has {rc['cover']} days of cover and needs {rc['short']} more units. "
                                  f"{dn['facility']} can spare {dn['donor']} after keeping {p['safety']} days of safety stock"
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
