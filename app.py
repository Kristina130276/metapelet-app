from flask import Flask, request, jsonify, send_file, Response, redirect
from flask_cors import CORS
from anthropic import Anthropic
from dotenv import load_dotenv
import os
import base64
import re
import requests as http_requests
from pathlib import Path
import logging

load_dotenv()

logging.basicConfig(level=logging.INFO)

import pilot_tracking as pilot
import budget_guard as budget
import tts_budget
import conversation_memory

app = Flask(__name__)
CORS(app)

# Configure logging if not already configured
if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=logging.INFO)

# Initialize database tables if DATABASE_URL is set
pilot.init_database()
tts_budget.init_database()
conversation_memory.init_database()

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PROFILE_FILES = {
    "demo": "profiles/demo.txt",
}

# Словарь: user_id → история сообщений
conversations = {}
MAX_MESSAGES = 10
GREETING_TRIGGER = "__greeting__"

# Track when to update memory summary (independent of trimmed history)
_user_message_counters = {}  # user_id -> count of user messages since last summary
_unsummarized_buffers = {}  # user_id -> list of unsummarized messages (user + assistant)
_summarization_in_progress = set()  # user_ids currently being summarized (concurrency guard)

_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U00002600-\U000026FF"
    "\U0000FE0F"
    "]+",
    flags=re.UNICODE,
)


def strip_emojis(text: str) -> str:
    cleaned = _EMOJI_RE.sub("", text or "")
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _tts_voice_for_language(language: str) -> dict:
    """Google TTS: Chirp3-HD (самый живой) → Wavenet → Standard."""
    presets = {
        "ru-RU": {
            "languageCode": "ru-RU",
            "names": [
                "ru-RU-Chirp3-HD-Kore",
                "ru-RU-Chirp3-HD-Aoede",
                "ru-RU-Wavenet-A",
                "ru-RU-Standard-A",
            ],
            "ssmlGender": "FEMALE",
            "speakingRate": 0.88,
            "pitch": -0.5,
        },
        "he-IL": {
            "languageCode": "he-IL",
            "names": ["he-IL-Wavenet-A", "he-IL-Standard-A"],
            "ssmlGender": "FEMALE",
            "speakingRate": 0.93,
            "pitch": 0.0,
        },
        "en-US": {
            "languageCode": "en-US",
            "names": ["en-US-Neural2-F", "en-US-Wavenet-F", "en-US-Standard-C"],
            "ssmlGender": "FEMALE",
            "speakingRate": 0.93,
            "pitch": 0.0,
        },
    }
    return presets.get(language, presets["ru-RU"])


def _google_api_key() -> str:
    return (os.environ.get("GOOGLE_API_KEY") or "").strip()


def _parse_google_error(resp: http_requests.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error", {})
        msg = err.get("message", resp.text[:300])
        status = err.get("status", "")
        details = err.get("details") or []
        project = ""
        reason = ""
        for d in details:
            if isinstance(d, dict):
                meta = d.get("metadata") or {}
                project = project or meta.get("containerInfo") or meta.get("consumer", "")
                reason = reason or d.get("reason", "")
        parts = [status, reason, msg]
        if project:
            parts.append(f"project={project}")
        return " | ".join(p for p in parts if p)
    except Exception:
        return resp.text[:300]


def _check_google_tts() -> dict:
    api_key = _google_api_key()
    if not api_key:
        return {"ok": False, "reason": "GOOGLE_API_KEY не задан на сервере"}

    url = f"https://texttospeech.googleapis.com/v1/voices?key={api_key}&languageCode=ru-RU"
    try:
        resp = http_requests.get(url, timeout=12)
        if resp.ok:
            voices = [v.get("name") for v in resp.json().get("voices", [])]
            chirp = [v for v in voices if "Chirp3" in v][:3]
            return {
                "ok": True,
                "key_set": bool(api_key),
                "voice_count": len(voices),
                "chirp3_sample": chirp,
            }
        return {
            "ok": False,
            "key_set": bool(api_key),
            "http_status": resp.status_code,
            "google_error": _parse_google_error(resp),
        }
    except Exception as e:
        return {"ok": False, "key_set": bool(api_key), "reason": str(e)}


def _read_prompt(relative_path: str) -> str:
    path = PROMPTS_DIR / relative_path
    return path.read_text(encoding="utf-8").strip()


def _language_label(language: str) -> str:
    labels = {
        "ru-RU": "русский",
        "he-IL": "иврит",
        "en-US": "английский",
    }
    return labels.get(language, language)


def _language_instruction(language: str) -> str:
    label = _language_label(language)
    return (
        f"ЯЗЫК ОБЩЕНИЯ (единственный): {label}.\n"
        f"Пиши, думай и отвечай ТОЛЬКО на {label} языке. "
        f"Язык интерфейса, предыдущих сообщений и системных настроек игнорируется. "
        f"Не переключай язык самостоятельно и не переводи речь пользователя, "
        f"если он прямо не попросил. Озвучивай на том же языке, на котором ведёшь диалог."
    )


def _build_anketa_block(interests: str, avoid_topics: str) -> str:
    """Профиль из анкеты на сайте (без БД). Память между сессиями — позже."""
    parts = []
    if interests.strip():
        parts.append(f"Интересы и что нравится:\n{interests.strip()}")
    if avoid_topics.strip():
        parts.append(f"Избегать тем:\n{avoid_topics.strip()}")
    if not parts:
        return ""
    return "[Профиль из анкеты пользователя]\n" + "\n".join(parts)


def _build_memory_block(summary: str, language: str) -> str:
    """Build memory block for system prompt from stored summary."""
    if not summary or not summary.strip():
        return ""
    
    # Language-specific headers
    headers = {
        "ru-RU": "Что ты помнишь о собеседнике из прошлых разговоров:",
        "he-IL": "מה אתה זוכר על המשתמש משיחות קודמות:",
        "en-US": "What you remember about this person from past conversations:",
    }
    header = headers.get(language, headers["ru-RU"])
    
    # Instructions for using memory
    instructions = {
        "ru-RU": (
            "\n\nИспользуй эту память естественно и тепло:\n"
            "- Можешь один раз за ответ мягко вспомнить что-то из памяти\n"
            "- При '[начало разговора]' можешь спросить о чём-то из памяти\n"
            "- НИКОГДА не выдумывай воспоминания\n"
            "- НИКОГДА не перечисляй всё подряд\n"
            "- Если не уверен — не утверждай, что помнишь"
        ),
        "he-IL": (
            "\n\nהשתמש בזיכרון הזה באופן טבעי וחם:\n"
            "- אתה יכול לזכור משהו מהזיכרון פעם אחת בתשובה\n"
            "- ב-'[начало разговора]' אתה יכול לשאול על משהו מהזיכרון\n"
            "- לעולם אל תמציא זיכרונות\n"
            "- לעולם אל תמנה הכל\n"
            "- אם אתה לא בטוח - אל תטען שאתה זוכר"
        ),
        "en-US": (
            "\n\nUse this memory naturally and warmly:\n"
            "- You may gently recall something from memory once per reply\n"
            "- At '[начало разговора]' you may ask about something from memory\n"
            "- NEVER invent memories\n"
            "- NEVER recite the whole list\n"
            "- If unsure, don't claim to remember"
        ),
    }
    instruction = instructions.get(language, instructions["ru-RU"])
    
    return f"[{header}]\n{summary.strip()}{instruction}"


def build_system_prompt(
    name: str,
    age: str,
    language: str = "ru-RU",
    profile_id=None,
    interests: str = "",
    avoid_topics: str = "",
    user_id: str = "",
) -> str:
    user_name = (name or "").strip() or "дорогой человек"
    user_age = (age or "").strip() if age else ""
    age_line = f"Возраст пользователя: {user_age} лет" if user_age else ""

    prompt = _read_prompt("base_metapelet.txt").format(
        user_name=user_name,
        age_line=age_line,
        language_line=_language_instruction(language or "ru-RU"),
    )

    # Add memory from past conversations (if exists)
    if user_id:
        memory_summary = conversation_memory.get_memory(user_id)
        if memory_summary:
            memory_block = _build_memory_block(memory_summary, language)
            if memory_block:
                prompt = prompt + "\n\n" + memory_block

    profile_key = str(profile_id).strip().lower() if profile_id else ""
    profile_file = PROFILE_FILES.get(profile_key) if profile_key else None

    if profile_file:
        prompt = prompt + "\n\n" + _read_prompt(profile_file)
    else:
        anketa_block = _build_anketa_block(interests, avoid_topics)
        if anketa_block:
            prompt = prompt + "\n\n" + anketa_block

    return prompt


def get_history(user_id: str) -> list:
    if user_id not in conversations:
        conversations[user_id] = []
    return conversations[user_id]


def trim_history(user_id: str):
    if len(conversations[user_id]) > MAX_MESSAGES:
        conversations[user_id] = conversations[user_id][-MAX_MESSAGES:]


def _add_to_unsummarized_buffer(user_id: str, role: str, content: str):
    """Add a message to the unsummarized buffer (capped at MAX_BUFFER_MESSAGES)."""
    if user_id not in _unsummarized_buffers:
        _unsummarized_buffers[user_id] = []
    
    _unsummarized_buffers[user_id].append({"role": role, "content": content})
    
    # Cap buffer size
    if len(_unsummarized_buffers[user_id]) > conversation_memory.MAX_BUFFER_MESSAGES:
        _unsummarized_buffers[user_id] = _unsummarized_buffers[user_id][-conversation_memory.MAX_BUFFER_MESSAGES:]


def _increment_user_message_counter(user_id: str):
    """Increment user message counter (for triggering summarization)."""
    _user_message_counters[user_id] = _user_message_counters.get(user_id, 0) + 1


def _should_summarize(user_id: str) -> bool:
    """Check if we should trigger summarization for this user."""
    count = _user_message_counters.get(user_id, 0)
    return count >= conversation_memory.UPDATE_EVERY_N_MESSAGES


def _clear_summarization_state(user_id: str):
    """Clear counter and buffer after successful summarization."""
    _user_message_counters[user_id] = 0
    _unsummarized_buffers[user_id] = []


def _summarize_conversation_async(user_id: str, language: str):
    """
    Update memory summary with recent conversation (non-blocking, best-effort).
    Called after reply is sent to user. Failures are logged but never break chat.
    """
    import threading
    
    def do_summarize():
        try:
            # Concurrency guard: skip if already summarizing for this user
            if user_id in _summarization_in_progress:
                logging.info(f"Summarization already in progress for user, skipping")
                return
            
            _summarization_in_progress.add(user_id)
            
            try:
                # Check budget before summarizing
                allowed, why = budget.try_consume_summary()
                if not allowed:
                    logging.info(f"Summarization skipped: {why}")
                    return
                
                # Get unsummarized messages from buffer
                buffer = _unsummarized_buffers.get(user_id, [])
                if not buffer:
                    return
                
                # Get existing memory
                existing_summary = conversation_memory.get_memory(user_id) or ""
                
                # Build summarization prompt from buffer
                recent_msgs = []
                for msg in buffer:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if content and content != "[начало разговора]":
                        recent_msgs.append(f"{role}: {content}")
                
                if not recent_msgs:
                    return
                
                recent_text = "\n".join(recent_msgs)
                
                # Language-specific summarization prompts
                prompts = {
                    "ru-RU": (
                        f"Предыдущая память о пользователе:\n{existing_summary}\n\n"
                        f"Недавние сообщения:\n{recent_text}\n\n"
                        "Обнови краткую память (до 800 символов): имена близких, события, "
                        "здоровье (только как сказал пользователь), настроение, планы, интересы. "
                        "Только факты. Без домыслов. Без пересказа диалога."
                    ),
                    "he-IL": (
                        f"זיכרון קודם על המשתמש:\n{existing_summary}\n\n"
                        f"הודעות אחרונות:\n{recent_text}\n\n"
                        "עדכן זיכרון תמציתי (עד 800 תווים): שמות של קרובים, אירועים, "
                        "בריאות (רק כפי שהמשתמש אמר), מצב רוח, תוכניות, תחומי עניין. "
                        "רק עובדות. ללא השערות. ללא תמלול של הדיאלוג."
                    ),
                    "en-US": (
                        f"Previous memory about user:\n{existing_summary}\n\n"
                        f"Recent messages:\n{recent_text}\n\n"
                        "Update brief memory (up to 800 chars): family names, events, "
                        "health (only as user said), mood, plans, interests. "
                        "Facts only. No speculation. No dialogue transcript."
                    ),
                }
                
                prompt = prompts.get(language, prompts["ru-RU"])
                
                # Call Claude Haiku for summarization
                try:
                    response = client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=300,
                        messages=[{"role": "user", "content": prompt}]
                    )
                    
                    new_summary = response.content[0].text.strip()
                    
                    # Store updated summary
                    if new_summary:
                        conversation_memory.store_memory(user_id, new_summary)
                        _clear_summarization_state(user_id)
                        # Log only success, never content
                        logging.info(f"Memory updated for user (len={len(new_summary)})")
                    
                except Exception as e:
                    # Log only exception type, never content
                    logging.warning(f"Memory summarization failed: {type(e).__name__}")
            
            finally:
                # Always remove from in-progress set
                _summarization_in_progress.discard(user_id)
        
        except Exception as e:
            # Catch-all: log type only
            logging.warning(f"Memory update error: {type(e).__name__}")
    
    # Run in background thread (non-blocking)
    thread = threading.Thread(target=do_summarize, daemon=True)
    thread.start()


@app.before_request
def canonical_host():
    host = (request.host or "").split(":")[0].lower()
    if host == "metapelet.org":
        return redirect(f"https://www.metapelet.org{request.full_path}", code=301)


@app.route("/")
def home():
    return send_file("index.html")


@app.route("/setup")
def setup():
    return send_file("setup.html")


@app.route("/talk")
def talk():
    return send_file("talk.html")


@app.route("/privacy")
def privacy():
    return send_file("privacy.html")


@app.route("/robots.txt")
def robots():
    return Response(
        "User-agent: *\n"
        "Allow: /\n"
        "Sitemap: https://www.metapelet.org/sitemap.xml\n",
        mimetype="text/plain"
    )


@app.route("/sitemap.xml")
def sitemap():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.metapelet.org/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://www.metapelet.org/setup</loc>
    <changefreq>weekly</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>https://www.metapelet.org/talk</loc>
    <changefreq>weekly</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>https://www.metapelet.org/privacy</loc>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
</urlset>"""
    return Response(xml, mimetype="application/xml")


@app.route("/static/manifest.json")
def manifest():
    return send_file("static/manifest.json", mimetype="application/manifest+json")


@app.route("/service-worker.js")
def service_worker():
    """Serve service worker from root for proper scope."""
    resp = send_file("static/service-worker.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/pilot/event", methods=["POST"])
def pilot_event():
    data = request.json or {}
    ok, err = pilot.record_event(
        session_id=data.get("session_id", ""),
        event=data.get("event", ""),
        utm_source=data.get("utm_source", ""),
        utm_campaign=data.get("utm_campaign", ""),
        utm_content=data.get("utm_content", ""),
        meta=data.get("meta"),
    )
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True})


@app.route("/pilot/feedback", methods=["POST"])
def pilot_feedback():
    data = request.json or {}
    ok, err = pilot.record_feedback(
        session_id=data.get("session_id", ""),
        comfortable=data.get("comfortable", ""),
        liked=data.get("liked", ""),
        continue_pilot=data.get("continue_pilot", ""),
        issues=data.get("issues", ""),
        language=data.get("language", ""),
        utm_source=data.get("utm_source", ""),
        utm_campaign=data.get("utm_campaign", ""),
        utm_content=data.get("utm_content", ""),
    )
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True})


@app.route("/pilot/summary", methods=["GET"])
def pilot_summary():
    token = request.args.get("token", "")
    if not pilot.admin_token_ok(token):
        return "Доступ закрыт. Нужен параметр token.", 403
    summary = pilot.build_summary()
    if request.args.get("format") == "json":
        return jsonify(summary)
    return Response(
        pilot.summary_html(summary),
        mimetype="text/html; charset=utf-8",
    )


@app.route("/admin/feedback.csv", methods=["GET"])
def admin_feedback_csv():
    """Export all feedback as CSV. Protected by ADMIN_TOKEN env var."""
    admin_token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not admin_token:
        return "Endpoint disabled. Set ADMIN_TOKEN environment variable.", 404
    
    # Check token from query parameter or Authorization header
    provided_token = request.args.get("token", "")
    if not provided_token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided_token = auth_header[7:]
    
    if provided_token.strip() != admin_token:
        return "Unauthorized. Invalid or missing token.", 403
    
    # Get all feedback
    feedback_rows = pilot.get_all_feedback_for_export()
    
    # Generate CSV
    import csv
    import io
    
    output = io.StringIO()
    if feedback_rows:
        fieldnames = ["ts", "session_id", "comfortable", "liked", "continue_pilot", 
                     "issues", "language", "utm_source", "utm_campaign", "utm_content"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in feedback_rows:
            # Only write the fields we want (no id, created_at, etc.)
            filtered_row = {k: row.get(k, "") for k in fieldnames}
            writer.writerow(filtered_row)
    else:
        output.write("# No feedback data available\n")
    
    csv_content = output.getvalue()
    return Response(
        csv_content,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=metapelet-feedback.csv"}
    )


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json or {}
    user_message = data.get("message")
    user_id  = data.get("user_id", "default")
    user_name = data.get("name", "").strip() or "дорогой человек"
    # Handle age as string or number robustly
    user_age_raw = data.get("age")
    user_age = str(user_age_raw).strip() if user_age_raw not in (None, "") else ""
    profile_id = data.get("profile_id") or None
    language = (data.get("language") or "ru-RU").strip() or "ru-RU"
    interests = (data.get("interests") or "").strip()
    avoid_topics = (data.get("avoid_topics") or "").strip()

    is_greeting = user_message == GREETING_TRIGGER
    if is_greeting:
        lang_label = _language_label(language)
        user_message = (
            f"Пользователь {user_name} только что начал разговор. "
            f"Язык из анкеты: {lang_label}. "
            "Сформируй первое сообщение: тёплое приветствие по имени и один простой "
            "вопрос о самочувствии или настроении. Одно-два коротких предложения. "
            f"Только на {lang_label} языке. Без эмодзи."
        )

    # Log without PII (no name, no age)
    print(
        f"CHAT | ID: {user_id} | PROFILE: {profile_id or '—'} | LANG: {language}"
        f" | GREETING: {is_greeting}"
    )

    if not user_message:
        return jsonify({"reply": "Я не услышала тебя. Попробуй ещё раз."}), 400

    if len(str(user_message)) > budget.MAX_USER_MESSAGE_CHARS:
        user_message = str(user_message)[:budget.MAX_USER_MESSAGE_CHARS]

    # Защита бюджета: проверяем лимиты ДО обращения к платному ИИ.
    ip = budget.client_ip(request)
    allowed, why = budget.try_consume_reply(user_id, ip)
    if not allowed:
        print(f"BUDGET LIMIT ({why})")
        return jsonify({"reply": budget.limit_message(language), "limit": True}), 429

    system_prompt = build_system_prompt(
        user_name,
        user_age,
        language,
        profile_id,
        interests,
        avoid_topics,
        user_id,
    )

    history = get_history(user_id)
    
    # At session start, check if there's unsummarized buffer from before
    if is_greeting:
        buffer = _unsummarized_buffers.get(user_id, [])
        has_unsummarized_user_msgs = any(
            msg.get("role") == "user" and msg.get("content") != "[начало разговора]"
            for msg in buffer
        )
        if has_unsummarized_user_msgs:
            _summarize_conversation_async(user_id, language)
    
    if not is_greeting:
        history.append({"role": "user", "content": user_message})
        # Add to unsummarized buffer (independent of trim_history)
        _add_to_unsummarized_buffer(user_id, "user", user_message)
        # Increment user message counter
        _increment_user_message_counter(user_id)
    else:
        history.append({"role": "user", "content": "[начало разговора]"})

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=system_prompt,
            messages=history
        )

        reply = strip_emojis(response.content[0].text)
        budget.allow_tts_for(reply)
        history.append({"role": "assistant", "content": reply})
        trim_history(user_id)
        
        # Add assistant reply to unsummarized buffer
        if not is_greeting:
            _add_to_unsummarized_buffer(user_id, "assistant", reply)
        
        # Trigger summarization if threshold reached (non-blocking, best-effort)
        if not is_greeting and _should_summarize(user_id):
            _summarize_conversation_async(user_id, language)

        return jsonify({"reply": reply})

    except Exception as e:
        history.pop()
        budget.refund_reply(user_id, ip)
        print(f"Ошибка Claude API: {e}")
        return jsonify({"reply": "Что-то пошло не так. Попробуй сказать ещё раз."}), 500


@app.route("/budget/status", methods=["GET"])
def budget_status():
    daily = budget.status()
    monthly = tts_budget.get_status()
    return jsonify({
        "daily": daily,
        "monthly": monthly,
    })


@app.route("/health", methods=["GET"])
def health():
    """Lightweight health check endpoint for uptime monitoring with storage info."""
    health_info = {"status": "ok"}
    health_info.update(pilot.check_database_health())
    return jsonify(health_info), 200


@app.route("/tts/check", methods=["GET"])
def tts_check():
    """Диагностика: включён ли TTS для GOOGLE_API_KEY на Render."""
    return jsonify(_check_google_tts())


@app.route("/tts", methods=["POST"])
def tts():
    data = request.json
    text = strip_emojis(data.get("text", "").strip())
    language = (data.get("language") or "ru-RU").strip() or "ru-RU"

    if not text:
        return jsonify({"error": "Нет текста"}), 400

    # Защита бюджета: озвучиваем только ответы, выданные сервером, и в пределах лимита.
    allowed, why = budget.try_consume_tts(text)
    if not allowed:
        print(f"TTS BUDGET BLOCK ({why})")
        return jsonify({"error": "limit", "reason": why}), 429

    api_key = _google_api_key()
    if not api_key:
        return jsonify({"error": "GOOGLE_API_KEY не задан на сервере"}), 500

    voice_cfg = _tts_voice_for_language(language)
    voice_names = voice_cfg.get("names") or [voice_cfg.get("name", "ru-RU-Wavenet-A")]

    # Log without conversation content
    text_len = len(text)
    print(f"TTS | LANG: {language} | LENGTH: {text_len} chars")

    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
    last_detail = None
    all_tiers_exhausted = True

    for voice_name in voice_names:
        # Check monthly budget before attempting
        allowed, reason = tts_budget.check_and_reserve(voice_name, text_len)
        if not allowed:
            tier = tts_budget._voice_to_tier(voice_name)
            print(f"TTS voice {voice_name} ({tier}) skipped: {reason}")
            continue
        
        all_tiers_exhausted = False
        
        # Build audioConfig per voice: Chirp3-HD does not support pitch parameter
        audio_config = {
            "audioEncoding": "MP3",
            "speakingRate": voice_cfg["speakingRate"],
            "volumeGainDb": 1.0,
        }
        
        # Chirp 3 HD voices do not support pitch; omit it for those voices
        if "Chirp3-HD" not in voice_name:
            audio_config["pitch"] = voice_cfg["pitch"]
        
        payload = {
            "input": {"text": text},
            "voice": {
                "languageCode": voice_cfg["languageCode"],
                "name": voice_name,
                "ssmlGender": voice_cfg["ssmlGender"],
            },
            "audioConfig": audio_config,
        }
        try:
            resp = http_requests.post(url, json=payload, timeout=15)
            if not resp.ok:
                last_detail = _parse_google_error(resp)
                print(f"TTS voice {voice_name} failed: {last_detail}")
                continue
            audio_bytes = base64.b64decode(resp.json()["audioContent"])
            
            # Only record usage after successful response from Google
            tts_budget.record_success(voice_name, text_len)
            
            print(f"TTS OK ({voice_name}): {len(audio_bytes)} байт")
            resp_out = Response(audio_bytes, mimetype="audio/mpeg")
            resp_out.headers["X-TTS-Voice"] = voice_name
            return resp_out
        except Exception as e:
            last_detail = str(e)
            print(f"TTS voice {voice_name} failed: {e}")

    # If all candidate voices were skipped due to budget, return 429
    if all_tiers_exhausted:
        print("TTS MONTHLY BUDGET: All tiers exhausted")
        return jsonify({"error": "limit", "reason": "monthly_tts_cap"}), 429

    print(f"Ошибка Google TTS: {last_detail}")
    return jsonify({
        "error": "Google TTS недоступен на сервере",
        "hint": "Проверьте GOOGLE_API_KEY в Render и Cloud Text-to-Speech API",
        "detail": last_detail,
    }), 500


@app.route("/memory/forget", methods=["POST"])
def memory_forget():
    """Delete stored conversation memory for a user."""
    data = request.json or {}
    user_id = data.get("user_id", "").strip()
    
    if not user_id:
        return jsonify({"ok": False, "error": "user_id required"}), 400
    
    success, error = conversation_memory.delete_memory(user_id)
    if not success:
        return jsonify({"ok": False, "error": error}), 400
    
    # Also clear summarization state
    if user_id in _user_message_counters:
        del _user_message_counters[user_id]
    if user_id in _unsummarized_buffers:
        del _unsummarized_buffers[user_id]
    _summarization_in_progress.discard(user_id)
    
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
