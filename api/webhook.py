# api/webhook.py
import os, json, re, time, html, traceback
from typing import Dict, Any, List, Tuple, Set
from flask import Flask, request, jsonify
import requests
from rapidfuzz import fuzz
from urllib.parse import quote

app = Flask(__name__)
BUILD_TAG = "kuwait-igcse-portal-v1.0"

# ------------ Telegram basics ------------
TELEGRAM_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
if not TELEGRAM_TOKEN:
    print("WARNING: Missing TELEGRAM_BOT_TOKEN")
BOT_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}" if TELEGRAM_TOKEN else None


def tg(method: str, payload: Dict[str, Any]):
    """Safe Telegram call with light logging."""
    if not BOT_API:
        print(f"[TG] skip {method} (no token)")
        return None
    try:
        r = requests.post(f"{BOT_API}/{method}", json=payload, timeout=20)
        try:
            j = r.json()
        except Exception:
            j = {}
        if r.status_code != 200 or (isinstance(j, dict) and not j.get("ok", True)):
            print(f"[TG ERR] {method} {r.status_code} -> {r.text[:500]}")
        else:
            print(f"[TG OK] {method}")
        return r
    except Exception as e:
        print("[TG EXC]", method, repr(e))
        return None


# ------------ Load data ------------
DATA_PATH = os.path.join(os.path.dirname(__file__), "teachers.json")
try:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        TEACHERS = json.load(f)
    print(f"Loaded {len(TEACHERS)} teachers.")
except Exception as e:
    print(f"ERROR loading teachers.json: {e}")
    TEACHERS = []


# ------------ Subject dictionaries ------------
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

# ------------ Helpers ------------
def h(x: str) -> str:
    return html.escape(x or "")

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def canonical_subject(label: str) -> str | None:
    t = _norm(label)
    for canonical, aliases in VALID_SUBJECTS.items():
        pool = [canonical] + aliases
        if any(_norm(x) == t for x in pool):
            nice = {
                "ict": "ICT",
                "cs": "Computer Science",
                "pe": "Physical Education",
                "english language": "English Language",
                "english literature": "English Literature",
                "math": "Math",
            }
            key = canonical.lower()
            return nice.get(key, canonical.title())
    return None

# preprocess teacher canonical subjects
for t in TEACHERS:
    subj = t.get("subjects", [])
    t["_subjects_canon"] = set()
    for s in subj:
        c = canonical_subject(s)
        if c:
            t["_subjects_canon"].add(c)

def match_teachers(subject=None, grade=None, board=None, limit=4):
    """Strict subject matching; score by grade + board."""
    results = []
    wanted = canonical_subject(subject) if subject else None
    for t in TEACHERS:
        if wanted and wanted not in t.get("_subjects_canon", set()):
            continue
        score = 0
        if grade and t.get("grades") and grade in t["grades"]:
            score += 50
        if board and t.get("boards") and any(_norm(board) == _norm(b) for b in t["boards"]):
            score += 50
        results.append((score, t))
    results.sort(key=lambda x: x[0], reverse=True)
    trimmed = [t for sc, t in results if sc > 0]
    if not trimmed:
        trimmed = [t for _, t in results]
    return trimmed[:limit]

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

def build_wa_link(t: Dict[str,Any], parent_name: str, board: str, grade: int, subjects: List[str]) -> str:
    contact = t.get("contact", {}) or {}
    wa = (contact.get("whatsapp") or contact.get("phone") or "").strip()
    # normalize to wa.me/NNN
    if wa.startswith("https://wa.me/"):
        base = wa
    else:
        num = re.sub(r"\D+", "", wa)
        base = f"https://wa.me/{num}" if num else "https://wa.me/"
    msg = (
        f"Hello, this is {parent_name}.\n"
        f"I'm interested in {t.get('name','the tutor')} for {', '.join(subjects)} "
        f"(Board: {board}, Grade: {grade}).\n"
        f"Could you please share availability and fees?"
    )
    return f"{base}?text={quote(msg)}"

def format_teacher_caption_html(t: Dict[str,Any], parent_name: str, board: str, grade: int, subjects: List[str]) -> str:
    quals = ", ".join(t.get("qualifications", []))
    boards = ", ".join(t.get("boards", []))
    grades = ""
    if t.get("grades"):
        gmin, gmax = min(t["grades"]), max(t["grades"])
        grades = f"Grades {gmin}-{gmax}"
    wa_link = build_wa_link(t, parent_name, board, grade, subjects)
    lines = [
        f"<b>{h(t['name'])}</b> — {h(', '.join(t.get('subjects', [])))}",
        "  " + " | ".join([x for x in [h(grades), f"Boards {h(boards)}" if boards else ""] if x]),
    ]
    if t.get("bio"):      lines.append("  " + h(t["bio"]))
    if quals:             lines.append("  " + f"Qualifications: {h(quals)}")
    lines.append(f'  <a href="{h(wa_link)}">WhatsApp</a>')
    return "\n".join(lines)

def build_overview_text(board: str, grade: int, subjects: List[str], first_photo: str | None) -> str:
    head = (
        f"Thanks! Here are the best matches for:\n"
        f"Board: <b>{h(board)}</b> | Grade: <b>{grade}</b>\n"
        f"Subjects: <b>{h(', '.join(subjects))}</b>"
    )
    if first_photo:
        return h(first_photo) + "\n\n" + head
    return head

# ------------ Inline keyboards (board/grade/subjects) ------------
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
    chosen = ", ".join(h(CODE_TO_SUBJECT[c]) for c in sorted(sel)) if sel else "—"
    return (f"<b>Step 3/3 – Subjects</b>\n"
            f"Board: <b>{h(board)}</b>   |   Grade: <b>{grade}</b>\n"
            f"Pick one or more subjects, then press <b>Done</b>.\n"
            f"Selected: {chosen}")


# ------------ Preferences UI (One-to-one/Group + 1/2 per week) ------------
def pref_text(code: str, board_code: str, grade: int, t_opt: str, w_opt: str) -> str:
    subject = CODE_TO_SUBJECT.get(code, code)
    board = BOARD_CODES.get(board_code, board_code)
    t1 = "✅" if t_opt == "O" else "☐"
    t2 = "✅" if t_opt == "G" else "☐"
    w1 = "✅" if w_opt == "1" else "☐"
    w2 = "✅" if w_opt == "2" else "☐"
    return (
        f"<b>Subject:</b> {h(subject)}\n"
        f"<b>Board:</b> {h(board)}  |  <b>Grade:</b> {grade}\n\n"
        f"<b>1)</b> Does your child prefer one-to-one or group tuition?\n"
        f"{t1} One-to-one    {t2} Group\n\n"
        f"<b>2)</b> How many lessons per week for this subject?\n"
        f"{w1} 1   {w2} 2\n\n"
        f"Press <b>Next</b> when both are selected."
    )

def kb_prefs(code: str, rest: str, board_code: str, grade: int, t_opt: str, w_opt: str):
    row1 = [
        {"text": ("✅ " if t_opt == "O" else "☐ ") + "One-to-one",
         "callback_data": f"Q|{board_code}|{grade}|{code}|{rest}|O|{w_opt}"},
        {"text": ("✅ " if t_opt == "G" else "☐ ") + "Group",
         "callback_data": f"Q|{board_code}|{grade}|{code}|{rest}|G|{w_opt}"},
    ]
    row2 = [
        {"text": ("✅ " if w_opt == "1" else "☐ ") + "1",
         "callback_data": f"Q|{board_code}|{grade}|{code}|{rest}|{t_opt}|1"},
        {"text": ("✅ " if w_opt == "2" else "☐ ") + "2",
         "callback_data": f"Q|{board_code}|{grade}|{code}|{rest}|{t_opt}|2"},
    ]
    row3 = [{"text": "Next ▶️", "callback_data": f"QN|{board_code}|{grade}|{code}|{rest}|{t_opt}|{w_opt}"}]
    return {"inline_keyboard": [row1, row2, row3]}

def start_subject_pref(chat_id: int, board_code: str, grade: int, sel_codes: List[str]) -> None:
    if not sel_codes:
        return
    rest = ".".join(sel_codes[1:])
    code = sel_codes[0]
    tg("sendMessage", {
        "chat_id": chat_id,
        "text": pref_text(code, board_code, grade, "?", "?"),
        "parse_mode": "HTML",
        "reply_markup": kb_prefs(code, rest, board_code, grade, "?", "?")
    })


# ------------ In-memory session & idempotency ------------
SESSIONS: Dict[int, Dict[str, Any]] = {}
RECENT_DONE: Dict[int, List[Tuple[str, float]]] = {}

def session(chat_id: int) -> Dict[str, Any]:
    if chat_id not in SESSIONS:
        SESSIONS[chat_id] = {"stage": "idle", "name": ""}
    return SESSIONS[chat_id]

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


# ------------ Routes ------------
@app.get("/api/webhook")
def ping():
    return jsonify(ok=True, build=BUILD_TAG, teachers=len(TEACHERS), bot=bool(BOT_API))


def _handle_webhook():
    try:
        update = request.get_json(force=True, silent=True) or {}
        print("[UPDATE]", json.dumps(update)[:1200])

        # ---------- Callback queries ----------
        if "callback_query" in update:
            cq = update["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            msg_id = cq["message"]["message_id"]
            data = cq.get("data", "")
            tg("answerCallbackQuery", {"callback_query_id": cq["id"]})

            if data == "noop":
                return jsonify({"ok": True})

            # Board chosen
            if data.startswith("B|"):
                b = data.split("|", 1)[1]
                s = session(chat_id)
                s["board_code"] = b
                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": "<b>Step 2/3 – Grade</b>\nSelect your child's current grade:",
                    "parse_mode": "HTML", "reply_markup": kb_grade(b)
                })
                return jsonify({"ok": True})

            # Grade chosen
            if data.startswith("G|"):
                _, g, b = data.split("|", 2)
                g = int(g)
                s = session(chat_id)
                s["board_code"] = b
                s["grade"] = g
                sel = set()
                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": summary_text(b, g, sel),
                    "parse_mode": "HTML", "reply_markup": kb_subjects(b, g, sel)
                })
                return jsonify({"ok": True})

            # Toggle subject
            if data.startswith("T|"):
                _, code, b, g, enc = data.split("|", 4)
                g = int(g)
                sel = decode_sel(enc)
                if code == "__RESET__":
                    sel = set()
                else:
                    if code in sel: sel.remove(code)
                    else: sel.add(code)
                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": summary_text(b, g, sel),
                    "parse_mode": "HTML", "reply_markup": kb_subjects(b, g, sel)
                })
                return jsonify({"ok": True})

            # Done selecting subjects
            if data.startswith("D|"):
                _, b, g, enc = data.split("|", 3)
                g = int(g)
                sel_codes = [x for x in enc.split(".") if x]
                if not sel_codes:
                    tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Please select at least one subject."})
                    return jsonify({"ok": True})

                s = session(chat_id)
                s["board_code"] = b
                s["grade"] = g
                s["sel_codes"] = sel_codes
                s["subjects"] = [CODE_TO_SUBJECT[c] for c in sel_codes]

                tg("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": msg_id, "reply_markup": {"inline_keyboard": []}})
                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": (f"Great! We’ll tailor recommendations per subject.\n"
                             f"Board: <b>{h(BOARD_CODES.get(b,b))}</b> | Grade: <b>{g}</b>\n"
                             f"Subjects: <b>{h(', '.join(s['subjects']))}</b>\n\n"
                             f"You’ll be asked two quick preferences for each subject."),
                    "parse_mode": "HTML"
                })

                # Ask preferences for the first subject
                start_subject_pref(chat_id, b, g, sel_codes)
                return jsonify({"ok": True})

            # Preference toggle (Q|...) and Next (QN|...)
            if data.startswith("Q|"):
                _, b, g, code, rest, t_opt, w_opt = data.split("|", 6)
                g = int(g)
                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": pref_text(code, b, g, t_opt, w_opt),
                    "parse_mode": "HTML",
                    "reply_markup": kb_prefs(code, rest, b, g, t_opt, w_opt)
                })
                return jsonify({"ok": True})

            if data.startswith("QN|"):
                _, b, g, code, rest, t_opt, w_opt = data.split("|", 6)
                g = int(g)
                if t_opt not in ("O","G") or w_opt not in ("1","2"):
                    tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Choose both options first."})
                    return jsonify({"ok": True})

                # move to next subject if there is
                if rest:
                    next_list = [x for x in rest.split(".") if x]
                    start_subject_pref(chat_id, b, g, next_list)
                    tg("editMessageText", {
                        "chat_id": chat_id, "message_id": msg_id,
                        "text": "Saved. Moving to the next subject…",
                        "parse_mode": "HTML"
                    })
                    return jsonify({"ok": True})

                # No more subjects -> produce matches once
                s = session(chat_id)
                board = BOARD_CODES.get(s.get("board_code", b), b)
                grade = s.get("grade", g)
                subjects = s.get("subjects", [])
                parent_name = s.get("name") or "Parent"

                signature = f"FINAL|{msg_id}|{board}|{grade}|{'.'.join(subjects)}"
                if already_done(chat_id, signature):
                    print("[SKIP] duplicate final")
                    return jsonify({"ok": True})

                matches = collect_best_matches(subjects, grade, board, k=4)
                first_photo = matches[0].get("photo_url") if matches and matches[0].get("photo_url") else None
                overview = build_overview_text(board, grade, subjects, first_photo)
                tg("sendMessage", {
                    "chat_id": chat_id, "text": overview, "parse_mode": "HTML",
                    "disable_web_page_preview": False
                })

                # Send each tutor as photo+caption
                for t in matches:
                    caption = format_teacher_caption_html(t, parent_name, board, grade, subjects)
                    photo = t.get("photo_url")
                    if photo:
                        tg("sendPhoto", {
                            "chat_id": chat_id, "photo": photo,
                            "caption": caption, "parse_mode": "HTML"
                        })
                    else:
                        tg("sendMessage", {"chat_id": chat_id, "text": caption, "parse_mode": "HTML"})

                if not matches:
                    tg("sendMessage", {"chat_id": chat_id,
                                       "text": "Sorry, no exact matches right now. We’ll expand the search and get back to you."})

                return jsonify({"ok": True})

            return jsonify({"ok": True})

        # ---------- Normal messages (/start, name, fallback) ----------
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return jsonify({"ok": True})

        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        s = session(chat_id)

        if text.lower() in ("/start", "start"):
            # reset session & ask for parent name
            SESSIONS[chat_id] = {"stage": "ask_name", "name": ""}
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": "Welcome to Kuwait IGCSE Portal 👋\nPlease type your full name (parent):",
            })
            return jsonify({"ok": True})

        if s.get("stage") == "ask_name" and text:
            s["name"] = text
            s["stage"] = "flow"
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": "<b>Step 1/3 – Board</b>\nWhich board or curriculum does your child follow?",
                "parse_mode": "HTML",
                "reply_markup": kb_board()
            })
            return jsonify({"ok": True})

        # Fallback: point user to guided flow
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": "Please use the guided flow 👇",
            "reply_markup": kb_board()
        })
        return jsonify({"ok": True})

    except Exception as e:
        print("[ERR]", repr(e))
        print(traceback.format_exc())
        # return 200 so Telegram doesn't retry (avoids duplicates)
        return jsonify({"ok": True}), 200


# Vercel entrypoint
@app.post("/api/webhook")
def webhook_api():
    return _handle_webhook()

# Catch-all (safety if Telegram hits root)
@app.route("/", defaults={"subpath": ""}, methods=["POST"])
@app.route("/<path:subpath>", methods=["POST"])
def webhook_catchall(subpath=None):
    return _handle_webhook()
