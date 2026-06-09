import sqlite3
import os

# On Vercel, /tmp is the only writable directory at runtime.
# For local dev, fall back to a local file.
DB_PATH = os.environ.get("DB_PATH", "/tmp/jlpt.db")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS questions (
                id          TEXT PRIMARY KEY,
                section     TEXT NOT NULL,          -- kanji | kanji_reverse | bunpou | kotoba | reading
                level       TEXT NOT NULL DEFAULT 'N4',
                instruction TEXT NOT NULL DEFAULT '',
                passage     TEXT NOT NULL DEFAULT '',
                question    TEXT NOT NULL,
                furigana    TEXT NOT NULL DEFAULT '',
                choices     TEXT NOT NULL,          -- JSON array of strings
                answer      INTEGER NOT NULL,       -- index into choices (0-based)
                explanation TEXT NOT NULL DEFAULT '',
                translation TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS stats (
                question_id TEXT PRIMARY KEY,
                seen        INTEGER NOT NULL DEFAULT 0,
                wrong       INTEGER NOT NULL DEFAULT 0,
                correct     INTEGER NOT NULL DEFAULT 0,
                last        TEXT,                   -- 'o' or 'x'
                last_date   TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_cache (
                date_key    TEXT PRIMARY KEY,
                payload     TEXT NOT NULL,          -- JSON blob of the full daily response
                created_at  INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_questions_section ON questions(section);
        """)
        conn.commit()
    finally:
        conn.close()
