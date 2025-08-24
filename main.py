import requests
from bs4 import BeautifulSoup
import time
import os
from flask import Flask
from threading import Thread

# =========================
# Environment
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
USERNAME = os.getenv("ARMS_USERNAME")
PASSWORD = os.getenv("ARMS_PASSWORD")

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
SEND_MSG_URL = f"{TELEGRAM_URL}/sendMessage"

# =========================
# State
# =========================
monitoring_enabled = False
current_courses = []                # list of course codes to track
courses_status = {}                 # { course: {"found": bool, "slot": str|None, "vacancy": int|None} }
last_update_id = None

# =========================
# Slot Map (keep your original O–T mapping)
# =========================
slot_map = {
    'G': '7',
    'H': '8',
    'M': '13',
    'N': '14',
    'O': '15',
    'P': '16',
    'Q': '17',
    'R': '18'
}

# =========================
# Telegram helper
# =========================
def send_telegram(text):
    try:
        requests.post(SEND_MSG_URL, data={"chat_id": CHAT_ID, "text": text})
    except:
        pass

# =========================
# Commands: /start /stop /list + course input
# =========================
def check_for_commands():
    global monitoring_enabled, current_courses, last_update_id, courses_status
    try:
        url = f"{TELEGRAM_URL}/getUpdates?timeout=5"
        if last_update_id is not None:
            url += f"&offset={last_update_id + 1}"

        resp = requests.get(url).json()
        updates = resp.get("result", [])

        for update in updates:
            msg = update.get("message", {})
            text = msg.get("text", "")
            chat_id = msg.get("chat", {}).get("id")
            update_id = update.get("update_id")

            if update_id is None:
                continue
            if str(chat_id) != CHAT_ID:
                # Ignore other chats
                last_update_id = update_id
                continue

            # Track offset
            last_update_id = update_id

            if not text:
                continue

            text_clean = text.strip()

            # /start
            if text_clean.lower() == "/start":
                monitoring_enabled = True
                current_courses = []
                courses_status = {}
                send_telegram("🤖 Monitoring started.\nPlease enter course codes (comma or space separated), e.g.:\nECA20, CSE15 MAT21")
                continue

            # /stop
            if text_clean.lower() == "/stop":
                monitoring_enabled = False
                current_courses = []
                courses_status = {}
                send_telegram("🛑 Monitoring stopped.")
                continue

            # /list
            if text_clean.lower() == "/list":
                if not monitoring_enabled:
                    send_telegram("ℹ️ Monitoring is not active. Send /start to begin.")
                elif not current_courses:
                    send_telegram("📋 No courses are currently being monitored.")
                else:
                    lines = []
                    for c in current_courses:
                        st = courses_status.get(c, {"found": False, "slot": None, "vacancy": None})
                        if st["found"]:
                            lines.append(f"{c}: ✅ Found (Slot {st['slot']}, Vacancy {st['vacancy']})")
                        else:
                            lines.append(f"{c}: 🔍 Searching")
                    send_telegram("📋 Courses status:\n" + "\n".join(lines))
                continue

            # Treat any other input as course list (if monitoring is enabled)
            if monitoring_enabled:
                # accept comma or space separated
                tokens = [t.strip().upper() for t in text_clean.replace(",", " ").split() if t.strip()]
                added = 0
                for c in tokens:
                    if c and c not in current_courses:
                        current_courses.append(c)
                        courses_status[c] = {"found": False, "slot": None, "vacancy": None}
                        added += 1
                if added > 0:
                    send_telegram("📌 Monitoring courses: " + ", ".join(current_courses))
                else:
                    send_telegram("ℹ️ No new courses added. Use /list to see status.")
    except Exception as e:
        send_telegram(f"⚠️ Error reading Telegram: {e}")

# =========================
# Core check (login once per cycle, then scan slots)
# =========================
def check_courses_cycle():
    """
    Logs in to ARMS, verifies Enrollment page, then for each slot (O–T),
    fetches the slot page and parses <td> cells. Each matching course's
    vacancy is read from <span class='badge badge-success'>NN</span>.
    A course counts as FOUND only if vacancy > 1.
    Updates courses_status in-place and sends Telegram messages.
    """
    global courses_status, monitoring_enabled, current_courses

    # Nothing to do
    pending = [c for c in current_courses if not courses_status.get(c, {}).get("found")]
    if not pending:
        return

    session = requests.Session()
    login_url = "https://arms.sse.saveetha.com/"
    enrollment_url = "https://arms.sse.saveetha.com/StudentPortal/Enrollment.aspx"
    api_base = "https://arms.sse.saveetha.com/Handler/Student.ashx?Page=StudentInfobyId&Mode=GetCourseBySlot&Id="

    try:
        # 1) GET login to collect hidden fields
        resp = session.get(login_url, timeout=20)
        soup = BeautifulSoup(resp.text, 'html.parser')

        vs = soup.find('input', {'name': '__VIEWSTATE'})
        vg = soup.find('input', {'name': '__VIEWSTATEGENERATOR'})
        ev = soup.find('input', {'name': '__EVENTVALIDATION'})

        if not (vs and vg and ev):
            send_telegram("❌ Login page fields not found.")
            return

        payload = {
            '__VIEWSTATE': vs.get('value', ''),
            '__VIEWSTATEGENERATOR': vg.get('value', ''),
            '__EVENTVALIDATION': ev.get('value', ''),
            'txtusername': USERNAME,
            'txtpassword': PASSWORD,
            'btnlogin': 'Login'
        }

        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Referer': login_url
        }

        # 2) POST login
        login_resp = session.post(login_url, data=payload, headers=headers, timeout=20)
        if "Logout" not in login_resp.text:
            send_telegram("❌ Login failed.")
            return

        # 3) Ensure Enrollment page loads
        enroll_resp = session.get(enrollment_url, timeout=20)
        if "Enrollment" not in enroll_resp.text:
            send_telegram("❌ Enrollment page failed.")
            return

        # 4) For each slot, fetch and parse
        for slot_name, slot_id in slot_map.items():
            if not monitoring_enabled:
                return  # user stopped

            api_url = api_base + slot_id
            try:
                response = session.get(api_url, timeout=20)
            except Exception as e:
                send_telegram(f"⚠️ Error fetching Slot {slot_name}: {e}")
                continue

            if response.status_code != 200:
                continue

            # Parse the HTML snippet for this slot
            slot_soup = BeautifulSoup(response.text, "html.parser")
            tds = slot_soup.find_all("td")
            if not tds:
                # Some slots may be empty – skip silently
                continue

            # Check each <td> for any pending course code and read vacancy
            for td in tds:
                td_text = td.get_text(" ", strip=True)
                # Quick skip if no pending course appears here
                hit_any = [c for c in pending if c in td_text]
                if not hit_any:
                    continue

                # Vacancy is inside <span class="badge badge-success">NN</span>
                span = td.find("span", class_="badge badge-success")
                vacancy = None
                if span:
                    try:
                        vacancy = int(span.get_text(strip=True))
                    except:
                        vacancy = None

                for course in hit_any:
                    # Only update if not already found
                    if not courses_status[course]["found"]:
                        if vacancy is not None:
                            if vacancy > 1:
                                courses_status[course]["found"] = True
                                courses_status[course]["slot"] = slot_name
                                courses_status[course]["vacancy"] = vacancy
                                send_telegram(f"🎯 {course}: Found in Slot {slot_name} ✅ (Vacancy: {vacancy})")
                            else:
                                send_telegram(f"⚠️ {course}: Found in Slot {slot_name}, but no seats (Vacancy: {vacancy}). Continuing...")
                        else:
                            # Vacancy unknown but course matched – keep monitoring conservatively
                            send_telegram(f"ℹ️ {course}: Appears in Slot {slot_name}, but vacancy unreadable. Continuing...")

        # 5) Post-cycle summary
        pending_after = [c for c in current_courses if not courses_status.get(c, {}).get("found")]
        if pending_after:
            send_telegram("⏳ Still monitoring: " + ", ".join(pending_after))
        else:
            send_telegram("🎉 All courses found! Monitoring complete.\n\n📌 Please enter the next course codes or send /stop.")
            # Keep monitoring enabled so user can just send new courses
            current_courses = []
            courses_status = {}

    except Exception as e:
        send_telegram(f"❌ Error during check: {e}")

# =========================
# Keep-alive (same as yours)
# =========================
app = Flask('')

@app.route('/')
def home():
    return "✅ Bot is alive!"

def run_web():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# =========================
# Start + Main loop (fixed timing)
# =========================
keep_alive()
send_telegram("🤖 Bot is running. Send /start to begin monitoring.")

CHECK_INTERVAL_SEC = 15 * 60  # 15 minutes
last_check_ts = 0

while True:
    # Always stay responsive to Telegram commands
    check_for_commands()

    # Run a check cycle exactly every 15 minutes (no double-sleep issues)
    if monitoring_enabled and current_courses:
        now = time.time()
        if now - last_check_ts >= CHECK_INTERVAL_SEC:
            check_courses_cycle()
            last_check_ts = now

    time.sleep(3)  # small delay to reduce CPU usage while staying responsive
