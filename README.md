---

## Prerequisites

- **Python 3.12+** — [python.org](https://www.python.org/downloads/) (see note below on very new Python versions)
- **Node.js 18+** — [nodejs.org](https://nodejs.org/)
- **PostgreSQL 16 or 17** — [postgresql.org](https://www.postgresql.org/download/) (or via Docker — see below)
- **Ollama** — [ollama.com](https://ollama.com/) (for local AI inference)
- **Git**

> **Note on Python version:** some pinned dependencies (`psycopg2-binary`, `pgvector`, `sentence-transformers`) may not have prebuilt wheels for the very latest Python release yet, which can cause `pip install` to try building from source and fail. If you hit build errors, either pin the offending package to a newer version that does have a wheel for your Python, or use Python 3.11/3.12 instead of a bleeding-edge release.

---

## Option A — Run Locally (Manual Setup)

### Step 1 — Clone the repo

```bash
git clone https://github.com/P-lahari93/ai-coach.git
cd ai-coach
```

---

### Step 2 — Set up PostgreSQL

**Via Docker (recommended — simplest path):**

```bash
docker run --name ai-coach-db \
  -e POSTGRES_USER=aicoach \
  -e POSTGRES_PASSWORD=aicoach \
  -e POSTGRES_DB=aicoach \
  -p 5432:5432 \
  -d pgvector/pgvector:pg16
```

**Or a native install** — create the database and user manually:

```bash
psql -U postgres -h localhost

CREATE USER aicoach WITH PASSWORD 'aicoach';
CREATE DATABASE aicoach OWNER aicoach;
GRANT ALL PRIVILEGES ON DATABASE aicoach TO aicoach;
\q
```

---

### Step 3 — Set up the Backend

```bash
cd backend

python -m venv .venv

# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

#### Create your `.env` file

```bash
# Windows
copy .env.example .env
# macOS / Linux
cp .env.example .env
```

Edit `.env`:

```dotenv
APP_NAME=AI Coach
APP_VERSION=1.0.0
ENVIRONMENT=development
DEBUG=true
API_V1_PREFIX=/api/v1

# Generate a strong secret: python -c "import secrets; print(secrets.token_urlsafe(64))"
# Minimum 32 chars in development, but the app HARD-FAILS at startup if
# ENVIRONMENT=production and this is under 64 chars or a known placeholder.
SECRET_KEY=your-secret-key-at-least-32-characters-long
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

DATABASE_URL=postgresql+asyncpg://aicoach:aicoach@localhost:5432/aicoach
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20

OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma3:latest
OLLAMA_TIMEOUT=600
OLLAMA_MAX_TOKENS=2048
OLLAMA_TEMPERATURE=0.7

EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
EMBEDDING_DIMENSION=384
EMBEDDING_BATCH_SIZE=32

RAG_CHUNK_SIZE=512
RAG_CHUNK_OVERLAP=64
RAG_TOP_K=6
RAG_SCORE_THRESHOLD=0.35
RAG_TOKEN_BUDGET=2048

UPLOAD_DIR=uploads
MAX_UPLOAD_SIZE_MB=50
ALLOWED_UPLOAD_EXTENSIONS=[".pdf",".docx",".pptx",".txt",".md"]

# CORS — must NOT contain localhost or "*" if ENVIRONMENT=production
ALLOWED_ORIGINS=["http://localhost:5173","http://localhost:3000"]
```

#### Run database migrations

```bash
alembic upgrade head
```

This runs all 11 migrations, including enabling Row Level Security, and seeds:
- SBI and GROW coaching frameworks
- Framework steps and prompt templates
- Roles (superadmin, tenant_admin, program_owner, learner)
- Default tenant

If `pgvector` isn't installed in your Postgres, migration `009` logs a warning and falls back to `FLOAT[]` for embeddings — everything else still works, but HNSW-accelerated vector search is disabled until `CREATE EXTENSION vector;` is run.

#### Start the backend server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Backend: **http://localhost:8000**
- API docs (Swagger): **http://localhost:8000/docs**
- Health check: **http://localhost:8000/health**

---

### Step 4 — Set up Ollama (AI Engine)

```bash
ollama pull gemma3:latest
ollama serve
```

Ollama runs at **http://localhost:11434**.

> If Ollama is not running, coaching and roleplay completion will fail on the AI-generation step but the session still completes with a fallback "AI feedback unavailable" report rather than crashing.

---

### Step 5 — Set up the Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend: **http://localhost:5173**

---

### Step 6 — Open the App

1. Go to **http://localhost:5173**
2. Register → Login → land on the **Dashboard**

---

## Option B — Run with Docker Compose

```bash
git clone https://github.com/P-lahari93/ai-coach.git
cd ai-coach
docker-compose up --build
```

```bash
docker-compose exec backend alembic upgrade head
docker-compose exec ollama ollama pull gemma3:latest
```

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |
| Ollama | http://localhost:11434 |

---

## Running Tests

```bash
cd backend
pytest tests/integration/test_cross_tenant_isolation.py -v
```

This is the one test suite currently in the repo — it's a real regression guard, not a smoke test: it creates two separate tenants with real data and asserts, via actual HTTP calls, that one tenant can never read, list, update, or delete another tenant's coaching sessions, modules, or knowledge bases. It requires a running, migrated Postgres (see Step 2–3 above) and reuses whatever `DATABASE_URL`/`SECRET_KEY` are set in your environment — there's no isolated test database yet, so it writes real rows to whatever DB you point it at.

Broader unit/integration test coverage beyond this one suite doesn't exist yet — see Roadmap.

---

## Application Walkthrough

### Register & Login
- Visit `/register` to create an account
- Login at `/login` for JWT access + refresh tokens

### Dashboard
- Active sessions, completion rate, average score, recent feedback

### Modules
- Lists available coaching modules (SBI, GROW, and any custom ones)

### Coaching Session (SBI / GROW)
- Select a module → start a session
- Fill in the dynamic intake form (driven by the module's `intake_schema`)
- Submit → real, model-generated per-dimension rubric scoring (not keyword heuristics) → structured feedback report with strengths, improvements, recommendations
- Both the intake you submit and the AI's own output are checked by `SafetyEngine` before anything is stored

### Roleplay Session
- Select a module with a roleplay persona, converse turn-by-turn
- Complete the session → same real AI-scoring and safety-check pipeline as coaching sessions

### Knowledge Base
- Upload documents (PDF, DOCX, PPTX, TXT, MD) or paste text
- Chunked, embedded (off the event loop, in a worker thread), retrieved via RAG and cited in coaching prompts

### Analytics
- Session statistics, completion rates, score trends, module-level breakdown

---

## API Endpoints

All routes are prefixed with `/api/v1`. Full interactive docs at `/docs`.

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Register a new user |
| POST | `/auth/login` | Login, returns JWT tokens |
| POST | `/auth/refresh` | Refresh access token |
| POST | `/auth/logout` | Logout (revoke refresh token) |
| GET | `/auth/me` | Get current user profile |
| GET | `/modules` | List coaching modules (tenant-scoped + global) |
| GET | `/modules/{id}` | Get module detail + intake schema |
| PATCH | `/modules/{id}` | Update module (owning tenant only) |
| POST | `/modules/{id}/publish` | Publish module (owning tenant only) |
| POST | `/modules/{id}/archive` | Archive module (owning tenant only) |
| DELETE | `/modules/{id}` | Soft-delete module (owning tenant only) |
| POST | `/sessions/coaching` | Start a coaching session |
| GET | `/sessions/coaching/{id}` | Get session + intake_schema (owner or tenant admin only) |
| POST | `/sessions/coaching/{id}/complete` | Submit intake + generate real AI feedback |
| POST | `/sessions/coaching/{id}/abandon` | Abandon a session (owner only) |
| POST | `/sessions/roleplay` | Start a roleplay session |
| GET | `/sessions/roleplay/{id}` | Get roleplay session (owner only) |
| POST | `/sessions/roleplay/{id}/turn` | Submit a roleplay message (safety-checked both ways) |
| POST | `/sessions/roleplay/{id}/complete` | Complete roleplay + generate feedback |
| GET | `/feedback/{id}` | Get feedback report (owner only) |
| POST | `/feedback/{id}/rate` | Submit a 1–5 rating |
| GET | `/knowledge` | List knowledge bases (tenant-scoped) |
| POST | `/knowledge` | Create knowledge base |
| DELETE | `/knowledge/{id}` | Delete knowledge base (owning tenant only) |
| GET | `/knowledge/{id}/sources` | List sources in a KB (owning tenant only) |
| POST | `/knowledge/{id}/sources/text` | Ingest pasted text |
| POST | `/knowledge/{id}/sources/upload` | Upload and ingest a document |
| DELETE | `/knowledge/{id}/sources/{source_id}` | Delete a source (owning tenant only) |
| GET | `/analytics/dashboard` | Dashboard metrics |
| GET | `/progress/me` | User progress and achievements |

---

## Database Migrations

```bash
alembic upgrade head
alembic current
alembic downgrade -1
alembic downgrade base
```

### Migration history

| Version | Description |
|---|---|
| 001 | Create PostgreSQL extensions (uuid-ossp, pgvector) |
| 002 | Base tables (tenants, users) |
| 003 | RBAC tables (roles, permissions, user_roles) |
| 004 | Module tables (coaching_modules, module_versions, framework_steps, personas, prompt_templates, rubrics) |
| 005 | Knowledge tables (knowledge_bases, knowledge_sources, knowledge_chunks) |
| 006 | Session tables (coaching_sessions, conversation_messages, roleplay_sessions, roleplay_messages, feedback_reports) |
| 007 | Progress and gamification tables (user_progress, achievements, notifications) |
| 008 | Analytics tables, including the `audit_logs` table used by the safety engine (partitioned by month) |
| 009 | HNSW vector index (pgvector — skipped, with a `FLOAT[]` fallback, if the extension is unavailable) |
| 010 | Seed data (roles, default tenant, SBI/GROW modules) |
| 011 | **Actually enables Row Level Security.** Migration 010 originally defined but never invoked RLS policy creation — this migration fixes that, correctly handling tables whose tenancy is inherited via a parent record rather than their own `tenant_id` column. |

---

## Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` \| `staging` \| `production`. Only `production` triggers the startup hardening checks below. |
| `SECRET_KEY` | *(required)* | JWT signing secret — min 32 chars always; **min 64 chars required, and default/placeholder values rejected, when `ENVIRONMENT=production`** |
| `DEBUG` | `false` | Must be `false` when `ENVIRONMENT=production` — startup fails otherwise |
| `DATABASE_URL` | *(required)* | PostgreSQL async DSN |
| `ALLOWED_ORIGINS` | `["http://localhost:5173"]` | CORS allowed origins. Must not be empty, wildcarded, or contain localhost when `ENVIRONMENT=production` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `gemma3:latest` | LLM model name |
| `OLLAMA_TIMEOUT` | `600` | Request timeout in seconds |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Sentence transformer model |
| `EMBEDDING_DIMENSION` | `384` | Vector dimension |
| `RAG_TOP_K` | `6` | Number of chunks to retrieve |
| `UPLOAD_DIR` | `uploads` | Directory for uploaded files |
| `MAX_UPLOAD_SIZE_MB` | `50` | Maximum file size |

---

## Troubleshooting

**Backend won't start — `SECRET_KEY` error**
Generate one: `python -c "import secrets; print(secrets.token_urlsafe(64))"`

**Backend refuses to start with `ENVIRONMENT=production`**
This is intentional — the app hard-fails on a weak `SECRET_KEY`, `DEBUG=True`, or a localhost/wildcard CORS origin rather than booting insecurely. The error message lists every specific problem found; fix each one and restart.

**`alembic upgrade head` fails — connection refused**
```bash
psql -U aicoach -h localhost -d aicoach   # password: aicoach
```

**`alembic upgrade head` fails on migration 011 — `column "tenant_id" does not exist`**
This means the migration's assumptions about which tables carry their own `tenant_id` column vs. inherit tenancy from a parent don't match your actual schema (e.g. if you've made local schema changes). Check the affected table's real columns before adjusting the migration.

**AI feedback returns a fallback / "unavailable" message**
```bash
ollama serve
ollama pull gemma3:latest
```

**A request that should succeed returns an empty list or a 403/404**
Since RLS is fail-closed, this usually means the request's tenant context (JWT `tenant_id` claim) doesn't match the resource's actual tenant — this is enforcement working as intended, not a bug, but worth double-checking which tenant the calling user actually belongs to.

**Frontend shows blank page or API errors**
Confirm the backend is on port 8000 and `ALLOWED_ORIGINS` includes your frontend's origin.

**`npm install` fails**
```bash
node --version   # should be 18+
```

---

## Roadmap / Known Gaps

Documented honestly rather than left implicit:

- **Identity & access**: no password reset, email verification, user invitations, MFA, or SSO yet — self-registration with local password auth only.
- **Module lifecycle**: no author-time preview/dry-run before publish, no draft→review→publish approval gate, no version rollback UI, and no public API endpoint to create/publish a module version at all yet (the version-authoring workflow isn't wired up server-side).
- **Billing & cost tracking**: no per-tenant token/cost metering, no AI-endpoint-specific rate limiting.
- **Compliance**: no GDPR data export or compliant hard-delete; audit events are now written (see Security section) but there's no admin-facing UI to read/filter/export them yet.
- **Test coverage**: one integration test suite exists (cross-tenant isolation). No unit tests, no CI pipeline running tests automatically yet.
- **RLS coverage**: extends to session/module/knowledge-base domains; RBAC/auth-plumbing tables are deliberately not yet covered (see Security section for why).

---

## License

MIT