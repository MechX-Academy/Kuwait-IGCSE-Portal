import os, json, re
from typing import Dict, Any, List
from flask import Flask, request, jsonify
import requests
from rapidfuzz import fuzz

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    print("WARNING: Missing TELEGRAM_BOT_TOKEN")
BOT_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}" if TELEGRAM_TOKEN else None

#DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "teachers.json")
DATA_PATH = os.path.join(os.path.dirname(__file__), "teachers.json")


with open(DATA_PATH, "r", encoding="utf-8") as f:
    TEACHERS = json.load(f)

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
    "art & design": ["art", "art & design"],
    "design & technology": ["design", "design & technology", "dt"],
    "music": ["music"],
    "drama": ["drama"],
    "physical education": ["pe", "physical education"],
    "global perspectives": ["global perspectives", "gp"],
    "environmental management": ["environmental management", "em"],
    "enterprise": ["enterprise"]
}

VALID_BOARDS = {
    "cambridge": ["cambridge", "caie", "cie", "cambridge international"],
    "edexcel": ["edexcel", "pearson", "pearson edexcel"],
    "oxfordaqa": ["oxford", "oxford aqa", "oxfordaqa", "aqa international"]
}

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

def extract_board(text: str):
    t = _norm(text)
    best, best_score = None, 0
    for canonical, aliases in VALID_BOARDS.items():
        for a in [canonical] + aliases:
            sc = fuzz.partial_ratio(a, t)
            if sc > best_score:
                best_score, best = sc, canonical
    if best_score >= 70:
        # unify display
        return {"cambridge": "Cambridge", "edexcel": "Edexcel", "oxfordaqa": "OxfordAQA"}[best]
    return None

def extract_all(message: str):
    return {
        "subject": extract_subject(message),
        "grade": extract_grade(message),
        "board": extract_board(message),
    }

def format_teacher_card(t: Dict[str, Any]) -> str:
    quals = ", ".join(t.get("qualifications", []))
    boards = ", ".join(t.get("boards", []))
    grades = f"Grades: {min(t['grades'])}-{max(t['grades'])}" if t.get("grades") else ""
    contact = t.get("contact", {})
    c_line = f"\nContact: {contact.get('whatsapp') or contact.get('phone','N/A')}"
    return (
        f"*{t['name']}*\n"
        f"Subjects: {', '.join(t.get('subjects', []))}\n"
        f"{grades}\n"
        f"Boards: {boards}\n"
        f"{t.get('bio','')}\n"
        f"Qualifications: {quals}{c_line}"
    )

def match_teachers(subject=None, grade=None, board=None, limit=4):
    scored = []
    for t in TEACHERS:
        score = 0
        # subject
        if subject:
            if any(_norm(subject) == _norm(s) for s in t.get("subjects", [])):
                score += 60
            else:
                best_sub = max((fuzz.partial_ratio(subject.lower(), s.lower()) for s in t.get("subjects", [])), default=0)
                score += best_sub * 0.3
        # grade
        if grade and t.get("grades"):
            if grade in t["grades"]:
                score += 20
        # board
        if board and t.get("boards"):
            if any(_norm(board) == _norm(b) for b in t["boards"]):
                score += 20
        scored.append((score, t))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for sc, t in scored[:limit] if sc > 30]

def tg(method: str, payload: Dict[str, Any]):
    if not BOT_API: return None
    return requests.post(f"{BOT_API}/{method}", json=payload, timeout=20)

@app.route("/", defaults={"subpath": ""}, methods=["POST"])
@app.route("/<path:subpath>", methods=["POST"])
@app.get("/")
@app.get("/api/webhook")
def ping():
    return jsonify(ok=True, msg="webhook alive")
def webhook(subpath=None):   # <— أضف ده
    if not BOT_API:
        return jsonify({"ok": False, "error": "Missing TELEGRAM_BOT_TOKEN"}), 500

    update = request.get_json(force=True, silent=True) or {}
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return jsonify({"ok": True})

    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

# /start welcome message
if text.lower() in ("/start", "start"):
    tg("sendMessage", {"chat_id": chat_id,
        "text": "Hi! Tell me the subject, grade, and board (Cambridge/Edexcel/OxfordAQA).\nExample: 'Math Grade 8 Cambridge'."})
    return jsonify({"ok": True})

found = extract_all(text)


    if not found.get("subject"):
        tg("sendMessage", {"chat_id": chat_id, "text": "Which subject do you need? (e.g., Math / Physics / Chemistry / Business / English...)"})
        return jsonify({"ok": True})
    if not found.get("grade"):
        tg("sendMessage", {"chat_id": chat_id, "text": "What grade/year? (e.g., Grade 7 / 8 / 9 / 10 / 11 / 12)"})
        return jsonify({"ok": True})
    if not found.get("board"):
        tg("sendMessage", {"chat_id": chat_id, "text": "Which exam board? (Cambridge / Edexcel / OxfordAQA)"})
        return jsonify({"ok": True})

    results = match_teachers(found["subject"], found["grade"], found["board"], limit=4)

    if not results:
        tg("sendMessage", {"chat_id": chat_id,
                           "text": (f"Sorry—no matches right now for {found['subject']} (Grade {found['grade']}) [{found['board']}]. "
                                    "Would you accept an online tutor from another board or a closely related subject?")})
        return jsonify({"ok": True})

    intro = (f"Great ✅ Best matches:\n"
             f"Subject: *{found['subject']}*, Grade: *{found['grade']}*, Board: *{found['board']}*\n\n"
             f"Choose a tutor to contact.")
    tg("sendMessage", {"chat_id": chat_id, "text": intro, "parse_mode": "Markdown"})

    for t in results:
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": format_teacher_card(t),
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        })
        if t.get("photo_url"):
            tg("sendPhoto", {"chat_id": chat_id, "photo": t["photo_url"]})

    tg("sendMessage", {"chat_id": chat_id, "text": "You can contact any tutor directly via the WhatsApp link in the card. 🌟"})
    return jsonify({"ok": True})
