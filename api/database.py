import libsql
import os


def get_db():
    url = os.environ["TURSO_DATABASE_URL"]
    token = os.environ["TURSO_AUTH_TOKEN"]
    return libsql.connect(url, auth_token=token)


def init_db():
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id          TEXT PRIMARY KEY,
                section     TEXT NOT NULL,
                level       TEXT NOT NULL DEFAULT 'N4',
                instruction TEXT NOT NULL DEFAULT '',
                passage     TEXT NOT NULL DEFAULT '',
                question    TEXT NOT NULL,
                furigana    TEXT NOT NULL DEFAULT '',
                choices     TEXT NOT NULL,
                answer      INTEGER NOT NULL,
                explanation TEXT NOT NULL DEFAULT '',
                translation TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                question_id TEXT PRIMARY KEY,
                seen        INTEGER NOT NULL DEFAULT 0,
                wrong       INTEGER NOT NULL DEFAULT 0,
                correct     INTEGER NOT NULL DEFAULT 0,
                last        TEXT,
                last_date   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_cache (
                date_key    TEXT PRIMARY KEY,
                payload     TEXT NOT NULL,
                created_at  INTEGER NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_questions_section ON questions(section)"
        )
        conn.commit()
    finally:
        conn.close()
