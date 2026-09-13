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
"""

import os
import sys
from datetime import datetime, timezone

import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

CFTC_DATASET_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# Display label -> (exact market_and_exchange_names value, fallback substring)
MARKETS = {
    "EURUSD (EUR FX)": (
        "EURO FX - CHICAGO MERCANTILE EXCHANGE",
        "EURO FX",
    ),
    "XAUUSD (GOLD)": (
        "GOLD - COMMODITY EXCHANGE INC.",
        "GOLD",
    ),
}


def fetch_latest_report(exact_name: str, fallback_substring: str) -> dict:
    """
    Queries the CFTC Socrata API for the most recent Legacy Futures-Only
    COT report row for a given market. Tries an exact name match first;
    if that returns nothing (e.g. CFTC tweaked the exact string), falls
    back to a substring match on market_and_exchange_names.
    Returns the raw record dict, or raises RuntimeError if nothing found.
    """
    headers = {"Accept": "application/json"}

    # Attempt 1: exact match
    params = {
        "$where": f"market_and_exchange_names='{exact_name}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": 1,
    }
    resp = requests.get(CFTC_DATASET_URL, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    rows = resp.json()

    if not rows:
        # Attempt 2: fallback substring match
        params = {
            "$where": f"market_and_exchange_names like '%25{fallback_substring}%25'",
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": 1,
        }
        resp = requests.get(CFTC_DATASET_URL, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        rows = resp.json()

    if not rows:
        raise RuntimeError(
            f"No CFTC rows found for '{exact_name}' (or substring '{fallback_substring}') — "
            "CFTC may have renamed this contract."
        )
    return rows[0]


def fetch_all_markets() -> dict:
    """
    Returns {label: (long_pct, short_pct, report_date)} for every market in
    MARKETS. A market is omitted from the result if it couldn't be fetched.
    """
    results = {}
    for label, (exact_name, fallback_substring) in MARKETS.items():
        try:
            row = fetch_latest_report(exact_name, fallback_substring)
            long_n = float(row["noncomm_positions_long_all"])
            short_n = float(row["noncomm_positions_short_all"])
            total = long_n + short_n
            if total == 0:
                raise RuntimeError("long+short positions are both zero")
            long_pct = 100 * long_n / total
            short_pct = 100 * short_n / total
            report_date = row.get("report_date_as_yyyy_mm_dd", "")[:10]
            results[label] = (long_pct, short_pct, report_date)
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: failed to fetch {label}: {exc}", file=sys.stderr)
    return results


def build_message(data: dict) -> str:
    today = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    lines = [f"\U0001F4CA Консенсус крупных трейдеров на {today} (UTC)\n"]
    lines.append("Источник: CFTC Commitment of Traders (данные обновляются раз в неделю, по пятницам)\n")

    if not data:
        lines.append(
            "Не удалось получить данные сегодня. Проверьте лог workflow на GitHub."
        )
        return "\n".join(lines)

    for label, (long_pct, short_pct, report_date) in data.items():
        bias = "большинство LONG" if long_pct > short_pct else "большинство SHORT"
        lines.append(
            f"{label}: Long {long_pct:.0f}% / Short {short_pct:.0f}%  ({bias})\n"
            f"  (по состоянию на {report_date})"
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
    message = build_message(data)
    send_telegram_message(message)
    print("Report sent.")


if __name__ == "__main__":
    main()
