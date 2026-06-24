"""
Проверяет наличие рейсов на mostanet.ru по заданному маршруту/дате
и шлёт уведомление в Telegram, если рейсы найдены.

Запускается по расписанию через GitHub Actions (см. .github/workflows/check-tickets.yml)
или вручную: python check_tickets.py
"""

import os
import urllib.parse
import urllib.request
from datetime import datetime

from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Настройки маршрута и даты — поменяй на свои
# ---------------------------------------------------------------------------
FROM_PORT = "Корсаков порт"
TO_PORT = "Малокурильское порт"
TARGET_DATE = "28.07.2026"  # формат как на сайте: дд.мм.гггг

# Фрагмент текста, который сайт показывает, когда рейсов нет.
# Если сайт поменяет формулировку — обнови строку ниже.
NO_RESULTS_TEXT = "не найдено"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def send_telegram_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, data=data)
    urllib.request.urlopen(req, timeout=15)


def check_tickets() -> bool:
    """
    Возвращает True, если рейсы найдены (т.е. на странице результатов
    НЕТ текста "не найдено").
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://mostanet.ru/main", wait_until="networkidle")

        # Переключаемся на вкладку "Билет на теплоход" и ищем маршрут
        page.get_by_role("tab", name="Билет на теплоход").click()
        page.get_by_role("textbox").first.click()
        page.get_by_role("textbox").first.fill("кор")
        page.get_by_text("Корсаков порт Сахалинская область Корсаков").click()
        page.get_by_role("textbox").nth(1).click()
        page.get_by_role("textbox").nth(1).fill("м")
        page.get_by_text("Малокурильское порт Сахалинская область").click()
        page.get_by_role("textbox", name="Выберите дату").click()
        page.get_by_role("button", name="Следующий месяц (PageDown)").click()
        page.get_by_text("28").click()
        page.get_by_role("button", name="Найти").click()
        page.wait_for_load_state("networkidle")

        content = page.content()
        browser.close()

        return NO_RESULTS_TEXT not in content


def main():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    found = check_tickets()

    if found:
        send_telegram_message(
            "🚢 Возможно появились билеты!\n"
            f"{FROM_PORT} → {TO_PORT}, {TARGET_DATE}\n"
            f"Проверено: {timestamp}\n"
            "Открыть сайт: https://mostanet.ru/main"
        )
        print("Билеты найдены, уведомление отправлено.")
    else:
        print(f"[{timestamp}] Рейсов пока нет.")


if __name__ == "__main__":
    main()
