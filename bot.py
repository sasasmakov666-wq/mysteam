import os
import json
import html
import time
from datetime import datetime, timezone

import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

HISTORY_FILE = "posted_games.json"
MAX_HISTORY_SIZE = 400       # Сколько последних ID помнить, чтобы не было повторов

MAX_FREE = 5                 # Максимум бесплатных раздач в одном посте
DEALS_PER_POST = 3           # Максимум скидок в одном посте
MIN_DISCOUNT = 30            # Минимальная скидка в процентах
MAX_CANDIDATES_TO_CHECK = 30 # Сколько игр проверять на карточки за один запуск (лимит запросов Steam)
REQUEST_DELAY = 1.5          # Пауза между запросами к Steam Store, чтобы не получить ошибку 429

CARD_CATEGORY_ID = 29        # Категория "Steam Trading Cards" в appdetails

# True  — публиковать и раздачи, и скидки каждый час
# False — раздачи каждый час, скидки только в 08:00 и 14:00 МСК (05:00 и 11:00 UTC)
DISCOUNTS_EVERY_HOUR = True


def load_posted_games():
    """Загружает список уже обработанных игр из файла."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception as e:
            print(f"Ошибка чтения {HISTORY_FILE}: {e}")
    return []


def save_posted_games(posted_ids):
    """Сохраняет список ID в файл, обрезая старые записи."""
    trimmed_ids = posted_ids[-MAX_HISTORY_SIZE:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed_ids, f, ensure_ascii=False, indent=2)


def fetch_steam_candidates(posted_ids):
    """Собирает кандидатов из витрины Steam, исключая уже обработанные игры."""
    candidates = []
    seen = set()

    try:
        url = "https://store.steampowered.com/api/featuredcategories?cc=ru&l=russian"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Ошибка при получении витрины Steam: {e}")
        return candidates

    items = []
    for value in data.values():
        if isinstance(value, dict) and isinstance(value.get("items"), list):
            items.extend(value["items"])

    for item in items:
        app_id = item.get("id")
        if not app_id:
            continue

        steam_id = f"steam_{app_id}"
        if steam_id in posted_ids or steam_id in seen:
            continue
        seen.add(steam_id)

        original = item.get("original_price", 0) / 100
        final = item.get("final_price", 0) / 100
        discount = item.get("discount_percent", 0)

        if original <= 0:
            continue

        candidates.append({
            "app_id": app_id,
            "steam_id": steam_id,
            "name": item.get("name", "Неизвестная игра"),
            "discount": discount,
            "final": final,
            "original": original,
            "free": discount == 100 or final == 0,
        })

    # Сначала бесплатные, потом по величине скидки
    candidates.sort(key=lambda c: (not c["free"], -c["discount"]))
    return candidates


def get_app_details(app_id):
    """Получает подробные данные об игре из Steam Store. Возвращает None при ошибке."""
    url = "https://store.steampowered.com/api/appdetails"
    params = {"appids": app_id, "cc": "ru", "l": "russian"}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            print(f"appdetails для {app_id}: статус {response.status_code}")
            return None
        payload = response.json().get(str(app_id), {})
        if not payload.get("success"):
            return None
        return payload.get("data")
    except Exception as e:
        print(f"Ошибка appdetails для {app_id}: {e}")
        return None


def has_trading_cards(details):
    """Проверяет, есть ли у игры торговые карточки Steam."""
    return any(c.get("id") == CARD_CATEGORY_ID for c in details.get("categories", []))


def get_card_games(posted_ids, include_regular_deals):
    """
    Возвращает (бесплатные игры с карточками, скидки на игры с карточками, проверенные ID).
    Проверенные ID сохраняются, чтобы не проверять одну и ту же игру каждый час.
    """
    free_games = []
    deals = []
    checked_ids = []
    checked_count = 0

    for cand in fetch_steam_candidates(posted_ids):
        if len(free_games) >= MAX_FREE and len(deals) >= DEALS_PER_POST:
            break
        if checked_count >= MAX_CANDIDATES_TO_CHECK:
            break

        wanted_free = cand["free"] and len(free_games) < MAX_FREE
        wanted_deal = (
            not cand["free"]
            and include_regular_deals
            and cand["discount"] >= MIN_DISCOUNT
            and len(deals) < DEALS_PER_POST
        )
        if not (wanted_free or wanted_deal):
            continue

        details = get_app_details(cand["app_id"])
        time.sleep(REQUEST_DELAY)
        checked_count += 1

        if details is None:
            continue  # ошибка запроса: не помечаем как проверенную, попробуем в следующий раз

        checked_ids.append(cand["steam_id"])

        if not has_trading_cards(details):
            continue

        game = {
            "name": html.escape(cand["name"]),
            "link": html.escape(f"https://store.steampowered.com/app/{cand['app_id']}/", quote=True),
            "discount": cand["discount"],
            "final": cand["final"],
            "original": cand["original"],
        }
        if cand["free"]:
            free_games.append(game)
        else:
            deals.append(game)

    return free_games, deals, checked_ids


def format_post(free_games, deals):
    """Формирует HTML-пост для Telegram."""
    lines = []

    if free_games:
        lines.append("🚨 <b>Бесплатная раздача игры с карточками Steam!</b>\n")
        lines.append("🆓 <b>Забрать бесплатно (для значка коллекционера):</b>")
        for g in free_games:
            lines.append(
                f"🃏 <a href='{g['link']}'><b>{g['name']}</b></a> — "
                f"Бесплатно (вместо {g['original']:.0f} ₽)"
            )
        lines.append("")
    else:
        lines.append("🃏 <b>Скидки на игры с карточками Steam!</b>\n")

    if deals:
        lines.append(f"📉 <b>Скидки от {MIN_DISCOUNT}% (для значка коллекционера):</b>")
        for g in deals:
            lines.append(
                f"🔥 <a href='{g['link']}'><b>{g['name']}</b></a>\n"
                f"Скидка: <b>-{g['discount']}%</b> | "
                f"Цена: <s>{g['original']:.0f} ₽</s> <b>{g['final']:.0f} ₽</b>"
            )

    return "\n".join(lines)


def send_to_telegram(text):
    """Отправляет сообщение в Telegram-канал."""
    if not BOT_TOKEN or not CHANNEL_ID:
        raise ValueError("Не заданы BOT_TOKEN или CHANNEL_ID в настройках GitHub Secrets!")

    clean_channel_id = CHANNEL_ID.strip()
    if "t.me/" in clean_channel_id:
        clean_channel_id = "@" + clean_channel_id.split("t.me/")[-1].split("/")[0]
    elif not clean_channel_id.startswith("@") and not clean_channel_id.lstrip("-").isdigit():
        clean_channel_id = "@" + clean_channel_id

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": clean_channel_id,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    response = requests.post(url, json=payload, timeout=10)
    if not response.ok:
        print(f"Ошибка Telegram API (chat_id={clean_channel_id}): {response.text}")
        response.raise_for_status()
    print("Пост успешно опубликован в Telegram!")


if __name__ == "__main__":
    posted_ids = load_posted_games()
    current_hour_utc = datetime.now(timezone.utc).hour
    should_post_deals = DISCOUNTS_EVERY_HOUR or current_hour_utc in (5, 11)

    free_games, deals, checked_ids = get_card_games(posted_ids, should_post_deals)

    if not free_games and not deals:
        print("Игр с карточками за этот запуск не найдено. Пост пропущен.")
    else:
        send_to_telegram(format_post(free_games, deals))

    # Сохраняем только после успешной отправки (или если постить было нечего)
    posted_ids.extend(checked_ids)
    save_posted_games(posted_ids)
    print(f"База обновлена! Добавлено ID: {len(checked_ids)}.")
