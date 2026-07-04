from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from typing import Optional, List
import os, json, math, hashlib, time
from datetime import datetime, timezone

from .database import get_db, init_db
from .daily import build_daily_set, get_cached_daily, save_cached_daily
from .unlimited import build_unlimited_batch
from .models import Question, QuestionIn, StatsUpdateIn, DailySession, VALID_SECTIONS

app = FastAPI(title="JLPT N4 Daily Drill API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    init_db()

# ── Serve frontend ────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"))

# ── Questions ─────────────────────────────────────────────────────────────────
@app.get("/api/questions", response_model=List[Question])
def list_questions(section: Optional[str] = None, limit: int = Query(100, le=500)):
    db = get_db()
    try:
        if section:
            rows = db.execute(
                "SELECT * FROM questions WHERE section=? LIMIT ?", (section, limit)
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM questions LIMIT ?", (limit,)).fetchall()
        return [_row_to_question(r) for r in rows]
    finally:
        db.close()


@app.post("/api/questions", response_model=Question, status_code=201)
def add_question(body: QuestionIn):
    db = get_db()
    try:
        # check duplicate id
        exists = db.execute("SELECT id FROM questions WHERE id=?", (body.id,)).fetchone()
        if exists:
            raise HTTPException(400, f"Question id '{body.id}' already exists")
        db.execute(
            """INSERT INTO questions
               (id,section,level,instruction,passage,question,furigana,choices,answer,explanation,translation)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                body.id, body.section, body.level, body.instruction,
                body.passage or "", body.question, body.furigana or "",
                json.dumps(body.choices, ensure_ascii=False),
                body.answer, body.explanation or "", body.translation or "",
            ),
        )
        db.commit()
        row = db.execute("SELECT * FROM questions WHERE id=?", (body.id,)).fetchone()
        return _row_to_question(row)
    finally:
        db.close()


@app.post("/api/questions/bulk", status_code=201)
def bulk_add_questions(body: List[QuestionIn]):
    db = get_db()
    inserted, skipped = 0, 0
    try:
        for q in body:
            exists = db.execute("SELECT id FROM questions WHERE id=?", (q.id,)).fetchone()
            if exists:
                skipped += 1
                continue
            db.execute(
                """INSERT INTO questions
                   (id,section,level,instruction,passage,question,furigana,choices,answer,explanation,translation)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    q.id, q.section, q.level, q.instruction,
                    q.passage or "", q.question, q.furigana or "",
                    json.dumps(q.choices, ensure_ascii=False),
                    q.answer, q.explanation or "", q.translation or "",
                ),
            )
            inserted += 1
        db.commit()
        return {"inserted": inserted, "skipped": skipped}
    finally:
        db.close()


@app.delete("/api/questions/{question_id}", status_code=204)
def delete_question(question_id: str):
    db = get_db()
    try:
        res = db.execute("DELETE FROM questions WHERE id=?", (question_id,))
        db.commit()
        if res.rowcount == 0:
            raise HTTPException(404, "Question not found")
    finally:
        db.close()


# ── Daily set ─────────────────────────────────────────────────────────────────
@app.get("/api/daily")
def get_daily(date: Optional[str] = None):
    """
    Returns today's 50-question set.
    Cached per calendar-date so the same seed is used all day.
    """
    today = date or _today_key()
    cached = get_cached_daily(today)
    if cached:
        return cached

    db = get_db()
    try:
        rows = db.execute("SELECT * FROM questions").fetchall()
    finally:
        db.close()

    if not rows:
        raise HTTPException(404, "No questions in database yet. POST some via /api/questions/bulk")

    questions = [_row_to_question(r) for r in rows]
    stats = _load_all_stats()
    daily = build_daily_set(today, questions, stats)
    save_cached_daily(today, daily)
    return daily


# ── Unlimited mode ────────────────────────────────────────────────────────────
@app.get("/api/unlimited/next")
def get_unlimited_next(count: int = Query(8, ge=1, le=20), section: Optional[str] = None):
    """
    Returns a fresh, never-cached batch of questions for endless drilling.

    Prioritizes never-seen questions first, then high-error-rate questions,
    while still letting long-mastered questions resurface over time via a
    recency-based score decay. Balances across the 5 question sections and
    avoids immediately repeating a small window of recently-served items.

    Query params:
      count   - how many questions to return (1-20, default 8)
      section - optional filter to one section (kanji, kanji_reverse,
                bunpou, kotoba, reading)
    """
    if section and section not in VALID_SECTIONS:
        raise HTTPException(400, f"section must be one of {sorted(VALID_SECTIONS)}")

    db = get_db()
    try:
        if section:
            rows = db.execute("SELECT * FROM questions WHERE section=?", (section,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM questions").fetchall()
    finally:
        db.close()

    if not rows:
        raise HTTPException(404, "No questions in database yet. POST some via /api/questions/bulk")

    questions = [_row_to_question(r).dict() for r in rows]
    stats = _load_all_stats()
    batch = build_unlimited_batch(questions, stats, count, section_filter=section)
    return batch


# ── Stats ─────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
def get_stats():
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM stats").fetchall()
        return {r["question_id"]: {
            "seen": r["seen"], "wrong": r["wrong"], "correct": r["correct"],
            "last": r["last"], "last_date": r["last_date"]
        } for r in rows}
    finally:
        db.close()


@app.post("/api/stats")
def update_stats(body: StatsUpdateIn):
    """
    Called by the frontend after each answer.
    body: { question_id, correct: bool, date: "YYYY-MM-DD" }
    """
    db = get_db()
    try:
        row = db.execute("SELECT * FROM stats WHERE question_id=?", (body.question_id,)).fetchone()
        if row:
            seen    = row["seen"] + 1
            wrong   = row["wrong"] + (0 if body.correct else 1)
            correct = row["correct"] + (1 if body.correct else 0)
            db.execute(
                "UPDATE stats SET seen=?,wrong=?,correct=?,last=?,last_date=? WHERE question_id=?",
                (seen, wrong, correct, "o" if body.correct else "x", body.date, body.question_id)
            )
        else:
            db.execute(
                "INSERT INTO stats (question_id,seen,wrong,correct,last,last_date) VALUES (?,?,?,?,?,?)",
                (body.question_id, 1, 0 if body.correct else 1, 1 if body.correct else 0,
                 "o" if body.correct else "x", body.date)
            )
        db.commit()
        # invalidate today's daily cache so next /api/daily re-weights
        # (unlimited mode is never cached, so nothing to invalidate there)
        _invalidate_daily_cache(body.date)
        return {"ok": True}
    finally:
        db.close()


@app.delete("/api/stats", status_code=204)
def reset_stats():
    db = get_db()
    try:
        db.execute("DELETE FROM stats")
        db.commit()
        _invalidate_daily_cache(_today_key())
    finally:
        db.close()


# ── Pool info ─────────────────────────────────────────────────────────────────
@app.get("/api/pool")
def pool_info():
    db = get_db()
    try:
        total = db.execute("SELECT COUNT(*) as c FROM questions").fetchone()["c"]
        by_section = db.execute(
            "SELECT section, COUNT(*) as c FROM questions GROUP BY section"
        ).fetchall()
        return {"total": total, "by_section": {r["section"]: r["c"] for r in by_section}}
    finally:
        db.close()


# ── helpers ───────────────────────────────────────────────────────────────────
def _row_to_question(r) -> Question:
    return Question(
        id=r["id"], section=r["section"], level=r["level"],
        instruction=r["instruction"], passage=r["passage"],
        question=r["question"], furigana=r["furigana"],
        choices=json.loads(r["choices"]),
        answer=r["answer"], explanation=r["explanation"],
        translation=r["translation"],
    )


def _today_key() -> str:
    d = datetime.now(timezone.utc)
    return f"{d.year}-{str(d.month).zfill(2)}-{str(d.day).zfill(2)}"


def _load_all_stats() -> dict:
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM stats").fetchall()
        return {r["question_id"]: {
            "seen": r["seen"], "wrong": r["wrong"], "correct": r["correct"],
            "last_date": r["last_date"],
        } for r in rows}
    finally:
        db.close()


def _invalidate_daily_cache(date_key: str):
    db = get_db()
    try:
        db.execute("DELETE FROM daily_cache WHERE date_key=?", (date_key,))
        db.commit()
    finally:
        db.close()
