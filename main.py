import requests
from bs4 import BeautifulSoup
import time
import os
from flask import Flask
from threading import Thread

# Load from environment (UNCHANGED)
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")  # single-user as in your original
USERNAME = os.getenv("ARMS_USERNAME")
PASSWORD = os.getenv("ARMS_PASSWORD")

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
SEND_MSG_URL = f"{TELEGRAM_URL}/sendMessage"

# ===== State (kept same style, extended for multi-course) =====
monitoring_enabled = False
current_courses = []                 # list of course codes being monitored
courses_found = {}                   # { "SPIC5": {"found": bool, "slot": "P", "vacancy": 12} }
last_update_id = None
course_just_found = False            # kept from your original (not strictly needed, but preserved)
next_check_ts = 0                    # schedule immediate first check, then every 15 minutes

# Slot Map (UNCHANGED except we can keep only what you had)
slot_map = {
    'O': '15',
    'P': '16',
    'Q': '17',
    'R': '18',
    'S': '19',
    'T': '20'
}

# ===== Telegram send (UNCHANGED) =====
def send_telegram(text):
    try:
        requests.post(SEND_MSG_URL, data={"chat_id": CHAT_ID, "text": text})
    except:
        pass

# ===== Handle /start, /stop, /list and course input (same polling approach) =====
def check_for_commands():
    global monitoring_enabled, current_courses, last_update_id, course_just_found, courses_found, next_check_ts
    try:
        url = f"{TELEGRAM_URL}/getUpdates?timeout=5"
        if last_update_id is not None:
            url += f"&offset={last_update_id + 1}"
        resp = requests.get(url).json()
        updates = resp.get("result", [])
        for update in updates:
            msg = update.get("message", {})
            text = msg.get("text", "")
            chat_id = str(msg.get("chat", {}).get("id"))
            update_id = update.get("update_id")

            if update_id is None:
                continue

            # Respect the single CHAT_ID you configured (UNCHANGED behavior)
            if chat_id != str(CHAT_ID):
                last_update_id = update_id
                continue

            last_update_id = update_id
            if not text:
                continue
            text = text.strip()

            if text.lower() == "/start":
                monitoring_enabled = True
                current_courses = []
                courses_found = {}
                course_just_found = False
                next_check_ts = 0  # force immediate check once courses arrive
                send_telegram("🤖 Monitoring started. Please enter the course codes (e.g. ECA20, SPIC5 SPIC6):")

            elif text.lower() == "/stop":
                monitoring_enabled = False
                current_courses = []
                courses_found = {}
                course_just_found = False
                next_check_ts = 0
                send_telegram("🛑 Monitoring stopped.")

            elif text.lower() == "/list":
                if not monitoring_enabled or not current_courses:
                    send_telegram("📋 No courses are currently being monitored.")
                else:
                    lines = []
                    for c in current_courses:
                        st = courses_found.get(c, {"found": False, "slot": None, "vacancy": None})
                        if st["found"]:
                            lines.append(f"{c}: ✅ Found (Slot {st['slot']}, Vacancy {st['vacancy']})")
                        else:
                            lines.append(f"{c}: 🔍 Searching")
                    send_telegram("📋 Courses status:\n" + "\n".join(lines))

            # When monitoring is enabled and no current_courses set, treat input as course list (UNCHANGED concept)
            elif monitoring_enabled and not current_courses:
                # accept comma or space separated, keep your style
                tokens = [t.strip().upper() for t in text.replace(",", " ").split() if t.strip()]
                if tokens:
                    current_courses = tokens
                    courses_found = {c: {"found": False, "slot": None, "vacancy": None} for c in current_courses}
                    course_just_found = False
                    send_telegram(f"📌 Monitoring courses: {', '.join(current_courses)}")
                    next_check_ts = 0  # trigger immediate check on next loop
                else:
                    send_telegram("⚠️ Please enter valid course codes.")

    except Exception as e:
        send_telegram(f"⚠️ Error reading Telegram: {e}")

# ===== Main course checking logic (kept your login flow + added vacancy parsing) =====
def check_courses_in_slots(courses):
    """
    Logs in exactly like your code, opens Enrollment, then loops slots O–T.
    For each slot response, it parses <td>...<label>COURSE</label> <span class="badge badge-success">NN</span>
    Marks a course FOUND only if vacancy > 1 (as you asked).
    """
    session = requests.Session()
    login_url = "https://arms.sse.saveetha.com/"
    enrollment_url = "https://arms.sse.saveetha.com/StudentPortal/Enrollment.aspx"
    api_base = "https://arms.sse.saveetha.com/Handler/Student.ashx?Page=StudentInfobyId&Mode=GetCourseBySlot&Id="

    try:
        # 1) GET login to extract ASP.NET hidden fields (UNCHANGED)
        resp = session.get(login_url, timeout=30)
        soup = BeautifulSoup(resp.text, 'html.parser')

        vs = soup.find('input', {'name': '__VIEWSTATE'})
        vg = soup.find('input', {'name': '__VIEWSTATEGENERATOR'})
        ev = soup.find('input', {'name': '__EVENTVALIDATION'})
        if not (vs and vg and ev):
            send_telegram("❌ Login page fields not found.")
            return False

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

        # 2) POST login (UNCHANGED)
        login_resp = session.post(login_url, data=payload, headers=headers, timeout=30)
        if "Logout" not in login_resp.text:
            send_telegram("❌ Login failed.")
            return False

        # 3) GET Enrollment (UNCHANGED)
        enroll_resp = session.get(enrollment_url, timeout=30)
        if "Enrollment" not in enroll_resp.text:
            send_telegram("❌ Enrollment page failed.")
            return False

        # 4) Check each slot (UNCHANGED structure, added vacancy reading)
        any_message_sent = False
        pending = [c for c in courses if not courses_found.get(c, {}).get("found")]

        for slot_name, slot_id in slot_map.items():
            if not monitoring_enabled or not pending:
                break  # user stopped or nothing to do

            api_url = api_base + slot_id
            try:
                response = session.get(api_url, timeout=30)
            except Exception as e:
                send_telegram(f"⚠️ Error fetching Slot {slot_name}: {e}")
                continue

            if response.status_code != 200:
                continue

            # Parse TD structure like you showed
            slot_soup = BeautifulSoup(response.text, "html.parser")
            tds = slot_soup.find_all("td")
            if not tds:
                continue

            for td in tds:
                td_text = td.get_text(" ", strip=True)
                # For each pending course, if it appears in this <td>, read vacancy from <span>
                hit_courses = [c for c in pending if c in td_text]
                if not hit_courses:
                    continue

                span = td.find("span", class_="badge badge-success")
                vacancy = None
                if span:
                    try:
                        vacancy = int(span.get_text(strip=True))
                    except:
                        vacancy = None

                for course in hit_courses:
                    if not courses_found[course]["found"]:
                        if vacancy is not None:
                            if vacancy > 1:
                                courses_found[course]["found"] = True
                                courses_found[course]["slot"] = slot_name
                                courses_found[course]["vacancy"] = vacancy
                                send_telegram(f"✅ {course}: Slot {slot_name} — Vacancy {vacancy}")
                            else:
                                send_telegram(f"❌ {course}: Slot {slot_name} — Vacancy {vacancy} (no seats)")
                            any_message_sent = True
                        else:
                            # Vacancy unreadable; keep monitoring
                            send_telegram(f"ℹ️ {course}: Appears in Slot {slot_name}, but vacancy unreadable. Continuing...")
                            any_message_sent = True

            # refresh pending list to avoid duplicate messages in other slots this cycle
            pending = [c for c in courses if not courses_found.get(c, {}).get("found")]

        # If nothing matched at all in any slot, keep you informed like your original
        if not any_message_sent:
            send_telegram("🔄 Checking courses:\n" + ", ".join(courses))
            send_telegram("❌ Not found in any slot (or no readable vacancy).")

        # Summary and completion behavior (same spirit as your code)
        still = [c for c in courses if not courses_found.get(c, {}).get("found")]
        if still:
            send_telegram("⏳ Still monitoring: " + ", ".join(still))
            return False
        else:
            send_telegram("🎉 All courses found! Monitoring complete.\n\n📌 Please enter the next course codes or /stop.")
            return True

    except Exception as e:
        send_telegram(f"❌ Error during check: {e}")
        return False

# ===== Keep-alive (UNCHANGED) =====
app = Flask('')

@app.route('/')
def home():
    return "✅ Bot is alive!"

def run_web():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# ===== Start (UNCHANGED intro) =====
keep_alive()
send_telegram("🤖 Bot is running. Send /start to begin monitoring.")

# ===== MAIN LOOP (same structure, but with immediate first check + precise 15-min scheduling) =====
CHECK_INTERVAL_SEC = 15 * 60  # 15 minutes

while True:
    check_for_commands()

    # Immediate first check: next_check_ts == 0 triggers one run right away after courses set
    if monitoring_enabled and current_courses:
        now = time.time()
        if next_check_ts == 0 or now >= next_check_ts:
            # Only pass courses that are not yet found
            pending_courses = [c for c in current_courses if not courses_found.get(c, {}).get("found")]
            if pending_courses:
                done = check_courses_in_slots(pending_courses)
                # If done, reset tracked courses (same behavior as your "send next course" flow)
                if done:
                    current_courses = []
                    courses_found = {}
                    course_just_found = True
            # schedule next run in 15 minutes
            next_check_ts = now + CHECK_INTERVAL_SEC

    # Keep loop responsive to /stop or new input
    time.sleep(3)
