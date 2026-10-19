import os
import json
import html
import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

HISTORY_FILE = "posted_games.json"
MAX_HISTORY_SIZE = 300  # Храним последние 300 игр, чтобы исключить повторы


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


def get_steam_discounts(posted_ids):
    """Получает новые скидки напрямую из Steam, пропуская уже опубликованные."""
    deals = []
    free_from_steam = []
    new_ids = []

    try:
        url = "https://store.steampowered.com/api/featuredcategories?cc=ru&l=russian"
        response = requests.get(url, timeout=10)
        data = response.json()
        specials = data.get("specials", {}).get("items", [])

        for item in specials:
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
            link = f"https://store.steampowered.com/app/{app_id}/"

            if discount == 100 or (final_price == 0 and original_price > 0):
                free_from_steam.append(
                    f"🎁 <a href='{link}'><b>{name}</b></a> — Бесплатно (вместо {original_price:.0f} ₽)"
                )
                new_ids.append(steam_id)
            elif discount >= 50 and len(deals) < 10:
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
    lines = ["🎮 <b>Свежая подборка скидок и раздач в Steam!</b>\n"]

    if free_games:
        lines.append("🆓 <b>Актуальные раздачи (100% скидка):</b>")
        lines.extend(free_games)
        lines.append("")

    if deals:
        lines.append("📉 <b>Новые скидки дня (от 50%):</b>")
        lines.extend(deals)

    return "\n".join(lines)


def send_to_telegram(text):
    """Отправляет сообщение в Telegram-канал."""
    if not BOT_TOKEN or not CHANNEL_ID:
        raise ValueError("Не заданы BOT_TOKEN или CHANNEL_ID в настройках GitHub Secrets!")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()
    print("Пост успешно опубликован в Telegram!")


if __name__ == "__main__":
    posted_ids = load_posted_games()

    free_giveaways, gp_new_ids = get_free_giveaways(posted_ids)
    free_steam, top_deals, steam_new_ids = get_steam_discounts(posted_ids)

    all_free = free_steam + free_giveaways

    if not all_free and not top_deals:
        print("Новых скидок и раздач с прошлого запуска не найдено. Пост пропущен.")
    else:
        post_text = format_post(all_free, top_deals)
        send_to_telegram(post_text)

        # Записываем новые игры в базу только после успешной отправки поста
        posted_ids.extend(gp_new_ids + steam_new_ids)
        save_posted_games(posted_ids)
        print(f"База обновлена! Всего игр в истории: {len(posted_ids)}")
