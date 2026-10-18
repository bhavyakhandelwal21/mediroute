"""Demand estimation: recent average x weekday pattern, spike check, and a likely range."""
import numpy as np

def mean(h):
    rec = [x for x in h if x is not None]
    return float(np.mean(rec)) if rec else 0.0

def fill(h):
    m = mean(h)                                  # only MISSING usage days are filled, with the average
    return [m if x is None else x for x in h]

def weekday_index(h):
    f = fill(h); m = float(np.mean(f))
    return [1.0] * 7 if m == 0 else [float(np.mean(f[k::7]) / m) for k in range(7)]

def residual_sd(h, idx):
    f = np.array(fill(h)[-28:]); off = len(h) - 28; m = f.mean()
    e = f - np.array([m * idx[(off + i) % 7] for i in range(28)])
    return float(e.std())

def level(h, spike_x, observed=None):
    """Daily demand level. If the last 7 days are far above the 30-day average, use the 7-day level."""
    a30, a14, a7 = mean(h[-30:]), mean(h[-14:]), mean(h[-7:])
    evidence = h if observed is None else observed
    n7 = sum(x is not None for x in evidence[-7:])
    spike = n7 >= 5 and a30 > 0 and a7 > spike_x * a30
    return (a7 if spike else a14), spike, (a7 / a30 if a30 else 0.0), a30, a7

def curve(h, fc, days=14):
    """Next `days` days: [median, low, high] (80% range from past forecast errors)."""
    idx = weekday_index(h); sd = residual_sd(h, idx)
    out = []
    for k in range(days):
        m = fc * idx[(len(h) + k) % 7]
        out.append([round(m, 1), round(max(0, m - 1.28 * sd), 1), round(m + 1.28 * sd, 1)])
    return out
