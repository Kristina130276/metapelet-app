"""Защита бюджета пилота MetaPelet.

Все проверки выполняются на сервере ДО обращения к платным сервисам
(Anthropic и Google TTS). Счётчики сбрасываются в полночь по Израилю.
"""
import hashlib
import os
import re
import threading
import time
from collections import OrderedDict
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

_lock = threading.Lock()
_day = None
_global = 0
_tts_calls = 0
_per_user = {}
_per_ip = {}
_allowed_tts = OrderedDict()   # хэш текста ответа -> оставшиеся воспроизведения


def _today():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _roll_day():
    global _day, _global, _tts_calls, _per_user, _per_ip
    d = _today()
    if d != _day:
        _day, _global, _tts_calls = d, 0, 0
        _per_user, _per_ip = {}, {}


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
    """True, если можно сделать ещё один ответ ИИ; сразу резервирует его."""
    with _lock:
        _roll_day()
        global _global
        uid = (user_id or "default")[:100]
        if _global >= DAILY_GLOBAL_REPLIES:
            return False, "global"
        if _per_user.get(uid, 0) >= DAILY_PER_USER_REPLIES:
            return False, "user"
        if _per_ip.get(ip, 0) >= DAILY_PER_IP_REPLIES:
            return False, "ip"
        _global += 1
        _per_user[uid] = _per_user.get(uid, 0) + 1
        _per_ip[ip] = _per_ip.get(ip, 0) + 1
        return True, None


def refund_reply(user_id, ip):
    """Ответ не получился (ошибка ИИ) — не засчитываем."""
    with _lock:
        global _global
        uid = (user_id or "default")[:100]
        _global = max(0, _global - 1)
        if _per_user.get(uid):
            _per_user[uid] -= 1
        if _per_ip.get(ip):
            _per_ip[ip] -= 1


def _norm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _h(text):
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


def allow_tts_for(text):
    """Озвучивать можно только тексты, которые выдал сам сервер."""
    with _lock:
        _allowed_tts[_h(text)] = TTS_PLAYS_PER_REPLY
        while len(_allowed_tts) > 2000:
            _allowed_tts.popitem(last=False)


STATIC_TTS_OK = {
    "Что-то пошло не так. Попробуй ещё раз.",
    "Что-то пошло не так. Попробуй сказать ещё раз.",
    "Я не услышала тебя. Попробуй ещё раз.",
}


def try_consume_tts(text):
    with _lock:
        _roll_day()
        global _tts_calls
        if len(text or "") > MAX_TTS_CHARS:
            return False, "too_long"
        if _tts_calls >= DAILY_TTS_CALLS:
            return False, "global"
        key = _h(text)
        if _norm(text) in STATIC_TTS_OK:
            pass
        elif _allowed_tts.get(key, 0) > 0:
            _allowed_tts[key] -= 1
        else:
            return False, "unknown_text"
        _tts_calls += 1
        return True, None


def status():
    with _lock:
        _roll_day()
        return {"day": _day, "replies_used": _global, "replies_limit": DAILY_GLOBAL_REPLIES,
                "tts_used": _tts_calls, "tts_limit": DAILY_TTS_CALLS}
