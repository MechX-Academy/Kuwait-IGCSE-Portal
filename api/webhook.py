import os, json, re, time, traceback
from typing import Dict, Any, List, Tuple, Set
from flask import Flask, request, jsonify
import requests
from rapidfuzz import fuzz

app = Flask(__name__)

# ================== Build tag ==================
BUILD_TAG = "no-echo-v8"

# ================== Telegram setup ==================
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
        print("[TG ERROR]", e)
        return None

# ================== Load teachers ==================
DATA_PATH = os.path.join(os.path.dirname(__file__), "teachers.json")
try:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        TEACHERS = json.load(f)
    print(f"[BOOT] Loaded {len(TEACHERS)} teachers from {DATA_PATH}")
except Exception as e:
    print(f"[BOOT] ERROR loading teachers.json from {DATA_PATH}: {e}")
    TEACHERS = []

# ================== Subject groups (UI) ==================
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
    "CS":  "Computer Science",
    "EM": "Environmental Management",
    "PE": "Physical Education",
    "TT": "Travel & Tourism",
}

BOARD_CODES = {"C": "Cambridge", "E": "Edexcel", "O": "OxfordAQA"}

# ================== helpers ==================
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def match_teachers(subject=None, grade=None, board=None, limit=4):
    scored = []
    for t in TEACHERS:
        score = 0
        if subject:
            if any(_norm(subject) == _norm(s) for s in t.get("subjects", [])):
                score += 60
            else:
                best = max((fuzz.partial_ratio(subject.lower(), s.lower())
                           for s in t.get("subjects", [])), default=0)
                score += best * 0.3
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
                row.append({"text": f"{tick(code)} {label}",
                            "callback_data": f"T|{code}|{board_code}|{grade}|{encode_sel(sel)}"})
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

# ============ Idempotency ============
RECENT_DONE: Dict[int, List[Tuple[str, float]]] = {}
def already_done(chat_id: int, signature: str, ttl: int = 300) -> bool:
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

# ================== Final message formatting ==================
def format_teacher_line(t: Dict[str, Any]) -> str:
    quals = ", ".join(t.get("qualifications", []))
    boards = ", ".join(t.get("boards", []))
    grades = ""
    if t.get("grades"):
        gmin, gmax = min(t["grades"]), max(t["grades"])
        grades = f"Grades {gmin}-{gmax}"
    contact = t.get("contact", {})
    wa = contact.get("whatsapp") or ""
    whatsapp = f"[WhatsApp]({wa})" if wa else ""
    photo_url = t.get("photo_url") or ""
    photo = f"[Photo]({photo_url})" if photo_url else ""
    parts = [
        f"*{t['name']}* — {', '.join(t.get('subjects', []))}",
        "  " + " | ".join([x for x in [grades, f"Boards {boards}" if boards else ""] if x]),
    ]
    if t.get("bio"):        parts.append("  " + t["bio"])
    if quals:               parts.append("  " + f"Qualifications: {quals}")
    if photo or whatsapp:   parts.append("  " + " • ".join([x for x in [photo, whatsapp] if x]))
    return "\n".join(parts)

def build_final_message(board: str, grade: int, subjects: List[str], matches: List[Dict[str, Any]]) -> str:
    header = (f"Thanks! Here are the best matches for:\n"
              f"Board: *{board}* | Grade: *{grade}*\n"
              f"Subjects: *{', '.join(subjects)}*")
    body_lines = []
    if matches:
        for i, t in enumerate(matches, 1):
            body_lines.append(f"\n*{i})* " + format_teacher_line(t))
    else:
        body_lines.append("\nSorry, no exact matches right now. We’ll expand the search and get back to you.")
    top_preview = ""
    if matches and matches[0].get("photo_url"):
        top_preview = matches[0]["photo_url"] + "\n\n"
    return top_preview + header + "\n" + "\n".join(body_lines)

def collect_best_matches(subjects: List[str], grade: int, board: str, k: int = 4) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for s in subjects:
        for t in match_teachers(s, grade, board, limit=3):
            tid = t.get("id") or t["name"]
            if tid in seen:
                continue
            seen.add(tid)
            out.append(t)
            if len(out) >= k:
                return out
    return out

# ================== Health ==================
@app.get("/api/webhook")
def ping():
    return jsonify(ok=True, msg="webhook alive", teachers=len(TEACHERS), build=BUILD_TAG, bot=bool(BOT_API))

# ================== Webhook core ==================
def _handle_webhook():
    try:
        # التوكن جوّا try عشان ما نرميش 500 أبداً
        if not BOT_API:
            print("[ERR] Missing TELEGRAM_BOT_TOKEN")
            return jsonify({"ok": True, "warn": "Missing TELEGRAM_BOT_TOKEN"}), 200

        update = request.get_json(force=True, silent=True) or {}
        try:
            print("[UPDATE]", json.dumps(update)[:2000])
        except Exception:
            print("[UPDATE] (non-serializable)")

        # ===== inline callbacks =====
        if "callback_query" in update:
            cq = update["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            msg_id  = cq["message"]["message_id"]
            data = cq.get("data", "")

            tg("answerCallbackQuery", {"callback_query_id": cq["id"]})

            if (cq.get("message", {}).get("text") or "").startswith("Thanks!"):
                return jsonify({"ok": True})

            def edit(text=None, reply_markup=None, parse_mode=None, disable_preview=None):
                if text is not None:
                    payload = {"chat_id": chat_id, "message_id": msg_id, "text": text}
                    if parse_mode: payload["parse_mode"] = parse_mode
                    if reply_markup is not None: payload["reply_markup"] = reply_markup
                    if disable_preview is not None: payload["disable_web_page_preview"] = disable_preview
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

                signature = f"{msg_id}|{b}|{g}|{'.'.join(sorted(sel))}"
                if already_done(chat_id, signature):
                    print(f"[SKIP] duplicate done {signature}")
                    return jsonify({"ok": True})

                tg("editMessageReplyMarkup", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "reply_markup": {"inline_keyboard": []}
                })

                matches = collect_best_matches(subjects, g, board, k=4)
                final_text = build_final_message(board, g, subjects, matches)
                print(f"[DONE] chat={chat_id} msg={msg_id} board={board} grade={g} subjects={subjects} matches={len(matches)}")

                tg("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": final_text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": False
                })
                return jsonify({"ok": True})

            return jsonify({"ok": True})

        # ===== normal text messages =====
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return jsonify({"ok": True})

        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip().lower()

        if text in ("/start", "start"):
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

    except Exception as e:
        print("[ERR]", repr(e))
        print(traceback.format_exc())
        return jsonify({"ok": True}), 200

# ================== Routes ==================
@app.post("/api/webhook")
def webhook_api():
    return _handle_webhook()

@app.route("/", defaults={"subpath": ""}, methods=["POST"])
@app.route("/<path:subpath>", methods=["POST"])
def webhook_catchall(subpath=None):
    return _handle_webhook()
