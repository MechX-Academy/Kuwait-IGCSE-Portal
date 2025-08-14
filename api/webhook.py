# api/webhook.py
import os, json, re, time, html, traceback
from typing import Dict, Any, List, Tuple, Set
from flask import Flask, request, jsonify
import requests
from urllib.parse import quote

app = Flask(__name__)
BUILD_TAG = "kuwait-igcse-portal-v2.9-en"

# ------------ Telegram basics ------------
TELEGRAM_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
if not TELEGRAM_TOKEN:
    print("WARNING: Missing TELEGRAM_BOT_TOKEN")
BOT_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}" if TELEGRAM_TOKEN else None

# WhatsApp number for the final unified portal link
PORTAL_WA_NUMBER = re.sub(r"\D+", "", os.getenv("PORTAL_WA_NUMBER", "+96597273411")) or "96597273411"

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
    print(f"Loaded {len(TEACHERS)} teachers from {DATA_PATH}.")
except Exception as e:
    print(f"ERROR loading teachers.json: {e}")
    TEACHERS = []


# ------------ Subject dictionaries ------------
VALID_SUBJECTS = {
    "math": ["math", "mathematics", "maths", "additional math", "further math", "igcse mathematics"],
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

def _nice_subject_name(key: str) -> str:
    nice = {
        "ict": "ICT",
        "cs": "Computer Science",
        "pe": "Physical Education",
        "english language": "English Language",
        "english literature": "English Literature",
        "math": "Math",
    }
    return nice.get(key, key.title())

def canonical_subject(label: str) -> str | None:
    """Normalize subject labels."""
    t = _norm(label)
    if not t:
        return None
    t_clean = re.sub(r"[^a-z0-9\s&]+", " ", t)
    t_clean = re.sub(r"\s+", " ", t_clean).strip()
    for canonical, aliases in VALID_SUBJECTS.items():
        pool = [canonical] + aliases
        pool_norm = [_norm(x) for x in pool]
        if any(t_clean == p for p in pool_norm):
            return _nice_subject_name(canonical.lower())
        for alias in pool_norm:
            if re.search(rf"\b{re.escape(alias)}\b", t_clean):
                return _nice_subject_name(canonical.lower())
    return None

def teacher_has_subject(teacher_subjects: List[str], wanted_label: str) -> bool:
    wanted = canonical_subject(wanted_label)
    if not wanted:
        return False
    for s in teacher_subjects or []:
        c = canonical_subject(s)
        if c == wanted:
            return True
    return False

# --- Board normalization (handles "Oxford" vs "OxfordAQA") ---
def canonical_board(label: str) -> str:
    t = _norm(label)
    if t in ("o", "oxford", "oxfordaqa", "oxford aqa"):
        return "oxfordaqa"
    if t in ("c", "cambridge"):
        return "cambridge"
    if t in ("e", "edexcel", "pearson edexcel", "pearson"):
        return "edexcel"
    return t or ""

# Precompute canonical subjects/boards per teacher
for t in TEACHERS:
    subj = t.get("subjects", []) or []
    t["_subjects_canon"] = set()
    for s in subj:
        c = canonical_subject(s)
        if c:
            t["_subjects_canon"].add(c)
    t["_boards_canon"] = [canonical_board(b) for b in (t.get("boards") or [])]

def match_teachers(subject=None, grade=None, board=None, limit=4):
    """
    STRICT matching: teacher must match SUBJECT + GRADE + BOARD. No fallbacks.
    """
    board_can = canonical_board(board) if board else ""
    results = []
    for t in TEACHERS:
        # Subject mandatory
        if subject and not teacher_has_subject(t.get("subjects", []), subject):
            continue
        # Grade mandatory
        if grade is not None:
            grades = t.get("grades") or []
            if grade not in grades:
                continue
        # Board mandatory (canonicalized)
        if board_can:
            if board_can not in (t.get("_boards_canon") or []):
                continue
        results.append(t)
    # stable alphabetical order
    results.sort(key=lambda tt: tt.get("name", "").lower())
    return results[:limit]

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

def build_wa_link(t: Dict[str,Any], student_full_name: str, board: str, grade: int, subjects: List[str]) -> str:
    """Build WA link PER TEACHER card: filter subjects to those taught by this teacher."""
    contact = t.get("contact", {}) or {}
    wa = (contact.get("whatsapp") or contact.get("phone") or "").strip()
    if wa.startswith("https://wa.me/"):
        base = wa
    else:
        num = re.sub(r"\D+", "", wa)
        base = f"https://wa.me/{num}" if num else f"https://wa.me/{PORTAL_WA_NUMBER}"
    teacher_subjs = set(t.get("_subjects_canon", set()) or [])
    filtered_subjects = [s for s in subjects if canonical_subject(s) in teacher_subjs]
    if not filtered_subjects:
        filtered_subjects = subjects
    msg = (
        f"Hello, this is {student_full_name}.\n"
        f"I'm interested in {t.get('name','the tutor')} for {', '.join(filtered_subjects)} "
        f"(Board: {board}, Grade: {grade}).\n"
        f"Could you please share availability and fees?"
    )
    return f"{base}?text={quote(msg)}"

def format_teacher_caption_html(t: Dict[str,Any], student_full_name: str, board: str, grade: int, subjects: List[str]) -> str:
    quals = ", ".join(t.get("qualifications", []))
    boards = ", ".join(t.get("boards", []))
    grades = ""
    if t.get("grades"):
        gmin, gmax = min(t["grades"]), max(t["grades"])
        grades = f"Grades {gmin}-{gmax}"
    wa_link = build_wa_link(t, student_full_name, board, grade, subjects)
    lines = [
        f"<b>{h(t['name'])}</b> — {h(', '.join(t.get('subjects', [])))}",
        "  " + " | ".join([x for x in [h(grades), f"Boards {h(boards)}" if boards else ""] if x]),
    ]
    if t.get("bio"):      lines.append("  " + h(t["bio"]))
    if quals:             lines.append("  " + f"Qualifications: {h(quals)}")
    # lines.append(f'  <a href="{h(wa_link)}">WhatsApp</a>')
    return "\n".join(lines)

# Append a Restart button to any inline keyboard
def kb_with_restart(markup: Dict[str, Any] | None) -> Dict[str, Any]:
    if not markup:
        markup = {"inline_keyboard": []}
    rows = markup.get("inline_keyboard", [])
    rows.append([{"text": "⟲ Restart", "callback_data": "FORCE_RESTART"}])
    return {"inline_keyboard": rows}


# ------------ Inline keyboards (board/grade/subjects) ------------
def encode_sel(sel: Set[str]) -> str:
    return ".".join(sorted(sel)) if sel else ""

def decode_sel(s: str) -> Set[str]:
    return set([x for x in s.split(".") if x])

def kb_board():
    return {"inline_keyboard": [[
        {"text": "Cambridge", "callback_data": "B|C"},
        {"text": "Edexcel",   "callback_data": "B|E"},
        {"text": "OxfordAQA", "callback_data": "B|O"},
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


# ------------ Selection of teachers (checkbox UI) ------------
def kb_select_teachers(matches: List[Dict[str, Any]], selected_ids: Set[str]):
    rows = []
    def tick(tid): return "✅" if tid in selected_ids else "☐"
    for t in matches:
        rows.append([{
            "text": f"{tick(t['id'])} {t['name']}",
            "callback_data": f"SEL_TEACHER|{t['id']}"
        }])
    if not rows:
        rows.append([{"text": "No matching results", "callback_data": "noop"}])
    rows.append([{"text": "📩 Send WhatsApp Link", "callback_data": "SEND_WA"}])
    rows.append([{"text": "➕ Add more subjects", "callback_data": "ADD_MORE"}])
    return {"inline_keyboard": rows}


# ------------ In-memory session & idempotency ------------
SESSIONS: Dict[int, Dict[str, Any]] = {}
RECENT_DONE: Dict[int, List[Tuple[str, float]]] = {}

def session(chat_id: int) -> Dict[str, Any]:
    if chat_id not in SESSIONS:
        SESSIONS[chat_id] = {"stage": "idle", "name": "", "selections": []}
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

            # Force restart
            if data == "FORCE_RESTART":
                SESSIONS[chat_id] = {"stage": "ask_name", "name": "", "selections": []}
                tg("sendMessage", {
                    "chat_id": chat_id,
                    "text": "👋 Welcome to Kuwait IGCSE Portal!\nPlease type your full name (student):",
                    "reply_markup": kb_with_restart({"inline_keyboard": []})
                })
                return jsonify({"ok": True})

            # Board chosen
            if data.startswith("B|"):
                b = data.split("|", 1)[1]
                s = session(chat_id)
                s["board_code"] = b
                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": "🔢 <b>Step 2/3 – Grade</b>\nSelect your current grade:",
                    "parse_mode": "HTML", "reply_markup": kb_with_restart(kb_grade(b))
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
                    "parse_mode": "HTML", "reply_markup": kb_with_restart(kb_subjects(b, g, sel))
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
                    "parse_mode": "HTML", "reply_markup": kb_with_restart(kb_subjects(b, g, sel))
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
                selection = {
                    "board_code": b,
                    "grade": g,
                    "subjects": [CODE_TO_SUBJECT[c] for c in sel_codes]
                }
                s.setdefault("selections", []).append(selection)

                tg("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": msg_id, "reply_markup": {"inline_keyboard": []}})
                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": (f"Saved ✅\n"
                             f"Board: <b>{h(BOARD_CODES.get(b,b))}</b> | Grade: <b>{g}</b>\n"
                             f"Subjects: <b>{h(', '.join(selection['subjects']))}</b>\n\n"
                             f"Do you want to add subjects from another Board/Grade?"),
                    "parse_mode": "HTML",
                    "reply_markup": kb_with_restart({
                        "inline_keyboard": [
                            [{"text": "➕ Add more", "callback_data": "ADD_MORE"}],
                            [{"text": "🚀 Show tutors", "callback_data": "SHOW_ALL"}]
                        ]
                    })
                })
                return jsonify({"ok": True})

            # Add more -> back to Step 1
            if data == "ADD_MORE":
                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": "🧭 <b>Step 1/3 – Board</b>\nChoose the board for the new selection:",
                    "parse_mode": "HTML",
                    "reply_markup": kb_with_restart(kb_board())
                })
                return jsonify({"ok": True})

            # Show all tutors
            if data == "SHOW_ALL":
                s = session(chat_id)
                selections = s.get("selections", [])
                if not selections:
                    tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "No selections yet."})
                    return jsonify({"ok": True})

                per_teacher_map: Dict[str, Dict[str, Any]] = {}
                ordered_cards: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

                for sel in selections:
                    board_name_display = BOARD_CODES.get(sel["board_code"], sel["board_code"])
                    matches = collect_best_matches(sel["subjects"], sel["grade"], board_name_display, k=4)
                    for t in matches:
                        tid = t.get("id") or t["name"]
                        if not any((t2.get("id") or t2["name"]) == tid for (t2, _) in ordered_cards):
                            ordered_cards.append((t, sel))
                        entry = per_teacher_map.setdefault(tid, {"id": tid, "name": t["name"], "parts": []})
                        entry["parts"].append({"subjects": sel["subjects"], "board": board_name_display, "grade": sel["grade"]})

                student_name = s.get("name") or "Student"
                for t, sel in ordered_cards:
                    caption = format_teacher_caption_html(
                        t, student_name,
                        BOARD_CODES.get(sel["board_code"], sel["board_code"]),
                        sel["grade"],
                        sel["subjects"]
                    )
                    photo = t.get("photo_url")
                    if photo:
                        tg("sendPhoto", {"chat_id": chat_id, "photo": photo, "caption": caption, "parse_mode": "HTML"})
                    else:
                        tg("sendMessage", {"chat_id": chat_id, "text": caption, "parse_mode": "HTML"})

                s["last_matches"] = [{"id": v["id"], "name": v["name"]} for v in per_teacher_map.values()]
                s["per_teacher_map"] = per_teacher_map
                s["selected_teachers"] = set()

                tg("sendMessage", {
                    "chat_id": chat_id,
                    "text": "Select the tutors you're interested in, then press <b>📩 Send WhatsApp Link</b>.",
                    "parse_mode": "HTML",
                    "reply_markup": kb_with_restart(kb_select_teachers(s["last_matches"], s["selected_teachers"]))
                })
                return jsonify({"ok": True})

            # Toggle teacher selection
            if data.startswith("SEL_TEACHER|"):
                _, tid = data.split("|", 1)
                s = session(chat_id)
                sel_ids: Set[str] = s.setdefault("selected_teachers", set())
                if tid in sel_ids: sel_ids.remove(tid)
                else: sel_ids.add(tid)
                rows = kb_select_teachers(s.get("last_matches", []), sel_ids)
                tg("editMessageReplyMarkup", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "reply_markup": kb_with_restart(rows)
                })
                return jsonify({"ok": True})

            # Send one WhatsApp link with all chosen tutors
            if data == "SEND_WA":
                s = session(chat_id)
                sel_ids: Set[str] = s.get("selected_teachers", set())
                if not sel_ids:
                    tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Pick at least one tutor."})
                    return jsonify({"ok": True})

                per_teacher_map = s.get("per_teacher_map", {})
                chosen = [per_teacher_map[tid] for tid in sel_ids if tid in per_teacher_map]

                msg_lines = [f"Hello, this is {s.get('name','Student')}.\nI'm interested in the following:"]
                for item in chosen:
                    name = item["name"]
                    parts = item["parts"]
                    collapsed: Dict[Tuple[str,int], Set[str]] = {}
                    for p in parts:
                        key = (p["board"], p["grade"])
                        collapsed.setdefault(key, set()).update(p["subjects"])
                    sub_parts = []
                    for (board, grade), subjset in collapsed.items():
                        sub_parts.append(f"{', '.join(sorted(subjset))} - {board} Grade {grade}")
                    msg_lines.append(f"- {name} ({' | '.join(sub_parts)})")

                msg_lines.append("Could you please share availability and fees?")
                final_msg = "\n".join(msg_lines)
                wa_link = f"https://wa.me/{PORTAL_WA_NUMBER}?text={quote(final_msg)}"

                tg("sendMessage", {
                    "chat_id": chat_id,
                    "text": f"<a href=\"{wa_link}\">📩 Open WhatsApp</a>",
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": kb_with_restart({"inline_keyboard": []})
                })
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
            # reset session & ask for student full name
            SESSIONS[chat_id] = {"stage": "ask_name", "name": "", "selections": []}
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": "👋 Welcome to Kuwait IGCSE Portal!\nPlease type your full name (student):",
                "reply_markup": kb_with_restart({"inline_keyboard": []})
            })
            return jsonify({"ok": True})

        if s.get("stage") == "ask_name" and text:
            s["name"] = text
            s["stage"] = "flow"
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": "🧭 <b>Step 1/3 – Board</b>\nChoose the board:",
                "parse_mode": "HTML",
                "reply_markup": kb_with_restart(kb_board())
            })
            return jsonify({"ok": True})

        # Fallback: point user to guided flow
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": "Please use the options below to continue 👇",
            "reply_markup": kb_with_restart(kb_board())
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
