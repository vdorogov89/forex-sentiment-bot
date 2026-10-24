"""
Daily EUR & Gold positioning report -> Telegram bot.

Data source: the U.S. CFTC's public "Commitments of Traders" (COT) Socrata
API (publicreporting.cftc.gov). This is an official, free, no-key-required
government data feed — no scraping, no bot-detection issues, safe to call
from any server including GitHub Actions.

Important caveat: the CFTC publishes this report ONCE A WEEK (Friday,
covering the prior Tuesday's positions), not daily. So on most days this
message will show the same figures as the previous day, with a new
"as of" date only appearing once a week. That's a limitation of using a
free, official source instead of paid intraday retail-sentiment feeds.

What "consensus" means here: the % of Non-Commercial (large speculative)
traders' futures positions that are Long vs Short, for:
  - EUR FX  (contract: EURO FX - CHICAGO MERCANTILE EXCHANGE)
  - GOLD    (contract: GOLD - COMMODITY EXCHANGE INC.)
from the Legacy Futures-Only COT report (dataset id 6dca-aqww).

Requirements (installed automatically by the GitHub Actions workflow):
    pip install requests

Environment variables required:
    TELEGRAM_BOT_TOKEN  - token from @BotFather
    TELEGRAM_CHAT_ID    - your personal or group chat id

NOTE ON RELIABILITY:
If CFTC ever renames a contract or restructures this dataset, the
MARKET_NAMES below (or the fallback CONTAINS filter) may need updating.

STATE FILE:
The script keeps a small local file (STATE_FILE) recording the report
date of the last message it actually sent. Before sending, it checks
the freshest report date from CFTC against this file — if there's no
new report yet (e.g. CFTC delayed publication for a US holiday), it
skips sending and exits quietly instead of re-sending stale data. The
GitHub Actions workflow commits this file back to the repo after each
run so the "last sent" state persists between scheduled runs.
"""

import os
import time
import sys
from datetime import datetime, timezone

import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

CFTC_DATASET_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
STATE_FILE = "last_report_date.txt"

# Display label -> (exact market_and_exchange_names value, fallback substring)
MARKETS = {
    "EURUSD (EUR FX)": (
        "EURO FX - CHICAGO MERCANTILE EXCHANGE",
        "EURO FX - CHICAGO",
    ),
    "GBPUSD (GBP FX)": (
        "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE",
        "BRITISH POUND - CHICAGO",
    ),
    "JPY, йена (пара USDJPY)*": (
        "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE",
        "JAPANESE YEN - CHICAGO",
    ),
    "CHF, франк (пара USDCHF)*": (
        "SWISS FRANC - CHICAGO MERCANTILE EXCHANGE",
        "SWISS FRANC - CHICAGO",
    ),
    "AUDUSD (AUD FX)": (
        "AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
        "AUSTRALIAN DOLLAR - CHICAGO",
    ),
    "CAD, кан. доллар (пара USDCAD)*": (
        "CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
        "CANADIAN DOLLAR - CHICAGO",
    ),
    "XAUUSD (GOLD)": (
        "GOLD - COMMODITY EXCHANGE INC.",
        "GOLD - COMMODITY EXCHANGE",
    ),
    "BTC (CME)**": (
        "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
        "BITCOIN - CHICAGO MERCANTILE",
    ),
    "ETH (CME)**": (
        "ETHER CASH SETTLED - CHICAGO MERCANTILE EXCHANGE",
        "ETHER CASH SETTLED - CHICAGO",
    ),
}


def read_last_sent_date() -> str:
    """Returns the report_date of the last successfully sent message,
    or an empty string if the state file doesn't exist yet (first run)."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def write_last_sent_date(report_date: str) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(report_date)


def _fetch_latest_two_reports_once(exact_name: str, fallback_substring: str) -> list:
    """
    Queries the CFTC Socrata API for the two most recent Legacy
    Futures-Only COT report rows for a given market (current + previous
    week), so we can compute a week-over-week change.
    Tries an exact name match first; falls back to a substring match.
    Returns a list of raw record dicts (newest first), or raises
    RuntimeError if nothing found.
    """
    headers = {"Accept": "application/json"}

    params = {
        "$where": f"market_and_exchange_names='{exact_name}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": 2,
    }
    resp = requests.get(CFTC_DATASET_URL, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    rows = resp.json()

    if not rows:
        params = {
            "$where": f"market_and_exchange_names like '%25{fallback_substring}%25'",
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": 2,
        }
        resp = requests.get(CFTC_DATASET_URL, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        rows = resp.json()

    if not rows:
        raise RuntimeError(
            f"No CFTC rows found for '{exact_name}' (or substring '{fallback_substring}') — "
            "CFTC may have renamed this contract."
        )
    return rows


def fetch_latest_two_reports(exact_name: str, fallback_substring: str, attempts: int = 3) -> list:
    """Same as _fetch_latest_two_reports_once, but retries on network/API
    errors (CFTC's API is occasionally slow or returns 5xx), so one bad
    request doesn't silently drop a market from the report."""
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return _fetch_latest_two_reports_once(exact_name, fallback_substring)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            print(f"Warning: CFTC fetch for '{exact_name}' failed (attempt {attempt}/{attempts}): {exc}",
                  file=sys.stderr)
            if attempt < attempts:
                time.sleep(5 * attempt)
    raise last_exc


def _pct_long_short(row: dict) -> tuple:
    long_n = float(row["noncomm_positions_long_all"])
    short_n = float(row["noncomm_positions_short_all"])
    total = long_n + short_n
    if total == 0:
        raise RuntimeError("long+short positions are both zero")
    return 100 * long_n / total, 100 * short_n / total


def fetch_all_markets(only: list = None) -> dict:
    """
    Returns {label: (long_pct, short_pct, report_date, long_pct_change,
    long_contract_change, short_contract_change)} for every market in
    MARKETS. long_pct_change is the change in Long % vs the previous
    week (None if unavailable). long/short_contract_change are the raw
    week-over-week contract changes as published directly by CFTC
    (fields change_in_noncomm_long_all / change_in_noncomm_short_all).
    A market is omitted from the result if it couldn't be fetched.
    """
    results = {}
    for label, (exact_name, fallback_substring) in MARKETS.items():
        if only is not None and label not in only:
            continue
        try:
            rows = fetch_latest_two_reports(exact_name, fallback_substring)
            current = rows[0]
            long_pct, short_pct = _pct_long_short(current)
            report_date = current.get("report_date_as_yyyy_mm_dd", "")[:10]

            long_pct_change = None
            if len(rows) > 1:
                try:
                    prev_long_pct, _ = _pct_long_short(rows[1])
                    long_pct_change = long_pct - prev_long_pct
                except Exception:  # noqa: BLE001
                    long_pct_change = None

            # CFTC publishes these week-over-week contract deltas directly,
            # computed by CFTC itself from the raw position counts.
            long_contract_change = current.get("change_in_noncomm_long_all")
            short_contract_change = current.get("change_in_noncomm_short_all")
            long_contract_change = (
                int(long_contract_change) if long_contract_change not in (None, "") else None
            )
            short_contract_change = (
                int(short_contract_change) if short_contract_change not in (None, "") else None
            )

            results[label] = (
                long_pct,
                short_pct,
                report_date,
                long_pct_change,
                long_contract_change,
                short_contract_change,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: failed to fetch {label}: {exc}", file=sys.stderr)
    return results


def build_message(data: dict, missing: list = None) -> str:
    today = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    lines = [f"\U0001F4CA Консенсус крупных трейдеров на {today} (UTC)\n"]
    lines.append("Источник: CFTC Commitment of Traders (данные обновляются раз в неделю, по пятницам)\n")

    if not data:
        lines.append(
            "Не удалось получить данные сегодня. Проверьте лог workflow на GitHub."
        )
        return "\n".join(lines)

    for label, values in data.items():
        (
            long_pct,
            short_pct,
            report_date,
            long_pct_change,
            long_contract_change,
            short_contract_change,
        ) = values
        bias = "большинство LONG" if long_pct > short_pct else "большинство SHORT"

        if long_pct_change is None:
            change_text = "изменение доли недоступно (нет данных за пред. неделю)"
        elif abs(long_pct_change) < 0.5:
            change_text = "доля почти не изменилась к пред. неделе"
        else:
            direction = "рост доли LONG" if long_pct_change > 0 else "рост доли SHORT"
            change_text = f"{direction} на {abs(long_pct_change):.1f} п.п. к пред. неделе"

        def fmt_contract_change(value):
            if value is None:
                return "н/д"
            sign = "+" if value > 0 else ""
            return f"{sign}{value:,}".replace(",", " ")

        lines.append(
            f"{label}: Long {long_pct:.0f}% / Short {short_pct:.0f}%  ({bias})\n"
            f"  (по состоянию на {report_date}, {change_text})\n"
            f"  Изменение контрактов за неделю: Long {fmt_contract_change(long_contract_change)}, "
            f"Short {fmt_contract_change(short_contract_change)}"
        )

    if missing:
        lines.append(
            "\n\u274C Не удалось получить данные: " + ", ".join(missing)
            + ". Запустите workflow вручную чуть позже (Actions → Run workflow), отчёт придёт полностью."
        )

    shown = list(data.keys())
    if any(label.endswith("*") and not label.endswith("**") for label in shown):
        lines.append(
            "\n* Это позиции по фьючерсу на саму валюту: Long = ставка на рост валюты, "
            "то есть против покупки пары (USDJPY/USDCHF/USDCAD вниз). "
            "Для продажи пары большинство Long — «с большинством»."
        )
    if any(label.endswith("**") for label in shown):
        lines.append(
            "** Крипто: у non-commercial много базисных сделок (покупка спота против шорта фьючерса), "
            "поэтому перекос Long/Short здесь слабее связан с ожиданием цены, чем по валютам."
        )

    lines.append(
        "\n\u26A0\uFE0F Это позиционирование крупных спекулянтов (non-commercial) по фьючерсам, "
        "не торговый сигнал и не гарантия направления цены — используйте как один из "
        "контекстных факторов."
    )
    return "\n".join(lines)


def send_telegram_message(text: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set. "
            "Set them as GitHub repo secrets (see README)."
        )
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    resp = requests.post(url, data=payload, timeout=20)
    resp.raise_for_status()


def main() -> None:
    data = fetch_all_markets()

    # A market that failed to load must not vanish silently. Try the missing
    # ones again (the CFTC API is flaky for a minute or two at a time).
    for _ in range(2):
        missing = [label for label in MARKETS if label not in data]
        if not missing:
            break
        time.sleep(30)
        data.update(fetch_all_markets(only=missing))
    missing = [label for label in MARKETS if label not in data]

    if not data:
        print("No data fetched for any market; nothing to send.", file=sys.stderr)
        sys.exit(1)

    # All markets come from the same weekly CFTC report, so their report
    # dates should match. Use the newest one found as "the" report date.
    latest_report_date = max(values[2] for values in data.values())

    last_sent_date = read_last_sent_date()
    force = os.environ.get("FORCE_SEND") == "1"
    if latest_report_date and latest_report_date == last_sent_date and not force:
        print(
            f"Latest CFTC report ({latest_report_date}) was already sent — "
            "no new data yet, skipping Telegram message."
        )
        return

    message = build_message(data, missing)
    send_telegram_message(message)
    if missing:
        # Deliberately NOT marking this report as sent: a manual re-run (or the
        # next scheduled run) should be able to deliver the complete report.
        print(f"Report sent for {latest_report_date} WITHOUT: {missing}; state not updated.")
    else:
        write_last_sent_date(latest_report_date)
        print(f"Report sent for {latest_report_date}.")


if __name__ == "__main__":
    main()
