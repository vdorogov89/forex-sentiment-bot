"""
MAE / MFE for the trading journal, from exact M1 bars exported by MT5.

  MFE (maximum favorable excursion): how far price went IN your favor
        at the best moment while the trade was open.
  MAE (maximum adverse excursion):   how far price went AGAINST you
        at the worst moment while the trade was open.

Both are measured from the entry price and reported in price units, in
percent of the entry price and, when Stop_Price is known, in R
(1R = distance from entry to your planned stop).

Why it matters:
  - A losing trade with a large MFE_R was once in solid profit: look at
    partial profit-taking or moving the stop.
  - A winning trade with MAE_R close to 1 almost hit the stop: the entry
    was early or the stop was barely big enough.
  - Capture = realized result / MFE: how much of the available move you took.

INPUT
  --bars-dir : folder with bars_<symbol>.csv written by mt5/ExportBars.mq5
               (default: %APPDATA%\\MetaQuotes\\Terminal\\Common\\Files)
  --journal  : the journal exported as CSV (file path) or a CSV URL; if
               omitted, the JOURNAL_SHEET_CSV_URL environment variable is used
  --extra    : optional JSON file with trades that are not in the journal:
               [{"instrument":"NDX","direction":"long",
                 "open":"2026.07.30 18:39:52","close":"2026.10.01 18:34:48",
                 "entry":27961.82,"exit":30335.52,"stop":null}]

All times are BROKER SERVER time (the same clock as the MT5 reports and
the journal's Open_Time/Close_Time), so no timezone conversion happens.

Accuracy: bars are Bid prices, one minute each. Excursions are exact to
about a minute. For shorts a stop is triggered by Ask, so MAE can be
understated by the spread. Entry/exit bars may contain ticks from just
before the entry or after the exit; excursions are clamped at 0.

Requirements: pip install requests   (only when --journal is a URL)
"""

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime, timedelta

# Journal "Instrument" -> MT5 symbol exported by ExportBars.mq5
SYMBOL_MAP = {
    "XAUUSD": "XAUUSDrfd",
    "EURUSD": "EURUSDrfd",
    "USDCHF": "USDCHFrfd",
    "NDX": "#NDX",
    "AVGO": "#AVGO",
    "GOOGL": "#GOOGL",
    "MSTR": "#MSTR",
    "AMZN": "#AMZN",
    "LCO": "#LCO",
    "GBPUSD": "GBPUSDrfd",
}

DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y")


# ---------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------

def parse_float(text):
    text = (str(text) if text is not None else "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_bar_time(text):
    text = text.strip()
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_date(text):
    text = (text or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_clock(text):
    text = (text or "").strip().lstrip("'")
    for fmt in ("%H:%M", "%H.%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def load_bars(path):
    """Returns a list of (start_datetime, high, low) sorted by time."""
    bars = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
        for row in csv.reader(f, delimiter=delimiter):
            if len(row) < 5:
                continue
            t = parse_bar_time(row[0])
            if t is None:
                continue  # header or garbage line
            high, low = parse_float(row[2]), parse_float(row[3])
            if high is None or low is None:
                continue
            bars.append((t, high, low))
    bars.sort(key=lambda b: b[0])
    return bars


# ---------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------

def excursions(bars, direction, entry, open_dt, close_dt):
    """
    Returns dict(mfe, mae, t_mfe_h, t_mae_h, bars_used, last_bar) or None.
    close_dt=None means the trade is still open: use all bars up to the end.
    """
    start = open_dt.replace(second=0, microsecond=0)
    used = [b for b in bars if b[0] >= start and (close_dt is None or b[0] <= close_dt)]
    if not used:
        return None
    best_fav = best_adv = 0.0
    t_fav = t_adv = used[0][0]
    for t, high, low in used:
        if direction == "long":
            fav, adv = high - entry, entry - low
        else:
            fav, adv = entry - low, high - entry
        if fav > best_fav:
            best_fav, t_fav = fav, t
        if adv > best_adv:
            best_adv, t_adv = adv, t
    return {
        "mfe": best_fav,
        "mae": best_adv,
        # the entry bar starts a few seconds before the exact entry time
        "t_mfe_h": max(0.0, (t_fav - open_dt).total_seconds() / 3600),
        "t_mae_h": max(0.0, (t_adv - open_dt).total_seconds() / 3600),
        "bars_used": len(used),
        "last_bar": used[-1][0],
    }


def analyse_trade(bars, trade):
    """trade: dict(instrument, direction, entry, open_dt, close_dt|None, exit|None, stop|None)."""
    ex = excursions(bars, trade["direction"], trade["entry"], trade["open_dt"], trade["close_dt"])
    if ex is None:
        return None
    entry, stop = trade["entry"], trade.get("stop")
    out = dict(ex)
    out["mfe_pct"] = ex["mfe"] / entry * 100
    out["mae_pct"] = ex["mae"] / entry * 100
    risk = abs(entry - stop) if stop else None
    out["risk"] = risk
    out["mfe_r"] = ex["mfe"] / risk if risk else None
    out["mae_r"] = ex["mae"] / risk if risk else None
    # realized move (for closed trades) and capture of the available move
    ext = trade.get("exit")
    if ext is not None:
        move = (ext - entry) if trade["direction"] == "long" else (entry - ext)
        out["realized"] = move
        out["realized_r"] = move / risk if risk else None
        # Only meaningful for winners: "what share of the best move did I keep".
        out["capture"] = move / ex["mfe"] if (ex["mfe"] > 0 and move > 0) else None
    return out


# ---------------------------------------------------------------------
# Reading trades
# ---------------------------------------------------------------------

def read_journal(source):
    if source.lower().startswith(("http://", "https://")):
        import requests
        resp = requests.get(source, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        text = resp.text
    else:
        with open(source, "r", encoding="utf-8-sig") as f:
            text = f.read()
    trades, notes = [], []
    for raw in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        name = row.get("Instrument", "")
        if not name:
            continue
        label = f"{row.get('Open_Date', '?')} {name}"
        od, ot = parse_date(row.get("Open_Date", "")), parse_clock(row.get("Open_Time", ""))
        entry = parse_float(row.get("Open_Price", ""))
        direction = row.get("Direction", "").lower()
        direction = "long" if direction.startswith(("long", "лонг", "buy")) else \
                    "short" if direction.startswith(("short", "шорт", "sell")) else ""
        if od is None or ot is None or entry is None or not direction:
            notes.append(f"{label}: пропущена — нужны Open_Date, Open_Time, Open_Price, Direction")
            continue
        cd, ct = parse_date(row.get("Close_Date", "")), parse_clock(row.get("Close_Time", ""))
        close_dt = datetime.combine(cd, ct) if cd and ct else None
        if row.get("Close_Date") and close_dt is None:
            notes.append(f"{label}: закрыта, но нет Close_Time — считаю как открытую до конца баров")
        trades.append({
            "label": label, "instrument": name, "direction": direction, "entry": entry,
            "open_dt": datetime.combine(od, ot), "close_dt": close_dt,
            "exit": parse_float(row.get("Close_Price", "")) if close_dt else None,
            "stop": parse_float(row.get("Stop_Price", "")),
        })
    return trades, notes


def read_extra(path):
    with open(path, "r", encoding="utf-8") as f:
        items = json.load(f)
    trades = []
    for it in items:
        trades.append({
            "label": f"{it['open'][:10]} {it['instrument']} (extra)",
            "instrument": it["instrument"], "direction": it["direction"], "entry": float(it["entry"]),
            "open_dt": parse_bar_time(it["open"]),
            "close_dt": parse_bar_time(it["close"]) if it.get("close") else None,
            "exit": it.get("exit"), "stop": it.get("stop"),
        })
    return trades


# ---------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------

def fmt(v, digits=2, suffix=""):
    return "—" if v is None else f"{v:.{digits}f}{suffix}"


def main():
    default_dir = os.path.join(os.environ.get("APPDATA", "."), "MetaQuotes", "Terminal", "Common", "Files")
    ap = argparse.ArgumentParser(description="MAE/MFE from MT5 M1 bars")
    ap.add_argument("--bars-dir", default=default_dir)
    ap.add_argument("--journal", default=os.environ.get("JOURNAL_SHEET_CSV_URL"))
    ap.add_argument("--extra")
    ap.add_argument("--out", default="mae_mfe_result.csv")
    args = ap.parse_args()

    trades, notes = [], []
    if args.journal:
        t, n = read_journal(args.journal)
        trades += t
        notes += n
    if args.extra:
        trades += read_extra(args.extra)
    if not trades:
        sys.exit("Нет сделок: укажите --journal и/или --extra.")

    cache, results = {}, []
    for tr in trades:
        sym = SYMBOL_MAP.get(tr["instrument"], tr["instrument"])
        path = os.path.join(args.bars_dir, f"bars_{sym}.csv")
        if sym not in cache:
            cache[sym] = load_bars(path) if os.path.exists(path) else None
        bars = cache[sym]
        if bars is None:
            notes.append(f"{tr['label']}: нет файла баров {path}")
            continue
        res = analyse_trade(bars, tr)
        if res is None:
            notes.append(f"{tr['label']}: в файле баров нет данных за период сделки")
            continue
        results.append((tr, res))

    print(f"{'Сделка':32} {'MFE_R':>7} {'MAE_R':>7} {'MFE%':>7} {'MAE%':>7} {'Захват':>7}  Когда (ч от входа)")
    for tr, r in results:
        cap = fmt(r.get("capture") * 100 if r.get("capture") is not None else None, 0, "%")
        print(f"{tr['label']:32} {fmt(r['mfe_r']):>7} {fmt(r['mae_r']):>7} "
              f"{fmt(r['mfe_pct']):>7} {fmt(r['mae_pct']):>7} {cap:>7}  "
              f"MFE {r['t_mfe_h']:.1f} / MAE {r['t_mae_h']:.1f}")
    for n in notes:
        print("!", n)

    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trade", "MAE_R", "MFE_R", "MAE_Pct", "MFE_Pct", "capture_pct", "hours_to_MFE", "hours_to_MAE"])
        for tr, r in results:
            w.writerow([tr["label"], fmt(r["mae_r"]), fmt(r["mfe_r"]), fmt(r["mae_pct"]), fmt(r["mfe_pct"]),
                        fmt(r["capture"] * 100 if r.get("capture") is not None else None, 0),
                        fmt(r["t_mfe_h"], 1), fmt(r["t_mae_h"], 1)])
    print(f"\nГотово: {args.out}")


if __name__ == "__main__":
    main()
