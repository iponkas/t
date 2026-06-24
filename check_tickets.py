"""
Проверяет наличие рейсов на mostanet.ru по маршруту/дате из config.json
и шлёт уведомление в Telegram, если рейсы найдены.

Заодно при каждом запуске проверяет, не прислала ли ты команду /menu
или не нажала ли кнопку в Telegram, и обновляет config.json соответственно.

Запускается через GitHub Actions (см. .github/workflows/check-tickets.yml).
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from playwright.sync_api import sync_playwright

CONFIG_PATH = "config.json"
NO_RESULTS_TEXT = "не найдено"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Доступные для выбора порты (одни и те же для "откуда" и "куда")
PORTS = [
    "Корсаков порт",
    "Малокурильское порт",
    "Южно-Курильск порт",
    "Курильск порт",
]


# ---------------------------------------------------------------------------
# Работа с файлом настроек
# ---------------------------------------------------------------------------
def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
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


def answer_callback(callback_query_id: str) -> None:
    telegram_api("answerCallbackQuery", {"callback_query_id": callback_query_id})


def build_from_keyboard() -> dict:
    return {"inline_keyboard": [[{"text": p, "callback_data": f"from:{p}"}] for p in PORTS]}


def build_to_keyboard() -> dict:
    return {"inline_keyboard": [[{"text": p, "callback_data": f"to:{p}"}] for p in PORTS]}


def process_telegram_updates(config: dict) -> dict:
    """Обрабатывает новые сообщения/нажатия кнопок и обновляет config."""
    offset = config.get("telegram_offset", 0)
    result = telegram_api("getUpdates", {"offset": str(offset + 1), "timeout": "0"})

    for update in result.get("result", []):
        config["telegram_offset"] = update["update_id"]

        if "message" in update:
            text = update["message"].get("text", "").strip()

            if text in ("/start", "/menu"):
                send_message("Выбери порт отправления:", reply_markup=build_from_keyboard())

            elif text == "/stop":
                config["active"] = False
                send_message("⏸ Проверка остановлена. Чтобы включить снова — напиши /resume.")

            elif text == "/resume":
                config["active"] = True
                send_message("▶️ Проверка снова включена.")

            elif re.match(r"^\d{2}\.\d{2}\.\d{4}$", text):
                try:
                    datetime.strptime(text, "%d.%m.%Y")
                except ValueError:
                    send_message(
                        "Такой даты не существует. Формат: ДД.MM.ГГГГ, например 28.07.2026."
                    )
                else:
                    config["target_date"] = text
                    send_message(f"✅ Дата обновлена: {text}")

            else:
                send_message(
                    "Не поняла 🙂\n"
                    "/menu — выбрать маршрут (откуда и куда)\n"
                    "Дата в формате ДД.MM.ГГГГ (например 28.07.2026) — обновить дату\n"
                    "/stop — остановить проверку\n"
                    "/resume — включить проверку снова"
                )

        elif "callback_query" in update:
            cq = update["callback_query"]
            data = cq.get("data", "")
            answer_callback(cq["id"])

            if data.startswith("from:"):
                config["from_port"] = data[len("from:"):]
                send_message(
                    f"✅ Отправление: {config['from_port']}\nТеперь выбери порт прибытия:",
                    reply_markup=build_to_keyboard(),
                )

            elif data.startswith("to:"):
                config["to_port"] = data[len("to:"):]
                send_message(
                    f"✅ Маршрут установлен: {config['from_port']} → {config['to_port']}"
                )

            elif data == "stop_checking":
                config["active"] = False
                send_message("⏸ Проверка остановлена. Чтобы включить снова — напиши /resume.")

    return config


# ---------------------------------------------------------------------------
# Проверка билетов на сайте
# ---------------------------------------------------------------------------
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
    config = process_telegram_updates(config)
    save_config(config)

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
