import os, json, re, time
from typing import Dict, Any, List, Tuple, Set
from flask import Flask, request, jsonify
import requests
from rapidfuzz import fuzz

app = Flask(__name__)

# --- Telegram setup ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    print("WARNING: Missing TELEGRAM_BOT_TOKEN")
BOT_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}" if TELEGRAM_TOKEN else None

def tg(method: str, payload: Dict[str, Any]):
    if not BOT_API:
        return None
    try:
        return requests.post(f"{BOT_API}/{method}", json=payload, timeout=20)
    except Exception as e:
        print("Telegram error:", e)
        return None

# --- Data ---
DATA_PATH = os.path.join(os.path.dirname(__file__), "teachers.json")
try:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        TEACHERS = json.load(f)
    print(f"Loaded {len(TEACHERS)} teachers from {DATA_PATH}")
except Exception as e:
    print(f"ERROR loading teachers.json from {DATA_PATH}: {e}")
    TEACHERS = []

VALID_SUBJECTS = {
    "math": ["math", "mathematics", "additional math", "further math"],
    "physics": ["physics", "phys"],
    "chemistry": ["chemistry", "chem"],
    "biology": ["biology", "bio"],
    "english language": ["english", "english language", "esl", "first language english", "second language english"],
    "english literature": ["english literature", "literature"],
    "computer science": ["computer science", "cs"],
    "ict": ["ict", "information and communication technology"],
    "business": ["business", "business studies"],
    "economics": ["economics", "econ"],
    "accounting": ["accounting", "accounts"],
    "geography": ["geography", "geo"],
    "history": ["history"],
    "arabic": ["arabic", "arabic first language", "arabic foreign language"],
    "french": ["french"],
    "german": ["german"],
    "spanish": ["spanish"],
    "sociology": ["sociology"],
    "humanities & social sciences": ["humanities", "social sciences"],
    "environmental management": ["environmental management", "em"],
    "physical education": ["pe", "physical education"],
    "travel & tourism": ["travel & tourism", "travel", "tourism"],
}

SUBJECT_GROUPS: Dict[str, List[Tuple[str, str]]] = {
    "Core subjects": [
        ("MTH", "Mathematics"),
        ("ENL", "English Language"),
        ("ENLIT", "English Literature"),
        ("BIO", "Biology"),
        ("CHE", "Chemistry"),
        ("PHY", "Physics"),
        ("HUM", "Humanities & Social Sciences"),
        ("BUS", "Business Studies"),
        ("ECO", "Economics"),
        ("ACC", "Accounting"),
        ("SOC", "Sociology"),
    ],
    "Languages": [
        ("FR", "French"),
        ("DE", "German"),
        ("AR", "Arabic (First or Second Language)"),
    ],
    "Creative & Technical": [
        ("ICT", "Information & Communication Technology (ICT)"),
        ("CS",  "Computer Science"),
    ],
    "Other options": [
        ("EM", "Environmental Management"),
        ("PE", "Physical Education (PE)"),
        ("TT", "Travel & Tourism"),
    ],
}

CODE_TO_SUBJECT = {
    "MTH": "Math",
    "ENL": "English Language",
    "ENLIT": "English Literature",
    "BIO": "Biology",
    "CHE": "Chemistry",
    "PHY": "Physics",
    "HUM": "Humanities & Social Sciences",
    "BUS": "Business",
    "ECO": "Economics",
    "ACC": "Accounting",
    "SOC": "Sociology",
    "FR": "French",
    "DE": "German",
    "AR": "Arabic",
    "ICT": "ICT",
    "CS": "Computer Science",
    "EM": "Environmental Management",
    "PE": "Physical Education",
    "TT": "Travel & Tourism",
}

BOARD_CODES = {"C": "Cambridge", "E": "Edexcel", "O": "OxfordAQA"}

# --- helpers ---
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def extract_grade(text: str):
    m = re.search(r"(grade|yr|year)\s*(\d{1,2})", text, re.I)
    if m:
        g = int(m.group(2))
        if 1 <= g <= 13: return g
    m2 = re.search(r"\b(\d{1,2})\b", text)
    if m2:
        g2 = int(m2.group(1))
        if 1 <= g2 <= 13: return g2
    return None

def extract_subject(text: str):
    t = _norm(text)
    best, best_score = None, 0
    for canonical, aliases in VALID_SUBJECTS.items():
        for a in [canonical] + aliases:
            sc = fuzz.partial_ratio(a, t)
            if sc > best_score:
                best_score, best = sc, canonical
    return best.title() if best_score >= 70 else None

def match_teachers(subject=None, grade=None, board=None, limit=4):
    scored = []
    for t in TEACHERS:
        score = 0
        if subject:
            if any(_norm(subject) == _norm(s) for s in t.get("subjects", [])):
                score += 60
            else:
                best_sub = max((fuzz.partial_ratio(subject.lower(), s.lower()) for s in t.get("subjects", [])), default=0)
                score += best_sub * 0.3
        if grade and t.get("grades"):
            if grade in t["grades"]:
                score += 20
        if board and t.get("boards"):
            if any(_norm(board) == _norm(b) for b in t["boards"]):
                score += 20
        scored.append((score, t))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for sc, t in scored[:limit] if sc > 30]

def encode_sel(sel: Set[str]) -> str:
    return ".".join(sorted(sel)) if sel else ""

def decode_sel(s: str) -> Set[str]:
    return set([x for x in s.split(".") if x])

def kb_board():
    return {"inline_keyboard": [[
        {"text": "Cambridge", "callback_data": "B|C"},
        {"text": "Edexcel",   "callback_data": "B|E"},
        {"text": "Oxford",    "callback_data": "B|O"},
    ]]}

def kb_grade(board_code: str):
    rows, row = [], []
    for g in range(7, 13):
        row.append({"text": f"{g}", "callback_data": f"G|{g}|{board_code}"})
        if len(row) == 4:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([{"text": "⬅️ Back", "callback_data": "B|"+board_code}])
    return {"inline_keyboard": rows}

def kb_subjects(board_code: str, grade: int, sel: Set[str]):
    rows = []
    def tick(code): return "✅" if code in sel else "☐"
    for group, items in SUBJECT_GROUPS.items():
        rows.append([{"text": f"— {group} —", "callback_data": "noop"}])
        for i in range(0, len(items), 2):
            row = []
            for code, label in items[i:i+2]:
                row.append({
                    "text": f"{tick(code)} {label}",
                    "callback_data": f"T|{code}|{board_code}|{grade}|{encode_sel(sel)}"
                })
            rows.append(row)
    rows.append([
        {"text": "Done ✅", "callback_data": f"D|{board_code}|{grade}|{encode_sel(sel)}"},
        {"text": "Reset ↩️", "callback_data": f"T|__RESET__|{board_code}|{grade}|{encode_sel(sel)}"},
    ])
    rows.append([{"text": "⬅️ Back", "callback_data": f"G|{grade}|{board_code}"}])
    return {"inline_keyboard": rows}

def summary_text(board_code: str, grade: int, sel: Set[str]) -> str:
    board = BOARD_CODES.get(board_code, board_code)
    chosen = ", ".join(CODE_TO_SUBJECT[c] for c in sorted(sel)) if sel else "—"
    return (f"*Step 3/3 – Subjects*\n"
            f"Board: *{board}*   |   Grade: *{grade}*\n"
            f"Pick one or more subjects, then press *Done*.\n"
            f"Selected: {chosen}")

# --------- DEDUPE (anti-duplicate) ----------
# Keep last selections per chat for 60s to ignore Telegram retries
RECENT_DONE: Dict[int, List[Tuple[str, float]]] = {}

def already_done(chat_id: int, signature: str, ttl: int = 60) -> bool:
    now = time.time()
    lst = RECENT_DONE.get(chat_id, [])
    lst = [(k, t) for (k, t) in lst if now - t < ttl]
    RECENT_DONE[chat_id] = lst
    for k, _ in lst:
        if k == signature:
            return True
    lst.append((signature, now))
    RECENT_DONE[chat_id] = lst
    return False

# --- health ---
@app.get("/api/webhook")
def ping():
    return jsonify(ok=True, msg="webhook alive", teachers=len(TEACHERS))

# --- webhook ---
@app.route("/", defaults={"subpath": ""}, methods=["POST"])
@app.route("/<path:subpath>", methods=["POST"])
def webhook(subpath=None):
    if not BOT_API:
        return jsonify({"ok": False, "error": "Missing TELEGRAM_BOT_TOKEN"}), 500

    update = request.get_json(force=True, silent=True) or {}

    # callbacks
    if "callback_query" in update:
        cq = update["callback_query"]
        chat_id = cq["message"]["chat"]["id"]
        msg_id  = cq["message"]["message_id"]
        data = cq.get("data", "")

        # ack immediately to prevent retries
        tg("answerCallbackQuery", {"callback_query_id": cq["id"]})

        # ignore late retries after we've converted message to Thanks
        if cq["message"].get("text", "").startswith("Thanks!"):
            return jsonify({"ok": True})

        def edit(text=None, reply_markup=None, parse_mode=None):
            if text is not None:
                payload = {"chat_id": chat_id, "message_id": msg_id, "text": text}
                if parse_mode: payload["parse_mode"] = parse_mode
                if reply_markup is not None: payload["reply_markup"] = reply_markup
                tg("editMessageText", payload)
            else:
                tg("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": msg_id, "reply_markup": reply_markup})

        if data.startswith("B|"):
            b = data.split("|", 1)[1]
            edit(text="*Step 2/3 – Grade*\nSelect your child's current grade:",
                 reply_markup=kb_grade(b), parse_mode="Markdown")
            return jsonify({"ok": True})

        if data.startswith("G|"):
            _, g, b = data.split("|", 2)
            g = int(g)
            sel: Set[str] = set()
            edit(text=summary_text(b, g, sel),
                 reply_markup=kb_subjects(b, g, sel),
                 parse_mode="Markdown")
            return jsonify({"ok": True})

        if data.startswith("T|"):
            _, code, b, g, enc = data.split("|", 4)
            g = int(g)
            sel = decode_sel(enc)
            if code == "__RESET__":
                sel = set()
            else:
                if code in sel: sel.remove(code)
                else: sel.add(code)
            edit(text=summary_text(b, g, sel),
                 reply_markup=kb_subjects(b, g, sel),
                 parse_mode="Markdown")
            return jsonify({"ok": True})

        if data.startswith("D|"):
            _, b, g, enc = data.split("|", 3)
            g = int(g)
            sel = decode_sel(enc)
            board = BOARD_CODES.get(b, b)
            subjects = sorted({CODE_TO_SUBJECT[c] for c in sel})

            if not subjects:
                tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Please select at least one subject."})
                return jsonify({"ok": True})

            # dedupe key
            signature = f"{b}|{g}|{'.'.join(sorted(sel))}"
            if already_done(chat_id, signature):
                return jsonify({"ok": True})

            # remove keyboard to block further presses
            tg("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": msg_id,
                                          "reply_markup": {"inline_keyboard": []}})

            conf = ("Thanks! Here are the best matches for:\n"
                    f"Board: *{board}* | Grade: *{g}*\n"
                    f"Subjects: *{', '.join(subjects)}*")
            tg("editMessageText", {
                "chat_id": chat_id, "message_id": msg_id,
                "text": conf, "parse_mode": "Markdown"
            })

            for s in subjects:
                results = match_teachers(s, g, board, limit=4)
                tg("sendMessage", {"chat_id": chat_id, "text": f"— *{s}* —", "parse_mode": "Markdown"})
                if not results:
                    tg("sendMessage", {"chat_id": chat_id,
                                       "text": "No exact matches right now. We'll expand the search and get back to you."})
                    continue
                for t in results:
                    tg("sendMessage", {
                        "chat_id": chat_id,
                        "text": format_teacher_card(t),
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True
                    })
                    if t.get("photo_url"):
                        tg("sendPhoto", {"chat_id": chat_id, "photo": t["photo_url"]})
            tg("sendMessage", {"chat_id": chat_id,
                               "text": "You can contact any tutor via the WhatsApp link on each card. 🌟"})
            return jsonify({"ok": True})

        return jsonify({"ok": True})

    # messages
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return jsonify({"ok": True})

    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    if text.lower() in ("/start", "start"):
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": "*Step 1/3 – Board*\nWhich board or curriculum does your child follow?",
            "parse_mode": "Markdown",
            "reply_markup": kb_board()
        })
        return jsonify({"ok": True})

    tg("sendMessage", {
        "chat_id": chat_id,
        "text": "Please use the guided flow 👇",
        "reply_markup": kb_board()
    })
    return jsonify({"ok": True})
