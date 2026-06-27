from flask import Flask, request, jsonify, send_file, Response, redirect
from flask_cors import CORS
from anthropic import Anthropic
from dotenv import load_dotenv
import os
import base64
import re
import requests as http_requests
from pathlib import Path

load_dotenv()

app = Flask(__name__)
CORS(app)

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PROFILE_FILES = {
    "sonya": "profiles/sonya.txt",
}

# Словарь: user_id → история сообщений
conversations = {}
MAX_MESSAGES = 10
GREETING_TRIGGER = "__greeting__"

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
                "key_prefix": api_key[:10] + "...",
                "voice_count": len(voices),
                "chirp3_sample": chirp,
            }
        return {
            "ok": False,
            "key_prefix": api_key[:10] + "...",
            "http_status": resp.status_code,
            "google_error": _parse_google_error(resp),
        }
    except Exception as e:
        return {"ok": False, "key_prefix": api_key[:10] + "...", "reason": str(e)}


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


def _build_anketa_block(language: str, interests: str, avoid_topics: str) -> str:
    """Профиль из анкеты на сайте (без БД). Память между сессиями — позже."""
    parts = [f"Язык общения: {_language_label(language)}."]
    if interests.strip():
        parts.append(f"Интересы и что нравится:\n{interests.strip()}")
    if avoid_topics.strip():
        parts.append(f"Избегать тем:\n{avoid_topics.strip()}")
    if len(parts) <= 1 and not interests.strip() and not avoid_topics.strip():
        return ""
    return "[Профиль из анкеты пользователя]\n" + "\n".join(parts)


def build_system_prompt(
    name: str,
    age: str,
    language: str = "ru-RU",
    profile_id=None,
    interests: str = "",
    avoid_topics: str = "",
) -> str:
    user_name = (name or "").strip() or "дорогой человек"
    user_age = (age or "").strip() if age else ""
    age_line = f"Возраст пользователя: {user_age} лет" if user_age else ""

    prompt = _read_prompt("base_metapelet.txt").format(
        user_name=user_name,
        age_line=age_line,
    )

    profile_key = str(profile_id).strip().lower() if profile_id else ""
    profile_file = PROFILE_FILES.get(profile_key) if profile_key else None

    if profile_file:
        prompt = prompt + "\n\n" + _read_prompt(profile_file)
    else:
        anketa_block = _build_anketa_block(language, interests, avoid_topics)
        if anketa_block:
            prompt = prompt + "\n\n" + anketa_block

    if language and language != "ru-RU":
        prompt = prompt + f"\n\nОтвечай на языке: {_language_label(language)}."

    return prompt


def get_history(user_id: str) -> list:
    if user_id not in conversations:
        conversations[user_id] = []
    return conversations[user_id]


def trim_history(user_id: str):
    if len(conversations[user_id]) > MAX_MESSAGES:
        conversations[user_id] = conversations[user_id][-MAX_MESSAGES:]


@app.before_request
def canonical_host():
    host = (request.host or "").split(":")[0].lower()
    if host == "metapelet.org":
        return redirect(f"https://www.metapelet.org{request.full_path}", code=301)


@app.route("/")
def home():
    return send_file("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_message = data.get("message")
    user_id  = data.get("user_id", "default")
    user_name = data.get("name", "").strip() or "дорогой человек"
    user_age  = data.get("age", "").strip() if data.get("age") else ""
    profile_id = data.get("profile_id") or None
    language = (data.get("language") or "ru-RU").strip() or "ru-RU"
    interests = (data.get("interests") or "").strip()
    avoid_topics = (data.get("avoid_topics") or "").strip()

    is_greeting = user_message == GREETING_TRIGGER
    if is_greeting:
        user_message = (
            f"Пользователь {user_name} только что начал разговор. "
            "Поприветствуй тепло по имени, одним-двумя короткими предложениями, "
            "и задай один простой добрый вопрос. Без эмодзи."
        )

    print(
        f"USER: {user_name} | AGE: {user_age or '—'} | ID: {user_id}"
        f" | PROFILE: {profile_id or '—'} | LANG: {language}"
        f" | GREETING: {is_greeting}"
    )

    if not user_message:
        return jsonify({"reply": "Я не услышала тебя. Попробуй ещё раз."}), 400

    system_prompt = build_system_prompt(
        user_name,
        user_age,
        language,
        profile_id,
        interests,
        avoid_topics,
    )

    history = get_history(user_id)
    if not is_greeting:
        history.append({"role": "user", "content": user_message})
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
        history.append({"role": "assistant", "content": reply})
        trim_history(user_id)

        return jsonify({"reply": reply})

    except Exception as e:
        history.pop()
        print(f"Ошибка Claude API: {e}")
        return jsonify({"reply": "Что-то пошло не так. Попробуй сказать ещё раз."}), 500


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

    api_key = _google_api_key()
    if not api_key:
        return jsonify({"error": "GOOGLE_API_KEY не задан на сервере"}), 500

    voice_cfg = _tts_voice_for_language(language)
    voice_names = voice_cfg.get("names") or [voice_cfg.get("name", "ru-RU-Wavenet-A")]

    print("TTS endpoint called")
    print("Text for TTS:", text[:60])
    print("Language:", language)
    print("Key prefix:", api_key[:10] + "...")

    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
    last_detail = None

    for voice_name in voice_names:
        payload = {
            "input": {"text": text},
            "voice": {
                "languageCode": voice_cfg["languageCode"],
                "name": voice_name,
                "ssmlGender": voice_cfg["ssmlGender"],
            },
            "audioConfig": {
                "audioEncoding": "MP3",
                "speakingRate": voice_cfg["speakingRate"],
                "pitch": voice_cfg["pitch"],
                "volumeGainDb": 1.0,
            },
        }
        try:
            resp = http_requests.post(url, json=payload, timeout=15)
            if not resp.ok:
                last_detail = _parse_google_error(resp)
                print(f"TTS voice {voice_name} failed: {last_detail}")
                continue
            audio_bytes = base64.b64decode(resp.json()["audioContent"])
            print(f"TTS OK ({voice_name}): {len(audio_bytes)} байт")
            resp_out = Response(audio_bytes, mimetype="audio/mpeg")
            resp_out.headers["X-TTS-Voice"] = voice_name
            return resp_out
        except Exception as e:
            last_detail = str(e)
            print(f"TTS voice {voice_name} failed: {e}")

    print(f"Ошибка Google TTS: {last_detail}")
    return jsonify({
        "error": "Google TTS недоступен на сервере",
        "hint": "Проверьте GOOGLE_API_KEY в Render и Cloud Text-to-Speech API",
        "detail": last_detail,
    }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
