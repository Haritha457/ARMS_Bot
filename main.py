import requests
from bs4 import BeautifulSoup
import time
import os
from flask import Flask
from threading import Thread

# Load from environment
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
USERNAME = os.getenv("ARMS_USERNAME")
PASSWORD = os.getenv("ARMS_PASSWORD")

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
SEND_MSG_URL = f"{TELEGRAM_URL}/sendMessage"

# State
monitoring_enabled = False
current_course = None
last_update_id = None
course_just_found = False

# Slot Map
slot_map = {
    'G': '7',
    'H': '8',
    'M': '13',
    'N': '14',
    'O': '15',
    'P': '16',
    'Q': '17',
    'R': '18',
    'S': '19',
    'T': '20'
}

# Send Telegram Message
def send_telegram(text):
    try:
        requests.post(SEND_MSG_URL, data={"chat_id": CHAT_ID, "text": text})
    except:
        pass

# Handle /start, /stop and course input
def check_for_commands():
    global monitoring_enabled, current_course, last_update_id, course_just_found
    try:
        url = f"{TELEGRAM_URL}/getUpdates?timeout=5"
        if last_update_id is not None:
            url += f"&offset={last_update_id + 1}"
        resp = requests.get(url).json()
        updates = resp.get("result", [])
        for update in updates:
            msg = update.get("message", {})
            text = msg.get("text", "").strip()
            chat_id = msg.get("chat", {}).get("id")
            update_id = update["update_id"]

            if str(chat_id) != CHAT_ID:
                continue

            last_update_id = update_id

            if text.lower() == "/start":
                monitoring_enabled = True
                current_course = None
                course_just_found = False
                send_telegram("🤖 Monitoring started. Please enter the course code (e.g. ECA20):")

            elif text.lower() == "/stop":
                monitoring_enabled = False
                current_course = None
                course_just_found = False
                send_telegram("🛑 Monitoring stopped.")

            elif monitoring_enabled and not current_course:
                current_course = text.upper()
                course_just_found = False
                send_telegram(f"📌 Monitoring course: {current_course}")

    except Exception as e:
        send_telegram(f"⚠️ Error reading Telegram: {e}")

# Main course checking logic
def check_course_in_slots(course_code):
    session = requests.Session()
    login_url = "https://arms.sse.saveetha.com/"
    enrollment_url = "https://arms.sse.saveetha.com/StudentPortal/Enrollment.aspx"
    api_base = "https://arms.sse.saveetha.com/Handler/Student.ashx?Page=StudentInfobyId&Mode=GetCourseBySlot&Id="

    try:
        # Login
        resp = session.get(login_url)
        soup = BeautifulSoup(resp.text, 'html.parser')

        payload = {
            '__VIEWSTATE': soup.find('input', {'name': '__VIEWSTATE'}).get('value'),
            '__VIEWSTATEGENERATOR': soup.find('input', {'name': '__VIEWSTATEGENERATOR'}).get('value'),
            '__EVENTVALIDATION': soup.find('input', {'name': '__EVENTVALIDATION'}).get('value'),
            'txtusername': USERNAME,
            'txtpassword': PASSWORD,
            'btnlogin': 'Login'
        }

        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Referer': login_url
        }

        login_resp = session.post(login_url, data=payload, headers=headers)
        if "Logout" not in login_resp.text:
            send_telegram("❌ Login failed.")
            return False

        enroll_resp = session.get(enrollment_url)
        if "Enrollment" not in enroll_resp.text:
            send_telegram("❌ Enrollment page failed.")
            return False

        # Check each slot
        for slot_name, slot_id in slot_map.items():
            if not monitoring_enabled:
                return False  # user stopped monitoring

            api_url = api_base + slot_id
            response = session.get(api_url)

            if response.status_code == 200 and course_code in response.text:
                send_telegram(f"🔄 Checking course: {course_code}\n🎯 Found in Slot {slot_name}!")
                return True

        send_telegram(f"🔄 Checking course: {course_code}\n❌ Not found in any slot.")
        return False

    except Exception as e:
        send_telegram(f"❌ Error during check: {e}")
        return False

# Uptime keep-alive for Railway
app = Flask('')

@app.route('/')
def home():
    return "✅ Bot is alive!"

def run_web():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# Start
keep_alive()
send_telegram("🤖 Bot is running. Send /start to begin monitoring.")

# 🔁 MAIN LOOP
while True:
    check_for_commands()

    if monitoring_enabled and current_course:
        found = check_course_in_slots(current_course)

        if found:
            send_telegram(f"✅ Monitoring complete for {current_course}. Please send the next course or /stop.")
            current_course = None
            course_just_found = True

        # Wait 15 minutes, checking every 3 seconds for /stop or new input
        for _ in range(300):
            check_for_commands()
            if not monitoring_enabled or course_just_found:
                course_just_found = False
                break
            time.sleep(3)

    else:
        time.sleep(5)
