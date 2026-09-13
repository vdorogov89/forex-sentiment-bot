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
scrapes their public "Community Outlook" HTML page. If Myfxbook changes
the structure of that page, the parsing in `fetch_myfxbook_outlook()`
will need to be updated (look for the table with retail long/short %).
"""

import os
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

SYMBOLS = ["EURUSD", "XAUUSD"]

OUTLOOK_URL = "https://www.myfxbook.com/community/outlook"


def fetch_myfxbook_outlook() -> dict:
    """
    Scrapes the public Myfxbook Community Outlook page.
    Returns {symbol: (long_pct, short_pct)} for the symbols we care about.
    Raises RuntimeError if the page structure isn't what we expect.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DailySentimentBot/1.0)"}
    resp = requests.get(OUTLOOK_URL, headers=headers, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results = {}

    # Myfxbook renders the outlook data in a table. We look for rows that
    # start with one of our target symbols and contain two percentage cells.
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        symbol_text = cells[0].get_text(strip=True).upper().replace(" ", "")
        for sym in SYMBOLS:
            if sym in symbol_text:
                long_text = cells[1].get_text(strip=True).replace("%", "")
                short_text = cells[2].get_text(strip=True).replace("%", "")
                try:
                    results[sym] = (float(long_text), float(short_text))
                except ValueError:
                    pass

    if not results:
        raise RuntimeError(
            "Could not parse any symbols from the Myfxbook outlook page — "
            "the page structure has likely changed and the script needs updating."
        )
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
