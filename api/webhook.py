# api/webhook.py
import os, json, re, time, traceback, html
from typing import Dict, Any, List, Tuple, Set, Optional
from flask import Flask, request, jsonify
import requests
from urllib.parse import quote_plus

app = Flask(__name__)

BUILD_TAG = "portal-whatsapp-prefill-v1"

# ---------- Telegram ----------
TELEGRAM_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
if not TELEGRAM_TOKEN:
    print("WARNING: Missing TELEGRAM_BOT_TOKEN")
BOT_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}" if TELEGRAM_TOKEN else None

def tg(method: str, payload: Dict[str, Any]):
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

# ---------- subjects / boards ----------
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

# ---------- helpers ----------
def h(x: str) -> str:
    return html.escape(x or "")

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def canonical_subject(label: str) -> Optional[str]:
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

# ----- preference helpers (optional fields in teachers.json) -----
def _teacher_modes_for_subject(t: Dict[str, Any], subj: str) -> List[str]:
    subj_cfg = (t.get("subject_prefs") or {}).get(subj, {})
    modes = subj_cfg.get("modes") or t.get("modes") or []
    return [m.strip().lower() for m in modes]

def _teacher_supports_lessons(t: Dict[str, Any], subj: str, lessons: int) -> bool:
    subj_cfg = (t.get("subject_prefs") or {}).get(subj, {})
    arr = subj_cfg.get("weekly_lessons_supported") or t.get("weekly_lessons_supported") or []
    try:
        return int(lessons) in [int(x) for x in arr]
    except Exception:
        return False

def _pref_score(t: Dict[str, Any], subj: Optional[str], mode: Optional[str], lessons: Optional[int]) -> int:
    s = 0
    if subj:
        if mode and mode.strip().lower() in _teacher_modes_for_subject(t, subj):
            s += 40
        if lessons and _teacher_supports_lessons(t, subj, lessons):
            s += 25
    return s

# ----- matching -----
def match_teachers(
    subject: Optional[str] = None,
    grade: Optional[int] = None,
    board: Optional[str] = None,
    prefer_mode: Optional[str] = None,      # "1to1" / "group" / None
    prefer_lessons: Optional[int] = None,   # 1 / 2 / None
    strict: bool = False,
    limit: int = 4
) -> List[Dict[str, Any]]:
    results: List[Tuple[int, Dict[str, Any]]] = []
    wanted = canonical_subject(subject) if subject else None

    for t in TEACHERS:
        if wanted and wanted not in t.get("_subjects_canon", set()):
            continue

        if strict:
            if prefer_mode and prefer_mode.strip().lower() not in _teacher_modes_for_subject(t, wanted or ""):
                continue
            if (prefer_lessons is not None) and not _teacher_supports_lessons(t, wanted or "", prefer_lessons):
                continue

        score = 0
        if grade and t.get("grades") and grade in t["grades"]:
            score += 50
        if board and t.get("boards") and any(_norm(board) == _norm(b) for b in t["boards"]):
            score += 50
        score += _pref_score(t, wanted, prefer_mode, prefer_lessons)

        results.append((score, t))

    results.sort(key=lambda x: x[0], reverse=True)
    trimmed = [t for sc, t in results if sc > 0] or [t for _, t in results]
    return trimmed[:limit]

# ---------- WhatsApp prefill helpers ----------
def compose_wa_text(parent: str, board: str, grade: int,
                    subject: str, mode: Optional[str], lessons: Optional[int],
                    tutor_name: str) -> str:
    mode_label = "One-to-one" if (mode or "") == "1to1" else ("Group" if (mode or "") == "group" else "-")
    parts = [
        "Hello Kuwait IGCSE Portal 👋",
        f"Parent: {parent or '-'}",
        f"Board: {board or '-'}",
        f"Grade: {grade if grade else '-'}",
        f"Subject: {subject or '-'}",
        f"Preference: {mode_label} | {lessons or '-'} per week",
        f"Tutor selected: {tutor_name or '-'}",
        "Please connect us. Thanks!"
    ]
    return "\n".join(parts)

def build_wa_link(base_url: str, msg: str) -> str:
    if not base_url:
        return ""
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}text={quote_plus(msg)}"

# ---------- formatting ----------
def format_teacher_line(t: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """HTML caption/body with dynamic WhatsApp link containing the parent's name + choices."""
    quals = ", ".join(t.get("qualifications", []))
    boards = ", ".join(t.get("boards", []))
    grades = ""
    if t.get("grades"):
        gmin, gmax = min(t["grades"]), max(t["grades"])
        grades = f"Grades {gmin}-{gmax}"

    # Build WA prefilled link
    wa_base = ((t.get("contact") or {}).get("whatsapp") or "").strip()
    prefill_text = compose_wa_text(
        parent=str(ctx.get("parent_name") or "").strip(),
        board=str(ctx.get("board") or ""),
        grade=int(ctx.get("grade") or 0) if ctx.get("grade") else 0,
        subject=str(ctx.get("subject") or ""),
        mode=ctx.get("mode"),
        lessons=ctx.get("lessons"),
        tutor_name=t.get("name", "")
    )
    wa_link = build_wa_link(wa_base, prefill_text)
    whatsapp = f'<a href="{h(wa_link)}">WhatsApp</a>' if wa_link else ""

    lines = [
        f"<b>{h(t['name'])}</b> — {h(', '.join(t.get('subjects', [])))}",
        "  " + " | ".join([x for x in [h(grades), f"Boards {h(boards)}" if boards else ""] if x]),
    ]
    if t.get("bio"):  lines.append("  " + h(t["bio"]))
    if quals:         lines.append("  " + f"Qualifications: {h(quals)}")
    if whatsapp:      lines.append("  " + whatsapp)
    return "\n".join(lines)

# ----- selection encoding in callback_data -----
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

# ---------- Idempotency ----------
RECENT_DONE: Dict[int, List[Tuple[str, float]]] = {}
def already_done(chat_id: int, signature: str, ttl: int = 300) -> bool:
    now = time.time()
    lst = RECENT_DONE.get(chat_id, [])
    lst = [(k, t) for (k, t) in lst if now - t < ttl]
    RECENT_DONE[chat_id] = lst
    for k, _ in lst:
        if k == signature: return True
    lst.append((signature, now))
    RECENT_DONE[chat_id] = lst
    return False

# ---------- Minimal in-memory session (name) ----------
SESSION: Dict[int, Dict[str, Any]] = {}

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

        # ----- callback buttons -----
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

            # Board
            if data.startswith("B|"):
                b = data.split("|", 1)[1]
                edit(text="<b>Step 2/3 – Grade</b>\nSelect your child's current grade:",
                     reply_markup=kb_grade(b), parse_mode="HTML")
                return jsonify({"ok": True})

            # Grade
            if data.startswith("G|"):
                _, g, b = data.split("|", 2)
                g = int(g)
                sel: Set[str] = set()
                edit(text=summary_text(b, g, sel),
                     reply_markup=kb_subjects(b, g, sel),
                     parse_mode="HTML")
                return jsonify({"ok": True})

            # Toggle Subject
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
                     parse_mode="HTML")
                return jsonify({"ok": True})

            # Done (subjects) -> move to per-subject preferences (new message)
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

                tg("editMessageReplyMarkup", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "reply_markup": {"inline_keyboard": []}
                })
                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": (f"Great! We’ll tailor recommendations per subject.\n"
                             f"Board: <b>{h(BOARD_CODES.get(b,b))}</b> | Grade: <b>{g}</b>\n"
                             f"Subjects: <b>{h(', '.join(subjects))}</b>\n\n"
                             f"You’ll be asked two quick preferences for each subject."),
                    "parse_mode": "HTML"
                })

                start_subject_pref(chat_id, b, g, sel_codes)
                return jsonify({"ok": True})

            # Preferences toggle (Q|...)
            if data.startswith("Q|"):
                _, b, g, code, rest, t_opt, w_opt = data.split("|", 6)
                g = int(g)
                tg("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": pref_text(code, b, g, t_opt, w_opt),
                    "parse_mode": "HTML",
                    "reply_markup": kb_prefs(code, rest, b, g, t_opt, w_opt)
                })
                return jsonify({"ok": True})

            # Preferences Next (QN|...)
            if data.startswith("QN|"):
                _, b, g, code, rest, t_opt, w_opt = data.split("|", 6)
                g = int(g)
                if t_opt not in ("O","G") or w_opt not in ("1","2"):
                    tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Please choose both options first."})
                    return jsonify({"ok": True})

                tg("editMessageReplyMarkup", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "reply_markup": {"inline_keyboard": []}
                })

                subject = CODE_TO_SUBJECT.get(code, code)
                mode_txt = "One-to-one" if t_opt == "O" else "Group"
                prefer_mode = "1to1" if t_opt == "O" else "group"
                prefer_lessons = int(w_opt)

                tg("sendMessage", {
                    "chat_id": chat_id,
                    "text": (f"<b>{h(subject)}</b>\n"
                             f"Mode: <b>{h(mode_txt)}</b> | Lessons/week: <b>{prefer_lessons}</b>"),
                    "parse_mode": "HTML"
                })

                board = BOARD_CODES.get(b, b)
                parent_name = (SESSION.get(chat_id) or {}).get("name", "")

                matches = match_teachers(
                    subject, g, board,
                    prefer_mode=prefer_mode,
                    prefer_lessons=prefer_lessons,
                    strict=False,
                    limit=4
                )

                if not matches:
                    tg("sendMessage", {
                        "chat_id": chat_id,
                        "text": "No exact matches right now. We'll expand the search and get back to you.",
                        "parse_mode": "HTML"
                    })
                else:
                    for ttr in matches:
                        caption = format_teacher_line(ttr, {
                            "parent_name": parent_name,
                            "board": board,
                            "grade": g,
                            "subject": subject,
                            "mode": prefer_mode,
                            "lessons": prefer_lessons
                        })
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

                rest_codes = [x for x in rest.split(".") if x]
                if rest_codes:
                    start_subject_pref(chat_id, b, g, rest_codes)
                else:
                    tg("sendMessage", {
                        "chat_id": chat_id,
                        "text": "All set! You can contact any tutor via the WhatsApp link on the cards. 🌟"
                    })
                return jsonify({"ok": True})

            return jsonify({"ok": True})

        # ----- normal messages -----
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return jsonify({"ok": True})

        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()

        # step 0: ask for parent name first
        st = SESSION.get(chat_id) or {}
        if text.lower() in ("/start", "start"):
            SESSION[chat_id] = {"stage": "ask_name"}
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": "Welcome to Kuwait IGCSE Portal 👋\nPlease type your full name (parent):"
            })
            return jsonify({"ok": True})

        if st.get("stage") == "ask_name":
            # save name and go to board step
            name = text[:80].strip()
            SESSION[chat_id] = {"stage": "flow", "name": name}
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": f"Thanks, {h(name)}!\n\n<b>Step 1/3 – Board</b>\nWhich board or curriculum does your child follow?",
                "parse_mode": "HTML",
                "reply_markup": kb_board()
            })
            return jsonify({"ok": True})

        # otherwise point to guided flow
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

# Explicit route (Vercel webhook)
@app.post("/api/webhook")
def webhook_api():
    return _handle_webhook()

# Catch-all
@app.route("/", defaults={"subpath": ""}, methods=["POST"])
@app.route("/<path:subpath>", methods=["POST"])
def webhook_catchall(subpath=None):
    return _handle_webhook()
