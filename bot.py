"""
LEVEL UP — Telegram-бот для мероприятий (v5)
=============================================
+ Картинки/видео к анонсам и описаниям
+ Рассылка новостей без кнопок
"""

import os, io, csv, json, sqlite3, threading, time, requests, re
from datetime import datetime, date
from flask import Flask, jsonify, render_template_string

BOT_TOKEN = "8898623835:AAGqfdD2vNcH4kWfZfqEV4PgufezS9R5Xwk"
ADMIN_CHAT_ID = 173317122
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1FEV3V5zjDQ7D8yGfMMhocQjpPjgJ6pHF7rqT2hDIP5c/export?format=csv&gid=0"

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_FILE = os.path.join(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"), "levelup_bot.db")
os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
WEB_PORT = int(os.environ.get("PORT", 5000))
admin_state = {}

MONTHS_RU = {"января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,
             "июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12}

def parse_ru_date(text):
    text = text.strip().lower()
    m = re.match(r"(\d{1,2})\s+(\S+)(?:\s+(\d{4}))?", text)
    if m:
        day=int(m.group(1)); month=MONTHS_RU.get(m.group(2))
        year=int(m.group(3)) if m.group(3) else date.today().year
        if month:
            try:
                d=date(year,month,day)
                if not m.group(3) and d<date.today(): d=date(year+1,month,day)
                return d
            except: pass
    return None

def get_db():
    conn=sqlite3.connect(DB_FILE); conn.row_factory=sqlite3.Row; return conn

def init_db():
    conn=get_db(); c=conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS contacts (username TEXT PRIMARY KEY, name TEXT, last_name TEXT DEFAULT '')")
    c.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, username TEXT, joined_at TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
    c.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, date TEXT, time TEXT,
        place TEXT, description TEXT DEFAULT '', date_iso TEXT DEFAULT '',
        media_type TEXT DEFAULT '', media_file_id TEXT DEFAULT '',
        desc_media_type TEXT DEFAULT '', desc_media_file_id TEXT DEFAULT '',
        created_at TEXT, is_active INTEGER DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS rsvp (
        event_id INTEGER, chat_id INTEGER, status TEXT, responded_at TEXT,
        first_name TEXT, last_name TEXT, username TEXT, PRIMARY KEY (event_id, chat_id))""")
    c.execute("CREATE TABLE IF NOT EXISTS sent_log (event_id INTEGER, chat_id INTEGER, sent_at TEXT, PRIMARY KEY (event_id, chat_id))")
    # Миграция: добавляем столбцы media если их нет
    try: c.execute("ALTER TABLE events ADD COLUMN media_type TEXT DEFAULT ''")
    except: pass
    try: c.execute("ALTER TABLE events ADD COLUMN media_file_id TEXT DEFAULT ''")
    except: pass
    try: c.execute("ALTER TABLE events ADD COLUMN desc_media_type TEXT DEFAULT ''")
    except: pass
    try: c.execute("ALTER TABLE events ADD COLUMN desc_media_file_id TEXT DEFAULT ''")
    except: pass
    conn.commit(); conn.close()

# ═══════════════════════════════════════════
# АВТО-АРХИВАЦИЯ
# ═══════════════════════════════════════════
def auto_archive():
    conn=get_db(); c=conn.cursor()
    c.execute("SELECT id,date,date_iso FROM events WHERE is_active=1")
    today=date.today(); n=0
    for row in c.fetchall():
        d=None
        if row["date_iso"]:
            try: d=date.fromisoformat(row["date_iso"])
            except: pass
        if not d: d=parse_ru_date(row["date"])
        if d and d<today: c.execute("UPDATE events SET is_active=0 WHERE id=?",(row["id"],)); n+=1
    conn.commit(); conn.close(); return n

def archive_loop():
    while True:
        try: auto_archive()
        except: pass
        time.sleep(3600)

# ═══════════════════════════════════════════
# МЕРОПРИЯТИЯ
# ═══════════════════════════════════════════
def create_event(name,dt,tm,place,desc="",mt="",mfid="",dmt="",dmfid=""):
    d_iso=""
    d=parse_ru_date(dt)
    if d: d_iso=d.isoformat()
    conn=get_db(); c=conn.cursor()
    c.execute("INSERT INTO events (name,date,time,place,description,date_iso,media_type,media_file_id,desc_media_type,desc_media_file_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (name,dt,tm,place,desc,d_iso,mt,mfid,dmt,dmfid,datetime.now().isoformat()))
    eid=c.lastrowid; conn.commit(); conn.close(); return eid

def get_event(eid):
    conn=get_db(); c=conn.cursor(); c.execute("SELECT * FROM events WHERE id=?",(eid,))
    r=c.fetchone(); conn.close(); return dict(r) if r else None

def update_event_field(eid,field,value):
    conn=get_db(); c=conn.cursor(); c.execute(f"UPDATE events SET {field}=? WHERE id=?",(value,eid))
    if field=="date":
        d=parse_ru_date(value)
        if d: c.execute("UPDATE events SET date_iso=? WHERE id=?",(d.isoformat(),eid))
    conn.commit(); conn.close()

def get_active_events():
    conn=get_db(); c=conn.cursor(); c.execute("SELECT * FROM events WHERE is_active=1 ORDER BY id ASC")
    rows=[dict(r) for r in c.fetchall()]; conn.close()
    for i,e in enumerate(rows): e["display_num"]=i+1
    return rows

def get_archived_events():
    conn=get_db(); c=conn.cursor(); c.execute("SELECT * FROM events WHERE is_active=0 ORDER BY id DESC")
    rows=[dict(r) for r in c.fetchall()]; conn.close(); return rows

def get_event_by_display_num(num):
    evts=get_active_events()
    if 1<=num<=len(evts): return evts[num-1]
    return None

def get_latest_event():
    conn=get_db(); c=conn.cursor(); c.execute("SELECT * FROM events WHERE is_active=1 ORDER BY id DESC LIMIT 1")
    r=c.fetchone(); conn.close(); return dict(r) if r else None

def resolve_event_num(parts):
    if len(parts)<2:
        e=get_latest_event(); return e["id"] if e else None
    try: e=get_event_by_display_num(int(parts[1])); return e["id"] if e else None
    except: return None

# ═══════════════════════════════════════════
# КОНТАКТЫ
# ═══════════════════════════════════════════
def sync_google_sheet():
    if not SHEET_CSV_URL: return 0,"URL не указан"
    try:
        r=requests.get(SHEET_CSV_URL,timeout=10); r.encoding="utf-8"
        reader=csv.reader(io.StringIO(r.text)); next(reader,None)
        conn=get_db(); c=conn.cursor(); count=0
        for row in reader:
            if len(row)>=3:
                ln=row[0].strip(); fn=row[1].strip(); un=row[2].strip().replace("@","").lower()
                if un: c.execute("INSERT OR REPLACE INTO contacts (username,name,last_name) VALUES (?,?,?)",(un,fn,ln)); count+=1
            elif len(row)>=2:
                nm=row[0].strip(); un=row[1].strip().replace("@","").lower()
                if un: c.execute("INSERT OR REPLACE INTO contacts (username,name,last_name) VALUES (?,?,?)",(un,nm,"")); count+=1
        conn.commit(); conn.close(); return count,"OK"
    except Exception as e: return 0,str(e)

def add_user(cid,fn,ln,un):
    conn=get_db(); c=conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (chat_id,first_name,last_name,username,joined_at) VALUES (?,?,?,?,?)",
              (cid,fn,ln or"",(un or"").lower(),datetime.now().isoformat()))
    conn.commit(); conn.close()

def get_all_active_users():
    conn=get_db(); c=conn.cursor()
    c.execute("SELECT u.chat_id,u.first_name,u.last_name,u.username FROM users u INNER JOIN contacts ct ON LOWER(u.username)=LOWER(ct.username)")
    u=[dict(r) for r in c.fetchall()]; conn.close(); return u

def get_contact_name(username):
    if not username: return None
    conn=get_db(); c=conn.cursor()
    c.execute("SELECT name FROM contacts WHERE LOWER(username)=?",(username.lower().replace("@",""),))
    r=c.fetchone(); conn.close(); return r["name"] if r else None

def get_display_name(fn,un):
    n=get_contact_name(un); return n if n else fn

def get_user_by_username(uname):
    conn=get_db(); c=conn.cursor()
    c.execute("SELECT * FROM users WHERE LOWER(username)=?",(uname.lower().replace("@",""),))
    r=c.fetchone(); conn.close(); return dict(r) if r else None

def log_sent(eid,cid):
    conn=get_db(); c=conn.cursor()
    c.execute("INSERT OR REPLACE INTO sent_log (event_id,chat_id,sent_at) VALUES (?,?,?)",(eid,cid,datetime.now().isoformat()))
    conn.commit(); conn.close()

def get_unsent_users(eid):
    conn=get_db(); c=conn.cursor()
    c.execute("SELECT u.chat_id,u.first_name,u.last_name,u.username FROM users u INNER JOIN contacts ct ON LOWER(u.username)=LOWER(ct.username) LEFT JOIN sent_log s ON u.chat_id=s.chat_id AND s.event_id=? WHERE s.chat_id IS NULL",(eid,))
    u=[dict(r) for r in c.fetchall()]; conn.close(); return u

def get_no_response_users(eid):
    conn=get_db(); c=conn.cursor()
    c.execute("SELECT u.chat_id,u.first_name,u.last_name,u.username FROM users u INNER JOIN contacts ct ON LOWER(u.username)=LOWER(ct.username) LEFT JOIN rsvp r ON u.chat_id=r.chat_id AND r.event_id=? WHERE r.chat_id IS NULL",(eid,))
    u=[dict(r) for r in c.fetchall()]; conn.close(); return u

def save_rsvp(eid,cid,status,fn,ln,un):
    conn=get_db(); c=conn.cursor()
    c.execute("INSERT OR REPLACE INTO rsvp (event_id,chat_id,status,responded_at,first_name,last_name,username) VALUES (?,?,?,?,?,?,?)",
              (eid,cid,status,datetime.now().isoformat(),fn,ln or"",un))
    conn.commit(); conn.close()

def get_event_stats(eid):
    conn=get_db(); c=conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM users u INNER JOIN contacts ct ON LOWER(u.username)=LOWER(ct.username)"); ta=c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) as cnt FROM contacts"); tc=c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) as cnt FROM rsvp WHERE event_id=? AND status='going'",(eid,)); g=c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) as cnt FROM rsvp WHERE event_id=? AND status='not_going'",(eid,)); ng=c.fetchone()["cnt"]
    c.execute("SELECT first_name,last_name,username,status,responded_at FROM rsvp WHERE event_id=? ORDER BY last_name ASC,first_name ASC",(eid,))
    resp=[dict(r) for r in c.fetchall()]
    c.execute("SELECT c.name,c.last_name as contact_last,c.username FROM contacts c LEFT JOIN users u ON LOWER(c.username)=LOWER(u.username) WHERE u.chat_id IS NULL")
    na=[dict(r) for r in c.fetchall()]; conn.close()
    return {"event":get_event(eid),"total_contacts":tc,"total_active":ta,"going":g,"not_going":ng,
            "no_response":ta-g-ng,"responses":resp,"not_activated":na,"not_activated_count":len(na)}

# ═══════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════
def send_message(cid,text,reply_markup=None):
    data={"chat_id":cid,"text":text,"parse_mode":"HTML"}
    if reply_markup: data["reply_markup"]=json.dumps(reply_markup)
    try: return requests.post(f"{API}/sendMessage",json=data,timeout=10).json()
    except: return {"ok":False}

def send_media(cid,media_type,file_id,caption="",reply_markup=None):
    """Отправляет фото или видео"""
    data={"chat_id":cid,"caption":caption,"parse_mode":"HTML"}
    if reply_markup: data["reply_markup"]=json.dumps(reply_markup)
    if media_type=="photo":
        data["photo"]=file_id
        try: return requests.post(f"{API}/sendPhoto",json=data,timeout=10).json()
        except: return {"ok":False}
    elif media_type=="video":
        data["video"]=file_id
        try: return requests.post(f"{API}/sendVideo",json=data,timeout=10).json()
        except: return {"ok":False}
    else:
        return send_message(cid,caption,reply_markup)

def send_event_message(cid,text,event=None,reply_markup=None,use_desc_media=False):
    """Отправляет сообщение с медиа мероприятия если есть"""
    if event:
        if use_desc_media and event.get("desc_media_file_id"):
            return send_media(cid,event["desc_media_type"],event["desc_media_file_id"],text,reply_markup)
        elif not use_desc_media and event.get("media_file_id"):
            return send_media(cid,event["media_type"],event["media_file_id"],text,reply_markup)
    return send_message(cid,text,reply_markup)

def answer_callback(cbid,text):
    try: requests.post(f"{API}/answerCallbackQuery",json={"callback_query_id":cbid,"text":text,"show_alert":True},timeout=10)
    except: pass

def get_bot_username():
    try:
        r=requests.get(f"{API}/getMe",timeout=10).json()
        if r.get("ok"): return r["result"].get("username","")
    except: pass
    return ""

def make_kb(eid):
    return {"inline_keyboard":[
        [{"text":"✅ ИДУ НА ВСТРЕЧУ","callback_data":f"go_{eid}"},{"text":"❌ НЕ СМОГУ","callback_data":f"no_{eid}"}],
        [{"text":"📋 Подробнее о мероприятии","callback_data":f"info_{eid}"}]]}

def etxt(e):
    return f"📌 <b>{e['name']}</b>\n📅 {e['date']} в {e['time']}\n📍 {e['place']}"

def extract_media(msg):
    """Извлекает тип и file_id из сообщения"""
    if msg.get("photo"):
        return "photo", msg["photo"][-1]["file_id"]
    elif msg.get("video"):
        return "video", msg["video"]["file_id"]
    return None, None

def send_event_to_user(eid,user):
    e=get_event(eid)
    if not e: return False
    text=f"🔔 <b>Приглашение на встречу</b>\n\n{etxt(e)}\n\nТы придёшь?"
    result=send_event_message(user["chat_id"],text,e,make_kb(eid))
    if result.get("ok"): log_sent(eid,user["chat_id"]); return True
    return False

def broadcast_event(eid,users=None):
    sync_google_sheet(); e=get_event(eid)
    if not e: return 0,0
    if users is None: users=get_all_active_users()
    sent=errors=0
    for u in users:
        if send_event_to_user(eid,u): sent+=1
        else: errors+=1
        time.sleep(0.05)
    return sent,errors

def send_reminder(eid):
    e=get_event(eid)
    if not e: return 0,0
    users=get_no_response_users(eid); sent=errors=0
    for u in users:
        result=send_event_message(u["chat_id"],f"⏰ <b>Напоминание!</b>\n\nТы ещё не ответил(а):\n\n{etxt(e)}\n\nПридёшь?",e,make_kb(eid))
        if result.get("ok"): sent+=1
        else: errors+=1
        time.sleep(0.05)
    return sent,errors

def broadcast_news(text,media_type=None,media_file_id=None):
    """Рассылка новости/анонса партнёра без кнопок"""
    users=get_all_active_users(); sent=errors=0
    for u in users:
        if media_type and media_file_id:
            result=send_media(u["chat_id"],media_type,media_file_id,text)
        else:
            result=send_message(u["chat_id"],text)
        if result.get("ok"): sent+=1
        else: errors+=1
        time.sleep(0.05)
    return sent,errors

# ═══════════════════════════════════════════
# ОБРАБОТКА
# ═══════════════════════════════════════════
def handle_update(update):
    if "callback_query" in update:
        cb=update["callback_query"]; cid=cb["from"]["id"]
        fn=cb["from"].get("first_name",""); ln=cb["from"].get("last_name","")
        un=cb["from"].get("username",""); data=cb.get("data","")

        if data.startswith("stdremind_") and cid==ADMIN_CHAT_ID:
            eid=int(data[10:]); answer_callback(cb["id"],"Отправляю...")
            sent,err=send_reminder(eid); send_message(cid,f"✅ Напоминание!\n📨 {sent} · ⚠️ {err}"); return
        if data.startswith("custremind_") and cid==ADMIN_CHAT_ID:
            eid=int(data[11:]); admin_state[cid]={"action":"custom_remind","step":"text","event_id":eid}
            answer_callback(cb["id"],""); send_message(cid,"✏️ Напишите текст напоминания:\n\n/cancel — отменить"); return

        if data.startswith("go_"):
            eid=int(data[3:]); e=get_event(eid)
            save_rsvp(eid,cid,"going",fn,ln,un); answer_callback(cb["id"],"🎉 Ты записан(а)!")
            if e:
                name=get_display_name(fn,un)
                send_message(cid,f"✅ <b>{name}, ты записан(а)!</b>\n\n📅 {e['date']} в {e['time']}\n📍 {e['place']}\n\nДо встречи!")
                send_message(ADMIN_CHAT_ID,f"✅ <b>{fn} {ln}</b> (@{un}) идёт на «{e['name']}»!")
        elif data.startswith("no_"):
            eid=int(data[3:]); e=get_event(eid)
            save_rsvp(eid,cid,"not_going",fn,ln,un); answer_callback(cb["id"],"Жаль!")
            name=get_display_name(fn,un)
            send_message(cid,f"{name}, понял — в этот раз не получится. Ждём на следующей! 🤝")
            if e: send_message(ADMIN_CHAT_ID,f"❌ <b>{fn} {ln}</b> (@{un}) не сможет — «{e['name']}»")
        elif data.startswith("info_"):
            eid=int(data[5:]); e=get_event(eid)
            rsvp_kb={"inline_keyboard":[[
                {"text":"✅ ИДУ НА ВСТРЕЧУ","callback_data":f"go_{eid}"},
                {"text":"❌ НЕ СМОГУ","callback_data":f"no_{eid}"}]]}
            if e and e.get("description"):
                send_event_message(cid,f"📋 <b>{e['name']}</b>\n\n{e['description']}\n\n📅 {e['date']} в {e['time']}\n📍 {e['place']}",e,rsvp_kb,use_desc_media=True)
            elif e:
                send_message(cid,f"📋 <b>{e['name']}</b>\n\n📅 {e['date']} в {e['time']}\n📍 {e['place']}\n\nОписание пока не добавлено.",rsvp_kb)
            answer_callback(cb["id"],"")
        return

    msg=update.get("message")
    if not msg: return
    cid=msg["chat"]["id"]; fn=msg["from"].get("first_name","")
    ln=msg["from"].get("last_name",""); un=msg["from"].get("username","")
    text=msg.get("text","").strip(); caption=msg.get("caption","").strip()
    mt,mfid=extract_media(msg)

    add_user(cid,fn,ln,un)

    # ── Состояния админа ──
    if cid==ADMIN_CHAT_ID and cid in admin_state:
        st=admin_state[cid]; step=st.get("step"); action=st.get("action","create")
        if text=="/cancel": del admin_state[cid]; send_message(cid,"❌ Отменено."); return

        if action=="create":
            if step=="name": st["name"]=text; st["step"]="date"; send_message(cid,"📅 <b>Дата</b>\n\n<i>Например: 15 августа</i>"); return
            elif step=="date": st["date"]=text; st["step"]="time"; send_message(cid,"🕐 <b>Время</b>\n\n<i>Например: 18:00</i>"); return
            elif step=="time": st["time"]=text; st["step"]="place"; send_message(cid,"📍 <b>Место</b>\n\n<i>Например: Азимут, ул. Ленина 21</i>"); return
            elif step=="place": st["place"]=text; st["step"]="description"; send_message(cid,"📝 <b>Подробное описание</b> для кнопки «Подробнее».\n\nЕсли пока нет — отправьте <b>-</b>"); return
            elif step=="description":
                st["desc"]="" if text=="-" else (text or caption or "")
                st["step"]="announce_media"
                send_message(cid,"🖼 Прикрепите <b>картинку или видео для анонса</b> (увидят все при рассылке).\n\nЕсли без медиа — отправьте <b>-</b>"); return
            elif step=="announce_media":
                if mt: st["mt"]=mt; st["mfid"]=mfid
                else: st["mt"]=""; st["mfid"]=""
                st["step"]="desc_media"
                send_message(cid,"🖼 Прикрепите <b>картинку или видео для подробного описания</b> (увидят при нажатии «Подробнее»).\n\nЕсли без медиа — отправьте <b>-</b>"); return
            elif step=="desc_media":
                dmt=mt or ""; dmfid=mfid or ""
                eid=create_event(st["name"],st["date"],st["time"],st["place"],st["desc"],
                                 st.get("mt",""),st.get("mfid",""),dmt,dmfid)
                evts=get_active_events(); dnum=next((e["display_num"] for e in evts if e["id"]==eid),"?")
                del admin_state[cid]
                send_message(cid,f"✅ <b>Мероприятие #{dnum} создано!</b>\n\n📌 {st['name']}\n📅 {st['date']} в {st['time']}\n📍 {st['place']}\n\nАнонс: <code>/broadcast {dnum}</code>\nСтатистика: <code>/stats {dnum}</code>"); return

        elif action=="edit":
            if step=="choose_field":
                fm={"1":"name","2":"date","3":"time","4":"place","5":"description","6":"announce_media","7":"desc_media"}
                if text in fm:
                    if text in ("6","7"):
                        st["field"]=fm[text]; st["step"]="new_media"
                        send_message(cid,"🖼 Прикрепите новую <b>картинку или видео</b> (или <b>-</b> чтобы убрать):"); return
                    st["field"]=fm[text]; st["step"]="new_value"
                    labels={"name":"название","date":"дату","time":"время","place":"место","description":"описание"}
                    send_message(cid,f"✏️ Введите новое <b>{labels[fm[text]]}</b>:"); return
                else: send_message(cid,"Введите число 1-7"); return
            elif step=="new_value":
                update_event_field(st["event_id"],st["field"],text)
                e=get_event(st["event_id"]); del admin_state[cid]
                send_message(cid,f"✅ Обновлено!\n\n{etxt(e)}"); return
            elif step=="new_media":
                eid=st["event_id"]
                if st["field"]=="announce_media":
                    if mt: update_event_field(eid,"media_type",mt); update_event_field(eid,"media_file_id",mfid)
                    else: update_event_field(eid,"media_type",""); update_event_field(eid,"media_file_id","")
                else:
                    if mt: update_event_field(eid,"desc_media_type",mt); update_event_field(eid,"desc_media_file_id",mfid)
                    else: update_event_field(eid,"desc_media_type",""); update_event_field(eid,"desc_media_file_id","")
                del admin_state[cid]
                send_message(cid,"✅ Медиа обновлено!"); return

        elif action=="custom_remind":
            if step=="text":
                eid=st["event_id"]; e=get_event(eid); users=get_no_response_users(eid)
                sent=errors=0
                for u in users:
                    result=send_event_message(u["chat_id"],f"⏰ <b>Напоминание</b>\n\n{etxt(e)}\n\n{text}",e,make_kb(eid))
                    if result.get("ok"): sent+=1
                    else: errors+=1
                    time.sleep(0.05)
                del admin_state[cid]; send_message(cid,f"✅ Напоминание!\n📨 {sent} · ⚠️ {errors}"); return

        elif action=="news":
            if step=="text":
                st["news_text"]=text or caption or ""
                if mt: st["news_mt"]=mt; st["news_mfid"]=mfid
                st["step"]="news_media"
                if mt:
                    send_message(cid,"Текст и медиа получены. Отправить рассылку?",
                        {"inline_keyboard":[[{"text":"✅ Отправить","callback_data":"news_send"},{"text":"❌ Отмена","callback_data":"news_cancel"}]]})
                else:
                    send_message(cid,"🖼 Прикрепите <b>картинку или видео</b> к новости.\n\nЕсли без медиа — отправьте <b>-</b>"); return
            elif step=="news_media":
                if mt: st["news_mt"]=mt; st["news_mfid"]=mfid
                send_message(cid,"Всё готово. Отправить рассылку?",
                    {"inline_keyboard":[[{"text":"✅ Отправить","callback_data":"news_send"},{"text":"❌ Отмена","callback_data":"news_cancel"}]]})
                return

    # Кнопки новостей
    if "callback_query" in update:
        return  # уже обработано выше

    # ── Команды ──
    if text=="/start":
        e=get_latest_event(); name=get_display_name(fn,un)
        is_contact=get_contact_name(un) is not None if un else False
        if not is_contact:
            send_message(cid,"Наш чат-бот только для резидентов мужского клуба предпринимателей Level Up.\n\nЕсли вы уже присоединились к нашему бизнес-клубу, напишите Службе заботы @Zabotalevelup и мы зарегистрируем вас в нашем чат-боте.\n\nДо встречи!")
            return
        if e: send_event_message(cid,f"👋 Привет, <b>{name}</b>!\n\nНаше ближайшее мероприятие мужского клуба предпринимателей Level Up:\n\n{etxt(e)}\n\nТы придёшь?",e,make_kb(e["id"]))
        else: send_message(cid,f"👋 Привет, <b>{name}</b>!\n\nЭто бот мужского клуба предпринимателей Level Up.\n\nПока мероприятий нет — я пришлю уведомление, когда появится.")

    elif text=="/newevent" and cid==ADMIN_CHAT_ID:
        admin_state[cid]={"action":"create","step":"name"}
        send_message(cid,"🆕 <b>Новое мероприятие</b>\n\nВведите <b>название</b>\n\n/cancel — отменить")

    elif text=="/news" and cid==ADMIN_CHAT_ID:
        admin_state[cid]={"action":"news","step":"text"}
        send_message(cid,"📢 <b>Новость / анонс партнёра</b>\n\nНапишите текст рассылки.\nМожно сразу прикрепить картинку или видео к сообщению.\n\n/cancel — отменить")

    elif text.startswith("/edit") and cid==ADMIN_CHAT_ID:
        parts=text.split(); eid=resolve_event_num(parts)
        if not eid: send_message(cid,"❌ Не найдено. /events"); return
        e=get_event(eid); admin_state[cid]={"action":"edit","step":"choose_field","event_id":eid}
        has_media="✅" if e.get("media_file_id") else "—"
        has_dmedia="✅" if e.get("desc_media_file_id") else "—"
        send_message(cid,f"✏️ <b>Редактирование: {e['name']}</b>\n\n<b>1</b> — Название: {e['name']}\n<b>2</b> — Дата: {e['date']}\n<b>3</b> — Время: {e['time']}\n<b>4</b> — Место: {e['place']}\n<b>5</b> — Описание\n<b>6</b> — Медиа анонса ({has_media})\n<b>7</b> — Медиа описания ({has_dmedia})\n\nОтправьте число (1-7) или /cancel")

    elif text=="/events" and cid==ADMIN_CHAT_ID:
        auto_archive(); evts=get_active_events()
        if not evts: send_message(cid,"Активных нет.\n/newevent — создать\n/archive — прошедшие"); return
        m="📋 <b>Активные мероприятия:</b>\n\n"
        for e in evts:
            s=get_event_stats(e["id"])
            m+=f"<b>#{e['display_num']}</b> {e['name']}\n   📅 {e['date']} в {e['time']} · 📍 {e['place']}\n   ✅ {s['going']} · ❌ {s['not_going']}\n\n"
        m+="<code>/broadcast N</code> · <code>/remind N</code> · <code>/edit N</code>"
        send_message(cid,m)

    elif text=="/archive" and cid==ADMIN_CHAT_ID:
        evts=get_archived_events()
        if not evts: send_message(cid,"Архив пуст."); return
        m="📦 <b>Архив:</b>\n\n"
        for e in evts:
            s=get_event_stats(e["id"]); m+=f"📌 {e['name']}\n   📅 {e['date']} · ✅ {s['going']} · ❌ {s['not_going']}\n\n"
        send_message(cid,m)

    elif text.startswith("/broadcast") and cid==ADMIN_CHAT_ID:
        parts=text.split(); eid=resolve_event_num(parts)
        if not eid: send_message(cid,"❌ Не найдено. /events"); return
        e=get_event(eid); send_message(cid,f"📤 Рассылка: {e['name']}...")
        sent,err=broadcast_event(eid)
        s=get_event_stats(eid); m=f"✅ <b>Рассылка завершена!</b>\n\n📨 {sent} · ⚠️ {err}"
        if s["not_activated"]:
            m+=f"\n\n⏳ <b>Не в боте ({s['not_activated_count']}):</b>\n"
            for c in s["not_activated"][:20]: m+=f"  • {c['name']} {c.get('contact_last','')} — @{c['username']}\n"
        send_message(cid,m)

    elif text.startswith("/sendnew") and cid==ADMIN_CHAT_ID:
        parts=text.split(); eid=resolve_event_num(parts)
        if not eid: send_message(cid,"❌ Не найдено."); return
        users=get_unsent_users(eid)
        if not users: send_message(cid,"Все уже получили анонс."); return
        send_message(cid,f"📤 Отправка новым ({len(users)})...")
        sent,err=broadcast_event(eid,users)
        send_message(cid,f"✅ {sent} отправлено, {err} ошибок")

    elif text.startswith("/send ") and cid==ADMIN_CHAT_ID:
        parts=text.split(None,2)
        if len(parts)<3: send_message(cid,"<code>/send N @username</code>"); return
        try: num=int(parts[1])
        except: send_message(cid,"<code>/send N @username</code>"); return
        e=get_event_by_display_num(num)
        if not e: send_message(cid,"❌ Не найдено."); return
        uname=parts[2].replace("@","").strip()
        user=get_user_by_username(uname)
        if not user: send_message(cid,f"❌ @{uname} не в боте."); return
        ok=send_event_to_user(e["id"],user)
        send_message(cid,f"✅ Отправлено @{uname}!" if ok else "❌ Ошибка")

    elif text.startswith("/remindall") and cid==ADMIN_CHAT_ID:
        parts=text.split(); eid=resolve_event_num(parts)
        if not eid: send_message(cid,"❌ Не найдено. /events"); return
        e=get_event(eid)
        conn=get_db(); c=conn.cursor()
        c.execute("SELECT u.chat_id,u.first_name,u.last_name,u.username FROM users u INNER JOIN contacts ct ON LOWER(u.username)=LOWER(ct.username) LEFT JOIN rsvp r ON u.chat_id=r.chat_id AND r.event_id=? WHERE r.status IS NULL OR r.status='going'",(eid,))
        users=[dict(r) for r in c.fetchall()]; conn.close()
        if not users: send_message(cid,"Все отказались."); return
        send_message(cid,f"⏰ Напоминание ({len(users)}) по «{e['name']}»...")
        sent=errors=0
        for u in users:
            result=send_event_message(u["chat_id"],f"⏰ <b>Напоминание!</b>\n\n{etxt(e)}\n\nТы придёшь?",e,make_kb(eid))
            if result.get("ok"): sent+=1
            else: errors+=1
            time.sleep(0.05)
        send_message(cid,f"✅ {sent} отправлено, {errors} ошибок")

    elif text.startswith("/remind") and cid==ADMIN_CHAT_ID:
        parts=text.split(); eid=resolve_event_num(parts)
        if not eid: send_message(cid,"❌ Не найдено."); return
        e=get_event(eid); nr=get_no_response_users(eid)
        if not nr: send_message(cid,"Все ответили! 🎉"); return
        send_message(cid,f"⏰ <b>{e['name']}</b>\n\nНе ответили: {len(nr)}",
            {"inline_keyboard":[[
                {"text":"📤 Стандартное","callback_data":f"stdremind_{eid}"},
                {"text":"✏️ Свой текст","callback_data":f"custremind_{eid}"}]]})

    elif text.startswith("/stats") and cid==ADMIN_CHAT_ID:
        parts=text.split(); eid=resolve_event_num(parts)
        if not eid: send_message(cid,"❌ Не найдено."); return
        s=get_event_stats(eid)
        send_message(cid,f"📊 <b>{s['event']['name']}</b>\n\n📋 {s['total_contacts']} в таблице · 🤖 {s['total_active']} в боте · ⏳ {s['not_activated_count']} не в боте\n\n✅ {s['going']} идут · ❌ {s['not_going']} не смогут · 🤷 {s['no_response']} молчат")

    elif text.startswith("/remove") and cid==ADMIN_CHAT_ID:
        parts=text.split()
        if len(parts)<2: send_message(cid,"<code>/remove @username</code>"); return
        uname=parts[1].replace("@","").strip().lower()
        conn=get_db(); c=conn.cursor()
        c.execute("DELETE FROM contacts WHERE LOWER(username)=?",(uname,))
        c.execute("DELETE FROM users WHERE LOWER(username)=?",(uname,))
        deleted=conn.total_changes; conn.commit(); conn.close()
        if deleted>0: send_message(cid,f"✅ @{uname} удалён.\n⚠️ Удалите из Google-таблицы тоже.")
        else: send_message(cid,f"❌ @{uname} не найден.")

    elif text=="/cleanup" and cid==ADMIN_CHAT_ID:
        n=auto_archive(); send_message(cid,f"🗂 Архивировано: {n}" if n else "Всё актуально.")

    elif text=="/sync" and cid==ADMIN_CHAT_ID:
        cnt,s=sync_google_sheet()
        send_message(cid,f"✅ Таблица обновлена! Контактов: {cnt}" if s=="OK" else f"❌ {s}")

    elif text=="/link" and cid==ADMIN_CHAT_ID:
        bu=get_bot_username()
        send_message(cid,f"🔗 https://t.me/{bu}" if bu else "Ошибка")

    elif text=="/cancel" and cid==ADMIN_CHAT_ID:
        if cid in admin_state: del admin_state[cid]
        send_message(cid,"❌ Отменено.")

    elif text=="/help":
        h="📋 <b>Команды:</b>\n\n/start — О ближайшей встрече\n"
        if cid==ADMIN_CHAT_ID:
            h+=("\n<b>Мероприятия:</b>\n/newevent — создать\n/events — активные\n/archive — прошедшие\n/edit N — редактировать\n/cleanup — архивировать\n\n"
                "<b>Рассылки:</b>\n/broadcast N — анонс всем\n/sendnew N — только новым\n/send N @user — одному\n/remind N — неответившим\n/remindall N — молчунам + идущим\n/news — новость / анонс партнёра\n\n"
                "<b>Участники:</b>\n/sync — обновить таблицу\n/remove @user — удалить\n/stats N — статистика\n/link — ссылка на бота\n")
        send_message(cid,h)
    else:
        if cid!=ADMIN_CHAT_ID:
            send_message(ADMIN_CHAT_ID,f"💬 <b>{fn} {ln}</b> (@{un}):\n\n{text}")
            send_message(cid,"Спасибо! Сообщение передано организатору. 🤝")

def handle_news_callbacks(update):
    if "callback_query" not in update: return False
    cb=update["callback_query"]; data=cb.get("data",""); cid=cb["from"]["id"]
    if cid!=ADMIN_CHAT_ID: return False
    if data=="news_send" and cid in admin_state and admin_state[cid].get("action")=="news":
        st=admin_state[cid]; answer_callback(cb["id"],"Отправляю...")
        sent,err=broadcast_news(st.get("news_text",""),st.get("news_mt"),st.get("news_mfid"))
        del admin_state[cid]; send_message(cid,f"✅ Новость отправлена!\n📨 {sent} · ⚠️ {err}"); return True
    elif data=="news_cancel":
        if cid in admin_state: del admin_state[cid]
        answer_callback(cb["id"],"Отменено"); send_message(cid,"❌ Рассылка отменена."); return True
    return False

def handle_all(update):
    if handle_news_callbacks(update): return
    handle_update(update)

def poll_updates():
    offset=0
    while True:
        try:
            r=requests.get(f"{API}/getUpdates",params={"offset":offset,"timeout":30},timeout=35).json()
            if r.get("ok"):
                for u in r["result"]:
                    offset=u["update_id"]+1
                    try: handle_all(u)
                    except Exception as e: print(f"Err: {e}")
        except Exception as e: print(f"Poll: {e}"); time.sleep(5)

# ═══════════════════════════════════════════
# ВЕБ
# ═══════════════════════════════════════════
app=Flask(__name__)

PG_LIST="""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Level Up — Мероприятия</title><style>
*{margin:0;padding:0;box-sizing:border-box;}body{background:#0A0D24;color:#fff;font-family:'Segoe UI',system-ui,sans-serif;padding:40px 20px;}
.wrap{max-width:900px;margin:0 auto;}h1{font-size:28px;font-weight:800;margin-bottom:8px;}.sub{color:#A6ACD4;font-size:15px;margin-bottom:30px;}
.tabs{display:flex;gap:8px;margin-bottom:30px;}.tab{padding:10px 20px;border-radius:999px;font-size:14px;font-weight:600;text-decoration:none;border:1px solid #33397A;color:#A6ACD4;transition:all .2s;}
.tab.active,.tab:hover{background:#33397A;color:#fff;}
.ec{background:#161C46;border:1px solid #33397A;border-radius:16px;padding:28px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center;text-decoration:none;color:#fff;transition:all .25s;cursor:pointer;}
.ec:hover{border-color:#5B9BFF;transform:translateY(-3px);}
.ec .i h2{font-size:20px;font-weight:800;margin-bottom:6px;}.ec .i p{font-size:14px;color:#A6ACD4;}
.ec .ns{display:flex;gap:20px;text-align:center;}.nb .n{font-size:28px;font-weight:800;}.nb .l{font-size:11px;color:#A6ACD4;text-transform:uppercase;}
.go .n{color:#4ADE80;}.ng .n{color:#F87171;}.empty{color:#A6ACD4;text-align:center;padding:60px 0;}
</style></head><body><div class="wrap"><h1>📊 Level Up — Мероприятия</h1><p class="sub">Мужской клуб предпринимателей</p>
<div class="tabs"><a class="tab {{'active' if tab=='active' else ''}}" href="/">Активные</a><a class="tab {{'active' if tab=='archive' else ''}}" href="/archive">Архив</a></div>
{% if events %}{% for e in events %}<a class="ec" href="/event/{{e.id}}"><div class="i"><h2>{{e.name}}</h2><p>📅 {{e.date}} в {{e.time}} · 📍 {{e.place}}</p></div>
<div class="ns"><div class="nb go"><div class="n">{{e.going}}</div><div class="l">Идут</div></div><div class="nb ng"><div class="n">{{e.not_going}}</div><div class="l">Не смогут</div></div></div></a>
{% endfor %}{% else %}<div class="empty">{{'Активных мероприятий нет' if tab=='active' else 'Архив пуст'}}</div>{% endif %}
</div><script>setTimeout(()=>location.reload(),30000);</script></body></html>"""

PG_DETAIL="""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{{event.name}}</title><style>
*{margin:0;padding:0;box-sizing:border-box;}body{background:#0A0D24;color:#fff;font-family:'Segoe UI',system-ui,sans-serif;padding:40px 20px;}
.wrap{max-width:900px;margin:0 auto;}.back{color:#5B9BFF;text-decoration:none;font-size:14px;display:inline-block;margin-bottom:20px;}
h1{font-size:26px;font-weight:800;margin-bottom:6px;}.sub{color:#A6ACD4;font-size:15px;margin-bottom:40px;}
.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:40px;}
.card{background:#161C46;border:1px solid #33397A;border-radius:16px;padding:22px 16px;text-align:center;}
.card .num{font-size:36px;font-weight:800;line-height:1;}.card .label{font-size:11px;color:#A6ACD4;text-transform:uppercase;margin-top:8px;}
.go .num{color:#4ADE80;}.no .num{color:#F87171;}.wa .num{color:#FACC15;}.to .num{color:#5B9BFF;}.bo .num{color:#818CF8;}
.bar-wrap{background:#161C46;border:1px solid #33397A;border-radius:12px;padding:20px;margin-bottom:40px;}
.bar-bg{background:#0A0D24;border-radius:8px;height:32px;display:flex;overflow:hidden;}
.bar-go{background:#4ADE80;height:100%;}.bar-no{background:#F87171;height:100%;}
.bl{display:flex;gap:20px;margin-top:12px;font-size:13px;color:#A6ACD4;}
.bl span::before{content:'';display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;}
.lg::before{background:#4ADE80!important;}.ln::before{background:#F87171!important;}.lw::before{background:#333!important;}
h2{font-size:20px;font-weight:700;margin-bottom:16px;}.sec{margin-bottom:40px;}
table{width:100%;border-collapse:collapse;}th{text-align:left;font-size:12px;color:#A6ACD4;text-transform:uppercase;padding:10px 12px;border-bottom:1px solid #33397A;}
td{padding:12px;border-bottom:1px solid #1D2456;font-size:14px;}
.warn{background:#261A00;border:1px solid #FB923C;border-radius:12px;padding:20px;margin-bottom:40px;}
.warn h3{color:#FB923C;font-size:16px;margin-bottom:10px;}.warn li{padding:4px 0;font-size:14px;color:#A6ACD4;list-style:none;}.warn b{color:#fff;}
.ref{color:#5B9BFF;font-size:13px;text-decoration:none;float:right;margin-top:-36px;}
@media(max-width:700px){.cards{grid-template-columns:repeat(2,1fr);}}
</style></head><body><div class="wrap">
<a class="back" href="/">← Назад</a><h1>📊 {{event.name}}</h1><p class="sub">📅 {{event.date}} в {{event.time}} · 📍 {{event.place}}</p>
<div class="cards"><div class="card to"><div class="num">{{total_contacts}}</div><div class="label">В таблице</div></div>
<div class="card bo"><div class="num">{{total_active}}</div><div class="label">В боте</div></div>
<div class="card go"><div class="num">{{going}}</div><div class="label">Идут</div></div>
<div class="card no"><div class="num">{{not_going}}</div><div class="label">Не смогут</div></div>
<div class="card wa"><div class="num">{{no_response}}</div><div class="label">Молчат</div></div></div>
{% if total_active>0 %}{% set pg=(going/total_active*100) %}{% set pn=(not_going/total_active*100) %}
<div class="bar-wrap"><div class="bar-bg"><div class="bar-go" style="width:{{pg}}%"></div><div class="bar-no" style="width:{{pn}}%"></div></div>
<div class="bl"><span class="lg">Идут ({{pg|round(0)|int}}%)</span><span class="ln">Не смогут ({{pn|round(0)|int}}%)</span><span class="lw">Не ответили</span></div></div>{% endif %}
{% if not_activated_count>0 %}<div class="warn"><h3>⏳ Не в боте ({{not_activated_count}})</h3>
<ul>{% for c in not_activated %}<li><b>{{c.name}} {{c.get('contact_last','')}}</b> — @{{c.username}}</li>{% endfor %}</ul></div>{% endif %}
<div class="sec"><h2>✅ Идут</h2><a class="ref" href="/event/{{event.id}}">🔄</a>
<table><tr><th>Фамилия</th><th>Имя</th><th>Telegram</th><th>Когда</th></tr>
{% for r in responses if r.status=='going' %}<tr><td>{{r.last_name}}</td><td>{{r.first_name}}</td><td>{{'@'+r.username if r.username else '—'}}</td><td>{{r.responded_at[:16]}}</td></tr>{% endfor %}</table></div>
<div class="sec"><h2>❌ Не смогут</h2><table><tr><th>Фамилия</th><th>Имя</th><th>Telegram</th><th>Когда</th></tr>
{% for r in responses if r.status=='not_going' %}<tr><td>{{r.last_name}}</td><td>{{r.first_name}}</td><td>{{'@'+r.username if r.username else '—'}}</td><td>{{r.responded_at[:16]}}</td></tr>{% endfor %}</table></div>
</div><script>setTimeout(()=>location.reload(),30000);</script></body></html>"""

@app.route("/")
def pg_active():
    auto_archive(); evts=get_active_events()
    for e in evts: s=get_event_stats(e["id"]); e["going"]=s["going"]; e["not_going"]=s["not_going"]
    return render_template_string(PG_LIST,events=evts,tab="active")

@app.route("/archive")
def pg_archive():
    evts=get_archived_events()
    for e in evts: s=get_event_stats(e["id"]); e["going"]=s["going"]; e["not_going"]=s["not_going"]
    return render_template_string(PG_LIST,events=evts,tab="archive")

@app.route("/event/<int:eid>")
def pg_detail(eid):
    s=get_event_stats(eid)
    if not s["event"]: return "Не найдено",404
    return render_template_string(PG_DETAIL,**s)

if __name__=="__main__":
    init_db(); bu=get_bot_username()
    print("="*55); print(f"  🤖  Level Up Bot v5")
    if bu: print(f"  🔗  https://t.me/{bu}")
    print(f"  📊  http://localhost:{WEB_PORT}"); print("="*55)
    threading.Thread(target=poll_updates,daemon=True).start()
    threading.Thread(target=archive_loop,daemon=True).start()
    app.run(host="0.0.0.0",port=WEB_PORT)
