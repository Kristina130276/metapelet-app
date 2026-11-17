#!/usr/bin/env python3
"""Mock Flask app for screenshot capture with stubbed responses."""

from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
import os
import sys

# Add parent directory to path to import from app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = Flask(__name__, 
            static_folder='../static',
            template_folder='..')
CORS(app)

# Mock responses for different languages
MOCK_GREETINGS = {
    'ru-RU': 'Привет, {name}! Как вы себя чувствуете сегодня? Расскажите, как прошёл ваш день.',
    'he-IL': '!{name} ,שלום איך הרגשת היום? אשמח לשמוע איך עבר עליך היום.',
    'en-US': 'Hello, {name}! How are you feeling today? Tell me about your day.'
}

MOCK_REPLIES = {
    'ru-RU': 'Как прекрасно! Я рада, что у вас хорошее настроение. Расскажите мне больше о том, что вас радует.',
    'he-IL': '!מה נפלא אני שמח ששלך מצב רוח טוב. ספר לי עוד מה משמח אותך.',
    'en-US': 'How wonderful! I\'m glad you\'re in good spirits. Tell me more about what makes you happy.'
}

@app.route("/")
def home():
    return send_file("../index.html")

@app.route("/setup")
def setup():
    return send_file("../setup.html")

@app.route("/talk")
def talk():
    return send_file("../talk.html")

@app.route("/static/manifest.json")
def manifest():
    return send_file("../static/manifest.json", mimetype="application/manifest+json")

@app.route("/service-worker.js")
def service_worker():
    resp = send_file("../static/service-worker.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp

@app.route("/pilot/event", methods=["POST"])
def pilot_event():
    return jsonify({"ok": True})

@app.route("/pilot/feedback", methods=["POST"])
def pilot_feedback():
    return jsonify({"ok": True})

@app.route("/chat", methods=["POST"])
def chat():
    """Mock chat endpoint with language-specific responses."""
    data = request.json or {}
    message = data.get("message", "")
    name = data.get("name", "")
    language = data.get("language", "ru-RU")
    
    if message == "__greeting__":
        reply = MOCK_GREETINGS.get(language, MOCK_GREETINGS['ru-RU']).format(name=name)
    else:
        reply = MOCK_REPLIES.get(language, MOCK_REPLIES['ru-RU'])
    
    return jsonify({"reply": reply})

@app.route("/tts", methods=["POST"])
def tts():
    """Mock TTS endpoint - returns silent audio."""
    # Return a minimal valid MP3 file (silent)
    silent_mp3 = bytes.fromhex(
        'fff3600000000000000000000000000000000000000000000000000049'
        '6e666f0000000f000000010000003f0000003f0000003f0000003f0000'
        '003f0000003f0000003f0000003f0000003f0000003f0000003f000000'
        '3f0000003f0000003f0000003f0000003f00000000'
    )
    resp = Response(silent_mp3, mimetype="audio/mpeg")
    resp.headers["X-TTS-Voice"] = "mock-voice"
    return resp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
