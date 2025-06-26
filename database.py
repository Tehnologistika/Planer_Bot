"""
database.py  •  Storage layer for the Telegram‑planner bot
TinyDB structure:
    - tasks      : day / week tasks linked to KR or free
    - okr        : objectives and key‑results (tree)
    - inbox      : quick notes
    - stats      : aggregated daily statistics
    - settings   : per‑user preferences (notifications, tz, etc.)
    - categories : life priorities linked to objectives
"""

from datetime import date, datetime, timedelta
from typing import Optional

from tinydb import TinyDB, Query, where

# pathlib & TinyDB middleware for robust file handling & caching
from pathlib import Path
from tinydb.storages import JSONStorage
from tinydb.middlewares import CachingMiddleware

#
# Use a hidden directory in the user's home for persistence
DATA_DIR = Path.home() / ".planner_bot"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "db.json"

# TinyDB with write‑cache; flushes to disk on close()
_db = TinyDB(DB_PATH, storage=CachingMiddleware(JSONStorage))

# ---------- helpers ---------- #
def _table(name: str):
    return _db.table(name)

# --- 1. Добавить категорию ---
def add_category(user_id: int, title: str, obj_id: Optional[int] = None) -> int:
    """Добавить новую категорию (жизненный приоритет, связан с целью)."""
    return _table("categories").insert({
        "uid": user_id,
        "title": title,
        "obj_id": obj_id,
        "created": datetime.utcnow().isoformat(),
    })

def list_categories(user_id: int):
    """Вернуть все категории пользователя."""
    return _table("categories").search(where("uid") == user_id)

def get_category(cat_id: int):
    """Вернуть одну категорию по doc_id."""
    return _table("categories").get(doc_id=cat_id)

# ---------- TASKS ---------- #
def add_task(user_id: int, text: str, due: date,
             lvl: str = "day",
             goal_id: Optional[int] = None,
             kr_id: Optional[int] = None, done: bool = False, start_ts: Optional[str] = None,
             end_ts: Optional[str] = None, status: str = "plan",
             duration_minutes: Optional[int] = None,
             category_id: Optional[int] = None) -> int:
    """Добавить задачу, опционально с привязкой к категории."""
    tbl = _table("tasks")
    return tbl.insert({
        "uid": user_id,
        "text": text,
        "due": due.isoformat(),
        "lvl": lvl,
        "goal_id": goal_id,
        "kr_id": kr_id,
        "done": done,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "status": status,        # plan | started | done
        "created": datetime.utcnow().isoformat(),
        "duration_minutes": duration_minutes,
        "category_id": category_id,
    })


def list_tasks(user_id: int, due: Optional[date] = None,
               lvl: Optional[str] = None, include_done: bool = True):
    tbl = _table("tasks")
    q = (where("uid") == user_id)
    if due:
        q &= where("due") == due.isoformat()
    if lvl:
        q &= where("lvl") == lvl
    if not include_done:
        q &= where("done") == False
    return tbl.search(q)

# --- 3. Получить задачи по категории ---
def list_tasks_by_category(user_id: int, category_id: int, due: Optional[date] = None):
    """Вернуть все задачи по user_id и category_id, опционально с датой due."""
    tbl = _table("tasks")
    q = (where("uid") == user_id) & (where("category_id") == category_id)
    if due:
        q &= where("due") == due.isoformat()
    return tbl.search(q)

# --- 4. Получить, сколько категорий покрыто задачами за день ---
def count_categories_covered(user_id: int, dt: date) -> int:
    """Вернуть число уникальных категорий, по которым есть задачи на dt."""
    tbl = _table("tasks")
    q = (where("uid") == user_id) & (where("due") == dt.isoformat())
    tasks = tbl.search(q)
    covered = set()
    for t in tasks:
        if t.get("category_id"):
            covered.add(t["category_id"])
    return len(covered)

# --- Added helper for listing future tasks ---
def list_future_tasks(user_id: int, days_ahead: int = 30, include_done: bool = True):
    """
    Return tasks for the user with due dates from today up to today + days_ahead.
    """
    tbl = _table("tasks")
    today = date.today()
    end = today + timedelta(days=days_ahead)
    q = (where("uid") == user_id) & \
        (where("due") >= today.isoformat()) & \
        (where("due") <= end.isoformat())
    if not include_done:
        q &= where("done") == False  # noqa: E712
    return tbl.search(q)

def toggle_done(task_id: int):
    tbl = _table("tasks")
    rec = tbl.get(doc_id=task_id)
    if rec:
        tbl.update({"done": not rec["done"]}, doc_ids=[task_id])

def move_task(task_id: int, new_due: date, new_lvl: str = "day"):
    _table("tasks").update({"due": new_due.isoformat(), "lvl": new_lvl}, doc_ids=[task_id])

def set_task_times(task_id: int, start_ts: Optional[str], end_ts: Optional[str]):
    """Update start/end timestamps for a task."""
    _table("tasks").update({"start_ts": start_ts, "end_ts": end_ts}, doc_ids=[task_id])

def set_task_status(task_id: int, status: str):
    """Update status: plan | started | done."""
    _table("tasks").update({"status": status}, doc_ids=[task_id])


# ---------- TASKS: helpers for fetch/update with history ---------- #
def get_task(task_id: int):
    """Return a task record or None."""
    return _table("tasks").get(doc_id=task_id)

def update_task(task_id: int, **new_fields):
    """
    Update one or more fields of a task.
    The previous snapshot is appended to the 'history' list with timestamp.
    """
    tbl = _table("tasks")
    rec = tbl.get(doc_id=task_id)
    if not rec:
        return
    history = rec.get("history", [])
    # store shallow copy without its existing history
    snapshot = {k: v for k, v in rec.items() if k != "history"}
    snapshot["ts"] = datetime.utcnow().isoformat()
    history.append(snapshot)
    # merge fields
    new_fields["history"] = history
    tbl.update(new_fields, doc_ids=[task_id])

# ---------- OKR ---------- #

def add_objective(user_id: int, title: str) -> int:
    return _table("okr").insert({
        "uid": user_id,
        "type": "objective",
        "title": title,
        "created": datetime.utcnow().isoformat(),
    })

# ---------- OBJECTIVE due-date helpers ---------- #
def get_objective(obj_id: int):
    """Fetch a single objective by doc_id."""
    return _table("okr").get(doc_id=obj_id)

def update_objective(obj_id: int, **fields):
    """
    Update fields of an objective, preserving history of 'due' changes.
    If 'due' is changing, push previous value into history list.
    """
    tbl = _table("okr")
    rec = tbl.get(doc_id=obj_id)
    if not rec:
        return
    history = rec.get("history", [])
    # track due change
    if "due" in fields and rec.get("due") != fields["due"]:
        history.append({"ts": datetime.utcnow().isoformat(), "due": rec.get("due")})
    if history:
        fields["history"] = history
    tbl.update(fields, doc_ids=[obj_id])

def add_key_result(
    user_id: int,
    objective_id: int,
    title: str,
    quarter: str,
    progress: int = 0,
) -> int:
    """
    quarter – string 'Q1' … 'Q4'
    """
    return _table("okr").insert({
        "uid": user_id,
        "type": "kr",
        "obj_id": objective_id,
        "title": title,
        "quarter": quarter,
        "progress": progress,
        "created": datetime.utcnow().isoformat(),
    })

def list_okr_tree(user_id: int):
    tbl = _table("okr")
    objs = tbl.search((where("uid") == user_id) & (where("type") == "objective"))
    tree = []
    for obj in objs:
        krs = tbl.search((where("uid") == user_id) & (where("type") == "kr") & (where("obj_id") == obj.doc_id))
        tree.append((obj, krs))
    return tree

def update_kr_progress(kr_id: int, progress: Optional[int] = None, delta: Optional[int] = None):
    """
    Either set absolute progress (0‑100) or adjust by delta (+/‑).
    """
    tbl = _table("okr")
    rec = tbl.get(doc_id=kr_id)
    if not rec:
        return
    current = rec.get("progress", 0)
    if delta is not None:
        new_val = max(0, min(100, current + delta))
    elif progress is not None:
        new_val = max(0, min(100, progress))
    else:
        return
    tbl.update({"progress": new_val}, doc_ids=[kr_id])

# ---------- INBOX ---------- #
def add_inbox(user_id: int, text: str) -> int:
    return _table("inbox").insert({
        "uid": user_id,
        "text": text,
        "ts": datetime.utcnow().isoformat(),
        "archived": False,
        "history": [],
    })

def list_inbox(user_id: int):
    return _table("inbox").search(where("uid") == user_id)

def clear_inbox_item(doc_id: int):
    _table("inbox").remove(doc_ids=[doc_id])

def update_inbox_text(doc_id: int, new_text: str):
    """Save new text while pushing old version to history."""
    tbl = _table("inbox")
    rec = tbl.get(doc_id=doc_id)
    if not rec:
        return
    history = rec.get("history", [])
    history.append({"ts": datetime.utcnow().isoformat(), "text": rec["text"]})
    tbl.update({"text": new_text, "history": history}, doc_ids=[doc_id])


def archive_inbox_item(doc_id: int):
    """Mark inbox note as archived (soft delete)."""
    _table("inbox").update({"archived": True}, doc_ids=[doc_id])

# ---------- STAGES (Goal → Monthly stages) ---------- #
def add_stage(uid: int, goal_id: int, title: str, month: int, year: int) -> int:
    """
    Добавить этап‑месяц для цели goal_id.
    month: 1‑12, year: календарный.
    """
    return _table("stages").insert({
        "uid": uid,
        "goal_id": goal_id,
        "title": title,
        "month": month,
        "year": year,
        "created": datetime.utcnow().isoformat(),
    })

def list_stages_for_month(uid: int, month: int, year: int):
    """
    Вернуть все этапы пользователя uid для указанного месяца/года.
    """
    tbl = _table("stages")
    return tbl.search(
        (where("uid") == uid) &
        (where("month") == month) &
        (where("year") == year)
    )

def get_stage(stage_id: int):
    return _table("stages").get(doc_id=stage_id)

# ---------- WEEKS (Stage → Weekly targets) ---------- #
def add_week_target(uid: int, stage_id: int, title: str, week_start: date) -> int:
    """
    Добавить недельную подцель; week_start — дата понедельника.
    """
    return _table("weeks").insert({
        "uid": uid,
        "stage_id": stage_id,
        "title": title,
        "week_start": week_start.isoformat(),
        "created": datetime.utcnow().isoformat(),
    })

def list_weeks_for_stage(uid: int, stage_id: int):
    tbl = _table("weeks")
    return tbl.search(
        (where("uid") == uid) & (where("stage_id") == stage_id)
    )

def get_week(week_id: int):
    return _table("weeks").get(doc_id=week_id)

# ---------- STATS ---------- #
def add_stat(user_id: int, stat_date: date, done: int, total: int):
    tbl = _table("stats")
    key = stat_date.isoformat()
    rec = tbl.get((where("uid") == user_id) & (where("date") == key))
    if rec:
        tbl.update({"done": done, "total": total}, doc_ids=[rec.doc_id])
    else:
        tbl.insert({"uid": user_id, "date": key, "done": done, "total": total})

def get_stat(user_id: int, stat_date: date):
    rec = _table("stats").get((where("uid") == user_id) & (where("date") == stat_date.isoformat()))
    return rec or {"done": 0, "total": 0}

# ---------- SETTINGS ---------- #
def get_setting(user_id: int, key: str, default=None):
    rec = _table("settings").get((where("uid") == user_id) & (where("key") == key))
    return rec["value"] if rec else default

def set_setting(user_id: int, key: str, value):
    tbl = _table("settings")
    rec = tbl.get((where("uid") == user_id) & (where("key") == key))
    if rec:
        tbl.update({"value": value}, doc_ids=[rec.doc_id])
    else:
        tbl.insert({"uid": user_id, "key": key, "value": value})

# ---------- Chat-remember helpers (для ежедневных job’ов) ----------
def remember_chat(uid: int, chat_id: int) -> None:
    """Сохранить (или обновить) chat_id пользователя, чтобы восстановить
    ежедневные задачи после перезапуска бота."""
    tbl = _table("settings")
    rec = tbl.get(where("uid") == uid)
    if rec:
        tbl.update({"chat": chat_id}, where("uid") == uid)
    else:
        tbl.insert({"uid": uid, "chat": chat_id})

def all_known_chats():
    """Вернуть список (uid, chat_id) всех пользователей, для которых мы
    уже ставили напоминание Инбокса."""
    return [
        (r["uid"], r["chat"]) for r in _table("settings").all() if r.get("chat")
    ]
    
# ---------- SAFE SHUTDOWN ----------
def close_db():
    """Flush TinyDB cache and close file."""
    _db.close()