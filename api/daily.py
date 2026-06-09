"""
Daily set builder — mirrors the JS logic in the original HTML exactly.

Algorithm  (Efraimidis–Spirakis weighted reservoir sampling):
  weight = 1 + 3*(wrong/seen) + 1.5*(1 if ever_wrong) + 0.6*(1 if never_seen)
  key    = rng() ^ (1/weight)   # higher weight → key closer to 1 → ranks higher
  Take top-n by key per section.

Choices are shuffled deterministically per (date, question_id) so the
frontend always gets the same order for a given day.
"""

from __future__ import annotations
import json, math, hashlib, time
from typing import List, Dict, Any
from .database import get_db


# ── Composition plan (mirrors PLAN in the HTML) ───────────────────────────────
PLAN = [
    {"section": "kanji",         "n": 10, "label": "漢字 → よみ"},
    {"section": "kanji_reverse", "n": 10, "label": "よみ → 漢字"},
    {"section": "bunpou",        "n": 10, "label": "文法"},
    {"section": "kotoba",        "n": 15, "label": "語彙"},
    {"section": "reading",       "n":  5, "label": "読解"},
]
TOTAL = sum(p["n"] for p in PLAN)  # 50


# ── Deterministic PRNG (Mulberry32) ──────────────────────────────────────────
def _hash_str(s: str) -> int:
    h = 2166136261
    for ch in s.encode():
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _mulberry32(seed: int):
    """Returns a closure that produces deterministic floats in [0,1)."""
    state = [seed & 0xFFFFFFFF]

    def _next() -> float:
        state[0] = (state[0] + 0x6D2B79F5) & 0xFFFFFFFF
        t = state[0]
        t = ((t ^ (t >> 15)) * (1 | t)) & 0xFFFFFFFF
        t = (t + ((t ^ (t >> 7)) * (61 | t))) & 0xFFFFFFFF
        t ^= t >> 14
        return (t & 0xFFFFFFFF) / 4294967296.0

    return _next


def _shuffle(lst: list, rng) -> list:
    arr = lst[:]
    for i in range(len(arr) - 1, 0, -1):
        j = int(rng() * (i + 1))
        arr[i], arr[j] = arr[j], arr[i]
    return arr


# ── Weighted pick ─────────────────────────────────────────────────────────────
def _pick_section(pool: list, n: int, rng, stats: dict) -> list:
    if len(pool) <= n:
        return _shuffle(pool, rng)

    scored = []
    for q in pool:
        st = stats.get(q["id"], {"seen": 0, "wrong": 0, "correct": 0})
        seen  = st.get("seen", 0)
        wrong = st.get("wrong", 0)

        wrong_rate  = wrong / seen if seen else 0.0
        ever_wrong  = 1.0 if wrong > 0 else 0.0
        never_seen  = 0.6 if seen == 0 else 0.0

        weight = 1.0 + 3.0 * wrong_rate + 1.5 * ever_wrong + never_seen
        r = rng()
        # avoid log(0)
        r = max(r, 1e-10)
        key = r ** (1.0 / weight)
        scored.append((key, q))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [q for _, q in scored[:n]]


# ── Public entry point ────────────────────────────────────────────────────────
def build_daily_set(date_key: str, questions: list, stats: dict) -> dict:
    # group by section
    by_section: Dict[str, list] = {}
    for q in questions:
        by_section.setdefault(q["section"] if isinstance(q, dict) else q.section, []).append(
            q if isinstance(q, dict) else q.dict()
        )

    items = []
    weak  = 0

    for plan in PLAN:
        section = plan["section"]
        pool    = by_section.get(section, [])
        seed    = _hash_str(f"{date_key}|{section}")
        rng     = _mulberry32(seed)
        chosen  = _pick_section(pool, plan["n"], rng, stats)

        for q in chosen:
            st = stats.get(q["id"], {})
            if st.get("wrong", 0) > 0:
                weak += 1

            # shuffle choice order deterministically
            crng   = _mulberry32(_hash_str(f"{date_key}|{q['id']}|choices"))
            idx    = list(range(len(q["choices"])))
            idx    = _shuffle(idx, crng)
            answer_shown = idx.index(q["answer"])

            enriched = dict(q)
            enriched["_plan"]        = plan["label"]
            enriched["_choiceOrder"] = idx          # [shown_pos] -> original_idx
            enriched["_answerShown"] = answer_shown  # shown position of correct answer
            items.append(enriched)

    return {"date": date_key, "weak": weak, "items": items}


# ── Cache helpers (stored in SQLite daily_cache table) ────────────────────────
def get_cached_daily(date_key: str):
    db = get_db()
    try:
        row = db.execute(
            "SELECT payload FROM daily_cache WHERE date_key=?", (date_key,)
        ).fetchone()
        if row:
            return json.loads(row["payload"])
        return None
    finally:
        db.close()


def save_cached_daily(date_key: str, payload: dict):
    db = get_db()
    try:
        db.execute(
            "INSERT OR REPLACE INTO daily_cache (date_key, payload, created_at) VALUES (?,?,?)",
            (date_key, json.dumps(payload, ensure_ascii=False), int(time.time())),
        )
        db.commit()
    finally:
        db.close()
