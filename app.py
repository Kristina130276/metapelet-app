from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
from anthropic import Anthropic
from dotenv import load_dotenv
import os
import base64
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

    print(
        f"USER: {user_name} | AGE: {user_age or '—'} | ID: {user_id}"
        f" | PROFILE: {profile_id or '—'} | LANG: {language}"
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
    history.append({"role": "user", "content": user_message})

    try:
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=200,
            system=system_prompt,
            messages=history
        )

        reply = response.content[0].text
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
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"error": "Нет текста"}), 400

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return jsonify({"error": "GOOGLE_API_KEY не задан"}), 500

    print("TTS endpoint called")
    print("Text for TTS:", text[:60])
    print("Using GOOGLE_API_KEY:", "YES" if api_key else "NO")

    payload = {
        "input": {"text": text},
        "voice": {
            "languageCode": "ru-RU",
            "name": "ru-RU-Wavenet-A",   # проверенный женский WaveNet голос
            "ssmlGender": "FEMALE"
        },
        "audioConfig": {
            "audioEncoding": "MP3",
            "speakingRate": 0.9,
            "pitch": 1.0
        }
    }

    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"

    try:
        resp = http_requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        audio_bytes = base64.b64decode(resp.json()["audioContent"])
        print(f"TTS OK: {len(audio_bytes)} байт")
        return Response(audio_bytes, mimetype="audio/mpeg")

    except Exception as e:
        print(f"Ошибка Google TTS: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
