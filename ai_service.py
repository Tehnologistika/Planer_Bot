"""
ai_service.py
-------------
Thin wrapper around DeepSeek (or any OpenAI‑compatible) chat completion API.

Functions exposed:
    build_context(uid) -> str
    ask_ai(prompt: str) -> str | dict
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, List

import httpx

import database
import config


# --- DeepSeek settings ---
DEESEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEESEEK_MODEL = "deepseek-chat"
HTTP_TIMEOUT = 60

# ---------- System prompt for chronobiology & brevity ---------- #
SYSTEM_PROMPT = (
    "You are an ultra‑concise personal planning assistant. "
    "Always reply with 1‑3 short bullet points in plain text. "
    "When suggesting time slots, use biologically optimal periods: "
    "morning (08:00‑12:00) for cognitively demanding tasks, "
    "midday (13:00‑15:00) for routine work, "
    "late afternoon (16:00‑19:00) for creative or reflective tasks. "
    "Offer the best specific windows (DD.MM HH:MM–HH:MM). "
    "Do not output explanations unless the user explicitly asks. "
    "If the answer needs no scheduling, still keep it under 40 words."
)


# ---------- Helpers to format data ---------- #
def _format_tasks(tasks: List[dict]) -> str:
    """Compact representation of upcoming tasks for prompt."""
    if not tasks:
        return "none"
    out: List[str] = []
    for t in tasks:
        status = "✅" if t.get("done") else "🔸"
        out.append(f"{status} {t['due']} · {t['text']}")
    return "\n".join(out)


def _format_goals(goals: List[dict]) -> str:
    if not goals:
        return "none"
    return "\n".join(
        f"• {g['title']} (deadline: {g.get('due','N/A')})" for g in goals
    )


# ---------- Public helpers ---------- #
def build_context(uid: int) -> str:
    """
    Collect next‑30‑days tasks + list of goals for this user
    and pack into prompt fragment.
    """
    today = date.today()
    future_tasks = database.list_future_tasks(uid, days_ahead=30)
    objectives = database._table("okr").search(
        (database.where("uid") == uid) & (database.where("type") == "objective")
    )

    ctx = (
        "You are a personal planning assistant. "
        "Help schedule tasks and give suggestions.\n"
        "## upcoming_tasks (next 30 days)\n"
        f"{_format_tasks(future_tasks)}\n"
        "## goals\n"
        f"{_format_goals(objectives)}\n"
    )
    return ctx


async def ask_ai(prompt: str) -> Any:
    """
    Send prompt to DeepSeek and return parsed response:
    - If JSON deserialises, return dict.
    - Else raw string.
    """
    payload = {
        "model": DEESEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 512,
    }
    headers = {"Authorization": f"Bearer {config.DEEPSEEK_KEY}"}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.post(DEESEEK_URL, json=payload, headers=headers)
        r.raise_for_status()
        content: str = r.json()["choices"][0]["message"]["content"].strip()

    # Try parse as JSON
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return content