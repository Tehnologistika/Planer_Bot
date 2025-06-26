"""
bot.py  •  Telegram‑бот «Личный планировщик»

Мини‑скелет на python‑telegram‑bot v20:
- Главное меню из 6 кнопок
- Заглушки‑хендлеры для разделов: Сегодня / Неделя / Цели‑OKR / Инбокс / Статистика / Настройки
- Старт точки: /start и ReplyKeyboard

Дальше можно постепенно наполнять каждую секцию логикой и inline‑кнопками.
"""

import logging
import stt_vosk
from datetime import date, datetime
from datetime import timedelta
from datetime import time
import re
from typing import List
from tinydb import where
import subprocess
from pathlib import Path
from calendar import month_name
 # Small DB helper
def get_objective(obj_id: int):
    return database._table("okr").get(doc_id=obj_id)

import database  # TinyDB helper functions
import ai_service  # DeepSeek wrapper module
from database import close_db
from database import get_task
from config import load
from planner.abacus_client import ask_rocky
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram import F

cfg = load()
bot = Bot(token=cfg.tg_token)
dp = Dispatcher()

# --- Simple Rocky handlers ---
# @dp.message(Command("add"))
# async def handle_add(message: types.Message) -> None:
#     query = message.text.split(maxsplit=1)
#     text = query[1] if len(query) > 1 else ""
#     resp = await ask_rocky(text)
#     await message.reply(resp)


# @dp.message(Command("free"))
# async def handle_free(message: types.Message) -> None:
#     query = message.text.split(maxsplit=1)
#     text = query[1] if len(query) > 1 else ""
#     resp = await ask_rocky(text)
#     await message.reply(resp)


# @dp.message()
# async def fallback(message: types.Message) -> None:
#     if message.text:
#         await message.answer(await ask_rocky(message.text))

from zoneinfo import ZoneInfo  # Python 3.9+

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# ---------- Reset user data handler ---------- #
async def cmd_reset_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Полное удаление всех данных пользователя (цели, задачи, категории, inbox)."""
    uid = update.effective_user.id
    for tab in ("okr", "tasks", "categories", "inbox"):
        database._table(tab).remove(where("uid") == uid)
    await update.message.reply_text(
        "Все твои данные полностью удалены!\n"
        "Бот сброшен. Введите /start для чистого теста."
    )
LIFEPLAN_QS = [
    "Опиши, какой жизни ты хочешь достичь. Как выглядит твоя идеальная картина жизни?",
    "Что для тебя по-настоящему важно? (семья, свобода, здоровье, признание и т.д.)",
    "Где ты хотел бы жить, работать, что делать каждый день?",
    "Какие главные достижения ты бы хотел оставить после себя?",
    "Кто и что тебя окружает в идеальной жизни?",
]
LIFEPLAN_STATE = "lifeplan_state"
LIFEPLAN_IDX = "lifeplan_idx"
LIFEPLAN_ANSWERS = "lifeplan_answers"

# --- FSM constants for Stage dialog ---
AWAIT_STAGE_TITLE = "await_stage_title"
AWAIT_STAGE_MONTH = "await_stage_month"
CURRENT_GOAL_ID = "current_goal_id"
CURRENT_STAGE_TITLE = "current_stage_title"
# --- Helper: month selection keyboard ---
def month_keyboard(base_cb: str) -> InlineKeyboardMarkup:
    """Return 12‑month keyboard; callback data = f'{base_cb}_<month>'."""
    rows, row = [], []
    for m in range(1, 13):
        row.append(InlineKeyboardButton(month_name[m][:3], callback_data=f"{base_cb}_{m}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✅ Готово", callback_data=f"{base_cb}_done")])
    return InlineKeyboardMarkup(rows)

# --- Новый хендлер для запуска диалога ---
async def cmd_lifeplan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[LIFEPLAN_IDX] = 0
    context.user_data[LIFEPLAN_ANSWERS] = []
    await update.message.reply_text(
        "Начнём стратегический диалог!\n" + LIFEPLAN_QS[0],
        reply_markup=ReplyKeyboardRemove(),
    )
    return LIFEPLAN_STATE

async def lifeplan_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import traceback
    try:
        idx = context.user_data.get(LIFEPLAN_IDX, 0)
        answers = context.user_data.get(LIFEPLAN_ANSWERS, [])
        txt = update.message.text.strip()
        logging.info(
            f"[lifeplan_router] idx={idx}, txt={txt!r}, n_qs={len(LIFEPLAN_QS)}, answers={answers!r}"
        )
        answers.append(txt)
        context.user_data[LIFEPLAN_ANSWERS] = answers
        idx += 1
        context.user_data[LIFEPLAN_IDX] = idx

        if idx < len(LIFEPLAN_QS):
            logging.info(f"[lifeplan_router] Next question idx={idx}: {LIFEPLAN_QS[idx]!r}")
            await update.message.reply_text(LIFEPLAN_QS[idx])
            return LIFEPLAN_STATE

        # Финал: генерируем draft целей с помощью DeepSeek, предлагаем сохранить
        logging.info(
            f"[lifeplan_router] All answers collected. Calling ai_service.ask_ai. answers={answers!r}"
        )
        await update.message.reply_text("Формулирую твои цели на основе ответов...")
        summary = await ai_service.ask_ai(
            "Сформулируй 3-5 конкретных жизненных цели и смысловых ориентира на основании:\n" + "\n".join(answers)
        )
        logging.info(f"[lifeplan_router] ai_service.ask_ai returned: {summary!r}")
        if isinstance(summary, str):
            await update.message.reply_text("Вариант целей:\n" + summary)
        else:
            await update.message.reply_text("Вариант целей:\n" + str(summary))
        await update.message.reply_text("Сохранить эти цели? (да/нет, либо пришли свой вариант формулировки)")
        return "lifeplan_confirm"
    except Exception as e:
        tb = traceback.format_exc()
        msg = (
            f"[lifeplan_router ERROR]: {e}\n"
            f"Traceback:\n{tb}\n"
            f"idx={context.user_data.get(LIFEPLAN_IDX, None)}, "
            f"answers={context.user_data.get(LIFEPLAN_ANSWERS, None)}, "
            f"text={(update.message.text.strip() if update and update.message and update.message.text else None)!r}, "
            f"n_qs={len(LIFEPLAN_QS)}"
        )
        logging.error(msg)
        await update.message.reply_text(
            "⚠️ Возникла техническая ошибка при обработке ответа. "
            "Пожалуйста, попробуй ещё раз или напиши /lifeplan.\n"
            "Тех. детали: " + str(e)
        )
        return ConversationHandler.END

async def lifeplan_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip().lower()
    uid = update.effective_user.id
    answers = context.user_data.get(LIFEPLAN_ANSWERS, [])
    if txt in ("да", "ok", "давай", "сохранить"):
        # Сохраняем цели как OKR (один obj на каждый абзац/пункт)
        goals = await ai_service.ask_ai("Выдели списком 3-5 ключевых жизненных целей на основании:\n" + "\n".join(answers))
        # допустим, goals — это список строк или 1 строка с \n
        if isinstance(goals, str):
            goals = [g.strip("–• \n") for g in goals.split("\n") if g.strip()]
        for g in goals:
            database.add_objective(uid, g)
        await update.message.reply_text("Цели сохранены! Теперь они всегда доступны в разделе 'Цели'.")
        # --- Запуск сбора категорий ---
        context.user_data["awaiting_categories"] = True
        context.user_data["categories"] = []
        await update.message.reply_text(
            "Теперь назови 3–5 ключевых факторов (категорий), наличие которых обеспечит воплощение твоей мечты.\n"
            "Например: «Здоровье», «Свобода», «Проекты».\n"
            "Вводи по одной категории за сообщение, когда всё — напиши «Готово»."
        )
        return "categories_state"
    else:
        # Если нет — пользователь может прислать свой текст, либо откорректировать
        if txt in ("нет", "no", "отмена", "cancel"):
            await update.message.reply_text("Диалог отменён. Можно начать заново с /lifeplan.")
            for k in [LIFEPLAN_IDX, LIFEPLAN_ANSWERS]:
                context.user_data.pop(k, None)
            return ConversationHandler.END
        # Принять пользовательский вариант целей (разделить по строкам)
        user_goals = [g.strip("–• \n") for g in txt.split("\n") if g.strip()]
        for g in user_goals:
            database.add_objective(uid, g)
        await update.message.reply_text("Твои формулировки целей сохранены! Теперь они всегда доступны в разделе 'Цели'.")
        # --- Запуск сбора категорий ---
        context.user_data["awaiting_categories"] = True
        context.user_data["categories"] = []
        await update.message.reply_text(
            "Теперь назови 3–5 ключевых факторов (категорий), наличие которых обеспечит воплощение твоей мечты.\n"
            "Например: «Здоровье», «Свобода», «Проекты».\n"
            "Вводи по одной категории за сообщение, когда всё — напиши «Готово»."
        )
        return "categories_state"
### --- Категории: FSM router для сбора категорий ---
async def categories_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt.lower() in ("готово", "всё", "done", "finish"):
        cats = context.user_data.get("categories", [])
        if len(cats) < 3:
            await update.message.reply_text("Лучше указать хотя бы 3 категории!")
            return "categories_state"
        uid = update.effective_user.id
        for c in cats:
            database.add_category(uid, c)
        await update.message.reply_text("Категории сохранены!\nТеперь все твои задачи будут планироваться по этим приоритетам.")
        context.user_data.pop("categories")
        context.user_data.pop("awaiting_categories")
        return ConversationHandler.END
    cats = context.user_data.setdefault("categories", [])
    if txt in cats:
        await update.message.reply_text("Такая категория уже есть. Введи другую.")
        return "categories_state"
    cats.append(txt)
    await update.message.reply_text(f"Добавлено: {txt}\nВведи ещё категорию или напиши «Готово».")
    return "categories_state"


import config  # файл config.py должен содержать BOT_TOKEN

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")  # HH:MM 24h

Q_RE = re.compile(r"Q([1-4])-(20\d{2})")
DATE_RE = re.compile(r"(\d{1,2})[.\-\/](\d{1,2})[.\-\/](20\d{2})")

USER_TZ = ZoneInfo("Europe/Moscow")  # adjust if user changes city

def parse_time(s: str) -> 'Optional[time]':
    m = TIME_RE.match(s.strip())
    if not m:
        return None
    h, mnt = map(int, m.groups())
    return time(hour=h, minute=mnt)

# --- Helper: parse AI plain text slot ---
AI_SLOT_RE = re.compile(
    r"(?:с|c)\s*(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})", re.I
)
AI_DATE_WORDS = {
    "сегодня": 0,
    "завтра": 1,
}

# pattern: 07.06 22:20–22:30 ...
AI_SLOT_DATE_FIRST_RE = re.compile(
    r"(\d{1,2})[.\-/](\d{1,2})\s+(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})",
    re.I,
)

def parse_ai_slot(text: str) -> "Optional[tuple[date, time, time, str]]":
    """
    Try to extract (date, start_time, end_time, description) from AI plain text.

    Expected fragment like:
    'сегодня 14:00–15:30 — Разобрать почту'
    or 'Завтра с 09:00‑10:00 созвон'
    Returns None if not found.
    """
    text_lc = text.lower()
    # date
    due = date.today()
    for k, delta in AI_DATE_WORDS.items():
        if k in text_lc:
            due = date.today() + timedelta(days=delta)
            break
    # times
    m = AI_SLOT_RE.search(text.replace("—", "-"))
    if m:
        t1 = parse_time(m.group(1).replace(".", ":"))
        t2 = parse_time(m.group(2).replace(".", ":"))
        if not (t1 and t2):
            return None
        # description = rest of line after times
        desc_part = text.split(m.group(0))[-1].strip(" —-")
        return due, t1, t2, desc_part or "Без названия"
    # --- Try pattern with date first: '07.06 22:20–22:30 ...'
    m2 = AI_SLOT_DATE_FIRST_RE.search(text.replace("—", "-"))
    if m2:
        d, mth, t1_raw, t2_raw = m2.groups()
        try:
            yr = date.today().year
            due = date(year=yr, month=int(mth), day=int(d))
            # if already passed this year, assume next year
            if due < date.today():
                due = due.replace(year=yr + 1)
        except ValueError:
            return None
        t1 = parse_time(t1_raw.replace(".", ":"))
        t2 = parse_time(t2_raw.replace(".", ":"))
        if not (t1 and t2):
            return None
        desc_part = text.split(m2.group(0))[-1].strip(" —-")
        return due, t1, t2, desc_part or "Без названия"
    return None

def parse_due(s: str) -> 'Optional[str]':
    """
    Accept 'Q1-2026' or '31.12.2025' or '31/12/2025'.
    Return normalized ISO date string or 'Qx-YYYY'
    """
    s = s.strip()
    m = Q_RE.match(s.upper())
    if m:
        q, yr = m.groups()
        return f"Q{q}-{yr}"
    m = DATE_RE.match(s)
    if m:
        d, mth, yr = map(int, m.groups())
        try:
            dt = datetime(year=yr, month=mth, day=d, tzinfo=USER_TZ)
            return dt.date().isoformat()
        except ValueError:
            return None
    return None


def due_within_year(due_str: str) -> bool:
    today = date.today()
    if due_str.startswith("Q"):
        q = int(due_str[1])
        yr = int(due_str.split("-")[1])
        # represent quarter by its first day
        month = (q - 1) * 3 + 1
        target = date(yr, month, 1)
    else:
        target = datetime.fromisoformat(due_str).date()
    return (target - today).days <= 365

# --- Progress dot helper ---
def progress_dot(p: int) -> str:
    """Return a colored square by progress (🟥 0‑29, 🟨 30‑69, 🟩 70‑100)."""
    if p < 30:
        return "🟥"
    if p < 70:
        return "🟨"
    return "🟩"

# --- Helper: find matching tasks for AI ---
def find_matching_tasks(uid: int, query: str, days_ahead: int = 30):
    """
    Return LIST of upcoming tasks whose text contains any keyword from query
    (case‑insensitive, ignores words ≤ 2 chars and common stopwords), sorted by due date/time.
    """
    words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 2]
    if not words:
        return []
    tasks = database.list_future_tasks(uid, days_ahead)
    # sort by due then start_ts (if any)
    tasks.sort(key=lambda t: (t["due"], t.get("start_ts") or ""))
    # Ignore common question words to avoid false negatives
    stop = {"когда", "что", "где", "сколько", "запланировано", "подскажи", "у", "меня"}
    key_words = [w for w in words if w not in stop]
    if not key_words:
        key_words = words  # fallback to original list
    return [
        t for t in tasks
        if any(w in t["text"].lower() for w in key_words)
    ]

# --- Secretary question auto-detect helper ---
QUESTION_WORDS = ("когда", "подскажи", "что", "где", "сколько", "запланировано")

def is_secretary_query(text: str) -> bool:
    """Heuristic: treat message as question for secretary."""
    low = text.lower().strip()
    return low.endswith("?") and any(w in low for w in QUESTION_WORDS)

def schedule_task_jobs(job_queue, chat_id: int, task_id: int, start_dt: datetime, end_dt: datetime):
    """Plan callback jobs for start and end reminders using delay seconds."""
    now = datetime.now(tz=USER_TZ)
    start_delay = max(0, (start_dt - now).total_seconds())
    end_delay = max(0, (end_dt - now).total_seconds())

    job_queue.run_once(start_notify, when=start_delay, data={"cid": chat_id, "tid": task_id})
    job_queue.run_once(end_notify, when=end_delay, data={"cid": chat_id, "tid": task_id})

# ---------- Voice handler ---------- #
async def voice_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle Telegram voice messages:
    1. Download voice.ogg
    2. Convert to 16 kHz mono WAV via ffmpeg
    3. Transcribe with Vosk
    4. Route resulting text through text_input_router
    """
    voice_file = await update.message.voice.get_file()
    ogg_path = Path("tmp_voice.ogg")
    wav_path = Path("tmp_voice.wav")
    await voice_file.download_to_drive(custom_path=ogg_path)

    # Convert with ffmpeg (suppress stdout/stderr)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(ogg_path), "-ar", "16000", "-ac", "1", str(wav_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    text = stt_vosk.transcribe_wav(str(wav_path))

    # Clean temp files
    ogg_path.unlink(missing_ok=True)
    wav_path.unlink(missing_ok=True)

    if not text:
        await update.message.reply_text("Не удалось распознать речь 🤷")
        return

    # Route recognized text as if it was a normal message
    from types import SimpleNamespace

    fake_message = SimpleNamespace(
        text=text,
        reply_text=update.message.reply_text,
    )
    fake_update = SimpleNamespace(
        effective_user=update.effective_user,
        effective_chat=update.effective_chat,
        message=fake_message,
    )
    await text_input_router(fake_update, context)

async def start_notify(context: ContextTypes.DEFAULT_TYPE):
    cid = context.job.data["cid"]
    tid = context.job.data["tid"]
    task = database._table("tasks").get(doc_id=tid)
    title = task["text"] if task else ""
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Начал", callback_data=f"task_start_ok_{tid}")],
            [InlineKeyboardButton("⏰ Отложить", callback_data=f"task_start_snooze_{tid}")],
        ]
    )
    await context.bot.send_message(
        cid,
        f"⏰ Время начать задачу «{title}»",
        reply_markup=keyboard,
    )

async def end_notify(context: ContextTypes.DEFAULT_TYPE):
    cid = context.job.data["cid"]
    tid = context.job.data["tid"]
    task = database._table("tasks").get(doc_id=tid)
    title = task["text"] if task else ""
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Завершил", callback_data=f"task_end_ok_{tid}")],
            [InlineKeyboardButton("⏰ Отложить", callback_data=f"task_end_snooze_{tid}")],
        ]
    )
    await context.bot.send_message(
        cid,
        f"🕑 Подходит время завершить задачу «{title}»",
        reply_markup=keyboard,
    )

# ---------- Клавиатуры ---------- #
# Compact main keyboard (2 columns, symmetric)
QUICK_MENU = ReplyKeyboardMarkup(
    [
        ["📋 Сегодня", "🔔 Инбокс"],
        ["🗓 Неделя", "📆 Месяц"],
        ["💼 Меню", "🤖 Секретарь"],
    ],
    resize_keyboard=True,
)
MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["🎯 Цели", "📊 Статистика"],
        ["⚙️ Настройки", "⬅️ Свернуть"],
    ],
    resize_keyboard=True,
)

# ---------- AI assistant ----------
async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask LLM assistant; after /ai bot waits for the question."""
    context.user_data["awaiting_ai_question"] = True
    await update.message.reply_text(
        "🤖 Что спросить ассистента? Отправь текст одним сообщением."
    )


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Relay /add text to Rocky."""
    query = update.message.text.split(maxsplit=1)
    text = query[1] if len(query) > 1 else ""
    resp = await ask_rocky(text)
    await update.message.reply_text(resp)


async def free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Relay /free text to Rocky."""
    query = update.message.text.split(maxsplit=1)
    text = query[1] if len(query) > 1 else ""
    resp = await ask_rocky(text)
    await update.message.reply_text(resp)


async def echo_to_rocky(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send any text message to Rocky and echo the reply."""
    if update.message and update.message.text:
        resp = await ask_rocky(update.message.text)
        await update.message.reply_text(resp)

# ---------- Базовые хендлеры ---------- #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Стартовое сообщение и главное меню."""
    user = update.effective_user
    # сохраняем chat_id для восстановления job’ов
    database.remember_chat(user.id, update.effective_chat.id)
    # Если целей нет — сразу lifeplan
    objs = database._table("okr").search(
        (database.where("uid") == user.id) & (database.where("type") == "objective")
    )
    if not objs:
        await update.message.reply_text(
            "Давай определим твои жизненные цели — это основа всей системы! Ответь на несколько вопросов."
        )
        await cmd_lifeplan(update, context)
        return
    text = (
        f"Привет, {user.first_name or 'друг'}! 👋\n"
        "Это твой личный планировщик. Выбирай раздел:"
    )
    await update.message.reply_text(text, reply_markup=QUICK_MENU)
    # Персональное напоминание Инбокса (не менять)
    jobs = [j for j in context.job_queue.get_jobs_by_name(f"inbox_reminder_{user.id}")]
    if not jobs:
        context.job_queue.run_daily(
            inbox_daily_reminder,
            time(hour=20, minute=0, tzinfo=USER_TZ),
            data={"uid": user.id, "chat_id": update.effective_chat.id},
            name=f"inbox_reminder_{user.id}",
        )


def render_today(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    """Return text and inline‑keyboard for today's tasks."""
    tasks = database.list_tasks(uid, date.today(), lvl="day", include_done=True)
    lines = []
    buttons = []
    if not tasks:
        text = "Сегодня пока нет задач. Добавь первую!"
    else:
        for idx, t in enumerate(tasks, 1):
            status = "✅" if t["done"] else "🔸"
            lines.append(f"{status} {idx}. {t['text']}")
            btn_row = [
                InlineKeyboardButton("✏️", callback_data=f"today_edit_{t.doc_id}"),
                InlineKeyboardButton("☑️" if not t["done"] else "↩️",
                                     callback_data=f"today_toggle_{t.doc_id}"),
            ]
            buttons.append(btn_row)
        text = "📅 Задачи на сегодня:\n" + "\n".join(lines)
    # add control row at bottom
    buttons.append(
        [
            InlineKeyboardButton("➕ Добавить", callback_data="today_add"),
            InlineKeyboardButton("🔄 Обновить", callback_data="today_refresh"),
        ]
    )
    return text, InlineKeyboardMarkup(buttons)


# --- Week helper functions ---
def monday_of_week(d: date) -> date:
    """Return Monday of week containing d (ISO weekday 1)."""
    return d - timedelta(days=d.weekday())

def next_monday(d: date) -> date:
    return monday_of_week(d) + timedelta(days=7)


def render_week(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    """Return text and inline‑keyboard for current week's tasks (lvl=week)."""
    week_start = monday_of_week(date.today())
    tasks = database.list_tasks(uid, week_start, lvl="week", include_done=True)

    lines, buttons = [], []
    if not tasks:
        text = "На этой неделе пока нет задач. Добавь первую!"
    else:
        for t in tasks:
            status = "✅" if t["done"] else "▫️"
            lines.append(f"{status} {t.doc_id}: {t['text']}")
            btn_row = [
                InlineKeyboardButton("📤 На день", callback_data=f"week_push_{t.doc_id}"),
                InlineKeyboardButton("☑️" if not t["done"] else "↩️", callback_data=f"week_toggle_{t.doc_id}"),
            ]
            buttons.append(btn_row)
        text = "Спринт недели:\n" + "\n".join(lines)

    # control row
    buttons.append(
        [
            InlineKeyboardButton("➕ Добавить", callback_data="week_add"),
            InlineKeyboardButton("↪️ След. нед", callback_data="week_move_next"),
            InlineKeyboardButton("🔄 Обновить", callback_data="week_refresh"),
        ]
    )
    return text, InlineKeyboardMarkup(buttons)


# --- Month helpers ---
def first_day_of_month(d: date) -> date:
    return d.replace(day=1)

def render_month(uid: int, month: int | None = None, year: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
    if month is None:
        today = date.today()
        month, year = today.month, today.year
    stages = database.list_stages_for_month(uid, month, year)
    lines, buttons = [], []
    if not stages:
        text = f"📆 {month_name[month]}: пока нет этапов.\nНажми ➕ чтобы добавить."
    else:
        grouped = {}
        for st in stages:
            grouped.setdefault(st["goal_id"], []).append(st)
        for gid, lst in grouped.items():
            goal = database._table("okr").get(doc_id=gid)
            lines.append(f"🎯 {goal['title']}")
            for st in lst:
                lines.append(f"   • {st['title']}")
        text = "📆 " + month_name[month] + ":\n" + "\n".join(lines)
    buttons.append([InlineKeyboardButton("➕ Этап", callback_data="month_add_stage")])
    buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data="month_refresh")])
    return text, InlineKeyboardMarkup(buttons)


# --- Month menu handler ---
async def show_month_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    text, kb = render_month(uid)
    await update.message.reply_text(text, reply_markup=kb)


async def show_today_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    text, kb = render_today(uid)
    await update.message.reply_text(text, reply_markup=kb)


async def show_week_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    text, kb = render_week(uid)
    await update.message.reply_text(text, reply_markup=kb)



# --- Dynamic goals/OKR rendering ---
def render_goals(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    """Return text + keyboard listing all top-level objectives."""
    objs = database._table("okr").search(
        (database.where("uid") == uid) & (database.where("type") == "objective")
    )
    buttons = []
    if objs:
        for obj in objs:
            # calculate average progress of all KR for this objective
            krs = database._table("okr").search(
                (database.where("type") == "kr") & (database.where("obj_id") == obj.doc_id)
            )
            if krs:
                avg = sum(k.get("progress", 0) for k in krs) // len(krs)
                prog_prefix = progress_dot(avg) + " "
                prog_suffix = f"  [{avg}%]"
            else:
                prog_prefix = ""
                prog_suffix = ""
            due = obj.get("due", "")
            label_core = f"{obj['title']}  ⏳{due}" if due else obj["title"]
            label = f"{prog_prefix}{label_core}{prog_suffix}"
            buttons.append([
                InlineKeyboardButton(label, callback_data=f"okr_obj_{obj.doc_id}"),
                InlineKeyboardButton("➕ Этап", callback_data=f"goal_add_stage_{obj.doc_id}")
            ])
        text = "🎯 Твои цели:"
    else:
        text = "У тебя пока нет целей. Добавь первую!"
    # control row
    buttons.append([InlineKeyboardButton("➕ Новая цель", callback_data="okr_add_goal")])
    return text, InlineKeyboardMarkup(buttons)

# --- OKR Quarters and KRs ---
def render_quarters(obj_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Show four quarter buttons for chosen objective."""
    buttons = [
        [
            InlineKeyboardButton("I кв", callback_data=f"okr_q_{obj_id}_Q1"),
            InlineKeyboardButton("II кв", callback_data=f"okr_q_{obj_id}_Q2"),
            InlineKeyboardButton("III кв", callback_data=f"okr_q_{obj_id}_Q3"),
            InlineKeyboardButton("IV кв", callback_data=f"okr_q_{obj_id}_Q4"),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data="okr_back")],
    ]
    return "Выбери квартал:", InlineKeyboardMarkup(buttons)


def render_krs(obj_id: int, quarter: str) -> tuple[str, InlineKeyboardMarkup]:
    """List KRs for an objective and quarter."""
    obj = get_objective(obj_id)
    if not obj:
        return "Цель не найдена.", InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Назад", callback_data="okr_back")]]
        )
    # KRs: type == "kr", obj_id == obj_id, quarter == quarter
    krs = database._table("okr").search(
        (database.where("type") == "kr") &
        (database.where("obj_id") == obj_id) &
        (database.where("quarter") == quarter)
    )
    lines = []
    buttons = []
    if krs:
        for kr in krs:
            prog = kr.get("progress", 0)
            lines.append(f"{progress_dot(prog)} {kr['title']}  [{prog}%]")
            buttons.append([
                InlineKeyboardButton(f"+10%", callback_data=f"okr_kr_pinc_{kr.doc_id}_10"),
                InlineKeyboardButton(f"-10%", callback_data=f"okr_kr_pinc_{kr.doc_id}_-10"),
                InlineKeyboardButton(f"✏️", callback_data=f"okr_kr_prog_{kr.doc_id}"),
                InlineKeyboardButton(f"📌", callback_data=f"okr_kr_pin_{kr.doc_id}"),
            ])
        text = f"Ключевые результаты для цели:\n«{obj['title']}»\nКвартал: {quarter}\n\n" + "\n".join(lines)
    else:
        text = f"Нет КР для цели «{obj['title']}», квартал {quarter}."
    # Add control row
    buttons.append([
        InlineKeyboardButton("➕ Новый КР", callback_data=f"okr_kr_add_{obj_id}_{quarter}"),
        InlineKeyboardButton("⬅️ К кварталам", callback_data=f"okr_obj_{obj_id}"),
    ])
    return text, InlineKeyboardMarkup(buttons)


async def show_goal_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    text, kb = render_goals(uid)
    await update.message.reply_text(text, reply_markup=kb)


def render_inbox(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    """Return text + keyboard for inbox notes."""
    notes = database.list_inbox(uid)
    buttons = []
    if notes:
        for n in notes:
            preview = n["text"][:40] + ("…" if len(n["text"]) > 40 else "")
            buttons.append(
                [InlineKeyboardButton(preview or "(пусто)", callback_data=f"inbox_note_{n.doc_id}")]
            )
        text = "🔔 Инбокс — идеи, мысли и планы.\nПозже ты сможешь превратить запись в цель или задачу:"
    else:
        text = (
            "Инбокс пуст.\n"
            "Добавь идею, мысль или план — позже их можно будет превратить в цель или задачу!"
        )
    buttons.append([InlineKeyboardButton("➕ Добавить", callback_data="inbox_add")])
    return text, InlineKeyboardMarkup(buttons)

async def show_inbox_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    text, kb = render_inbox(uid)
    await update.message.reply_text(text, reply_markup=kb)


async def show_stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Root statistics menu with inline buttons."""
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📆 Сегодня", callback_data="stats_today")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="stats_back")],
        ]
    )
    await update.message.reply_text("📊 Статистика:", reply_markup=kb)
# ---------- Statistics helpers ----------
def render_stats_today(uid: int) -> str:
    """Return textual summary for today's stats."""
    tasks = database.list_tasks(uid, date.today(), lvl="day", include_done=True)
    total = len(tasks)
    done = sum(1 for t in tasks if t["done"])
    percent = int(done / total * 100) if total else 0
    return (
        f"📆 Сегодня\n"
        f"Задач всего: {total}\n"
        f"Выполнено:   {done}\n"
        f"Процент дня:  {percent}%"
    )



async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚙️ Настройки (заглушка). Здесь можно будет настроить уведомления.",
        reply_markup=ReplyKeyboardMarkup([["⬅️ В меню"]], resize_keyboard=True),
    )

# --- Helper to return to main menu ---
# --- Helper to return to main menu ---
async def return_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle '⬅️ В меню' button in settings."""
    await update.message.reply_text("Главное меню:", reply_markup=QUICK_MENU)

# --- Меню "Вариант 3": показать полное/свернуть ---
async def show_full_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Expand to full menu."""
    await update.message.reply_text("Полное меню:", reply_markup=MAIN_MENU)

async def collapse_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Return to compact quick menu."""
    await update.message.reply_text("Главное меню:", reply_markup=QUICK_MENU)


### --- Категории: получить список категорий, не покрытых задачами сегодня ---
def get_uncovered_categories_for_today(uid: int):
    """Вернуть id и названия категорий, по которым сегодня нет задач."""
    cats = database.list_categories(uid)
    covered = set()
    today = date.today()
    for c in cats:
        tasks = database.list_tasks_by_category(uid, c.doc_id, due=today)
        if tasks:
            covered.add(c.doc_id)
    return [c for c in cats if c.doc_id not in covered]

### --- Категории: router для выбора категории при добавлении задачи ---
async def choose_category_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id
    if data.startswith("choose_cat_"):
        if data == "choose_cat_none":
            context.user_data["category_id"] = None
        else:
            cat_id = int(data.split("_")[-1])
            context.user_data["category_id"] = cat_id
        context.user_data["awaiting_todo_text"] = True
        await query.edit_message_text("Введи текст задачи для этой категории:")
        return

# ---------- Callback‑router ---------- #
async def inline_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Ensure we have callback data before any checks
    if not update.callback_query:
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id
    # ---------- STATISTICS ----------
    # --- GOAL → NEW STAGE title ---
    if data.startswith("goal_add_stage_"):
        goal_id = int(data.split("_")[-1])
        context.user_data[CURRENT_GOAL_ID] = goal_id
        context.user_data[AWAIT_STAGE_TITLE] = True
        await query.edit_message_text("Введите название этапа:")
        return

    # month selection for stage
    if data.startswith("stage_month_"):
        _, _, m = data.split("_")
        if m == "done":
            context.user_data.pop(AWAIT_STAGE_MONTH, None)
            context.user_data.pop(CURRENT_STAGE_TITLE, None)
            context.user_data.pop(CURRENT_GOAL_ID, None)
            await query.edit_message_text("Добавление этапов завершено.")
            return
        month = int(m)
        goal_id = context.user_data[CURRENT_GOAL_ID]
        title = context.user_data[CURRENT_STAGE_TITLE]
        uid = query.from_user.id
        database.add_stage(uid, goal_id, title, month, date.today().year)
        # reset stage title, stay in loop
        context.user_data.pop(CURRENT_STAGE_TITLE, None)
        await query.edit_message_text(
            f"Этап «{title}» добавлен на {month_name[month]}.\nДобавить ещё этап к этой цели?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Да", callback_data=f"goal_add_stage_{goal_id}")],
                [InlineKeyboardButton("✅ Готово", callback_data="stage_month_done")],
            ])
        )
        return
    # ---------- LINK TASK TO GOAL ----------
    if data == "link_skip":
        # simply refresh today list
        context.user_data.pop("new_task_id", None)
        text, kb = render_today(uid)
        await query.edit_message_text("Ок, без привязки.", reply_markup=kb)
        return

    if data == "link_choose_goal":
        task_id = context.user_data.get("new_task_id")
        if not task_id:
            await query.edit_message_text("Нет задачи для привязки.")
            return
        # build goal selection keyboard
        goals = database._table("okr").search(
            (database.where("uid") == uid) & (database.where("type") == "objective")
        )
        rows = [
            [InlineKeyboardButton(g["title"], callback_data=f"link_goal_{task_id}_{g.doc_id}")]
            for g in goals
        ] or [[InlineKeyboardButton("Нет целей", callback_data="link_skip")]]
        rows.append([InlineKeyboardButton("⬅️ Отмена", callback_data="link_skip")])
        kb = InlineKeyboardMarkup(rows)
        await query.edit_message_text("Выбери цель:", reply_markup=kb)
        return

    if data.startswith("link_goal_"):
        _, _, tid_str, gid_str = data.split("_")
        task_id = int(tid_str)
        goal_id = int(gid_str)
        database.update_task(task_id, goal_id=goal_id)
        context.user_data.pop("new_task_id", None)
        await query.edit_message_text("Задача привязана к цели! 🎯")
        text, kb = render_today(uid)
        await query.message.reply_text(text, reply_markup=kb)
        return
    if data == "stats_today":
        text = render_stats_today(uid)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="stats_back")]])
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data == "stats_back":
        # return to stats root
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📆 Сегодня", callback_data="stats_today")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="okr_back_disabled")],
            ]
        )
        await query.edit_message_text("📊 Статистика:", reply_markup=kb)
        return

    # MONTH actions
    if data == "month_add":
        context.user_data["awaiting_month_text"] = True
        await query.edit_message_text("Введите текст задачи для месяца:")
        return

    if data == "month_add_stage":
        # Предлагаем выбрать, к какой цели относится этап
        goals = database._table("okr").search(
            (database.where("uid") == uid) & (database.where("type") == "objective")
        )
        if not goals:
            await query.edit_message_text("Сначала создай хотя бы одну цель!")
            return
        rows = [
            [InlineKeyboardButton(g["title"], callback_data=f"stage_goal_{g.doc_id}")]
            for g in goals
        ]
        rows.append([InlineKeyboardButton("⬅️ Отмена", callback_data="month_refresh")])
        kb = InlineKeyboardMarkup(rows)
        await query.edit_message_text("К какой цели добавить этап?", reply_markup=kb)
        return

    if data.startswith("month_toggle_"):
        tid = int(data.split("_")[-1])
        database.toggle_done(tid)
        text, kb = render_month(uid)
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data.startswith("month_push_"):
        tid = int(data.split("_")[-1])
        database.move_task(tid, monday_of_week(date.today()), new_lvl="week")
        text, kb = render_month(uid)
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data == "month_refresh":
        text, kb = render_month(uid)
        await query.edit_message_text(text, reply_markup=kb)
        return

    # --- ADD NEW KR ---
    if data.startswith("okr_kr_add_"):
        # format okr_kr_add_<objId>_<Qx>
        _, _, _, obj_id_str, quarter = data.split("_")
        obj_id = int(obj_id_str)
        context.user_data["new_kr_obj"] = obj_id
        context.user_data["new_kr_q"] = quarter
        context.user_data["awaiting_kr_title"] = True
        await query.edit_message_text(f"Введите текст КР для {quarter}:")
        return
    if data.startswith("okr_kr_pinc_"):
        _, _, _, kr_id_str, delta_str = data.split("_")
        kr_id = int(kr_id_str)
        delta = int(delta_str)
        database.update_kr_progress(kr_id, delta=delta)
        kr = database._table("okr").get(doc_id=kr_id)
        parent = kr["obj_id"]; q = kr["quarter"]
        text, kb = render_krs(parent, q)
        await query.edit_message_text(text, reply_markup=kb)
        return
    # --- INBOX ---
    if data == "inbox_add":
        context.user_data["awaiting_inbox_text"] = True
        await query.edit_message_text("Напиши идею / заметку для инбокса:")
        return

    if data.startswith("inbox_note_"):
        note_id = int(data.split("_")[-1])
        n = database._table("inbox").get(doc_id=note_id)
        if n:
            dt = n["ts"].replace("T", " ")[:16]
            text = f"🗒 Идея (ID {note_id})\n«{n['text']}»\n\n⏱ {dt}"
        else:
            text = "Запись не найдена."
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✏️ Изменить", callback_data=f"inbox_edit_{note_id}"),
                    InlineKeyboardButton("🎯 В цель", callback_data=f"inbox_goal_{note_id}"),
                ],
                [
                    InlineKeyboardButton("🗓 В задачу", callback_data=f"inbox_task_{note_id}"),
                    InlineKeyboardButton("🗄 Архив", callback_data=f"inbox_archive_{note_id}"),
                ],
                [InlineKeyboardButton("⬅️ Назад", callback_data="inbox_back")],
            ]
        )
        await query.edit_message_text(text, reply_markup=kb)
        return

    # --- INBOX actions on note ---
    if data.startswith("inbox_edit_"):
        nid = int(data.split("_")[-1])
        context.user_data["edit_note_id"] = nid
        context.user_data["awaiting_note_edit"] = True
        await query.edit_message_text("Новый текст заметки? (оставь «-» чтобы не менять)")
        return

    if data.startswith("inbox_archive_"):
        nid = int(data.split("_")[-1])
        database.archive_inbox_item(nid)
        await query.edit_message_text("Запись перемещена в архив.")
        text, kb = render_inbox(uid)
        await query.message.reply_text(text, reply_markup=kb)
        return

    if data.startswith("inbox_goal_"):
        nid = int(data.split("_")[-1])
        note = database._table("inbox").get(doc_id=nid)
        if note:
            obj_id = database.add_objective(uid, note["text"][:60])
            await query.edit_message_text(f"Создана цель из заметки! ID цели: {obj_id}")
            database.archive_inbox_item(nid)
        else:
            await query.edit_message_text("Заметка не найдена.")
        text, kb = render_inbox(uid)
        await query.message.reply_text(text, reply_markup=kb)
        return

    if data.startswith("inbox_task_"):
        nid = int(data.split("_")[-1])
        note = database._table("inbox").get(doc_id=nid)
        if not note:
            await query.edit_message_text("Заметка не найдена.")
            return
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Завтра", callback_data=f"task_day_{nid}_tomorrow"),
                    InlineKeyboardButton("📅 Дата…", callback_data=f"task_day_{nid}_ask"),
                ],
                [InlineKeyboardButton("⬅️ Назад", callback_data=f"inbox_note_{nid}")],
            ]
        )
        await query.edit_message_text("На какой день поставить задачу?", reply_markup=kb)
        return

    # Handle task_day_<nid>_<choice>
    if data.startswith("task_day_"):
        _, _, nid_str, choice = data.split("_")
        nid = int(nid_str)
        note = database._table("inbox").get(doc_id=nid)
        if not note:
            await query.edit_message_text("Заметка не найдена.")
            return
        if choice == "ask":
            context.user_data["note_to_task_id"] = nid
            context.user_data["awaiting_task_date"] = True
            await query.edit_message_text("Введите дату задачи (DD.MM):")
            return

        # only 'tomorrow' option remains
        due = date.today() + timedelta(days=1)
        msg = "Задача добавлена на завтра!"

        database.add_task(uid, note["text"], due, lvl="day")
        database.archive_inbox_item(nid)
        await query.edit_message_text(msg)
        return

    if data == "inbox_back":
        text, kb = render_inbox(uid)
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data == "today_add":
        # Проверяем покрытие категорий
        cats = get_uncovered_categories_for_today(uid)
        if cats:
            context.user_data["awaiting_category_choice"] = True
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton(c["title"], callback_data=f"choose_cat_{c.doc_id}")] for c in cats]
                + [[InlineKeyboardButton("Без категории", callback_data="choose_cat_none")]]
            )
            await query.edit_message_text("Выбери категорию для новой задачи:", reply_markup=kb)
            return
        else:
            context.user_data["awaiting_todo_text"] = True
            await query.edit_message_text("Введи текст задачи:")
            return

    if data.startswith("today_edit_"):
        task_id = int(data.split("_")[-1])
        context.user_data["edit_task_id"] = task_id
        context.user_data["awaiting_edit_text"] = True
        await query.edit_message_text("Новый текст задачи? (оставь «-» чтобы не менять)")
        return

    if data.startswith("today_toggle_"):
        task_id = int(data.split("_")[-1])
        task = database.get_task(task_id)
        prev_done = task["done"]
        database.toggle_done(task_id)
        task = database.get_task(task_id)
        kr_id = task.get("kr_id")
        if kr_id:
            delta = 10 if task["done"] and not prev_done else -10
            database.update_kr_progress(kr_id, delta=delta)
        text, kb = render_today(uid)
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data == "today_refresh":
        text, kb = render_today(uid)
        await query.edit_message_text(text, reply_markup=kb)
        return

    # WEEK actions
    if data == "week_add":
        context.user_data["awaiting_week_text"] = True
        await query.edit_message_text("Введите текст задачи для этой недели:")
        return

    if data.startswith("week_toggle_"):
        task_id = int(data.split("_")[-1])
        database.toggle_done(task_id)
        text, kb = render_week(uid)
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data.startswith("week_push_"):
        task_id = int(data.split("_")[-1])
        database.move_task(task_id, date.today(), new_lvl="day")
        text, kb = render_week(uid)
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data == "week_move_next":
        # move all open tasks to next Monday
        week_start = monday_of_week(date.today())
        tasks = database.list_tasks(uid, week_start, lvl="week", include_done=False)
        for t in tasks:
            database.move_task(t.doc_id, next_monday(date.today()), new_lvl="week")
        text, kb = render_week(uid)
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data == "week_refresh":
        text, kb = render_week(uid)
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data.startswith("task_start_ok_"):
        task_id = int(data.split("_")[-1])
        database.set_task_status(task_id, "started")
        await query.edit_message_text("Старт подтверждён ✔️")
        return

    # --- SNOOZE start/end ---
    if data.startswith("task_start_snooze_"):
        tid = int(data.split("_")[-1])
        # schedule repeat start_notify in 15 minutes (900 sec)
        context.job_queue.run_once(
            start_notify,
            when=900,
            data={"cid": uid, "tid": tid},
        )
        await query.edit_message_text("Напоминание отложено на 15 минут ⏰")
        return

    if data.startswith("task_end_ok_"):
        task_id = int(data.split("_")[-1])
        # 1) помечаем статус 'done'
        database.set_task_status(task_id, "done")
        # 2) ставим флаг done=True, чтобы галочка отображалась в списке
        database.toggle_done(task_id)
        # --- bump KR progress if linked
        task = database.get_task(task_id)
        kr_id = task.get("kr_id")
        if kr_id:
            database.update_kr_progress(kr_id, delta=10)
        # 3) обновляем экран "Сегодня", чтобы сразу увидеть выполненную задачу
        text, kb = render_today(uid)
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data.startswith("task_end_snooze_"):
        tid = int(data.split("_")[-1])
        context.job_queue.run_once(
            end_notify,
            when=900,
            data={"cid": uid, "tid": tid},
        )
        await query.edit_message_text("Напоминание отложено на 15 минут ⏰")
        return

    # --- GOALS/OKR ---
    if data == "okr_add_goal":
        context.user_data["awaiting_goal_title"] = True
        cancel_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Отмена", callback_data="okr_cancel_goal")]]
        )
        await query.edit_message_text("Введите название новой цели:", reply_markup=cancel_kb)
        return

    if data == "okr_cancel_goal":
        # Clear any pending goal creation flags
        context.user_data.pop("awaiting_goal_title", None)
        context.user_data.pop("awaiting_goal_due", None)
        context.user_data.pop("new_goal_title", None)
        text, kb = render_goals(uid)
        await query.edit_message_text("Добавление цели отменено.", reply_markup=kb)
        return

    if context.user_data.get("awaiting_goal_title"):
        # handled in text router
        pass

    if data.startswith("okr_obj_"):
        obj_id = int(data.split("_")[-1])
        obj = get_objective(obj_id)
        due = obj.get("due", "—")
        # Show quarter selection
        text, kb = render_quarters(obj_id)
        await query.edit_message_text(
            f"Цель: {obj['title']}\nСрок: ⏳{due}\n\n{text}", reply_markup=kb
        )
        return

    if data.startswith("okr_q_"):
        # e.g. okr_q_123_Q1
        parts = data.split("_")
        obj_id = int(parts[2])
        quarter = parts[3]
        text, kb = render_krs(obj_id, quarter)
        await query.edit_message_text(text, reply_markup=kb)
        return
    # --- OKR KR progress and pin ---
    if data.startswith("okr_kr_prog_"):
        kr_id = int(data.split("_")[-1])
        kr = database._table("okr").get(doc_id=kr_id)
        if not kr:
            await query.edit_message_text("КР не найден.")
            return
        context.user_data["edit_kr_id"] = kr_id
        context.user_data["awaiting_kr_progress"] = True
        await query.edit_message_text(
            f"Текущий прогресс КР:\n«{kr['title']}»\n\nСейчас: {kr.get('progress',0)}%\nВведи новый прогресс (0-100):"
        )
        return

    if data.startswith("okr_kr_pin_"):
        kr_id = int(data.split("_")[-1])
        kr = database._table("okr").get(doc_id=kr_id)
        if not kr:
            await query.edit_message_text("КР не найден.")
            return
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📆 В месяц", callback_data=f"okr_pin_lvl_{kr_id}_month"),
                    InlineKeyboardButton("🗓 В неделю", callback_data=f"okr_pin_lvl_{kr_id}_week"),
                    InlineKeyboardButton("📋 На день", callback_data=f"okr_pin_lvl_{kr_id}_day"),
                ],
                [InlineKeyboardButton("⬅️ Отмена", callback_data="okr_back")]
            ]
        )
        await query.edit_message_text(
            f"Куда добавить задачу из КР «{kr['title']}»?", reply_markup=kb
        )
        return

    if data.startswith("okr_pin_lvl_"):
        _, _, _, kr_id_str, lvl = data.split("_")
        kr_id = int(kr_id_str)
        kr = database._table("okr").get(doc_id=kr_id)
        if not kr:
            await query.edit_message_text("КР не найден.")
            return
        title = kr["title"]
        today = date.today()
        if lvl == "month":
            due = today.replace(day=1)
        elif lvl == "week":
            due = monday_of_week(today)
        else:  # day
            due = today
        database.add_task(uid, title, due, lvl=lvl, kr_id=kr_id)
        await query.edit_message_text(f"Задача добавлена в {lvl}! 🔗 связана с КР.")
        # optional: mark pinned true
        database._table("okr").update({"pinned": True}, doc_ids=[kr_id])
        return
    # --- OKR: KR progress edit via text input ---
    if context.user_data.get("awaiting_kr_progress"):
        kr_id = context.user_data.pop("edit_kr_id")
        context.user_data.pop("awaiting_kr_progress", None)
        try:
            val = int(txt)
            if not (0 <= val <= 100):
                raise ValueError
        except Exception:
            await update.message.reply_text("Введи число от 0 до 100.")
            return
        database.update_kr_progress(kr_id, progress=val)
        kr = database._table("okr").get(doc_id=kr_id)
        parent = kr.get("parent")
        quarter = kr.get("quarter")
        await update.message.reply_text(f"Прогресс КР обновлён: {val}%")
        # Show updated KR list
        if parent and quarter:
            text, kb = render_krs(parent, quarter)
            await update.message.reply_text(text, reply_markup=kb)
        return

    if data.startswith("okr_due_"):
        obj_id = int(data.split("_")[-1])
        context.user_data["edit_obj_id"] = obj_id
        context.user_data["awaiting_due_edit"] = True
        await query.edit_message_text("Новый срок? (Qx-YYYY или DD.MM.YYYY, «-» чтобы оставить)")
        return

    if data == "okr_back":
        text, kb = render_goals(uid)
        await query.edit_message_text(text, reply_markup=kb)
        return

    await query.edit_message_text(f"Нажата кнопка: {data} (ещё не реализовано)")

# ---------- Text handler for adding today/week task ---------- #
async def text_input_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    txt = update.message.text.strip()
    # Auto-switch to secretary if it's a standalone question
    if (
        not context.user_data  # no FSM flags active
        or all(not k.startswith("awaiting_") for k in context.user_data)
    ) and is_secretary_query(txt):
        # process as if awaiting_ai_question already set
        context.user_data["awaiting_ai_question"] = True
    # --- AI QUESTION ---
    # Check existing tasks before invoking AI
    if context.user_data.get("awaiting_ai_question"):
        matches = find_matching_tasks(uid, txt)
        if matches:
            lines = []
            for t in matches:
                when = t["due"]
                if t.get("start_ts"):
                    tm1 = datetime.fromisoformat(t["start_ts"]).strftime("%H:%M")
                    tm2 = (
                        datetime.fromisoformat(t["end_ts"]).strftime("%H:%M")
                        if t.get("end_ts")
                        else ""
                    )
                    span = f"{tm1}–{tm2}" if tm2 else tm1
                    lines.append(f"• {when} {span} {t['text']}")
                else:
                    lines.append(f"• {when} (без времени) {t['text']}")
            reply_text = "📌 Запланировано:\n" + "\n".join(lines)
            context.user_data.pop("awaiting_ai_question", None)
            await update.message.reply_text(reply_text)
            return
        context.user_data.pop("awaiting_ai_question")
        await update.message.reply_text("Думаю… (это может занять несколько секунд) ⏳")
        prompt = ai_service.build_context(uid) + "\n\n## user-question\n" + txt
        try:
            resp = await ai_service.ask_ai(prompt)
            # If JSON returned, just pretty‑print; else text
            if isinstance(resp, dict):
                import json, textwrap
                await update.message.reply_text(
                    "Ответ ассистента:\n" + textwrap.fill(json.dumps(resp, ensure_ascii=False, indent=2), 80)
                )
                # basic action: create tasks if specified
                if resp.get("action") == "create_tasks":
                    for t in resp["tasks"]:
                        due = datetime.strptime(t["date"], "%Y-%m-%d").date()
                        database.add_task(uid, t["text"], due, lvl="day")
                    await update.message.reply_text("Новые задачи созданы ✅")
            else:
                # Try parse plain text into task
                slot = parse_ai_slot(resp)
                if slot:
                    due, t1, t2, desc = slot
                    tid = database.add_task(
                        uid,
                        desc,
                        due,
                        lvl="day",
                        start_ts=datetime.combine(due, t1, tzinfo=USER_TZ).isoformat(),
                        end_ts=datetime.combine(due, t2, tzinfo=USER_TZ).isoformat(),
                    )
                    schedule_task_jobs(
                        context.job_queue,
                        update.effective_chat.id,
                        tid,
                        datetime.combine(due, t1, tzinfo=USER_TZ),
                        datetime.combine(due, t2, tzinfo=USER_TZ),
                    )
                    await update.message.reply_text(
                        f"🆕 Задача создана: {desc} — {due} {t1.strftime('%H:%M')}–{t2.strftime('%H:%M')}"
                    )
                    # Always refresh today's list if task is for today
                    if due == date.today():
                        text_today, kb_today = render_today(uid)
                        await update.message.reply_text(text_today, reply_markup=kb_today)
                else:
                    await update.message.reply_text(str(resp))
        except Exception as e:
            logger.exception("AI error")
            await update.message.reply_text(f"Ошибка AI: {e}")
        return
    # --- GOAL CREATION: title entered ---
    if context.user_data.get("awaiting_goal_title"):
        title = txt.strip()
        if not title:
            await update.message.reply_text("Пустой текст — отмена.")
            context.user_data.pop("awaiting_goal_title")
            text, kb = render_goals(uid)
            await update.message.reply_text(text, reply_markup=kb)
            return

        # сохраняем цель без срока (срок можно будет добавить позднее при редактировании)
        goal_id = database.add_objective(uid, title)
        context.user_data.pop("awaiting_goal_title", None)

        # сразу переходим к этапам
        context.user_data[CURRENT_GOAL_ID] = goal_id
        context.user_data[AWAIT_STAGE_TITLE] = True
        await update.message.reply_text(
            f"🎯 Цель «{title}» создана!\n"
            "Введите название первого этапа (шаг к цели):"
        )
        return
    # --- STAGE TITLE ---
    if context.user_data.get(AWAIT_STAGE_TITLE):
        title = txt.strip()
        if not title:
            await update.message.reply_text("Пустой текст — отмена.")
            context.user_data.pop(AWAIT_STAGE_TITLE)
            return
        context.user_data[CURRENT_STAGE_TITLE] = title
        context.user_data.pop(AWAIT_STAGE_TITLE)
        context.user_data[AWAIT_STAGE_MONTH] = True
        await update.message.reply_text("Выберите месяц:", reply_markup=month_keyboard("stage_month"))
        return

    # --- CREATE KR STEP 1: title ---
    if context.user_data.get("awaiting_kr_title"):
        title = txt.strip()
        if not title:
            await update.message.reply_text("Пустой текст — отмена.")
            context.user_data.pop("awaiting_kr_title")
            context.user_data.pop("new_kr_obj", None)
            context.user_data.pop("new_kr_q", None)
            return
        context.user_data["new_kr_title"] = title
        context.user_data.pop("awaiting_kr_title")
        context.user_data["awaiting_kr_init"] = True
        await update.message.reply_text("Стартовый прогресс КР (0‑100)?")
        return

    # --- STEP 1: text for today's task (с учётом категории) ---
    if context.user_data.get("awaiting_todo_text"):
        if txt:
            context.user_data["new_task_txt"] = txt
            context.user_data["awaiting_todo_text"] = False
            context.user_data["awaiting_todo_start"] = True
            await update.message.reply_text("Время начала? (HH:MM)")
            return
        else:
            await update.message.reply_text("Пустой текст — отмена.")
            context.user_data.pop("awaiting_todo_text", None)
            return
    # --- CREATE KR STEP 2: initial progress ---
        # --- STEP 2: start time ---
    if context.user_data.get("awaiting_todo_start"):
        t = parse_time(txt)
        if not t:
            await update.message.reply_text("Формат времени HH:MM, попробуй ещё раз.")
            return
        context.user_data["new_task_start"] = t
        context.user_data["awaiting_todo_start"] = False
        context.user_data["awaiting_todo_duration"] = True
        await update.message.reply_text("Сколько минут займёт задача? (или в формате ЧЧ:ММ)")
        return

    # --- STEP 3: duration + save ---
    if context.user_data.get("awaiting_todo_duration"):
        val = txt.replace(" ", "").replace(",", ":")
        if ":" in val:
            try:
                h, m = map(int, val.split(":"))
                duration = h * 60 + m
            except Exception:
                await update.message.reply_text("Введите число минут или в формате ЧЧ:ММ.")
                return
        else:
            try:
                duration = int(val)
            except Exception:
                await update.message.reply_text("Введите число минут или в формате ЧЧ:ММ.")
                return
        if duration <= 0 or duration > 720:
            await update.message.reply_text("Длительность должна быть от 1 до 720 минут.")
            return
        today_dt = date.today()
        start_dt = datetime.combine(today_dt, context.user_data["new_task_start"], tzinfo=USER_TZ)
        # --- Категория для задачи ---
        category_id = context.user_data.pop("category_id", None) if "category_id" in context.user_data else None
        task_id = database.add_task(
            uid,
            context.user_data["new_task_txt"],
            today_dt,
            lvl="day",
            start_ts=start_dt.isoformat(),
            duration_minutes=duration,
            status="plan",
            category_id=category_id,
        )
        context.user_data["new_task_id"] = task_id
        kb_link = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Нет", callback_data="link_skip"),
                    InlineKeyboardButton("Выбрать цель", callback_data="link_choose_goal"),
                ]
            ]
        )
        await update.message.reply_text(
            f"Задача добавлена! Время старта: {start_dt.strftime('%H:%M')}, длительность: {duration} минут.\nПривязать её к одной из целей?",
            reply_markup=kb_link,
        )
        # --- СТАВИМ JOB НА СТАРТ И КОНЕЦ задачи ---
        from datetime import timedelta
        end_dt = start_dt + timedelta(minutes=duration)
        schedule_task_jobs(
            context.job_queue,
            update.effective_chat.id,
            task_id,
            start_dt,
            end_dt
        )
        for k in ["awaiting_todo_duration", "new_task_txt", "new_task_start"]:
            context.user_data.pop(k, None)
        # --- Проверить покрытие категорий ---
        cats = database.list_categories(uid)
        covered = set()
        for c in cats:
            tasks = database.list_tasks_by_category(uid, c.doc_id, due=today_dt)
            if tasks:
                covered.add(c.doc_id)
        if len(covered) < 2 and len(cats) >= 2:
            # Нужно минимум 2 разные категории
            await update.message.reply_text("Добавь задачу ещё по другой категории. Выбери категорию:")
            # Запустить выбор категории
            uncov = [c for c in cats if c.doc_id not in covered]
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton(c["title"], callback_data=f"choose_cat_{c.doc_id}")] for c in uncov]
                + [[InlineKeyboardButton("Без категории", callback_data="choose_cat_none")]]
            )
            await update.message.reply_text("Выбери категорию для новой задачи:", reply_markup=kb)
            context.user_data["awaiting_category_choice"] = True
            return
        return


# ---------- Daily inbox reminder ---------- #
async def inbox_daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    """At 20:00 remind user about new Inbox notes created today."""
    uid = context.job.data.get("uid")
    chat_id = context.job.data.get("chat_id")
    if not uid or not chat_id:
        return
    today_iso = date.today().isoformat()
    notes_today = [
        n
        for n in database.list_inbox(uid)
        if not n.get("archived") and n["ts"].startswith(today_iso)
    ]
    if notes_today:
        await context.bot.send_message(
            chat_id,
            f"🔔 Сегодня появилось {len(notes_today)} новых заметок в Инбоксе.\n"
            "Подумай, нужно ли превратить их в цели или задачи!",
        )

async def on_shutdown(application: Application) -> None:
    """Flush TinyDB cache to disk when the bot stops."""
    close_db()
    logger.info("Database closed and cache flushed.")

# ---------- main ---------- #
def main() -> None:
    application: Application = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_shutdown(on_shutdown)
        .build()
    )

    from telegram.ext import JobQueue
    if application.job_queue is None:
        jq = JobQueue()
        jq.set_application(application)
        application.job_queue = jq

    # Slash‑команды
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("today", show_today_menu))
    application.add_handler(CommandHandler("week", show_week_menu))
    application.add_handler(CommandHandler("month", show_month_menu))
    application.add_handler(CommandHandler("okr", show_goal_menu))
    application.add_handler(CommandHandler("inbox", show_inbox_menu))
    application.add_handler(CommandHandler("stats", show_stats_menu))
    application.add_handler(CommandHandler("settings", show_settings_menu))
    application.add_handler(CommandHandler("ai", cmd_ai))
    application.add_handler(CommandHandler("add", add_cmd))
    application.add_handler(CommandHandler("free", free_cmd))
    # --- Временная команда для полного сброса пользователя ---
    application.add_handler(CommandHandler("reset_me", cmd_reset_me))
    # --- Жизненный план/стратегия ---
    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("lifeplan", cmd_lifeplan)],
            states={
                LIFEPLAN_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, lifeplan_router)],
                "lifeplan_confirm": [MessageHandler(filters.TEXT & ~filters.COMMAND, lifeplan_confirm)],
                "categories_state": [MessageHandler(filters.TEXT & ~filters.COMMAND, categories_router)],
            },
            fallbacks=[CommandHandler("cancel", lambda u, c: u.message.reply_text("Диалог отменён."))],
            name="lifeplan_conv",
            persistent=False,
        )
    )

    # Reply‑кнопки
    application.add_handler(MessageHandler(filters.Regex("^📋 Сегодня$"), show_today_menu))
    application.add_handler(MessageHandler(filters.Regex("^🗓 Неделя$"), show_week_menu))
    application.add_handler(MessageHandler(filters.Regex("^📆 Месяц$"), show_month_menu))
    application.add_handler(MessageHandler(filters.Regex("^🎯 Цели$"), show_goal_menu))
    application.add_handler(MessageHandler(filters.Regex("^🔔 Инбокс$"), show_inbox_menu))
    application.add_handler(MessageHandler(filters.Regex("^📊 Статистика$"), show_stats_menu))
    application.add_handler(MessageHandler(filters.Regex("^⚙️ Настройки$"), show_settings_menu))
    application.add_handler(MessageHandler(filters.Regex("^⬅️ В меню$"), return_to_main))
    application.add_handler(MessageHandler(filters.Regex("^💼 Меню$"), show_full_menu))
    application.add_handler(MessageHandler(filters.Regex("^⬅️ Свернуть$"), collapse_menu))
    application.add_handler(MessageHandler(filters.Regex("^🤖 Секретарь$"), cmd_ai))

    # Inline callback handler
    application.add_handler(CallbackQueryHandler(choose_category_router, pattern="^choose_cat_"))
    application.add_handler(CallbackQueryHandler(inline_router))

    # Voice handler
    application.add_handler(MessageHandler(filters.VOICE, voice_router))

    # Text input router
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_input_router,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            echo_to_rocky,
        )
    )

    logger.info("Bot started…")
        # --- восстановить ежедневные напоминания Инбокса после рестарта ---
    from datetime import time as _t

    for uid, chat in database.all_known_chats():
        name = f"inbox_reminder_{uid}"
        if not application.job_queue.get_jobs_by_name(name):
            application.job_queue.run_daily(
                inbox_daily_reminder,
                time(hour=20, minute=0, tzinfo=USER_TZ),
                data={"uid": uid, "chat_id": chat},
                name=name,
            )
    application.run_polling()


if __name__ == "__main__":
    dp.run_polling(bot)
