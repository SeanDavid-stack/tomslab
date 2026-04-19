"""Daily study picker — a light nudge to learn one Tom concept a day.

Picks a concept from Tom's glossary, weighted by mention count (so the
user lands on active topics more often than niche ones), but avoiding
recent repeats so consecutive days don't both surface 'NVPOC' just
because it's the most-discussed term. The picked concept is handed off
to the existing evolution timeline dialog.

Repeat-avoidance is cheap: we store the last N concept names and the
last-picked date in the `settings` table. Crossing the 24-hour mark
resets the 'next pick' pool.
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timezone
from typing import List

from tomslab import db as dbmod


_RECENT_MEMORY = 7   # avoid re-picking within the last week
_SETTING_KEY = "daily_study_recent_json"
_LAST_DATE_KEY = "daily_study_last_date"


def _load_recent(conn: sqlite3.Connection) -> List[str]:
    raw = dbmod.get_setting(conn, _SETTING_KEY, "") or ""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [str(x) for x in data][-_RECENT_MEMORY:]
    except Exception:
        return []


def _save_recent(conn: sqlite3.Connection, concepts: List[str]) -> None:
    dbmod.set_setting(
        conn, _SETTING_KEY, json.dumps(concepts[-_RECENT_MEMORY:])
    )


def pick_concept(conn: sqlite3.Connection) -> str | None:
    """Weighted-random concept pick with repeat avoidance.

    Weights are mention counts in the Discord corpus (computed the same
    way the chip bar does) so the pool biases toward concepts Tom
    actually talks about. Returns None if the concepts table is empty.
    """
    rows = conn.execute(
        """
        SELECT name, description
          FROM concepts
         WHERE COALESCE(name, '') != ''
        """
    ).fetchall()
    if not rows:
        return None

    # Extract (term, weight) pairs. "term" is the abbreviation when the
    # description starts with "(ABBR)", otherwise the name.
    def _abbr(desc: str) -> str:
        if desc and desc.startswith("(") and ")" in desc:
            return desc[1:desc.index(")")].strip()
        return ""

    pool: list[tuple[str, int]] = []
    for r in rows:
        name = r["name"] or ""
        desc = r["description"] or ""
        term = _abbr(desc) or name
        if not term:
            continue
        # Mentions in the Discord corpus; cheap LIKE is fine for seed data.
        mentions = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE content LIKE ? COLLATE NOCASE",
            (f"%{term}%",),
        ).fetchone()["n"]
        # Everyone gets baseline weight 1 so niche-but-real concepts can
        # still surface occasionally.
        pool.append((term, max(1, int(mentions))))

    recent = set(_load_recent(conn))
    fresh = [p for p in pool if p[0] not in recent]
    # If the avoid-list would empty the pool (small glossary, big memory),
    # fall back to the full pool — some recency is better than none.
    candidates = fresh or pool
    total = sum(w for _, w in candidates)
    if total <= 0:
        return None
    roll = random.uniform(0, total)
    acc = 0.0
    for term, weight in candidates:
        acc += weight
        if acc >= roll:
            picked = term
            break
    else:
        picked = candidates[-1][0]

    # Record the pick for the avoid list + today's date so the caller
    # can short-circuit to the same concept if the user re-opens the
    # dialog within the same calendar day.
    recent_list = _load_recent(conn)
    recent_list.append(picked)
    _save_recent(conn, recent_list)
    dbmod.set_setting(
        conn, _LAST_DATE_KEY,
        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )
    return picked


def last_picked_today(conn: sqlite3.Connection) -> str | None:
    """If the user already triggered the daily pick today, return that
    concept so subsequent Help → Daily study clicks don't keep
    shuffling. Crossing midnight resets."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    last_date = dbmod.get_setting(conn, _LAST_DATE_KEY, "") or ""
    if last_date != today:
        return None
    recent = _load_recent(conn)
    return recent[-1] if recent else None
