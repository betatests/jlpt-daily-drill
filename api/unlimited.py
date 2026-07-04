"""
Unlimited mode — endless adaptive question stream.

Unlike /api/daily (one fixed 50-question set per calendar day, cached and
seeded so it's stable across reloads), unlimited mode has no fixed size and
is never cached: every call recomputes a fresh batch from current stats.

Priority model (continuous score, not hard tiers):

  score = 1000                                  if never seen (seen == 0)
        + 300 * (wrong / seen)                  error rate, if seen
        + min(days_since_last_seen, 60) * 2      recency decay
        + random jitter (0-15)                  avoid robotic repeats

This means:
  - Never-seen questions dominate (they always rank highest).
  - Among seen questions, high error-rate items rank above clean ones.
  - Even "mastered" (always-correct) items are not abandoned forever —
    the longer it's been since they were last shown, the more their score
    grows, so they resurface on a soft spaced-repetition-like schedule
    instead of only after every other question has been exhausted.

Section balance: each request pulls proportionally across the 5 sections
using the same ratio as the daily plan (10:10:10:15:5 -> 2:2:2:3:1), so a
session doesn't get stuck drilling only one section for a long stretch.

Repeat prevention: a small rolling window of recently-served question ids
is kept server-side (unlimited_recent table) and avoided when possible,
without requiring the client to send/maintain a growing exclude list.
"""

from __future__ import annotations
import random, time
from datetime import date, datetime
from typing import Dict, List, Optional

from .database import get_db
from .daily import enrich_question_choices

SECTION_RATIO = {
    "kanji":         2,
    "kanji_reverse": 2,
    "bunpou":        2,
    "kotoba":        3,
    "reading":       1,
}

RECENT_WINDOW = 30   # how many recently-served ids to try to avoid repeating
RECENT_KEEP   = 200  # trim table so it never grows unbounded


def _parse_date(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _score(q: dict, stats: dict, today: date, rng: random.Random) -> float:
    st = stats.get(q["id"], {})
    seen  = st.get("seen", 0)
    wrong = st.get("wrong", 0)
    last_date_str = st.get("last_date")

    unseen_boost = 1000.0 if seen == 0 else 0.0
    error_rate   = (wrong / seen) if seen else 0.0

    if last_date_str:
        d = _parse_date(last_date_str)
        days_since = (today - d).days if d else 999
    else:
        days_since = 999

    recency_bonus = min(max(days_since, 0), 60) * 2.0
    jitter = rng.uniform(0, 15)

    return unseen_boost + 300.0 * error_rate + recency_bonus + jitter


def _largest_remainder_quotas(ratio: Dict[str, int], count: int) -> Dict[str, int]:
    """Distribute `count` items across sections proportionally to `ratio`."""
    total_ratio = sum(ratio.values())
    if total_ratio == 0 or count <= 0:
        return {k: 0 for k in ratio}
    raw = {k: (v / total_ratio) * count for k, v in ratio.items()}
    floors = {k: int(v) for k, v in raw.items()}
    remainder = count - sum(floors.values())
    fracs = sorted(raw.items(), key=lambda kv: kv[1] - int(kv[1]), reverse=True)
    for k, _ in fracs[:max(remainder, 0)]:
        floors[k] += 1
    return floors


# ── Cooldown tracking (unlimited_recent table) ────────────────────────────────
def get_recent_ids() -> List[str]:
    db = get_db()
    try:
        rows = db.execute(
            "SELECT question_id FROM unlimited_recent ORDER BY seq DESC LIMIT ?",
            (RECENT_WINDOW,),
        ).fetchall()
        return [r["question_id"] for r in rows]
    finally:
        db.close()


def record_served(ids: List[str]):
    if not ids:
        return
    db = get_db()
    try:
        now = int(time.time())
        for qid in ids:
            db.execute(
                "INSERT INTO unlimited_recent (question_id, served_at) VALUES (?, ?)",
                (qid, now),
            )
        db.commit()
        db.execute(
            """DELETE FROM unlimited_recent WHERE seq NOT IN (
                   SELECT seq FROM unlimited_recent ORDER BY seq DESC LIMIT ?
               )""",
            (RECENT_KEEP,),
        )
        db.commit()
    finally:
        db.close()


# ── Public entry point ────────────────────────────────────────────────────────
def build_unlimited_batch(
    questions: list,
    stats: dict,
    count: int,
    section_filter: Optional[str] = None,
) -> dict:
    """
    questions: list of question dicts (already loaded from DB, optionally
               pre-filtered by section by the caller)
    Returns {"items": [...enriched question dicts...]}
    """
    rng = random.Random()  # true randomness — this is a live, non-cached session
    today = date.today()

    recent_ids = set(get_recent_ids())

    by_section: Dict[str, list] = {}
    for q in questions:
        sec = q["section"] if isinstance(q, dict) else q.section
        if section_filter and sec != section_filter:
            continue
        by_section.setdefault(sec, []).append(q if isinstance(q, dict) else q.dict())

    if not by_section:
        return {"items": []}

    # score + sort each section; keep not-recently-served items ahead of
    # recently-served ones (but still include recent ones as a fallback so
    # small pools / small sections never come up empty)
    ranked: Dict[str, list] = {}
    for sec, pool in by_section.items():
        fresh = [q for q in pool if q["id"] not in recent_ids]
        stale = [q for q in pool if q["id"] in recent_ids]
        fresh.sort(key=lambda q: _score(q, stats, today, rng), reverse=True)
        stale.sort(key=lambda q: _score(q, stats, today, rng), reverse=True)
        ranked[sec] = fresh + stale

    if section_filter:
        quotas = {section_filter: count}
    else:
        active_ratio = {k: v for k, v in SECTION_RATIO.items() if k in ranked}
        quotas = _largest_remainder_quotas(active_ratio, count)

    # interleave round-robin across sections (heavier-weighted sections get
    # more turns) so a batch looks like a mini balanced daily set rather
    # than being blocked out by whichever section has the most items
    section_order = sorted(ranked.keys(), key=lambda s: -SECTION_RATIO.get(s, 1))
    cursors   = {sec: 0 for sec in ranked}
    remaining = dict(quotas)
    picked: List[dict] = []

    max_rounds = max(quotas.values()) if quotas else 0
    for _ in range(max_rounds):
        for sec in section_order:
            if remaining.get(sec, 0) <= 0:
                continue
            pool = ranked[sec]
            c = cursors[sec]
            if c >= len(pool):
                continue
            picked.append(pool[c])
            cursors[sec] = c + 1
            remaining[sec] -= 1
            if len(picked) >= count:
                break
        if len(picked) >= count:
            break

    # top up from any section with leftover items if quotas under-filled
    # (e.g. a small/thin section ran out of unique questions)
    if len(picked) < count:
        for sec in section_order:
            pool = ranked[sec]
            c = cursors[sec]
            while c < len(pool) and len(picked) < count:
                picked.append(pool[c])
                c += 1
            cursors[sec] = c
            if len(picked) >= count:
                break

    items = [enrich_question_choices(dict(q), lambda: rng.random()) for q in picked]

    record_served([q["id"] for q in picked])

    return {"items": items}
