"""
Market regime at trade entry: тренд / флэт / шок (pure Python, no pandas).

Built from the same M1 bar files as mae_mfe.py (bars_<symbol>.csv).
M1 bars are grouped into 4-hour bars (08-12 ... in broker time, offset 2h as in
MT5 H4). Only bars that were already CLOSED at entry time are used.

  slope = (SMA20 now - SMA20 6 bars ago) / ATR14        (ATR units per ~day)
  ratio = ATR14 / average ATR14 of the last 60 bars (min 30)

  ratio > SHOCK_RATIO            -> "шок"
  |slope| > TREND_SLOPE          -> "тренд"
  otherwise                      -> "флэт"
Trend_Dir: "по тренду" / "против тренда" (only when |slope| > WEAK_SLOPE).
Needs about 14 days of history before entry, otherwise "н/д".
"""
import csv
from datetime import datetime, timedelta

SHOCK_RATIO = 1.4
TREND_SLOPE = 1.0
WEAK_SLOPE = 0.3


def _h4_bars(path):
    groups = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 5:
                continue
            try:
                t = datetime.strptime(row[0].strip(), "%Y.%m.%d %H:%M:%S")
                o, h, l, c = (float(x) for x in row[1:5])
            except ValueError:
                continue
            tt = t - timedelta(hours=2)
            key = tt.replace(hour=tt.hour // 4 * 4, minute=0, second=0) + timedelta(hours=2)
            g = groups.get(key)
            if g is None:
                groups[key] = [h, l, c]
            else:
                g[0] = max(g[0], h); g[1] = min(g[1], l); g[2] = c
    return [(k, v[0], v[1], v[2]) for k, v in sorted(groups.items())]


def build_indicators(path):
    bars = _h4_bars(path)
    out = []
    trs, atrs, closes = [], [], []
    for i, (t, h, l, c) in enumerate(bars):
        pc = bars[i - 1][3] if i else c
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        closes.append(c)
        atr = sum(trs[-14:]) / 14 if len(trs) >= 14 else None
        atrs.append(atr)
        sma = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        out.append({"end": t + timedelta(hours=4), "atr": atr, "sma": sma})
    for i, x in enumerate(out):
        x["slope"] = x["ratio"] = None
        if i >= 6 and x["atr"] and x["sma"] and out[i - 6]["sma"]:
            x["slope"] = (x["sma"] - out[i - 6]["sma"]) / x["atr"]
        window = [a for a in atrs[max(0, i - 59):i + 1] if a]
        if x["atr"] and len(window) >= 30:
            x["ratio"] = x["atr"] / (sum(window) / len(window))
    return out


def regime_at(indicators, open_dt, direction):
    """-> (regime, trend_dir, slope, ratio)"""
    done = [x for x in indicators if x["end"] <= open_dt]
    if not done or done[-1]["slope"] is None or done[-1]["ratio"] is None:
        return "н/д", "", None, None
    x = done[-1]
    s, r = x["slope"], x["ratio"]
    reg = "шок" if r > SHOCK_RATIO else ("тренд" if abs(s) > TREND_SLOPE else "флэт")
    rel = ""
    if abs(s) > WEAK_SLOPE:
        rel = "по тренду" if (s > 0) == (direction == "long") else "против тренда"
    return reg, rel, s, r
