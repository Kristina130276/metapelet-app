"""Защита бюджета пилота MetaPelet.

Все проверки выполняются на сервере ДО обращения к платным сервисам
(Anthropic и Google TTS). Счётчики сбрасываются в полночь по Израилю.
"""
import hashlib
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Jerusalem")

DAILY_GLOBAL_REPLIES = int(os.environ.get("PILOT_DAILY_REPLIES", "300"))
DAILY_PER_USER_REPLIES = int(os.environ.get("PILOT_DAILY_PER_USER", "40"))
# Один IP может быть общим (семья, дневной центр) — даём запас на ~3 участников.
DAILY_PER_IP_REPLIES = int(os.environ.get("PILOT_DAILY_PER_IP", "120"))
MAX_USER_MESSAGE_CHARS = 1000
MAX_TTS_CHARS = 1200
TTS_PLAYS_PER_REPLY = 4          # первое воспроизведение + повторы «Не слышно?»
DAILY_TTS_CALLS = DAILY_GLOBAL_REPLIES * 2

LIMIT_MESSAGES = {
    "ru": "На сегодня лимит бесплатного тестирования исчерпан. Спасибо, что разговариваете с MetaPelet! Пожалуйста, возвращайтесь завтра.",
    "he": "מכסת הבדיקה החינמית להיום הסתיימה. תודה ששוחחתם עם MetaPelet! נשמח לראותכם שוב מחר.",
    "en": "Today's free testing limit has been reached. Thank you for talking with MetaPelet! Please come back tomorrow.",
}

# Must point to a persistent mounted volume on Render. Local SQLite survives
# process restarts, but an ephemeral filesystem does NOT survive deployments.
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("BUDGET_DB_PATH", "data/budget.sqlite3"))


def _today():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), timeout=5, isolation_level=None)
    db.execute("CREATE TABLE IF NOT EXISTS counters (day TEXT, scope TEXT, used INTEGER NOT NULL, PRIMARY KEY(day, scope))")
    db.execute("CREATE TABLE IF NOT EXISTS permits (hash TEXT PRIMARY KEY, remaining INTEGER NOT NULL, day TEXT)")
    return db


def _used(db, day, scope):
    row = db.execute("SELECT used FROM counters WHERE day=? AND scope=?", (day, scope)).fetchone()
    return row[0] if row else 0


def _bump(db, day, scope):
    db.execute("INSERT INTO counters VALUES (?, ?, 1) ON CONFLICT(day, scope) DO UPDATE SET used=used+1", (day, scope))


def client_ip(request):
    # Заголовки, которые выставляет прокси Render/Cloudflare (клиент их подделать не может).
    for h in ("CF-Connecting-IP", "True-Client-IP"):
        v = request.headers.get(h)
        if v:
            return v.strip()
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        # Последний адрес добавлен ближайшим доверенным прокси.
        return fwd.split(",")[-1].strip()
    return request.remote_addr or "unknown"


def limit_message(language):
    lang = (language or "ru").lower()[:2]
    return LIMIT_MESSAGES.get(lang, LIMIT_MESSAGES["ru"])


def try_consume_reply(user_id, ip):
    db = None
    try:
        db = _db()
        db.execute("BEGIN IMMEDIATE")
        day = _today()
        scopes = [("reply", DAILY_GLOBAL_REPLIES, "global"),
                  ("user:" + _h((user_id or "default")[:100]), DAILY_PER_USER_REPLIES, "user"),
                  ("ip:" + _h(ip), DAILY_PER_IP_REPLIES, "ip")]
        for scope, limit, reason in scopes:
            if _used(db, day, scope) >= limit:
                db.rollback()
                return False, reason
        for scope, _, _ in scopes:
            _bump(db, day, scope)
        db.execute("DELETE FROM counters WHERE day < ?", (day,))
        db.commit()
        return True, None
    except (sqlite3.Error, OSError):
        if db is not None:
            db.rollback()
        return False, "storage_unavailable"
    finally:
        if db is not None:
            db.close()


def refund_reply(user_id, ip):
    # A timeout/error does not establish that the provider did not bill the call.
    # Keep the reservation: fail conservatively instead of allowing free retries.
    return None


def _norm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _h(text):
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


def allow_tts_for(text):
    db = None
    try:
        db = _db()
        day = _today()
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM permits WHERE day <> ?", (day,))
        db.execute("INSERT OR REPLACE INTO permits VALUES (?, ?, ?)", (_h(text), TTS_PLAYS_PER_REPLY, day))
        db.commit()
        return True
    except (sqlite3.Error, OSError):
        if db is not None:
            db.rollback()
        return False
    finally:
        if db is not None:
            db.close()


STATIC_TTS_OK = {
    "Что-то пошло не так. Попробуй ещё раз.",
    "Что-то пошло не так. Попробуй сказать ещё раз.",
    "Я не услышала тебя. Попробуй ещё раз.",
}


def try_consume_tts(text):
    if len(text or "") > MAX_TTS_CHARS:
        return False, "too_long"
    db = None
    try:
        db = _db()
        db.execute("BEGIN IMMEDIATE")
        day = _today()
        if _used(db, day, "tts") >= DAILY_TTS_CALLS:
            db.rollback()
            return False, "global"
        if _norm(text) not in STATIC_TTS_OK:
            row = db.execute("SELECT remaining FROM permits WHERE hash=? AND day=?", (_h(text), day)).fetchone()
            if not row or row[0] <= 0:
                db.rollback()
                return False, "unknown_text"
            db.execute("UPDATE permits SET remaining=remaining-1 WHERE hash=?", (_h(text),))
        _bump(db, day, "tts")
        db.commit()
        return True, None
    except (sqlite3.Error, OSError):
        if db is not None:
            db.rollback()
        return False, "storage_unavailable"
    finally:
        if db is not None:
            db.close()


def status():
    db = None
    try:
        db = _db()
        day = _today()
        return {"day": day, "replies_used": _used(db, day, "reply"),
                "replies_limit": DAILY_GLOBAL_REPLIES, "tts_used": _used(db, day, "tts"),
                "tts_limit": DAILY_TTS_CALLS}
    except (sqlite3.Error, OSError):
        return {"error": "storage_unavailable"}
    finally:
        if db is not None:
            db.close()
