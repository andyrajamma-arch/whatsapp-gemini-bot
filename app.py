import os
import json
import logging
import requests
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
WHATSAPP_TOKEN = os.environ["WHATSAPP_TOKEN"]
PHONE_NUMBER_ID = os.environ["PHONE_NUMBER_ID"]
VERIFY_TOKEN = os.environ["VERIFY_TOKEN"]
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "You are a helpful, friendly WhatsApp assistant. Keep replies concise and use plain text only."
)

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent?key=" + GEMINI_API_KEY
)

conversations = {}
MAX_HISTORY = 20


def send_whatsapp_message(to, text):
    url = "https://graph.facebook.com/v19.0/" + PHONE_NUMBER_ID + "/messages"
    headers = {
        "Authorization": "Bearer " + WHATSAPP_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    resp = requests.post(url, json=payload, headers=headers)
    if not resp.ok:
        logger.error("WhatsApp send failed: %s", resp.text)


def mark_as_read(message_id):
    url = "https://graph.facebook.com/v19.0/" + PHONE_NUMBER_ID + "/messages"
    headers = {
        "Authorization": "Bearer " + WHATSAPP_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }
    requests.post(url, json=payload, headers=headers)


def get_gemini_reply(user_id, user_text):
    history = conversations.setdefault(user_id, [])
    history.append({"role": "user", "parts": [{"text": user_text}]})
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
        conversations[user_id] = history

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": history
    }

    try:
        resp = requests.post(GEMINI_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        reply = data["candidates"][0]["content"]["parts"][0]["text"]
        history.append({"role": "model", "parts": [{"text": reply}]})
        return reply
    except Exception as e:
        logger.error("Gemini error: %s", e)
        history.pop()
        return "Sorry, I am having trouble right now. Please try again!"


def handle_text(from_number, message_id, text):
    mark_as_read(message_id)
    if text.strip().lower() in ("/reset", "reset", "clear", "/clear"):
        conversations.pop(from_number, None)
        send_whatsapp_message(from_number, "Chat cleared! Starting fresh.")
        return
    reply = get_gemini_reply(from_number, text)
    send_whatsapp_message(from_number, reply)


def handle_image(from_number, message_id, caption):
    mark_as_read(message_id)
    prompt = "[User sent an image: " + caption + "]" if caption else "[User sent an image]"
    reply = get_gemini_reply(from_number, prompt)
    send_whatsapp_message(from_number, reply)


@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Webhook verified!")
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "no data"}), 400
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for message in value.get("messages", []):
                    from_number = message["from"]
                    message_id = message["id"]
                    msg_type = message.get("type")
                    if msg_type == "text":
                        handle_text(from_number, message_id, message["text"]["body"])
                    elif msg_type == "image":
                        handle_image(from_number, message_id, message["image"].get("caption", ""))
                    else:
                        mark_as_read(message_id)
                        send_whatsapp_message(from_number,
                            "I received your " + msg_type + ", but I can only handle text and images!")
    except Exception as e:
        logger.error("Webhook error: %s", e)
    return jsonify({"status": "ok"}), 200


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "bot": "WhatsApp + Gemini"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
