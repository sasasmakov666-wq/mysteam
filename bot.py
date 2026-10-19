import os
import json
import html
from datetime import datetime, timezone
import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

HISTORY_FILE = "posted_games.json"
MAX_HISTORY_SIZE = 400       # Сколько последних игр помнить, чтобы не было повторов
DEALS_PER_POST = 3           # По 3 скидки в час, чтобы игр хватало на весь день
MIN_DISCOUNT = 30            # Минимальная скидка в процентах

# True — публиковать и раздачи, и обычные скидки каждый час
# False — проверять 100% раздачи каждый час, а обычные скидки постить только в 08:00 и 14:00 МСК
DISCOUNTS_EVERY_HOUR = True


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


def get_free_giveaways(posted_ids):
    """Ищет новые 100% раздачи игр в Steam через GamerPower API."""
    free_games = []
    new_ids = []
    try:
        url = "https://www.gamerpower.com/api/giveaways?platform=steam&type=game"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            for item in data:
                giveaway_id = f"gp_{item.get('id')}"
                if giveaway_id in posted_ids or giveaway_id in new_ids:
                    continue

                title = html.escape(item.get("title", "Игра в Steam"))
                link = item.get("open_giveaway_url", "")
                worth = item.get("worth", "")
                worth_text = f" (обычно {worth})" if worth and worth != "N/A" else ""

                free_games.append(f"🎁 <a href='{link}'><b>{title}</b></a> — Бесплатно{worth_text}")
                new_ids.append(giveaway_id)

                if len(free_games) >= 5:
                    break
    except Exception as e:
        print(f"Ошибка при проверке раздач: {e}")

    return free_games, new_ids


def get_steam_discounts(posted_ids, include_regular_deals=True):
    """Получает новые раздачи и скидки из всех разделов витрины Steam."""
    deals = []
    free_from_steam = []
    new_ids = []

    try:
        url = "https://store.steampowered.com/api/featuredcategories?cc=ru&l=russian"
        response = requests.get(url, timeout=10)
        data = response.json()

        items = []
        for category in ["specials", "top_sellers", "new_releases"]:
            items.extend(data.get(category, {}).get("items", []))

        for key, value in data.items():
            if key not in ["specials", "top_sellers", "new_releases"] and isinstance(value, dict):
                if "items" in value and isinstance(value["items"], list):
                    items.extend(value["items"])

        for item in items:
            app_id = item.get("id")
            if not app_id:
                continue

            steam_id = f"steam_{app_id}"
            if steam_id in posted_ids or steam_id in new_ids:
                continue

            name = html.escape(item.get("name", "Неизвестная игра"))
            discount = item.get("discount_percent", 0)
            final_price = item.get("final_price", 0) / 100
            original_price = item.get("original_price", 0) / 100

            if original_price <= 0:
                continue

            link = f"https://store.steampowered.com/app/{app_id}/"

            # 100% бесплатные раздачи проверяем всегда каждый час
            if discount == 100 or final_price == 0:
                free_from_steam.append(
                    f"🎁 <a href='{link}'><b>{name}</b></a> — Бесплатно (вместо {original_price:.0f} ₽)"
                )
                new_ids.append(steam_id)
            elif include_regular_deals and discount >= MIN_DISCOUNT and len(deals) < DEALS_PER_POST:
                deals.append(
                    f"🔥 <a href='{link}'><b>{name}</b></a>\n"
                    f"Скидка: <b>-{discount}%</b> | Цена: <s>{original_price:.0f} ₽</s> <b>{final_price:.0f} ₽</b>"
                )
                new_ids.append(steam_id)

    except Exception as e:
        print(f"Ошибка при получении скидок Steam: {e}")

    return free_from_steam, deals, new_ids


def format_post(free_games, deals):
    """Формирует HTML-пост для Telegram."""
    if free_games:
        lines = ["🚨 <b>Обнаружена бесплатная раздача в Steam!</b>\n"]
    else:
        lines = ["🎮 <b>Свежие скидки этого часа в Steam!</b>\n"]

    if free_games:
        lines.append("🆓 <b>Забрать бесплатно (100% скидка):</b>")
        lines.extend(free_games)
        lines.append("")

    if deals:
        lines.append(f"📉 <b>Новые скидки (от {MIN_DISCOUNT}%):</b>")
        lines.extend(deals)

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
        "disable_web_page_preview": True,
    }
    response = requests.post(url, json=payload, timeout=10)

    if not response.ok:
        print(f"Ошибка Telegram API (chat_id={clean_channel_id}): {response.text}")
        response.raise_for_status()

    print("Пост успешно опубликован в Telegram!")


if __name__ == "__main__":
    posted_ids = load_posted_games()
    current_hour_utc = datetime.now(timezone.utc).hour

    # Определяем, нужно ли в этот час брать обычные скидки
    should_post_deals = DISCOUNTS_EVERY_HOUR or (current_hour_utc in [5, 11])

    free_giveaways, gp_new_ids = get_free_giveaways(posted_ids)
    free_steam, top_deals, steam_new_ids = get_steam_discounts(posted_ids, include_regular_deals=should_post_deals)

    all_free = free_steam + free_giveaways

    if not all_free and not top_deals:
        print("Новых раздач и скидок за этот час не появилось. Пост пропущен.")
    else:
        post_text = format_post(all_free, top_deals)
        send_to_telegram(post_text)

        posted_ids.extend(gp_new_ids + steam_new_ids)
        save_posted_games(posted_ids)
        print(f"База обновлена! Добавлено новых игр: {len(gp_new_ids) + len(steam_new_ids)}.")
