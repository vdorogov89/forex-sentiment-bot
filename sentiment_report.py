"""
Daily forex/gold sentiment report -> Telegram bot.

What it does:
1. Fetches retail trader positioning (Long % / Short %) for EURUSD and
   XAUUSD from Myfxbook's public Community Outlook page.
2. Formats a short summary message.
3. Sends it to your Telegram chat via the Telegram Bot API.

Requirements (installed automatically by the GitHub Actions workflow):
    pip install requests beautifulsoup4

Environment variables required:
    TELEGRAM_BOT_TOKEN  - token from @BotFather
    TELEGRAM_CHAT_ID    - your personal or group chat id

NOTE ON RELIABILITY:
Myfxbook does not offer a free public API for this data, so this script
scrapes the public per-symbol Myfxbook outlook pages (e.g.
myfxbook.com/community/outlook/EURUSD), reading the plain-text summary
sentence on each page ("NN% ... going short ... NN% ... going long").
If Myfxbook changes that wording, `SENTIMENT_PATTERN` in this file will
need to be updated to match the new phrasing.
"""

import os
import re
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

SYMBOLS = ["EURUSD", "XAUUSD"]

# Myfxbook's aggregate /community/outlook page is rendered client-side (JS),
# so it has no static table to scrape. Each symbol's own page, however,
# includes a plain-text summary sentence like:
#   "60% of the forex traders are currently going short with EUR/USD,
#    ... meanwhile 40% ... are going long with EUR/USD, ..."
# That sentence is what we parse — it's simpler and more stable than the
# HTML table markup on the same page.
SYMBOL_URL_TEMPLATE = "https://www.myfxbook.com/community/outlook/{symbol}"

SENTIMENT_PATTERN = re.compile(
    r"(\d+)\s*%\s*of the forex traders are currently going short.*?"
    r"(\d+)\s*%\s*of the forex traders are going long",
    re.IGNORECASE | re.DOTALL,
)


def fetch_symbol_sentiment(symbol: str) -> tuple:
    """
    Fetches one symbol's Myfxbook outlook page and extracts (long_pct, short_pct)
    from the plain-text summary sentence on the page.
    Raises RuntimeError if the sentence can't be found (site structure changed).
    """
    url = SYMBOL_URL_TEMPLATE.format(symbol=symbol)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DailySentimentBot/1.0)"}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    match = SENTIMENT_PATTERN.search(page_text)
    if not match:
        raise RuntimeError(
            f"Could not find the sentiment sentence for {symbol} — "
            "Myfxbook may have changed their page wording/structure."
        )

    short_pct = float(match.group(1))
    long_pct = float(match.group(2))
    return long_pct, short_pct


def fetch_myfxbook_outlook() -> dict:
    """
    Returns {symbol: (long_pct, short_pct)} for every symbol in SYMBOLS.
    A symbol is simply omitted from the result if it couldn't be fetched —
    the caller reports "no data" for that symbol rather than failing entirely.
    """
    results = {}
    for symbol in SYMBOLS:
        try:
            results[symbol] = fetch_symbol_sentiment(symbol)
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: failed to fetch {symbol}: {exc}", file=sys.stderr)
    return results


def build_message(data: dict) -> str:
    today = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    lines = [f"\U0001F4CA Консенсус трейдеров на {today} (UTC)\n"]

    if not data:
        lines.append(
            "Не удалось получить данные сегодня — источник мог изменить структуру страницы. "
            "Проверьте лог workflow на GitHub."
        )
        return "\n".join(lines)

    for symbol in SYMBOLS:
        if symbol in data:
            long_pct, short_pct = data[symbol]
            bias = "большинство LONG" if long_pct > short_pct else "большинство SHORT"
            lines.append(
                f"{symbol}: Long {long_pct:.0f}% / Short {short_pct:.0f}%  ({bias})"
            )
        else:
            lines.append(f"{symbol}: нет данных")

    lines.append(
        "\n\u26A0\uFE0F Это розничный (retail) консенсус трейдеров, а не торговый сигнал. "
        "Розничная толпа нередко ошибается на разворотах — используйте как один из "
        "контекстных факторов, а не как самостоятельный триггер для сделки."
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
    try:
        data = fetch_myfxbook_outlook()
    except Exception as exc:  # noqa: BLE001 - we want to report any failure, then still notify
        print(f"Error fetching sentiment data: {exc}", file=sys.stderr)
        data = {}

    message = build_message(data)
    send_telegram_message(message)
    print("Report sent.")


if __name__ == "__main__":
    main()
