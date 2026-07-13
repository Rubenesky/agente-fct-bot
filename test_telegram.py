# test_telegram.py
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
import requests

def send_test():
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    response = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "🔍 Prueba de notificación del Agente FCT\n✅ Si ves esto, el bot funciona correctamente."
    })
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

if __name__ == "__main__":
    send_test()