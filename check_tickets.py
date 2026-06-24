"""
Проверяет наличие рейсов на mostanet.ru по маршруту/дате из config.json
и шлёт уведомление в Telegram, если рейсы найдены.

Настройки (маршрут, дата, пауза) обновляет отдельный лёгкий скрипт
bot_listener.py — этот файл их только читает.

Запускается через GitHub Actions (см. .github/workflows/check-tickets.yml).
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from playwright.sync_api import sync_playwright

CONFIG_PATH = "config.json"
NO_RESULTS_TEXT = "не найдено"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def telegram_api(method: str, params: dict) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Telegram API ({method}) вернул ошибку: {body}")
        raise


def send_message(text: str, reply_markup: dict | None = None) -> None:
    params = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if reply_markup is not None:
        params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    telegram_api("sendMessage", params)


def check_tickets(config: dict) -> bool:
    """
    Возвращает True, если рейсы найдены (т.е. на странице результатов
    НЕТ текста "не найдено").
    """
    from_port = config["from_port"]
    to_port = config["to_port"]
    target_date = config["target_date"]

    target_dt = datetime.strptime(target_date, "%d.%m.%Y")
    today = datetime.now()
    months_ahead = (target_dt.year - today.year) * 12 + (target_dt.month - today.month)
    months_ahead = max(months_ahead, 0)
    target_day = str(target_dt.day)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://mostanet.ru/main", wait_until="networkidle")

        try:
            page.get_by_role("tab", name="Билет на теплоход").click()

            page.get_by_role("textbox").first.click()
            page.get_by_role("textbox").first.fill(from_port[:3].lower())
            page.get_by_text(from_port, exact=False).first.click()

            page.get_by_role("textbox").nth(1).click()
            page.get_by_role("textbox").nth(1).fill(to_port[:3].lower())
            page.get_by_text(to_port, exact=False).first.click()

            page.get_by_role("textbox", name="Выберите дату").click()
            for _ in range(months_ahead):
                page.get_by_role("button", name="Следующий месяц (PageDown)").click()
            page.get_by_text(target_day, exact=True).first.click()

            page.get_by_role("button", name="Найти").click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)

            content = page.content()
        finally:
            page.screenshot(path="debug.png", full_page=True)
            browser.close()

        return NO_RESULTS_TEXT not in content


def main():
    config = load_config()

    if not config.get("active", True):
        print("Проверка приостановлена (/stop). Захода на сайт не будет.")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    found = check_tickets(config)

    if found:
        send_message(
            "🚢 Возможно появились билеты!\n"
            f"{config['from_port']} → {config['to_port']}, {config['target_date']}\n"
            f"Проверено: {timestamp}\n"
            "Открыть сайт: https://mostanet.ru/main",
            reply_markup={
                "inline_keyboard": [[{"text": "⏸ Остановить проверку", "callback_data": "stop_checking"}]]
            },
        )
        print("Билеты найдены, уведомление отправлено.")
    else:
        print(
            f"[{timestamp}] Рейсов пока нет "
            f"({config['from_port']} → {config['to_port']}, {config['target_date']})."
        )


if __name__ == "__main__":
    main()
