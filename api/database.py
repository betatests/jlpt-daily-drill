import libsql
import os


class _Row:
    """Wraps a tuple row to support column-name access (like sqlite3.Row)."""
    def __init__(self, row, columns):
        self._row = row
        self._map = dict(zip(columns, row))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._row[key]
        return self._map[key]


class _Cursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self._columns = [c[0] for c in (cursor.description or [])]

    def fetchone(self):
        row = self._cursor.fetchone()
        return _Row(row, self._columns) if row is not None else None

    def fetchall(self):
        return [_Row(row, self._columns) for row in self._cursor.fetchall()]

    @property
    def rowcount(self):
        return self._cursor.rowcount


class _Connection:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return _Cursor(self._conn.execute(sql, params))

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_db() -> _Connection:
    url = os.environ["TURSO_DATABASE_URL"]
    token = os.environ["TURSO_AUTH_TOKEN"]
    return _Connection(libsql.connect(url, auth_token=token))


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS unlimited_recent (
                seq         INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id TEXT NOT NULL,
                served_at   INTEGER NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_questions_section ON questions(section)"
        )
        conn.commit()
    finally:
        conn.close()
