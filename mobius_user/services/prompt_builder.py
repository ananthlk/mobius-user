"""Deterministic user profile builder.

Pure function. Given a user's preferences + activities, produces a
structured JSON envelope consumers (chat, rag, future surfaces) can either
splice into their own system prompts, or use the pre-rendered text in
``rendered_prompt`` directly.

Versioning
----------
Bump ``CURRENT_TEMPLATE_VERSION`` whenever the rendered prompt language or
schema changes. Stored profiles with an older version get lazily
regenerated on the next /me read.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

CURRENT_TEMPLATE_VERSION = 2


# ── Phrasing knobs ─────────────────────────────────────────────────
# Template v2 (2026-07-17): tone hints carry docs/tone-style-guide.md's
# litmus tests verbatim. rendered_prompt is the LOAD-BEARING tone
# instruction — chat's planner format rules defer to it (chat commit
# 8636103) — so each hint must be strong enough to elicit the voice,
# not just name it.
_TONE_HINTS = {
    "professional": (
        "Tone: professional. Complete sentences, no contractions, exact "
        "terminology, formal source citations. Open with the finding in a "
        "full clause. Phrase offers as capability (\"I can \u2026\"). The "
        "answer should read as if presented to a regulator."
    ),
    "friendly": (
        "Tone: friendly. Contractions throughout. Open with a brief human "
        "beat before the substance, translate jargon in passing, and keep "
        "it the way you'd say it across a desk. At most one emoji, and "
        "only where natural."
    ),
    "concise": (
        "Tone: concise. Verdict first \u2014 start with the verdict noun or "
        "verb. Sentence fragments preferred. NO bullet scaffolding, no "
        "greetings, no hedging. Aim for about a third the length of a "
        "professional answer \u2014 readable off a pager in a hallway. "
        "Phrase offers as one word plus a question mark (\"Appeal?\")."
    ),
}

_EXPERIENCE_HINTS = {
    "beginner": "Assume limited AI experience — explain what you're doing and confirm before non-trivial actions.",
    "regular": "Assume regular AI experience — explain anything unusual but skip the basics.",
    "expert": "Assume expert use — minimal hand-holding; surface tradeoffs and edge cases.",
}

_AUTONOMY_HINTS = {
    "automatic": "do them automatically without confirming",
    "confirm_first": "show your plan and wait for confirmation",
    "manual": "guide the user through doing it themselves; don't act for them",
}


def _normalize_tone(tone: Optional[str]) -> str:
    return tone if tone in _TONE_HINTS else "professional"


def _normalize_experience(level: Optional[str]) -> str:
    return level if level in _EXPERIENCE_HINTS else "beginner"


def _normalize_autonomy(mode: Optional[str], default: str = "confirm_first") -> str:
    return mode if mode in _AUTONOMY_HINTS else default


def _format_task_list(activities: Iterable[dict]) -> str:
    """Turn a list of activity dicts into a comma-and-and human phrase.

    e.g. [{label: "X"}, {label: "Y"}, {label: "Z"}] → "X, Y, and Z"
    """
    labels = [a["label"] for a in activities if a.get("label")]
    if not labels:
        return "general operations work"
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def _render_prompt(
    *,
    preferred_name: Optional[str],
    tasks_phrase: str,
    tone: str,
    experience_level: str,
    autonomy_routine: str,
    autonomy_sensitive: str,
    greeting_enabled: bool,
) -> str:
    name_clause = (
        f"The user's name is {preferred_name}."
        if preferred_name
        else "The user has not set a preferred name."
    )
    greeting_clause = (
        ""
        if greeting_enabled
        else " Skip greetings and personalized openers — go straight to the answer."
    )

    return (
        f"{name_clause} They focus on {tasks_phrase}. "
        f"{_TONE_HINTS[tone]} "
        f"{_EXPERIENCE_HINTS[experience_level]} "
        f"For routine tasks, {_AUTONOMY_HINTS[autonomy_routine]}. "
        f"For sensitive tasks (billing, PHI, anything irreversible), "
        f"{_AUTONOMY_HINTS[autonomy_sensitive]}."
        f"{greeting_clause}"
    )


def build_user_profile(
    *,
    preferred_name: Optional[str],
    first_name: Optional[str],
    display_name: Optional[str],
    timezone_str: Optional[str],
    activities: Iterable[dict],
    tone: Optional[str],
    ai_experience_level: Optional[str],
    greeting_enabled: bool,
    autonomy_routine_tasks: Optional[str],
    autonomy_sensitive_tasks: Optional[str],
) -> dict[str, Any]:
    """Build the structured profile envelope.

    Args:
        activities: iterable of {"code": ..., "label": ...} dicts in display order.
                    Pass already-resolved labels (don't pass codes only).

    Returns:
        Dict ready to JSONB-store and return to consumers.
    """
    name = (preferred_name or first_name or display_name or "").strip() or None
    norm_tone = _normalize_tone(tone)
    norm_exp = _normalize_experience(ai_experience_level)
    norm_routine = _normalize_autonomy(autonomy_routine_tasks)
    norm_sensitive = _normalize_autonomy(autonomy_sensitive_tasks, default="confirm_first")

    task_list = list(activities)
    tasks_phrase = _format_task_list(task_list)

    rendered = _render_prompt(
        preferred_name=name,
        tasks_phrase=tasks_phrase,
        tone=norm_tone,
        experience_level=norm_exp,
        autonomy_routine=norm_routine,
        autonomy_sensitive=norm_sensitive,
        greeting_enabled=bool(greeting_enabled),
    )

    return {
        "preferred_name": name,
        "tasks": [
            {"code": a.get("code") or a.get("activity_code"), "label": a.get("label")}
            for a in task_list
        ],
        "communication": {
            "tone": norm_tone,
            "ai_experience_level": norm_exp,
            "greeting_enabled": bool(greeting_enabled),
        },
        "autonomy": {
            "routine_tasks": norm_routine,
            "sensitive_tasks": norm_sensitive,
        },
        "timezone": timezone_str or "America/New_York",
        "rendered_prompt": rendered,
        "version": CURRENT_TEMPLATE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
