import os, json, re
from typing import Dict, Any, List
from flask import Flask, request, jsonify
import requests
from rapidfuzz import fuzz

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    print("WARNING: Missing TELEGRAM_BOT_TOKEN environment variable")
BOT_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}" if TELEGRAM_TOKEN else None

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "teachers.json")
with open(DATA_PATH, "r", encoding="utf-8") as f:
    TEACHERS = json.load(f)

VALID_SUBJECTS = {
    "math": ["math", "mathematics", "further math", "fmath", "further"],
    "physics": ["physics", "phys"],
    "chemistry": ["chemistry", "chem"],
    "biology": ["biology", "bio"],
    "english": ["english", "eng"],
    "computer science": ["cs", "computer", "ict", "computer science"]
}

def norm(s: str) -> str:
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
    t = norm(text)
    best, best_score = None, 0
    for canonical, aliases in VALID_SUBJECTS.items():
        for a in [canonical] + aliases:
            sc = fuzz.partial_ratio(a, t)
            if sc > best_score:
                best_score, best = sc, canonical
    return best.title() if best_score >= 70 else None

def extract_school(text: str):
    m = re.search(r"\b([A-Za-z ]{2,50}(International|Language)?\s*(School|IS|College|Academy))\b", text, re.I)
    return m.group(0).strip() if m else None

def extract_area_city(text: str):
    words = re.findall(r"[A-Za-z\u0600-\u06FF]+", text)
    if not words: return None
    for k in [4,3,2]:
        if len(words) >= k:
            guess = " ".join(words[-k:])
            if len(guess) >= 4: return guess
    return "Cairo"

def extract_all(message: str):
    return {
        "subject": extract_subject(message),
        "grade": extract_grade(message),
        "school": extract_school(message),
        "area": extract_area_city(message),
    }

def format_teacher_card(t: Dict[str, Any]) -> str:
    quals = ", ".join(t.get("qualifications", []))
    exp_schools = ", ".join(t.get("schools_experience", []))
    grades = f"Grades: {min(t['grades'])}-{max(t['grades'])}" if t.get("grades") else ""
    contact = t.get("contact", {})
    c_line = f"\nContact: {contact.get('whatsapp') or contact.get('phone','N/A')}"
    return (
        f"*{t['name']}*\n"
        f"Subjects: {', '.join(t.get('subjects', []))}\n"
        f"{grades}\n"
        f"Areas: {', '.join(t.get('areas', []))}\n"
        f"Schools exp.: {exp_schools}\n"
        f"{t.get('bio','')}\n"
        f"Qualifications: {quals}{c_line}"
    )

def match_teachers(subject=None, grade=None, area=None, school=None, limit=4) -> List[Dict[str, Any]]:
    scored = []
    for t in TEACHERS:
        score = 0
        if subject:
            if any(norm(subject) == norm(s) for s in t.get("subjects", [])):
                score += 50
            else:
                best_sub = max((fuzz.partial_ratio(subject.lower(), s.lower()) for s in t.get("subjects", [])), default=0)
                score += best_sub * 0.3
        if grade and t.get("grades"):
            if grade in t["grades"]:
                score += 25
            else:
                gmin, gmax = min(t["grades"]), max(t["grades"])
                if abs(grade - gmin) <= 1 or abs(grade - gmax) <= 1:
                    score += 10
        if area and t.get("areas"):
            score += max((fuzz.partial_ratio(area.lower(), a.lower()) for a in t["areas"]), default=0) * 0.2
        if school and t.get("schools_experience"):
            score += max((fuzz.partial_ratio(school.lower(), s.lower()) for s in t.get("schools_experience"]), default=0) * 0.2
        scored.append((score, t))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for sc, t in scored[:limit] if sc > 20]

def tg(method: str, payload: Dict[str, Any]):
    if not BOT_API:
        return None
    return requests.post(f"{BOT_API}/{method}", json=payload, timeout=20)

@app.get("/api/health")
def health():
    ok = TELEGRAM_TOKEN is not None
    return {"ok": ok, "msg": "Running", "has_token": ok}

@app.post("/api/webhook")
def webhook():
    if not BOT_API:
        return jsonify({"ok": False, "error": "Missing TELEGRAM_BOT_TOKEN"}), 500

    update = request.get_json(force=True, silent=True) or {}
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return jsonify({"ok": True})

    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    found = extract_all(text)

    if not found.get("subject"):
        tg("sendMessage", {"chat_id": chat_id, "text": "إيه المادة المطلوبة؟ (Math / Physics / Chemistry / Biology / English / CS)"})
        return jsonify({"ok": True})
    if not found.get("grade"):
        tg("sendMessage", {"chat_id": chat_id, "text": "الجريد كام؟ (اكتب: Grade 7 / 8 / 9 ...)"})
        return jsonify({"ok": True})
    if not found.get("school"):
        tg("sendMessage", {"chat_id": chat_id, "text": "اسم المدرسة؟ (مثال: NIS / AIS / BISC أو اسم المدرسة كامل)"})
        return jsonify({"ok": True})
    if not found.get("area"):
        tg("sendMessage", {"chat_id": chat_id, "text": "المنطقة/المدينة فين؟ (مثال: Sheikh Zayed / New Cairo / Dokki ...)"})
        return jsonify({"ok": True})

    results = match_teachers(found["subject"], found["grade"], found["area"], found["school"], limit=4)

    if not results:
        tg("sendMessage", {"chat_id": chat_id,
                           "text": (f"حالياً مفيش مطابقات لمادة {found['subject']} – Grade {found['grade']} في {found['area']} مع خبرة {found['school']}.\n"
                                    "تحب نرشّح أونلاين؟ اكتب: Online أو ابعت منطقة بديلة.")})
        return jsonify({"ok": True})

    intro = (f"تمام ✅ لقينا الأنسب لطلبك:\n"
             f"Subject: *{found['subject']}*, Grade: *{found['grade']}*\n"
             f"School: *{found['school']}*, Area: *{found['area']}*\n\n"
             f"اختَر مدرّس للتواصل.")
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

    tg("sendMessage", {
        "chat_id": chat_id,
        "text": "تقدر تتواصل مباشرة على واتساب مع أي مدرس من الروابط اللي فوق (Contact). بالتوفيق 🌟"
    })

    return jsonify({"ok": True})
