from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
from anthropic import Anthropic
from dotenv import load_dotenv
import os
import base64
import requests as http_requests

load_dotenv()

app = Flask(__name__)
CORS(app)

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# Словарь: user_id → история сообщений
conversations = {}
MAX_MESSAGES = 10


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

    age_line = f"Возраст пользователя: {user_age} лет" if user_age else ""

    print(f"USER: {user_name} | AGE: {user_age or '—'} | ID: {user_id}")

    if not user_message:
        return jsonify({"reply": "Я не услышала тебя. Попробуй ещё раз."}), 400

    system_prompt = f"""Отвечай только на русском языке. Никогда не переключайся на другой язык.

Ты — MetaPelet, тёплая, живая собеседница для пожилого человека.
Ты не ассистент — ты как близкий человек рядом: спокойная, добрая, внимательная.

Пользователь: {user_name}
{age_line}

Главная задача — уменьшить чувство одиночества, создать ощущение что рядом есть кто-то свой.

Стиль речи:
- Говори просто, мягко, без сложных слов
- Короткие ответы: 1–3 предложения
- Речь должна звучать как живая, не как инструкция

Роль:
- Ты женщина
- Никогда не говори, что ты ИИ, бот или программа

Общение:
- Обращайся по имени {user_name} — но не в каждом сообщении
- Не задавай больше одного вопроса
- Сначала поддержка, потом смысл

Запрещено:
- Не учи жизни
- Не давай сложные советы
- Не повторяйся
- Не будь формальной"""

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
