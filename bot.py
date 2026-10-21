import os
import re
import sys
import json
import time
import html
import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

HISTORY_FILE = "posted_games.json"
MAX_HISTORY_SIZE = 2000  # Сколько последних игр помнить, чтобы не было повторов

# --- Что отслеживаем ---
ALLOW_DEALS = True  # Обычные скидки (от MIN_DISCOUNT %)
MIN_DISCOUNT = 30  # Минимальная скидка в процентах
SHOW_FREE_TO_PLAY = True  # Новые бесплатные игры (Free to Play)
# Сортировка поиска Steam: "Released_DESC" — самые свежие, "Reviews_DESC" — самые популярные
F2P_SORT = "Released_DESC"

# --- Лимиты за один запуск (защита от спама в канале) ---
GIVEAWAYS_PER_RUN = 5
F2P_PER_RUN = 3
DEALS_PER_RUN = 5
MAX_MESSAGES_PER_RUN = 10  # Остальное уйдёт при следующей проверке
SEND_DELAY = 3  # Пауза между сообщениями, секунд (лимит Telegram для каналов)

# --- Режим постоянной работы (python bot.py --loop), например на сервере ---
CHECK_INTERVAL = 120  # Как часто проверять Steam, секунд


def load_posted_games():
    """Загружает список уже опубликованных игр из файла."""
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
    """Сохраняет обновлённый список ID игр в файл."""
    trimmed_ids = posted_ids[-MAX_HISTORY_SIZE:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed_ids, f, ensure_ascii=False, indent=2)


def get_free_giveaways(posted):
    """Новые 100% раздачи игр в Steam через GamerPower API. Возвращает [(id, текст)]."""
    result = []
    try:
        url = "https://www.gamerpower.com/api/giveaways?platform=steam&type=game"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            for item in response.json():
                giveaway_id = f"gp_{item.get('id')}"
                if giveaway_id in posted:
                    continue

                title = html.escape(item.get("title", "Игра в Steam"))
                link = item.get("open_giveaway_url", "")
                worth = item.get("worth", "")
                worth_text = f" (обычно {worth})" if worth and worth != "N/A" else ""

                text = (
                    "🚨 <b>Бесплатная раздача в Steam!</b>\n"
                    f"🎁 <a href='{link}'><b>{title}</b></a> — Бесплатно{worth_text}"
                )
                result.append((giveaway_id, text))
                if len(result) >= GIVEAWAYS_PER_RUN:
                    break
    except Exception as e:
        print(f"Ошибка при проверке раздач: {e}")
    return result


def get_new_free_to_play(posted):
    """Недавно вышедшие бесплатные игры (Free to Play) через поиск Steam."""
    result = []
    try:
        url = "https://store.steampowered.com/search/results/"
        params = {
            "query": "",
            "start": 0,
            "count": 50,
            "maxprice": "free",  # только бесплатные
            "category1": 998,  # только игры (без DLC, софта и т.п.)
            "sort_by": F2P_SORT,
            "infinite": 1,
            "cc": "ru",
            "l": "russian",
        }
        response = requests.get(
            url, params=params, timeout=15, headers={"User-Agent": "Mozilla/5.0"}
        )
        response.raise_for_status()
        results_html = response.json().get("results_html", "")

        rows = re.findall(r'data-ds-appid="(\d+)"[^>]*>(.*?)</a>', results_html, re.DOTALL)
        for app_id, row_html in rows:
            steam_id = f"steam_{app_id}"
            if steam_id in posted:
                continue

            title_match = re.search(r'<span class="title">(.*?)</span>', row_html, re.DOTALL)
            if not title_match:
                continue
            name = html.escape(html.unescape(title_match.group(1)).strip())
            if not name:
                continue

            link = f"https://store.steampowered.com/app/{app_id}/"
            text = (
                "🆕 <b>Новая бесплатная игра в Steam!</b>\n"
                f"<a href='{link}'><b>{name}</b></a> — Free to Play"
            )
            result.append((steam_id, text))
            if len(result) >= F2P_PER_RUN:
                break
    except Exception as e:
        print(f"Ошибка при поиске новых бесплатных игр: {e}")
    return result


def get_steam_discounts(posted, include_regular_deals=True):
    """Раздачи (100%) и скидки с витрины Steam. Возвращает (раздачи, скидки) как [(id, текст)]."""
    free_from_steam = []
    deals = []

    try:
        url = "https://store.steampowered.com/api/featuredcategories?cc=ru&l=russian"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        items = []
        for category in ["specials", "top_sellers", "new_releases"]:
            items.extend(data.get(category, {}).get("items", []))

        for key, value in data.items():
            if key not in ["specials", "top_sellers", "new_releases"] and isinstance(value, dict):
                if "items" in value and isinstance(value["items"], list):
                    items.extend(value["items"])

        seen_here = set()
        for item in items:
            app_id = item.get("id")
            if not app_id:
                continue

            steam_id = f"steam_{app_id}"
            if steam_id in posted or steam_id in seen_here:
                continue

            name = html.escape(item.get("name", "Неизвестная игра"))
            discount = item.get("discount_percent", 0)
            final_price = item.get("final_price", 0) / 100
            original_price = item.get("original_price", 0) / 100

            if original_price <= 0:
                continue

            link = f"https://store.steampowered.com/app/{app_id}/"

            if discount == 100 or final_price == 0:
                text = (
                    "🚨 <b>Бесплатная раздача в Steam!</b>\n"
                    f"🎁 <a href='{link}'><b>{name}</b></a> — Бесплатно (вместо {original_price:.0f} ₽)"
                )
                free_from_steam.append((steam_id, text))
                seen_here.add(steam_id)
            elif include_regular_deals and discount >= MIN_DISCOUNT and len(deals) < DEALS_PER_RUN:
                text = (
                    "🔥 <b>Новая скидка в Steam!</b>\n"
                    f"<a href='{link}'><b>{name}</b></a>\n"
                    f"Скидка: <b>-{discount}%</b> | Цена: <s>{original_price:.0f} ₽</s> <b>{final_price:.0f} ₽</b>"
                )
                deals.append((steam_id, text))
                seen_here.add(steam_id)

    except Exception as e:
        print(f"Ошибка при получении скидок Steam: {e}")

    return free_from_steam, deals


def collect_new_items(posted_ids):
    """Собирает все новые события в порядке важности: раздачи, новые F2P, скидки."""
    posted = set(posted_ids)
    giveaways = get_free_giveaways(posted)
    free_steam, deals = get_steam_discounts(posted, include_regular_deals=ALLOW_DEALS)
    f2p = get_new_free_to_play(posted) if SHOW_FREE_TO_PLAY else []

    items = []
    seen = set()
    for item_id, text in free_steam + giveaways + f2p + deals:
        if item_id in seen:
            continue
        seen.add(item_id)
        items.append((item_id, text))
    return items


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
        "disable_web_page_preview": True,
    }

    for attempt in range(2):
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 429 and attempt == 0:
            # Telegram просит подождать — ждём и пробуем ещё раз
            try:
                wait = int(response.json().get("parameters", {}).get("retry_after", 5))
            except Exception:
                wait = 5
            print(f"Telegram просит подождать {wait} с...")
            time.sleep(wait + 1)
            continue
        if not response.ok:
            print(f"Ошибка Telegram API (chat_id={clean_channel_id}): {response.text}")
        response.raise_for_status()
        return


def run_once():
    """Одна проверка: каждое новое событие уходит в канал отдельным сообщением сразу."""
    posted_ids = load_posted_games()
    items = collect_new_items(posted_ids)

    if not items:
        print("Новых раздач, скидок и бесплатных игр не появилось.")
        return 0

    sent = 0
    for item_id, text in items[:MAX_MESSAGES_PER_RUN]:
        try:
            send_to_telegram(text)
        except Exception as e:
            print(f"Не удалось отправить сообщение: {e}")
            break
        # Запоминаем сразу после отправки, чтобы при сбое не было повторов
        posted_ids.append(item_id)
        save_posted_games(posted_ids)
        sent += 1
        time.sleep(SEND_DELAY)

    print(f"Отправлено сообщений: {sent}. Ждёт следующей проверки: {max(len(items) - sent, 0)}.")
    return sent


if __name__ == "__main__":
    if "--loop" in sys.argv:
        print(f"Режим постоянной работы: проверка каждые {CHECK_INTERVAL} с.")
        while True:
            try:
                run_once()
            except Exception as e:
                print(f"Ошибка в цикле: {e}")
            time.sleep(CHECK_INTERVAL)
    else:
        run_once()
