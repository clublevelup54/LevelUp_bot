"""
LEVEL UP — Telegram-бот для мероприятий (v2)
=============================================
Многоивентовая версия:
- Создание нескольких мероприятий через Telegram
- Отдельная страница статистики по каждому мероприятию
- Фамилии на странице статистики, имена в Telegram
- Кнопка «Подробнее» для описания мероприятия
- Рассылка анонсов по каждому мероприятию отдельно

Команды админа:
  /newevent    — создать новое мероприятие
  /events      — список всех мероприятий
  /broadcast N — разослать напоминание по мероприятию #N
  /stats N     — статистика по мероприятию #N
  /sync        — загрузить контакты из Google-таблицы
  /link        — ссылка на бота
  /help        — все команды
"""

import os, io, csv, json, sqlite3, threading, time, requests
from datetime import datetime
from flask import Flask, jsonify, render_template_string, redirect

BOT_TOKEN = "8898623835:AAGqfdD2vNcH4kWfZfqEV4PgufezS9R5Xwk"
ADMIN_CHAT_ID = 173317122
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1FEV3V5zjDQ7D8yGfMMhocQjpPjgJ6pHF7rqT2hDIP5c/export?format=csv&gid=0"

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_FILE = "levelup_bot.db"
WEB_PORT = int(os.environ.get("PORT", 5000))

# Хранилище состояний для создания мероприятий (в памяти)
admin_state = {}

# ═══════════════════════════════════════════
# БАЗА ДАННЫХ
# ═══════════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS contacts (
        username TEXT PRIMARY KEY,
        name TEXT,
        last_name TEXT DEFAULT ''
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        first_name TEXT,
        last_name TEXT,
        username TEXT,
        joined_at TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
    c.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        date TEXT,
        time TEXT,
        place TEXT,
        description TEXT DEFAULT '',
        created_at TEXT,
        is_active INTEGER DEFAULT 1
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS rsvp (
        event_id INTEGER,
        chat_id INTEGER,
        status TEXT,
        responded_at TEXT,
        first_name TEXT,
        last_name TEXT,
        username TEXT,
        PRIMARY KEY (event_id, chat_id)
    )""")
    conn.commit()
    conn.close()

# ═══════════════════════════════════════════
# МЕРОПРИЯТИЯ
# ═══════════════════════════════════════════
def create_event(name, date, time_str, place, description=""):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO events (name, date, time, place, description, created_at) VALUES (?,?,?,?,?,?)",
              (name, date, time_str, place, description, datetime.now().isoformat()))
    event_id = c.lastrowid
    conn.commit()
    conn.close()
    return event_id

def get_event(event_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM events WHERE id=?", (event_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def get_all_events():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM events ORDER BY id DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_latest_event():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM events WHERE is_active=1 ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

# ═══════════════════════════════════════════
# КОНТАКТЫ И ПОЛЬЗОВАТЕЛИ
# ═══════════════════════════════════════════
def sync_google_sheet():
    if not SHEET_CSV_URL:
        return 0, "URL таблицы не указан"
    try:
        r = requests.get(SHEET_CSV_URL, timeout=10)
        r.encoding = "utf-8"
        reader = csv.reader(io.StringIO(r.text))
        header = next(reader, None)
        conn = get_db()
        c = conn.cursor()
        count = 0
        for row in reader:
            if len(row) >= 2:
                name_full = row[0].strip()
                username = row[1].strip().replace("@", "").lower()
                # Разделяем имя и фамилию
                parts = name_full.split(None, 1)
                first = parts[0] if parts else name_full
                last = parts[1] if len(parts) > 1 else ""
                if username:
                    c.execute("INSERT OR REPLACE INTO contacts (username, name, last_name) VALUES (?,?,?)",
                              (username, first, last))
                    count += 1
        conn.commit()
        conn.close()
        return count, "OK"
    except Exception as e:
        return 0, str(e)

def add_user(chat_id, first_name, last_name, username):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO users (chat_id, first_name, last_name, username, joined_at)
                 VALUES (?,?,?,?,?)""",
              (chat_id, first_name, last_name or "",
               (username or "").lower(), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_all_active_users():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT chat_id, first_name, last_name, username FROM users")
    users = [dict(r) for r in c.fetchall()]
    conn.close()
    return users

def save_rsvp(event_id, chat_id, status, first_name, last_name, username):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO rsvp (event_id, chat_id, status, responded_at, first_name, last_name, username)
                 VALUES (?,?,?,?,?,?,?)""",
              (event_id, chat_id, status, datetime.now().isoformat(), first_name, last_name or "", username))
    conn.commit()
    conn.close()

def get_event_stats(event_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM users")
    total_active = c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) as cnt FROM contacts")
    total_contacts = c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) as cnt FROM rsvp WHERE event_id=? AND status='going'", (event_id,))
    going = c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) as cnt FROM rsvp WHERE event_id=? AND status='not_going'", (event_id,))
    not_going = c.fetchone()["cnt"]
    c.execute("""SELECT first_name, last_name, username, status, responded_at
                 FROM rsvp WHERE event_id=? ORDER BY responded_at DESC""", (event_id,))
    responses = [dict(r) for r in c.fetchall()]
    c.execute("""SELECT c.name, c.last_name as contact_last, c.username FROM contacts c
                 LEFT JOIN users u ON LOWER(c.username) = LOWER(u.username)
                 WHERE u.chat_id IS NULL""")
    not_activated = [dict(r) for r in c.fetchall()]
    event = get_event(event_id)
    conn.close()
    return {
        "event": event,
        "total_contacts": total_contacts,
        "total_active": total_active,
        "going": going,
        "not_going": not_going,
        "no_response": total_active - going - not_going,
        "responses": responses,
        "not_activated": not_activated,
        "not_activated_count": len(not_activated)
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

def make_event_keyboard(event_id):
    return {
        "inline_keyboard": [
            [
                {"text": "✅ ИДУ НА ВСТРЕЧУ", "callback_data": f"rsvp_going_{event_id}"},
                {"text": "❌ НЕ СМОГУ", "callback_data": f"rsvp_not_{event_id}"}
            ],
            [
                {"text": "📋 Подробнее о мероприятии", "callback_data": f"details_{event_id}"}
            ]
        ]
    }

def make_event_text(event):
    return (
        f"📌 <b>{event['name']}</b>\n"
        f"📅 {event['date']} в {event['time']}\n"
        f"📍 {event['place']}\n"
        f"🏛 Мужской клуб предпринимателей Level Up"
    )

def broadcast_event(event_id):
    sync_google_sheet()
    event = get_event(event_id)
    if not event:
        return 0, 0
    users = get_all_active_users()
    text = (
        f"🔔 <b>Приглашение на встречу</b>\n\n"
        f"{make_event_text(event)}\n\n"
        f"Вы придёте?"
    )
    keyboard = make_event_keyboard(event_id)
    sent = 0
    errors = 0
    for user in users:
        if user["chat_id"] == ADMIN_CHAT_ID:
            continue
        result = send_message(user["chat_id"], text, keyboard)
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
        last_name = cb["from"].get("last_name", "")
        username = cb["from"].get("username", "")
        data = cb.get("data", "")

        if data.startswith("rsvp_going_"):
            event_id = int(data.replace("rsvp_going_", ""))
            event = get_event(event_id)
            save_rsvp(event_id, chat_id, "going", first_name, last_name, username)
            answer_callback(cb["id"], "🎉 Отлично! Вы записаны на встречу!")
            if event:
                send_message(chat_id,
                    f"✅ <b>{first_name}, вы записаны!</b>\n\n"
                    f"📅 {event['date']} в {event['time']}\n"
                    f"📍 {event['place']}\n\nДо встречи!")
                send_message(ADMIN_CHAT_ID,
                    f"✅ <b>{first_name} {last_name}</b> (@{username}) идёт на «{event['name']}»!")

        elif data.startswith("rsvp_not_"):
            event_id = int(data.replace("rsvp_not_", ""))
            event = get_event(event_id)
            save_rsvp(event_id, chat_id, "not_going", first_name, last_name, username)
            answer_callback(cb["id"], "Жаль! Ждём на следующей встрече.")
            send_message(chat_id,
                f"{first_name}, понял — в этот раз не получится. "
                "Будем рады видеть вас на следующей встрече! 🤝")
            if event:
                send_message(ADMIN_CHAT_ID,
                    f"❌ <b>{first_name} {last_name}</b> (@{username}) не сможет прийти на «{event['name']}».")

        elif data.startswith("details_"):
            event_id = int(data.replace("details_", ""))
            event = get_event(event_id)
            if event and event.get("description"):
                send_message(chat_id,
                    f"📋 <b>{event['name']}</b>\n\n"
                    f"{event['description']}\n\n"
                    f"📅 {event['date']} в {event['time']}\n"
                    f"📍 {event['place']}")
            elif event:
                send_message(chat_id,
                    f"📋 <b>{event['name']}</b>\n\n"
                    f"📅 {event['date']} в {event['time']}\n"
                    f"📍 {event['place']}\n\n"
                    f"Подробное описание пока не добавлено.")
            answer_callback(cb["id"], "")
        return

    # Сообщения
    msg = update.get("message")
    if not msg:
        return

    chat_id = msg["chat"]["id"]
    first_name = msg["from"].get("first_name", "")
    last_name = msg["from"].get("last_name", "")
    username = msg["from"].get("username", "")
    text = msg.get("text", "").strip()

    add_user(chat_id, first_name, last_name, username)

    # ── Состояние создания мероприятия ──
    if chat_id == ADMIN_CHAT_ID and chat_id in admin_state:
        state = admin_state[chat_id]
        step = state.get("step")

        if step == "name":
            state["name"] = text
            state["step"] = "date"
            send_message(chat_id, "📅 Введите <b>дату</b> мероприятия\n\n<i>Например: 15 августа</i>")
            return
        elif step == "date":
            state["date"] = text
            state["step"] = "time"
            send_message(chat_id, "🕐 Введите <b>время</b> начала\n\n<i>Например: 18:00</i>")
            return
        elif step == "time":
            state["time"] = text
            state["step"] = "place"
            send_message(chat_id, "📍 Введите <b>место</b> проведения\n\n<i>Например: Азимут, ул. Ленина 21</i>")
            return
        elif step == "place":
            state["place"] = text
            state["step"] = "description"
            send_message(chat_id,
                "📝 Введите <b>подробное описание</b> мероприятия.\n\n"
                "Это текст, который увидят участники при нажатии кнопки «Подробнее».\n\n"
                "Можно использовать несколько строк. Если описания пока нет — отправьте <b>-</b>")
            return
        elif step == "description":
            desc = "" if text == "-" else text
            event_id = create_event(state["name"], state["date"], state["time"], state["place"], desc)
            del admin_state[chat_id]
            send_message(chat_id,
                f"✅ <b>Мероприятие #{event_id} создано!</b>\n\n"
                f"📌 {state['name']}\n"
                f"📅 {state['date']} в {state['time']}\n"
                f"📍 {state['place']}\n\n"
                f"Чтобы разослать анонс:\n"
                f"<code>/broadcast {event_id}</code>\n\n"
                f"Чтобы посмотреть статистику:\n"
                f"<code>/stats {event_id}</code>")
            return

    # ── Команды ──
    if text == "/start":
        event = get_latest_event()
        if event:
            send_message(chat_id,
                f"👋 Привет, <b>{first_name}</b>!\n\n"
                f"Это бот <b>Мужского клуба предпринимателей Level Up</b>.\n\n"
                f"Ближайшее мероприятие:\n"
                f"{make_event_text(event)}\n\n"
                f"Вы планируете прийти?",
                make_event_keyboard(event["id"])
            )
        else:
            send_message(chat_id,
                f"👋 Привет, <b>{first_name}</b>!\n\n"
                f"Это бот <b>Мужского клуба предпринимателей Level Up</b>.\n\n"
                f"Пока мероприятий нет — я пришлю уведомление, когда появится новая встреча.")

    elif text == "/newevent" and chat_id == ADMIN_CHAT_ID:
        admin_state[chat_id] = {"step": "name"}
        send_message(chat_id,
            "🆕 <b>Создание нового мероприятия</b>\n\n"
            "Введите <b>название</b> мероприятия\n\n"
            "<i>Например: АПЕКС — Точка идеального поворота</i>")

    elif text == "/cancel" and chat_id == ADMIN_CHAT_ID:
        if chat_id in admin_state:
            del admin_state[chat_id]
            send_message(chat_id, "❌ Создание мероприятия отменено.")
        else:
            send_message(chat_id, "Нечего отменять.")

    elif text == "/events" and chat_id == ADMIN_CHAT_ID:
        events = get_all_events()
        if not events:
            send_message(chat_id, "Мероприятий пока нет. Создайте: /newevent")
            return
        msg_text = "📋 <b>Все мероприятия:</b>\n\n"
        for e in events:
            stats = get_event_stats(e["id"])
            msg_text += (
                f"<b>#{e['id']}</b> {e['name']}\n"
                f"   📅 {e['date']} в {e['time']} · 📍 {e['place']}\n"
                f"   ✅ {stats['going']} идут · ❌ {stats['not_going']} не смогут\n\n"
            )
        msg_text += (
            "<b>Команды:</b>\n"
            "<code>/broadcast N</code> — разослать анонс\n"
            "<code>/stats N</code> — статистика"
        )
        send_message(chat_id, msg_text)

    elif text.startswith("/broadcast") and chat_id == ADMIN_CHAT_ID:
        parts = text.split()
        if len(parts) < 2:
            event = get_latest_event()
            if event:
                send_message(chat_id,
                    f"📤 Рассылка по последнему мероприятию <b>#{event['id']}</b>: {event['name']}...")
                sent, errors = broadcast_event(event["id"])
            else:
                send_message(chat_id, "Нет мероприятий. Создайте: /newevent")
                return
        else:
            try:
                event_id = int(parts[1])
                event = get_event(event_id)
                if not event:
                    send_message(chat_id, f"❌ Мероприятие #{event_id} не найдено.")
                    return
                send_message(chat_id, f"📤 Рассылка по <b>#{event_id}</b>: {event['name']}...")
                sent, errors = broadcast_event(event_id)
            except ValueError:
                send_message(chat_id, "Используйте: <code>/broadcast N</code> (N — номер мероприятия)")
                return

        stats = get_event_stats(event["id"] if "event" in dir() else event_id)
        msg_text = f"✅ <b>Рассылка завершена!</b>\n\n📨 Отправлено: {sent}\n⚠️ Ошибок: {errors}"
        if stats["not_activated"]:
            msg_text += f"\n\n⏳ <b>Не подключились к боту ({stats['not_activated_count']}):</b>\n"
            for c in stats["not_activated"][:20]:
                msg_text += f"  • {c['name']} {c.get('contact_last','')} — @{c['username']}\n"
        send_message(chat_id, msg_text)

    elif text.startswith("/stats") and chat_id == ADMIN_CHAT_ID:
        parts = text.split()
        if len(parts) < 2:
            event = get_latest_event()
            if not event:
                send_message(chat_id, "Нет мероприятий. Создайте: /newevent")
                return
            event_id = event["id"]
        else:
            try:
                event_id = int(parts[1])
            except ValueError:
                send_message(chat_id, "Используйте: <code>/stats N</code>")
                return
        stats = get_event_stats(event_id)
        if not stats["event"]:
            send_message(chat_id, f"❌ Мероприятие #{event_id} не найдено.")
            return
        send_message(chat_id,
            f"📊 <b>Статистика: {stats['event']['name']}</b>\n\n"
            f"📋 В Google-таблице: {stats['total_contacts']}\n"
            f"🤖 В боте: {stats['total_active']}\n"
            f"⏳ Не подключились: {stats['not_activated_count']}\n\n"
            f"✅ Идут: {stats['going']}\n"
            f"❌ Не смогут: {stats['not_going']}\n"
            f"🤷 Молчат: {stats['no_response']}")

    elif text == "/sync" and chat_id == ADMIN_CHAT_ID:
        count, status = sync_google_sheet()
        if status == "OK":
            send_message(chat_id, f"✅ Таблица синхронизирована! Контактов: {count}")
        else:
            send_message(chat_id, f"❌ Ошибка: {status}")

    elif text == "/link" and chat_id == ADMIN_CHAT_ID:
        bot_user = get_bot_username()
        link = f"https://t.me/{bot_user}" if bot_user else "(не удалось)"
        send_message(chat_id, f"🔗 Ссылка на бота:\n\n{link}")

    elif text == "/help":
        help_text = "📋 <b>Команды:</b>\n\n/start — О ближайшей встрече\n"
        if chat_id == ADMIN_CHAT_ID:
            help_text += (
                "\n<b>Админ:</b>\n"
                "/newevent — создать мероприятие\n"
                "/events — список всех мероприятий\n"
                "/broadcast N — разослать анонс (#N)\n"
                "/stats N — статистика (#N)\n"
                "/sync — обновить Google-таблицу\n"
                "/link — ссылка на бота\n"
                "/cancel — отменить создание\n"
            )
        send_message(chat_id, help_text)

    else:
        if chat_id != ADMIN_CHAT_ID:
            send_message(ADMIN_CHAT_ID,
                f"💬 <b>{first_name} {last_name}</b> (@{username}):\n\n{text}")
            send_message(chat_id, "Спасибо! Ваше сообщение передано организатору. 🤝")

def poll_updates():
    offset = 0
    while True:
        try:
            r = requests.get(f"{API}/getUpdates",
                             params={"offset": offset, "timeout": 30}, timeout=35)
            data = r.json()
            if data.get("ok"):
                for update in data["result"]:
                    offset = update["update_id"] + 1
                    try:
                        handle_update(update)
                    except Exception as e:
                        print(f"Ошибка: {e}")
        except Exception as e:
            print(f"Polling: {e}")
            time.sleep(5)

# ═══════════════════════════════════════════
# ВЕБ — СПИСОК МЕРОПРИЯТИЙ
# ═══════════════════════════════════════════
app = Flask(__name__)

PAGE_EVENTS = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Level Up — Мероприятия</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{background:#0A0D24;color:#fff;font-family:'Segoe UI',system-ui,sans-serif;padding:40px 20px;}
.wrap{max-width:900px;margin:0 auto;}
h1{font-size:28px;font-weight:800;margin-bottom:8px;}
.sub{color:#A6ACD4;font-size:15px;margin-bottom:40px;}
.event-card{background:#161C46;border:1px solid #33397A;border-radius:16px;padding:28px;margin-bottom:20px;
  display:flex;justify-content:space-between;align-items:center;text-decoration:none;color:#fff;
  transition:border-color .25s ease,transform .25s ease;cursor:pointer;}
.event-card:hover{border-color:#5B9BFF;transform:translateY(-3px);}
.event-card .info h2{font-size:20px;font-weight:800;margin-bottom:6px;}
.event-card .info p{font-size:14px;color:#A6ACD4;}
.event-card .nums{display:flex;gap:20px;text-align:center;}
.event-card .num-box .n{font-size:28px;font-weight:800;}
.event-card .num-box .l{font-size:11px;color:#A6ACD4;text-transform:uppercase;}
.going .n{color:#4ADE80;}
.notg .n{color:#F87171;}
.empty{color:#A6ACD4;text-align:center;padding:60px 0;font-size:16px;}
</style></head><body>
<div class="wrap">
<h1>📊 Level Up — Мероприятия</h1>
<p class="sub">Мужской клуб предпринимателей</p>
{% if events %}
  {% for e in events %}
  <a class="event-card" href="/event/{{ e.id }}">
    <div class="info">
      <h2>#{{ e.id }} · {{ e.name }}</h2>
      <p>📅 {{ e.date }} в {{ e.time }} · 📍 {{ e.place }}</p>
    </div>
    <div class="nums">
      <div class="num-box going"><div class="n">{{ e.going }}</div><div class="l">Идут</div></div>
      <div class="num-box notg"><div class="n">{{ e.not_going }}</div><div class="l">Не смогут</div></div>
    </div>
  </a>
  {% endfor %}
{% else %}
  <div class="empty">Мероприятий пока нет</div>
{% endif %}
</div>
<script>setTimeout(()=>location.reload(),30000);</script>
</body></html>"""

PAGE_EVENT_DETAIL = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{{ event.name }} — Статистика</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{background:#0A0D24;color:#fff;font-family:'Segoe UI',system-ui,sans-serif;padding:40px 20px;}
.wrap{max-width:900px;margin:0 auto;}
.back{color:#5B9BFF;text-decoration:none;font-size:14px;display:inline-block;margin-bottom:20px;}
h1{font-size:26px;font-weight:800;margin-bottom:6px;}
.sub{color:#A6ACD4;font-size:15px;margin-bottom:40px;}
.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:40px;}
.card{background:#161C46;border:1px solid #33397A;border-radius:16px;padding:22px 16px;text-align:center;}
.card .num{font-size:36px;font-weight:800;line-height:1;}
.card .label{font-size:11px;color:#A6ACD4;text-transform:uppercase;letter-spacing:0.06em;margin-top:8px;}
.going .num{color:#4ADE80;} .not .num{color:#F87171;} .wait .num{color:#FACC15;} .total .num{color:#5B9BFF;} .inactive .num{color:#FB923C;}
.bar-wrap{background:#161C46;border:1px solid #33397A;border-radius:12px;padding:20px;margin-bottom:40px;}
.bar-bg{background:#0A0D24;border-radius:8px;height:32px;display:flex;overflow:hidden;}
.bar-go{background:#4ADE80;height:100%;transition:width .6s ease;}
.bar-no{background:#F87171;height:100%;transition:width .6s ease;}
.bar-legend{display:flex;gap:20px;margin-top:12px;font-size:13px;color:#A6ACD4;}
.bar-legend span::before{content:'';display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;}
.bar-legend .lg::before{background:#4ADE80;} .bar-legend .ln::before{background:#F87171;} .bar-legend .lw::before{background:#333;}
h2{font-size:20px;font-weight:700;margin-bottom:16px;}
.section{margin-bottom:40px;}
table{width:100%;border-collapse:collapse;}
th{text-align:left;font-size:12px;color:#A6ACD4;text-transform:uppercase;letter-spacing:0.06em;padding:10px 12px;border-bottom:1px solid #33397A;}
td{padding:12px;border-bottom:1px solid #1D2456;font-size:14px;}
.status-going{color:#4ADE80;} .status-not{color:#F87171;}
.warn{background:#261A00;border:1px solid #FB923C;border-radius:12px;padding:20px;margin-bottom:40px;}
.warn h3{color:#FB923C;font-size:16px;margin-bottom:10px;}
.warn li{padding:4px 0;font-size:14px;color:#A6ACD4;list-style:none;}
.warn li b{color:#fff;}
.refresh{color:#5B9BFF;font-size:13px;text-decoration:none;float:right;margin-top:-36px;}
@media(max-width:700px){.cards{grid-template-columns:repeat(2,1fr);}}
</style></head><body>
<div class="wrap">
<a class="back" href="/">← Все мероприятия</a>
<h1>📊 {{ event.name }}</h1>
<p class="sub">📅 {{ event.date }} в {{ event.time }} · 📍 {{ event.place }}</p>

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
  <div class="bar-bg"><div class="bar-go" style="width:{{pct_go}}%"></div><div class="bar-no" style="width:{{pct_no}}%"></div></div>
  <div class="bar-legend">
    <span class="lg">Идут ({{ pct_go|round(0)|int }}%)</span>
    <span class="ln">Не смогут ({{ pct_no|round(0)|int }}%)</span>
    <span class="lw">Не ответили</span>
  </div>
</div>
{% endif %}

{% if not_activated_count > 0 %}
<div class="warn">
  <h3>⏳ Не подключились к боту ({{ not_activated_count }})</h3>
  <ul>{% for c in not_activated %}<li><b>{{ c.name }} {{ c.get('contact_last','') }}</b> — @{{ c.username }}</li>{% endfor %}</ul>
</div>
{% endif %}

<div class="section">
  <h2>✅ Идут на встречу</h2>
  <a class="refresh" href="/event/{{ event.id }}">🔄 Обновить</a>
  <table>
    <tr><th>Имя</th><th>Фамилия</th><th>Telegram</th><th>Когда ответил</th></tr>
    {% for r in responses if r.status == 'going' %}
    <tr><td>{{ r.first_name }}</td><td>{{ r.last_name }}</td><td>{{ '@' + r.username if r.username else '—' }}</td><td>{{ r.responded_at[:16] }}</td></tr>
    {% endfor %}
  </table>
</div>

<div class="section">
  <h2>❌ Не смогут</h2>
  <table>
    <tr><th>Имя</th><th>Фамилия</th><th>Telegram</th><th>Когда ответил</th></tr>
    {% for r in responses if r.status == 'not_going' %}
    <tr><td>{{ r.first_name }}</td><td>{{ r.last_name }}</td><td>{{ '@' + r.username if r.username else '—' }}</td><td>{{ r.responded_at[:16] }}</td></tr>
    {% endfor %}
  </table>
</div>
</div>
<script>setTimeout(()=>location.reload(),30000);</script>
</body></html>"""

@app.route("/")
def page_events():
    events = get_all_events()
    for e in events:
        s = get_event_stats(e["id"])
        e["going"] = s["going"]
        e["not_going"] = s["not_going"]
    return render_template_string(PAGE_EVENTS, events=events)

@app.route("/event/<int:event_id>")
def page_event_detail(event_id):
    stats = get_event_stats(event_id)
    if not stats["event"]:
        return "Мероприятие не найдено", 404
    return render_template_string(PAGE_EVENT_DETAIL, **stats)

@app.route("/api/stats/<int:event_id>")
def api_stats(event_id):
    return jsonify(get_event_stats(event_id))

# ═══════════════════════════════════════════
if __name__ == "__main__":
    init_db()
    bot_user = get_bot_username()
    print("=" * 55)
    print(f"  🤖  Бот Level Up v2 запущен")
    if bot_user:
        print(f"  🔗  https://t.me/{bot_user}")
    print(f"  📊  Статистика: http://localhost:{WEB_PORT}")
    print(f"=" * 55)

    bot_thread = threading.Thread(target=poll_updates, daemon=True)
    bot_thread.start()
    app.run(host="0.0.0.0", port=WEB_PORT)
