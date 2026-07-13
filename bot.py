"""
LEVEL UP — Telegram-бот для мероприятий
=======================================
Что умеет:
- Подтягивает контакты из Google-таблицы
- Собирает chat_id при /start (без него Telegram не даёт писать)
- Рассылает напоминалки с кнопками ИДУ / НЕ СМОГУ
- Фиксирует ответы
- Веб-страница со статистикой + кто ещё не подключился

Подготовка Google-таблицы:
1. Создайте таблицу с колонками:  Имя | Username
   (username без @, например: ivan_petrov)
2. Файл → Поделиться → Опубликовать в интернете → CSV → Опубликовать
3. Скопируйте ссылку и вставьте в SHEET_CSV_URL ниже

Запуск:  pip install flask requests  →  python bot.py
"""

import os
import io
import csv
import json
import sqlite3
import threading
import time
import requests
from datetime import datetime
from flask import Flask, jsonify, render_template_string

# ═══════════════════════════════════════════
# НАСТРОЙКИ — ЗАМЕНИТЕ НА СВОИ
# ═══════════════════════════════════════════
BOT_TOKEN = "8898623835:AAGqfdD2vNcH4kWfZfqEV4PgufezS9R5Xwk"
ADMIN_CHAT_ID = 173317122

# Ссылка на Google-таблицу, опубликованную как CSV
# Формат: https://docs.google.com/spreadsheets/d/e/XXXX/pub?output=csv
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1FEV3V5zjDQ7D8yGfMMhocQjpPjgJ6pHF7rqT2hDIP5c/export?format=csv&gid=0"

# Ссылка на бота (для приглашений)
BOT_LINK = f"https://t.me/{BOT_TOKEN.split(':')[0]}"

# Данные мероприятия
EVENT = {
    "name": "СТИНТ: отрабатываем клиента на новом уровне. Кому продавать сегодня? Где и как быстрее находить своих клиентов?",
    "date": "20 июля",
    "time": "18:00",
    "place": "Азимут, ул. Ленина 21",
    "club": "Мужской клуб предпринимателей Level Up"
}

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_FILE = "levelup_bot.db"
WEB_PORT = int(os.environ.get("PORT", 5000))

# ═══════════════════════════════════════════
# БАЗА ДАННЫХ
# ═══════════════════════════════════════════
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Контакты из Google-таблицы
    c.execute("""CREATE TABLE IF NOT EXISTS contacts (
        username TEXT PRIMARY KEY,
        name TEXT
    )""")
    # Активированные пользователи (написали /start боту)
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        first_name TEXT,
        last_name TEXT,
        username TEXT,
        joined_at TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
    # Ответы
    c.execute("""CREATE TABLE IF NOT EXISTS rsvp (
        chat_id INTEGER PRIMARY KEY,
        status TEXT,
        responded_at TEXT,
        first_name TEXT,
        username TEXT
    )""")
    conn.commit()
    conn.close()

def sync_google_sheet():
    """Подтягивает контакты из Google-таблицы"""
    if not SHEET_CSV_URL:
        return 0, "URL таблицы не указан"
    try:
        r = requests.get(SHEET_CSV_URL, timeout=10)
        r.encoding = "utf-8"
        reader = csv.reader(io.StringIO(r.text))
        header = next(reader, None)  # пропускаем заголовок
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        count = 0
        for row in reader:
            if len(row) >= 2:
                name = row[0].strip()
                username = row[1].strip().replace("@", "").lower()
                if username:
                    c.execute("INSERT OR REPLACE INTO contacts (username, name) VALUES (?, ?)",
                              (username, name))
                    count += 1
        conn.commit()
        conn.close()
        return count, "OK"
    except Exception as e:
        return 0, str(e)

def add_user(chat_id, first_name, last_name, username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO users (chat_id, first_name, last_name, username, joined_at)
                 VALUES (?, ?, ?, ?, ?)""",
              (chat_id, first_name, last_name,
               (username or "").lower(), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_all_active_users():
    """Все, кто написал боту /start"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT chat_id, first_name, last_name, username FROM users")
    users = c.fetchall()
    conn.close()
    return users

def save_rsvp(chat_id, status, first_name, username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO rsvp (chat_id, status, responded_at, first_name, username)
                 VALUES (?, ?, ?, ?, ?)""",
              (chat_id, status, datetime.now().isoformat(), first_name, username))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Контакты из таблицы
    c.execute("SELECT COUNT(*) FROM contacts")
    total_contacts = c.fetchone()[0]
    # Активированные (написали боту)
    c.execute("SELECT COUNT(*) FROM users")
    total_active = c.fetchone()[0]
    # Контакты из таблицы, которые ещё НЕ активировались
    c.execute("""SELECT c.name, c.username FROM contacts c
                 LEFT JOIN users u ON LOWER(c.username) = LOWER(u.username)
                 WHERE u.chat_id IS NULL""")
    not_activated = [{"name": r[0], "username": r[1]} for r in c.fetchall()]
    # RSVP
    c.execute("SELECT COUNT(*) FROM rsvp WHERE status='going'")
    going = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM rsvp WHERE status='not_going'")
    not_going = c.fetchone()[0]
    # Детальные ответы
    c.execute("""SELECT first_name, username, status, responded_at
                 FROM rsvp ORDER BY responded_at DESC""")
    responses = [{"name": r[0], "username": r[1], "status": r[2], "time": r[3]}
                 for r in c.fetchall()]
    conn.close()
    return {
        "total_contacts": total_contacts,
        "total_active": total_active,
        "not_activated": not_activated,
        "not_activated_count": len(not_activated),
        "going": going,
        "not_going": not_going,
        "no_response": total_active - going - not_going,
        "responses": responses,
        "event": EVENT,
        "bot_link": BOT_LINK
    }

# ═══════════════════════════════════════════
# TELEGRAM API
# ═══════════════════════════════════════════
def send_message(chat_id, text, reply_markup=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(f"{API}/sendMessage", json=data, timeout=10)
        return r.json()
    except:
        return {"ok": False}

def answer_callback(callback_query_id, text):
    try:
        requests.post(f"{API}/answerCallbackQuery", json={
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": True
        }, timeout=10)
    except:
        pass

def get_bot_username():
    try:
        r = requests.get(f"{API}/getMe", timeout=10)
        data = r.json()
        if data.get("ok"):
            return data["result"].get("username", "")
    except:
        pass
    return ""

def broadcast_reminder():
    """Рассылка всем активированным пользователям"""
    # Сначала синхронизируем таблицу
    sync_google_sheet()
    users = get_all_active_users()
    text = (
        f"🔔 <b>Напоминание о встрече</b>\n\n"
        f"📌 <b>{EVENT['name']}</b>\n"
        f"📅 {EVENT['date']} в {EVENT['time']}\n"
        f"📍 {EVENT['place']}\n"
        f"🏛 {EVENT['club']}\n\n"
        f"Вы придёте?"
    )
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ ИДУ НА ВСТРЕЧУ", "callback_data": "rsvp_going"},
            {"text": "❌ НЕ СМОГУ", "callback_data": "rsvp_not_going"}
        ]]
    }
    sent = 0
    errors = 0
    for user in users:
        chat_id = user[0]
        if chat_id == ADMIN_CHAT_ID:
            continue  # не шлём самому себе
        result = send_message(chat_id, text, keyboard)
        if result.get("ok"):
            sent += 1
        else:
            errors += 1
        time.sleep(0.05)
    return sent, errors

# ═══════════════════════════════════════════
# ОБРАБОТКА ОБНОВЛЕНИЙ
# ═══════════════════════════════════════════
def handle_update(update):
    # Кнопки
    if "callback_query" in update:
        cb = update["callback_query"]
        chat_id = cb["from"]["id"]
        first_name = cb["from"].get("first_name", "")
        username = cb["from"].get("username", "")
        data = cb.get("data", "")

        if data == "rsvp_going":
            save_rsvp(chat_id, "going", first_name, username)
            answer_callback(cb["id"], "🎉 Отлично! Вы записаны на встречу!")
            send_message(chat_id,
                f"✅ <b>Вы записаны!</b>\n\n"
                f"📅 {EVENT['date']} в {EVENT['time']}\n"
                f"📍 {EVENT['place']}\n\nДо встречи!")
            send_message(ADMIN_CHAT_ID,
                f"✅ <b>{first_name}</b> (@{username}) идёт на встречу!")

        elif data == "rsvp_not_going":
            save_rsvp(chat_id, "not_going", first_name, username)
            answer_callback(cb["id"], "Жаль! Ждём на следующей встрече.")
            send_message(chat_id,
                "Понял, в этот раз не получится. "
                "Будем рады видеть вас на следующей встрече! 🤝")
            send_message(ADMIN_CHAT_ID,
                f"❌ <b>{first_name}</b> (@{username}) не сможет прийти.")
        return

    # Сообщения
    msg = update.get("message")
    if not msg:
        return

    chat_id = msg["chat"]["id"]
    first_name = msg["from"].get("first_name", "")
    last_name = msg["from"].get("last_name", "")
    username = msg["from"].get("username", "")
    text = msg.get("text", "")

    add_user(chat_id, first_name, last_name, username)

    if text == "/start":
        send_message(chat_id,
            f"👋 Привет, <b>{first_name}</b>!\n\n"
            f"Это бот <b>{EVENT['club']}</b>.\n\n"
            f"Ближайшее мероприятие:\n"
            f"📌 <b>{EVENT['name']}</b>\n"
            f"📅 {EVENT['date']} в {EVENT['time']}\n"
            f"📍 {EVENT['place']}\n\n"
            f"Вы планируете прийти?",
            {"inline_keyboard": [[
                {"text": "✅ ИДУ НА ВСТРЕЧУ", "callback_data": "rsvp_going"},
                {"text": "❌ НЕ СМОГУ", "callback_data": "rsvp_not_going"}
            ]]}
        )

    elif text == "/broadcast" and chat_id == ADMIN_CHAT_ID:
        send_message(chat_id, "📤 Начинаю рассылку...")
        sent, errors = broadcast_reminder()
        stats = get_stats()
        msg_text = (
            f"✅ <b>Рассылка завершена!</b>\n\n"
            f"📨 Отправлено: {sent}\n"
            f"⚠️ Ошибок: {errors}"
        )
        if stats["not_activated"]:
            msg_text += (
                f"\n\n⏳ <b>Ещё не подключились к боту ({stats['not_activated_count']}):</b>\n"
            )
            for c in stats["not_activated"][:20]:
                msg_text += f"  • {c['name']} — @{c['username']}\n"
            msg_text += f"\nОтправьте им ссылку на бота, чтобы они могли получать рассылки."
        send_message(chat_id, msg_text)

    elif text == "/stats" and chat_id == ADMIN_CHAT_ID:
        stats = get_stats()
        send_message(chat_id,
            f"📊 <b>Статистика</b>\n\n"
            f"📋 В Google-таблице: {stats['total_contacts']}\n"
            f"🤖 Подключились к боту: {stats['total_active']}\n"
            f"⏳ Ещё не подключились: {stats['not_activated_count']}\n\n"
            f"✅ Идут: {stats['going']}\n"
            f"❌ Не смогут: {stats['not_going']}\n"
            f"🤷 Не ответили: {stats['no_response']}"
        )

    elif text == "/sync" and chat_id == ADMIN_CHAT_ID:
        count, status = sync_google_sheet()
        if status == "OK":
            stats = get_stats()
            send_message(chat_id,
                f"✅ Таблица синхронизирована!\n\n"
                f"📋 Контактов в базе: {count}\n"
                f"🤖 Из них подключились к боту: {stats['total_active']}\n"
                f"⏳ Ещё не подключились: {stats['not_activated_count']}")
        elif status == "URL таблицы не указан":
            send_message(chat_id,
                "⚠️ URL Google-таблицы не указан.\n\n"
                "Откройте bot.py и впишите ссылку в SHEET_CSV_URL.")
        else:
            send_message(chat_id, f"❌ Ошибка синхронизации: {status}")

    elif text == "/link" and chat_id == ADMIN_CHAT_ID:
        bot_user = get_bot_username()
        link = f"https://t.me/{bot_user}" if bot_user else "(не удалось получить)"
        send_message(chat_id,
            f"🔗 <b>Ссылка на бота для приглашений:</b>\n\n"
            f"{link}\n\n"
            f"Отправьте эту ссылку контактам из таблицы. "
            f"Как только они нажмут /start — смогут получать рассылки.")

    elif text == "/help":
        help_text = "📋 <b>Команды:</b>\n\n/start — О ближайшей встрече\n"
        if chat_id == ADMIN_CHAT_ID:
            help_text += (
                "\n<b>Админ-команды:</b>\n"
                "/sync — Синхронизировать Google-таблицу\n"
                "/broadcast — Разослать напоминание\n"
                "/stats — Статистика ответов\n"
                "/link — Ссылка на бота для приглашений\n"
            )
        send_message(chat_id, help_text)

    else:
        if chat_id != ADMIN_CHAT_ID:
            send_message(ADMIN_CHAT_ID,
                f"💬 Сообщение от <b>{first_name}</b> "
                f"(@{username}):\n\n{text}")
            send_message(chat_id,
                "Спасибо! Ваше сообщение передано организатору. 🤝")

def poll_updates():
    offset = 0
    while True:
        try:
            r = requests.get(f"{API}/getUpdates",
                             params={"offset": offset, "timeout": 30},
                             timeout=35)
            data = r.json()
            if data.get("ok"):
                for update in data["result"]:
                    offset = update["update_id"] + 1
                    try:
                        handle_update(update)
                    except Exception as e:
                        print(f"Ошибка обработки: {e}")
        except Exception as e:
            print(f"Ошибка polling: {e}")
            time.sleep(5)

# ═══════════════════════════════════════════
# ВЕБ-СТРАНИЦА СО СТАТИСТИКОЙ
# ═══════════════════════════════════════════
app = Flask(__name__)

STATS_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Статистика — {{ event.name }}</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box;}
  body{background:#0A0D24;color:#fff;font-family:'Segoe UI',system-ui,sans-serif;padding:40px 20px;}
  .wrap{max-width:900px;margin:0 auto;}
  h1{font-size:28px;font-weight:800;margin-bottom:8px;}
  .sub{color:#A6ACD4;font-size:15px;margin-bottom:40px;}
  .cards{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:40px;}
  .card{background:#161C46;border:1px solid #33397A;border-radius:16px;padding:22px 16px;text-align:center;}
  .card .num{font-size:38px;font-weight:800;line-height:1;}
  .card .label{font-size:11px;color:#A6ACD4;text-transform:uppercase;letter-spacing:0.08em;margin-top:8px;}
  .card.going .num{color:#4ADE80;}
  .card.not .num{color:#F87171;}
  .card.wait .num{color:#FACC15;}
  .card.total .num{color:#5B9BFF;}
  .card.inactive .num{color:#FB923C;}
  .bar-wrap{background:#161C46;border:1px solid #33397A;border-radius:12px;padding:20px;margin-bottom:40px;}
  .bar-bg{background:#0A0D24;border-radius:8px;height:32px;display:flex;overflow:hidden;}
  .bar-go{background:#4ADE80;height:100%;transition:width .6s ease;}
  .bar-no{background:#F87171;height:100%;transition:width .6s ease;}
  .bar-legend{display:flex;gap:20px;margin-top:12px;font-size:13px;color:#A6ACD4;}
  .bar-legend span::before{content:'';display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;}
  .bar-legend .lg::before{background:#4ADE80;}
  .bar-legend .ln::before{background:#F87171;}
  .bar-legend .lw::before{background:#333;}
  h2{font-size:20px;font-weight:700;margin-bottom:16px;}
  .section{margin-bottom:40px;}
  table{width:100%;border-collapse:collapse;}
  th{text-align:left;font-size:12px;color:#A6ACD4;text-transform:uppercase;letter-spacing:0.06em;padding:10px 12px;border-bottom:1px solid #33397A;}
  td{padding:12px;border-bottom:1px solid #1D2456;font-size:14px;}
  .status-going{color:#4ADE80;}
  .status-not{color:#F87171;}
  .warn{background:#261A00;border:1px solid #FB923C;border-radius:12px;padding:20px;margin-bottom:40px;}
  .warn h3{color:#FB923C;font-size:16px;margin-bottom:10px;}
  .warn ul{list-style:none;padding:0;}
  .warn li{padding:4px 0;font-size:14px;color:#A6ACD4;}
  .warn li b{color:#fff;}
  .refresh{color:#5B9BFF;font-size:13px;text-decoration:none;float:right;margin-top:-36px;}
  @media(max-width:700px){.cards{grid-template-columns:repeat(2,1fr);}}
</style>
</head>
<body>
<div class="wrap">
  <h1>📊 {{ event.name }}</h1>
  <p class="sub">{{ event.date }} · {{ event.time }} · {{ event.place }}</p>

  <div class="cards">
    <div class="card total"><div class="num">{{ total_contacts }}</div><div class="label">В таблице</div></div>
    <div class="card" style=""><div class="num" style="color:#818CF8;">{{ total_active }}</div><div class="label">В боте</div></div>
    <div class="card going"><div class="num">{{ going }}</div><div class="label">Идут</div></div>
    <div class="card not"><div class="num">{{ not_going }}</div><div class="label">Не смогут</div></div>
    <div class="card wait"><div class="num">{{ no_response }}</div><div class="label">Молчат</div></div>
  </div>

  {% if total_active > 0 %}
  <div class="bar-wrap">
    {% set pct_go = (going / total_active * 100) if total_active > 0 else 0 %}
    {% set pct_no = (not_going / total_active * 100) if total_active > 0 else 0 %}
    <div class="bar-bg">
      <div class="bar-go" style="width:{{ pct_go }}%"></div>
      <div class="bar-no" style="width:{{ pct_no }}%"></div>
    </div>
    <div class="bar-legend">
      <span class="lg">Идут ({{ pct_go|round(0)|int }}%)</span>
      <span class="ln">Не смогут ({{ pct_no|round(0)|int }}%)</span>
      <span class="lw">Не ответили</span>
    </div>
  </div>
  {% endif %}

  {% if not_activated_count > 0 %}
  <div class="warn">
    <h3>⏳ Ещё не подключились к боту ({{ not_activated_count }})</h3>
    <p style="font-size:13px;color:#A6ACD4;margin-bottom:12px;">Эти контакты из таблицы ещё не написали боту /start — отправьте им ссылку</p>
    <ul>
    {% for c in not_activated %}
      <li><b>{{ c.name }}</b> — @{{ c.username }}</li>
    {% endfor %}
    </ul>
  </div>
  {% endif %}

  <div class="section">
    <h2>Ответы</h2>
    <a class="refresh" href="/">🔄 Обновить</a>
    {% if responses %}
    <table>
      <tr><th>Имя</th><th>Telegram</th><th>Статус</th><th>Когда</th></tr>
      {% for r in responses %}
      <tr>
        <td>{{ r.name }}</td>
        <td>{{ '@' + r.username if r.username else '—' }}</td>
        <td class="{{ 'status-going' if r.status == 'going' else 'status-not' }}">
          {{ '✅ Идёт' if r.status == 'going' else '❌ Не сможет' }}
        </td>
        <td>{{ r.time[:16] }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <p style="color:#A6ACD4;padding:20px 0;">Пока никто не ответил. Отправьте /broadcast в боте.</p>
    {% endif %}
  </div>
</div>
<script>setTimeout(()=>location.reload(), 30000);</script>
</body>
</html>"""

@app.route("/")
def stats_page():
    return render_template_string(STATS_PAGE, **get_stats())

@app.route("/api/stats")
def stats_api():
    return jsonify(get_stats())

# ═══════════════════════════════════════════
# ЗАПУСК
# ═══════════════════════════════════════════
if __name__ == "__main__":
    init_db()
    bot_user = get_bot_username()
    print("=" * 55)
    print(f"  🤖  Бот Level Up запущен")
    if bot_user:
        print(f"  🔗  https://t.me/{bot_user}")
    print(f"  📊  Статистика: http://localhost:{WEB_PORT}")
    print(f"=" * 55)
    print(f"  Команды (в Telegram):")
    print(f"    /sync      — загрузить контакты из Google-таблицы")
    print(f"    /broadcast — разослать напоминание")
    print(f"    /stats     — статистика в чате")
    print(f"    /link      — ссылка на бота для приглашений")
    print(f"=" * 55)

    bot_thread = threading.Thread(target=poll_updates, daemon=True)
    bot_thread.start()
    app.run(host="0.0.0.0", port=WEB_PORT)
