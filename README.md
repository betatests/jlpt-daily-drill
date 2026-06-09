# JLPT N4 Daily Drill — FastAPI + Vercel

A full-stack JLPT N4 quiz app: **FastAPI** backend + plain HTML/JS frontend,
deployable on the **Vercel free tier**.

---

## Project structure

```
jlpt-api/
├── api/
│   ├── __init__.py
│   ├── index.py        ← FastAPI app (Vercel entry point)
│   ├── database.py     ← SQLite helpers + schema
│   ├── daily.py        ← Weighted daily-set builder
│   └── models.py       ← Pydantic schemas
├── static/
│   └── index.html      ← Frontend (calls the API)
├── seed_db.py          ← One-time DB seeder
├── requirements.txt
├── vercel.json
└── README.md
```

---

## ⚠️ Vercel free tier — SQLite caveat

Vercel's serverless functions use **ephemeral `/tmp` storage** (512 MB, reset on
cold start). This means:

- The SQLite DB lives in `/tmp/jlpt.db` and **is wiped between deployments or
  after inactivity.**
- For a personal/study tool this is fine — just re-seed after a cold start.
- For persistent production data, swap `database.py` to use a hosted DB such as
  **Turso** (SQLite-compatible, free tier), **PlanetScale**, or **Supabase**.

---

## Local development

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Seed the database
python seed_db.py          # writes to /tmp/jlpt.db by default
# or:
DB_PATH=./dev.db python seed_db.py

# 3. Run the server
DB_PATH=./dev.db uvicorn api.index:app --reload --port 8001

# 4. Open http://localhost:8001
```

---

## Deploy to Vercel

```bash
# Install Vercel CLI (once)
npm i -g vercel

# Inside the project root:
vercel          # follow prompts; choose "Other" framework
vercel --prod   # promote to production
```

After deploy, seed the remote DB via the bulk endpoint:

```bash
# export the QUESTIONS array from seed_db.py to a JSON file, then:
curl -X POST https://<your-app>.vercel.app/api/questions/bulk \
  -H "Content-Type: application/json" \
  -d @questions.json
```

Or hit the `/api/questions/bulk` endpoint from the Swagger UI at
`https://<your-app>.vercel.app/docs`.

---

## API reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/daily` | Get today's 50 questions (cached per day) |
| `GET` | `/api/stats` | Get all answer statistics |
| `POST` | `/api/stats` | Record one answer `{question_id, correct, date}` |
| `DELETE` | `/api/stats` | Reset all stats |
| `GET` | `/api/questions` | List questions (optional `?section=kanji`) |
| `POST` | `/api/questions` | Add a single question |
| `POST` | `/api/questions/bulk` | Add many questions at once |
| `DELETE` | `/api/questions/{id}` | Delete a question |
| `GET` | `/api/pool` | Question count by section |

Interactive docs: `GET /docs`

---

## Question schema

```json
{
  "id":          "k001",
  "section":     "kanji",           // kanji | kanji_reverse | bunpou | kotoba | reading
  "level":       "N4",
  "instruction": "＿＿の ことばは ひらがなで どう かきますか。",
  "passage":     "",                // reading passages go here
  "question":    "友だちと 【映画】 を 見ました。",
  "furigana":    "ともだちと えいが を みました。",
  "choices":     ["えいが", "えいか", "えが", "えいかく"],
  "answer":      0,                 // 0-based index into choices
  "explanation": "映画 dibaca えいが …",
  "translation": "Saya menonton film …"
}
```

Daily composition: **10 kanji + 10 kanji_reverse + 10 bunpou + 15 kotoba + 5 reading = 50**

---

## Weighted selection algorithm

Mirrors the original JS exactly (Efraimidis–Spirakis reservoir sampling):

```
weight = 1 + 3×(wrong/seen) + 1.5×(1 if ever wrong) + 0.6×(1 if never seen)
key    = random() ^ (1/weight)     # higher weight → key closer to 1
pick top-n by key per section
```

The same date seed is used every time for the same day, so the set is stable
across page reloads. Stats updates invalidate the cache so the next `/api/daily`
call re-weights.

---

## Persistent DB (recommended for production)

Replace `database.py` with a **Turso** client — it's SQLite over HTTPS and has
a generous free tier:

```bash
pip install libsql-experimental
```

```python
import libsql_experimental as libsql
conn = libsql.connect("your-db.turso.io", auth_token="...")
```
