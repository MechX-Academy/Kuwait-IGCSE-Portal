import os, json, re, time, traceback, html
from typing import Dict, Any, List, Tuple, Set
from flask import Flask, request, jsonify
import requests
from rapidfuzz import fuzz

app = Flask(__name__)

BUILD_TAG = "portal-subject-prefs-v1"

# ---------- Telegram ----------
TELEGRAM_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
if not TELEGRAM_TOKEN:
    print("WARNING: Missing TELEGRAM_BOT_TOKEN")
BOT_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}" if TELEGRAM_TOKEN else None

def tg(method: str, payload: Dict[str, Any]):
    """Telegram call with logging; never crash."""
    if not BOT_API:
        print("[TG] BOT_API missing; skip", method)
        return None
    try:
        r = requests.post(f"{BOT_API}/{method}", json=payload, timeout=20)
        try:
            j = r.json()
        except Exception:
            j = {}
        if r.status_code != 200 or (isinstance(j, dict) and not j.get("ok", True)):
            print(f"[TG ERR] {method} {r.status_code} -> {r.text[:800]}")
        else:
            print(f"[TG OK] {method}")
        return r
    except Exception as e:
        print("[TG EXC]", method, repr(e))
        return None

# ---------- data ----------
DATA_PATH = os.path.join(os.path.dirname(__file__), "teachers.json")
try:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        TEACHERS = json.load(f)
    print(f"Loaded {len(TEACHERS)} teachers from {DATA_PATH}")
except Exception as e:
    print(f"ERROR loading teachers.json from {DATA_PATH}: {e}")
    TEACHERS = []

# canonical subjects used for matching
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

# short codes for multi-select keyboards
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

# mapping code -> canonical subject label used in TEACHERS/VALID_SUBJECTS
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

BOARD_CODES = {"C": "Cambridge", "E": "Edexcel", "O": "OxfordAQA"}  # "Oxford" → OxfordAQA

# ---------- helpers ----------
def h(x: str) -> str:
    return html.escape(x or "")

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def canonical_subject(label: str) -> str | None:
    """Map any input label to one canonical subject name (strict)."""
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

# preprocess normalized subjects per teacher
for t in TEACHERS:
    subj = t.get("subjects", [])
    t["_subjects_canon"] = set()
    for s in subj:
        c = canonical_subject(s)
        if c:
            t["_subjects_canon"].add(c)

def match_teachers(subject=None, grade=None, board=None, limit=4):
    """Strict subject match, then rank by grade & board overlap."""
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
    trimmed = [t for sc, t in results if sc > 0] or [t for _, t in results]
    return trimmed[:limit]

def format_teacher_line(t: Dict[str, Any]) -> str:
    """HTML caption/body (لا يوجد رابط Photo هنا)."""
    quals = ", ".join(t.get("qualifications", []))
    boards = ", ".join(t.get("boards", []))
    grades = ""
    if t.get("grades"):
        gmin, gmax = min(t["grades"]), max(t["grades"])
        grades = f"Grades {gmin}-{gmax}"
    contact = t.get("contact", {})
    wa = contact.get("whatsapp") or ""
    whatsapp = f'<a href="{h(wa)}">WhatsApp</a>' if wa else ""
    lines = [
        f"<b>{h(t['name'])}</b> — {h(', '.join(t.get('subjects', [])))}",
        "  " + " | ".join([x for x in [h(grades), f"Boards {h(boards)}" if boards else ""] if x]),
    ]
    if t.get("bio"):  lines.append("  " + h(t["bio"]))
    if quals:         lines.append("  " + f"Qualifications: {h(quals)}")
    if whatsapp:      lines.append("  " + whatsapp)
    return "\n".join(lines)

# -------- subject preferences UI (per subject) --------
def pref_text(code: str, b: str, g: int, t: str, w: str) -> str:
    board = BOARD_CODES.get(b, b)
    subj  = CODE_TO_SUBJECT.get(code, code)
    mode  = "One-to-one" if t == "O" else ("Group" if t == "G" else "—")
    weeks = w if w in ("1","2") else "—"
    return (f"<b>Preferences for {h(subj)}</b>\n"
            f"Board: <b>{h(board)}</b> | Grade: <b>{g}</b>\n"
            f"Choose tuition mode and lessons/week:\n"
            f"Mode: <b>{h(mode)}</b> | Lessons/week: <b>{h(weeks)}</b>")

def kb_prefs(code: str, rest: str, b: str, g: int, t: str, w: str):
    def tick(cur, val): return "✅" if cur == val else "☐"
    return {
        "inline_keyboard": [
            [
                {"text": f"{tick(t,'O')} One-to-one", "callback_data": f"Q|{b}|{g}|{code}|{rest}|O|{w}"},
                {"text": f"{tick(t,'G')} Group",      "callback_data": f"Q|{b}|{g}|{code}|{rest}|G|{w}"},
            ],
            [
                {"text": f"{tick(w,'1')} 1 / week",   "callback_data": f"Q|{b}|{g}|{code}|{rest}|{t}|1"},
                {"text": f"{tick(w,'2')} 2 / week",   "callback_data": f"Q|{b}|{g}|{code}|{rest}|{t}|2"},
            ],
            [
                {"text": "Next ➡️",                  "callback_data": f"QN|{b}|{g}|{code}|{rest}|{t}|{w}"},
            ]
        ]
    }

def start_subject_pref(chat_id: int, b: str, g: int, codes: List[str]):
    """Send first subject preferences screen (new message)."""
    if not codes:
        return
    code = codes[0]
    rest = ".".join(codes[1:])
    tg("sendMessage", {
        "chat_id": chat_id,
        "text": pref_text(code, b, g, "-", "-"),
        "parse_mode": "HTML",
        "reply_markup": kb_prefs(code, rest, b, g, "-", "-")
    })

# ---------- Idempotency ----------
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

# ---------- routes ----------
@app.get("/api/webhook")
def ping():
    return jsonify(ok=True, msg="webhook alive", teachers=len(TEACHERS),
                   build=BUILD_TAG, bot=bool(BOT_API))

def _handle_webhook():
    try:
        if not BOT_API:
            print("[ERR] Missing TELEGRAM_BOT_TOKEN")
            return jsonify({"ok": True, "warn": "Missing TELEGRAM_BOT_TOKEN"}), 200

        update = request.get_json(force=True, silent=True) or {}
        try:
            print("[UPDATE]", json.dumps(update)[:2000])
        except Exception:
            print("[UPDATE] (non-serializable)")

        # 1) callback buttons
        if "callback_query" in update:
            cq = update["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            msg_id  = cq["message"]["message_id"]
            data = cq.get("data","")

            tg("answerCallbackQuery", {"callback_query_id": cq["id"]})

            def edit(text=None, reply_markup=None, parse_mode=None, disable_preview=None):
                if text is not None:
                    payload = {"chat_id": chat_id, "message_id": msg_id, "text": text}
                    if reply_markup is not None: payload["reply_markup"] = reply_markup
                    if parse_mode: payload["parse_mode"] = parse_mode
                    if disable_preview is not None: payload["disable_web_page_preview"] = disable_preview
                    tg("editMessageText", payload)
                else:
                    tg("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": msg_id, "reply_markup": reply_markup})

            if data == "noop":
                return jsonify({"ok": True})

            # --- Board chosen ---
            if data.startswith("B|"):
                b = data.split("|", 1)[1]
                edit(text="<b>Step 2/3 – Grade</b>\nSelect your child's current grade:",
                     reply_markup={
                         "inline_keyboard": (
                             [[{"text": f"{g}", "callback_data": f"G|{g}|{b}"} for g in range(7,11)],
                              [{"text": f"{g}", "callback_data": f"G|{g}|{b}"} for g in range(11,13)],
                              [{"text": "⬅️ Back", "callback_data": "B|"+b}]]
                         )
                     }, parse_mode="HTML")
                return jsonify({"ok": True})

            # --- Grade chosen ---
            if data.startswith("G|"):
                _, g, b = data.split("|", 2)
                g = int(g)
                sel: Set[str] = set()
                # subjects screen
                def tick(code, sel): return "✅" if code in sel else "☐"
                def kb_subjects(board_code: str, grade: int, sel: Set[str]):
                    rows = []
                    for group, items in SUBJECT_GROUPS.items():
                        rows.append([{"text": f"— {group} —", "callback_data": "noop"}])
                        for i in range(0, len(items), 2):
                            row = []
                            for code, label in items[i:i+2]:
                                row.append({
                                    "text": f"{tick(code, sel)} {label}",
                                    "callback_data": f"T|{code}|{board_code}|{grade}|{'.'.join(sorted(sel))}"
                                })
                            rows.append(row)
                    rows.append([
                        {"text": "Done ✅", "callback_data": f"D|{b}|{g}|{'.'.join(sorted(sel))}"},
                        {"text": "Reset ↩️", "callback_data": f"T|__RESET__|{b}|{g}|{'.'.join(sorted(sel))}"},
                    ])
                    rows.append([{"text": "⬅️ Back", "callback_data": f"B|{b}"}])
                    return {"inline_keyboard": rows}

                def summary_text(board_code: str, grade: int, sel: Set[str]) -> str:
                    board = BOARD_CODES.get(board_code, board_code)
                    chosen = ", ".join(h(CODE_TO_SUBJECT[c]) for c in sorted(sel)) if sel else "—"
                    return (f"<b>Step 3/3 – Subjects</b>\n"
                            f"Board: <b>{h(board)}</b>   |   Grade: <b>{grade}</b>\n"
                            f"Pick one or more subjects, then press <b>Done</b>.\n"
                            f"Selected: {chosen}")

                edit(text=summary_text(b, g, sel),
                     reply_markup=kb_subjects(b, g, sel),
                     parse_mode="HTML")
                return jsonify({"ok": True})

            # --- Toggle subject ---
            if data.startswith("T|"):
                _, code, b, g, enc = data.split("|", 4)
                g = int(g)
                sel = set([x for x in enc.split(".") if x])
                if code == "__RESET__":
                    sel = set()
                else:
                    if code in sel: sel.remove(code)
                    else: sel.add(code)

                # re-render subjects screen
                def tick(c): return "✅" if c in sel else "☐"
                def kb_subjects(board_code: str, grade: int):
                    rows = []
                    for group, items in SUBJECT_GROUPS.items():
                        rows.append([{"text": f"— {group} —", "callback_data": "noop"}])
                        for i in range(0, len(items), 2):
                            row = []
                            for c, label in items[i:i+2]:
                                row.append({
                                    "text": f"{tick(c)} {label}",
                                    "callback_data": f"T|{c}|{board_code}|{grade}|{'.'.join(sorted(sel))}"
                                })
                            rows.append(row)
                    rows.append([
                        {"text": "Done ✅", "callback_data": f"D|{b}|{g}|{'.'.join(sorted(sel))}"},
                        {"text": "Reset ↩️", "callback_data": f"T|__RESET__|{b}|{g}|{'.'.join(sorted(sel))}"},
                    ])
                    rows.append([{"text": "⬅️ Back", "callback_data": f"B|{b}"}])
                    return {"inline_keyboard": rows}

                def summary_text(board_code: str, grade: int) -> str:
                    board = BOARD_CODES.get(board_code, board_code)
                    chosen = ", ".join(h(CODE_TO_SUBJECT[c]) for c in sorted(sel)) if sel else "—"
                    return (f"<b>Step 3/3 – Subjects</b>\n"
                            f"Board: <b>{h(board)}</b>   |   Grade: <b>{grade}</b>\n"
                            f"Pick one or more subjects, then press <b>Done</b>.\n"
                            f"Selected: {chosen}")

                edit(text=summary_text(b, g),
                     reply_markup=kb_subjects(b, g),
                     parse_mode="HTML")
                return jsonify({"ok": True})

            # --- Done subjects -> start per-subject preferences flow ---
            if data.startswith("D|"):
                _, b, g, enc = data.split("|", 3)
                g = int(g)
                sel_codes = [x for x in enc.split(".") if x]
                subjects = sorted({CODE_TO_SUBJECT[c] for c in sel_codes})

                if not subjects:
                    tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Please select at least one subject."})
                    return jsonify({"ok": True})

                signature = f"{msg_id}|{b}|{g}|{'.'.join(sorted(sel_codes))}"
                if already_done(chat_id, signature):
                    print(f"[SKIP] duplicate done {signature}")
                    return jsonify({"ok": True})

                # Close keyboard & show brief header
                tg("editMessageReplyMarkup", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "reply_markup": {"inline_keyboard": []}
                })
                tg("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": (f"Great! We’ll tailor recommendations per subject.\n"
                             f"Board: <b>{h(BOARD_CODES.get(b,b))}</b> | Grade: <b>{g}</b>\n"
                             f"Subjects: <b>{h(', '.join(subjects))}</b>\n\n"
                             f"You’ll be asked two quick preferences for each subject."),
                    "parse_mode": "HTML"
                })

                # Start: first subject pref screen as a new message
                start_subject_pref(chat_id, b, g, sel_codes)
                return jsonify({"ok": True})

            # --- Preferences toggles (Q|...) ---
            if data.startswith("Q|"):
                _, b, g, code, rest, t, w = data.split("|", 6)
                g = int(g)
                # just update the same message with toggled value
                tg("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": pref_text(code, b, g, t, w),
                    "parse_mode": "HTML",
                    "reply_markup": kb_prefs(code, rest, b, g, t, w)
                })
                return jsonify({"ok": True})

            # --- Preferences Next (QN|...) -> send matches for this subject, then move to next ---
            if data.startswith("QN|"):
                _, b, g, code, rest, t, w = data.split("|", 6)
                g = int(g)
                if t not in ("O","G") or w not in ("1","2"):
                    tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Please choose both options first."})
                    return jsonify({"ok": True})

                # Lock this message (remove keyboard)
                tg("editMessageReplyMarkup", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "reply_markup": {"inline_keyboard": []}
                })
                subject = CODE_TO_SUBJECT.get(code, code)
                mode_txt = "One-to-one" if t == "O" else "Group"

                # Send heading for this subject with preferences
                tg("sendMessage", {
                    "chat_id": chat_id,
                    "text": (f"<b>{h(subject)}</b>\n"
                             f"Mode: <b>{h(mode_txt)}</b> | Lessons/week: <b>{h(w)}</b>"),
                    "parse_mode": "HTML"
                })

                # Send tutors
                board = BOARD_CODES.get(b, b)
                matches = match_teachers(subject, g, board, limit=4)
                if not matches:
                    tg("sendMessage", {
                        "chat_id": chat_id,
                        "text": "No exact matches right now. We'll expand the search and get back to you.",
                        "parse_mode": "HTML"
                    })
                else:
                    for ttr in matches:
                        caption = format_teacher_line(ttr)
                        photo = ttr.get("photo_url")
                        if photo:
                            tg("sendPhoto", {
                                "chat_id": chat_id,
                                "photo": photo,
                                "caption": caption,
                                "parse_mode": "HTML"
                            })
                        else:
                            tg("sendMessage", {
                                "chat_id": chat_id,
                                "text": caption,
                                "parse_mode": "HTML",
                                "disable_web_page_preview": True
                            })

                # Move to next subject (if any)
                rest_codes = [x for x in rest.split(".") if x]
                if rest_codes:
                    start_subject_pref(chat_id, b, g, rest_codes)
                else:
                    tg("sendMessage", {"chat_id": chat_id,
                                       "text": "All set! You can contact any tutor via the WhatsApp link on the cards. 🌟"})
                return jsonify({"ok": True})

            return jsonify({"ok": True})

        # 2) normal messages
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return jsonify({"ok": True})

        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()

        if text.lower() in ("/start", "start"):
            # Step 1: Board
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": "<b>Step 1/3 – Board</b>\nWhich board or curriculum does your child follow?",
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [[
                        {"text": "Cambridge", "callback_data": "B|C"},
                        {"text": "Edexcel",   "callback_data": "B|E"},
                        {"text": "Oxford",    "callback_data": "B|O"},
                    ]]
                }
            })
            return jsonify({"ok": True})

        # fallback: point to guided flow
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": "Please use the guided flow 👇",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "Cambridge", "callback_data": "B|C"},
                    {"text": "Edexcel",   "callback_data": "B|E"},
                    {"text": "Oxford",    "callback_data": "B|O"},
                ]]
            }
        })
        return jsonify({"ok": True})

    except Exception as e:
        print("[ERR]", repr(e))
        print(traceback.format_exc())
        return jsonify({"ok": True}), 200

# Explicit route (Vercel webhook)
@app.post("/api/webhook")
def webhook_api():
    return _handle_webhook()

# Catch-all (safety)
@app.route("/", defaults={"subpath": ""}, methods=["POST"])
@app.route("/<path:subpath>", methods=["POST"])
def webhook_catchall(subpath=None):
    return _handle_webhook()
