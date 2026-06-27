from flask import Flask, request, jsonify, send_file, Response
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
    """Голоса Google TTS — Neural2 предпочтительнее Wavenet."""
    presets = {
        "ru-RU": {
            "languageCode": "ru-RU",
            "names": ["ru-RU-Neural2-A", "ru-RU-Wavenet-A", "ru-RU-Standard-A"],
            "ssmlGender": "FEMALE",
            "speakingRate": 0.92,
            "pitch": -0.5,
        },
        "he-IL": {
            "languageCode": "he-IL",
            "names": ["he-IL-Wavenet-A", "he-IL-Standard-A"],
            "ssmlGender": "FEMALE",
            "speakingRate": 0.92,
            "pitch": 0.0,
        },
        "en-US": {
            "languageCode": "en-US",
            "names": ["en-US-Neural2-F", "en-US-Wavenet-F"],
            "ssmlGender": "FEMALE",
            "speakingRate": 0.92,
            "pitch": 0.0,
        },
    }
    return presets.get(language, presets["ru-RU"])


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


@app.route("/tts", methods=["POST"])
def tts():
    data = request.json
    text = strip_emojis(data.get("text", "").strip())
    language = (data.get("language") or "ru-RU").strip() or "ru-RU"

    if not text:
        return jsonify({"error": "Нет текста"}), 400

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return jsonify({"error": "GOOGLE_API_KEY не задан"}), 500

    voice_cfg = _tts_voice_for_language(language)
    voice_names = voice_cfg.get("names") or [voice_cfg.get("name", "ru-RU-Wavenet-A")]

    print("TTS endpoint called")
    print("Text for TTS:", text[:60])
    print("Language:", language)

    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
    last_error = None

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
                "volumeGainDb": 1.5,
            },
        }
        try:
            resp = http_requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            audio_bytes = base64.b64decode(resp.json()["audioContent"])
            print(f"TTS OK ({voice_name}): {len(audio_bytes)} байт")
            return Response(audio_bytes, mimetype="audio/mpeg")
        except Exception as e:
            last_error = e
            print(f"TTS voice {voice_name} failed: {e}")

    print(f"Ошибка Google TTS: {last_error}")
    return jsonify({"error": str(last_error)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
