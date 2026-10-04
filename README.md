# AI Research and Outreach Framework

This application coordinates evidence-based discovery and outreach workflows for students. AI interest is classified only from preserved source evidence; degree, branch, institution, and generic programming skills do not establish AI interest. Use only public or authorized sources. Do not bypass authentication, CAPTCHAs, access controls, or privacy controls, and do not collect private contact information.

## Architecture

FastAPI → workflow controls → specialized agents → tools → public/authorized sources. Crawl4AI is a tool invoked by `StudentCrawlerAgent`; it is not an agent. The workflow preserves final-year and AI-interest classifications, provenance, explainable matches, human approval, opt-out handling, and audit history.

The current application wires `MockCrawl4AITool` and `MockEmailProvider`. The provider does not send messages. The workflow pauses for explicit human approval before outreach. The crawler implementation remains allowlist and permission controlled; this Step 11 app factory runs its mock tool.

### Demo and synthetic test data

The local application uses mock tools, but it does not seed or invent student profiles. `MockCrawl4AITool` returns a warning-marked non-student response; it must not be treated as evidence about a candidate. The React UI displays data returned by the API and has no synthetic-candidate seed route. Its outreach view labels the `MockEmailProvider`; no real email transport is configured.

The complete synthetic workflow fixture is test-only in `tests/test_postgres_persistence.py`. It substitutes an explicitly named synthetic profile and synthetic example-domain evidence for extraction, then exercises the normal validation, matching, approval, mock outreach, and persistence workflow. The PostgreSQL integration test creates a unique temporary schema and drops that schema at cleanup; it does not delete or reset existing database schemas or tables. Use a database role permitted to create and drop schemas, and preferably point the integration test at a disposable database. These synthetic records are never presented as real candidates in the application UI. Real workflows still require evidence from public or authorized sources, and remain subject to explicit human email-draft approval.

## Stack

Python, FastAPI, PostgreSQL, SQLAlchemy 2.x async, Alembic, psycopg 3, Pydantic Settings, Crawl4AI, and pytest. DBeaver may be used to inspect PostgreSQL.

## Local setup

1. Install PostgreSQL and create a dedicated application database and role using your normal PostgreSQL administration workflow.
2. Create/activate a virtual environment and install the project dependencies:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and replace the safe `DATABASE_URL` placeholder with your own PostgreSQL connection URL. Do not commit `.env`. For example, the URL format is `postgresql+psycopg://<user>:<password>@<host>:5432/<database>`; percent-encode reserved characters in credentials. Set `ALLOWED_DOMAINS` to the domains approved for your use. An empty allowlist denies source crawling.
4. Apply the database migration:

   ```powershell
   alembic upgrade head
   ```

5. Start FastAPI:

   ```powershell
   uvicorn app.main:app --reload
   ```

   On Windows with the async psycopg driver, Uvicorn's default Proactor event loop cannot run psycopg async I/O. Use a SelectorEventLoop for local API development:

   ```powershell
   python -c "import asyncio,uvicorn; asyncio.run(uvicorn.Server(uvicorn.Config('app.main:app',host='127.0.0.1',port=8000,loop='none')).serve(), loop_factory=asyncio.SelectorEventLoop)"
   ```

## Production deployment preparation

The application can run on Linux with the existing FastAPI/PostgreSQL architecture. Deploy the API as an ASGI service, the built frontend as static assets, and PostgreSQL as a hosted PostgreSQL service. No cloud-provider-specific files or deployment credentials are included in this repository.

Configure these application settings in the backend hosting environment (keep secrets in the host's secret manager, not source control):

| Variable | Production use |
| --- | --- |
| `APP_NAME` | Optional API title; defaults to `AI Research and Outreach Framework`. |
| `DATABASE_URL` | Required PostgreSQL connection URL using `postgresql+psycopg://`; keep credentials secret and use the provider's TLS settings when required. |
| `APP_ENV` | Set to `production`; this disables the development-only localhost CORS default. |
| `CORS_ALLOWED_ORIGINS` | Comma-separated, exact HTTPS frontend origin(s), without paths. Wildcards and HTTP origins outside development are rejected. |
| `DEBUG` | Leave false in production. |
| `LOG_LEVEL` | Optional; defaults to `INFO` and logs to standard output. |
| `ALLOWED_DOMAINS` | Only needed if the real crawler is explicitly wired later. An empty value denies crawling. Restrict it to approved public/authorized source domains. |
| `CRAWL4_AI_BASE_DIRECTORY` | Optional; only needed if the real Crawl4AI adapter needs an explicit writable browser/cache directory. |

The root `.gitignore` excludes `.env` and `.env.*` files (while allowing the safe `.env.example` templates), including frontend-local secret variants.

`PORT` is supplied by some hosting platforms; it is consumed by the shell in the startup command below, not by an application setting. In the frontend build environment, set the existing Vite variable `VITE_API_BASE_URL` to the assigned HTTPS API URL. Vite embeds that URL into the build, so rebuild when the API URL changes. The checked-in `frontend/.env.example` and localhost fallback are development-only; a production build requires `VITE_API_BASE_URL` and has no localhost fallback.

Run `alembic upgrade head` as a release/migration step with the hosted `DATABASE_URL` configured, before starting the API. The online Alembic environment reads `DATABASE_URL` (or the configured settings file); the existing migration chain targets PostgreSQL. Do not run downgrade as part of deployment. Keep the platform's database hostname, credentials, TLS options, and backup policy in its protected configuration.

On Linux, use Uvicorn's normal ASGI startup; the Windows SelectorEventLoop workaround is not required:

```sh
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
```

Build the static frontend with the actual assigned HTTPS API origin:

```sh
cd frontend
npm ci
VITE_API_BASE_URL="https://<assigned-api-host>" npm run build
```

Replace the angle-bracket placeholder with the real API origin in the deployment environment; do not commit it as a configuration value. Configure `CORS_ALLOWED_ORIGINS` on the backend to the exact HTTPS origin assigned to the frontend. Development retains `http://localhost:5173` by default. CORS does not allow credentials or wildcard origins.

The `/health` route is a lightweight liveness check and does not test PostgreSQL readiness. The service platform can use it to confirm the API process responds; database connectivity remains verified by normal database operations and migrations.

The current application factory still wires `MockCrawl4AITool` and `MockEmailProvider`; deployment does not enable live crawling or email. If the real Crawl4AI adapter is intentionally enabled later, it will require a Linux-compatible Playwright browser and system libraries. Install/check those during image or environment preparation with Crawl4AI's `crawl4ai-setup` (or its documented `python -m playwright install --with-deps chromium` fallback). The adapter must continue using approved domain allowlists and robots checks; it reports restricted access rather than bypassing it. See the [Crawl4AI installation guide](https://docs.crawl4ai.com/core/installation/) for current OS/browser setup details.

## Web frontend (Step 12A)

The React + TypeScript + Vite application lives in `frontend/` and reads workflow results only through FastAPI. It does not connect to PostgreSQL. Copy `frontend/.env.example` to `frontend/.env` to set `VITE_API_BASE_URL` (the local default is `http://localhost:8000`), then run:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Keep FastAPI running at `http://localhost:8000` in another terminal. In development mode, FastAPI allows the explicit `CORS_ALLOWED_ORIGINS` values plus `http://localhost:5173` by default. Outside development, only configured HTTPS origins are allowed. Build the frontend with `npm run build` from `frontend/`.

The UI includes dashboard/KPI, workflow list and details, structured candidate search, candidate evidence, email draft review/approval, outreach status, and analytics views. Email approval resumes the configured demo workflow; the only provider currently wired is `MockEmailProvider`, which does not send real email.

The app validates `DATABASE_URL` when a database operation is requested. A missing URL, placeholder URL, or unreachable/misconfigured PostgreSQL instance fails clearly; `/health` remains independent of database availability. No credentials are embedded in source code or documentation.

## Workflow API

- `POST /workflows` creates a workflow from structured `UserRequirement` criteria and explicitly supplied `SourceCandidate` records.
- `GET /workflows` and `GET /workflows/{workflow_id}` read durable execution state and results.
- `GET /workflows/{workflow_id}/steps` and `/audit` expose step and audit history.
- `POST /workflows/{workflow_id}/approvals` applies a human approve/edit/reject action. An edit leaves the workflow waiting for a subsequent explicit approval or rejection.
- `POST /workflows/{workflow_id}/cancel` cancels pending workflow work.
- `GET /workflows/observability/kpis` returns collected KPI aggregates.

Workflow snapshots and execution metadata are stored in PostgreSQL. Provenance, validation, matching, draft versions/approvals, outreach idempotency/events, opt-outs, follow-up plans, audit rows, and per-workflow KPI records have normalized relational projections. Workflow state reloads from its persisted snapshot after process restart.

## Migrations

```powershell
alembic upgrade head
alembic current
alembic downgrade -1
alembic upgrade head
```

The persistence migration is reversible. Do not edit PostgreSQL tables manually; use Alembic revisions.

For a local migration to Neon that must not use a localhost `.env`, run this single command from the repository root:

```powershell
python -m app.db.migrate_neon
```

It asks for the Neon `DATABASE_URL` with hidden terminal input, refuses hosts outside `*.neon.tech`, applies `alembic upgrade head`, and verifies `alembic_version` plus all SQLAlchemy application tables. The URL exists only in that process and is not written to `.env` or logged. Do not use this command with a local database URL.

`GET /health` is the application liveness check. `GET /health/database` performs a PostgreSQL `SELECT 1` and reports database reachability separately; it returns a safe 503 when the database is unavailable. Workflow/database failures also return a generic 503 while a redacted server-side diagnostic records the failure type, SQLSTATE when available, and safe driver detail.

## Tests

Run the general suite:

```powershell
pytest
```

The regular tests use deterministic local mocks and SQLite metadata checks. They do not establish that PostgreSQL is available. To run the PostgreSQL persistence/restart integration test, preferably create a disposable database and set `TEST_DATABASE_URL` to it, then run:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://<user>:<password>@<host>:5432/<database_test>"
pytest tests/test_postgres_persistence.py -v
```

That integration test creates an isolated, uniquely named temporary schema inside the configured database, runs the persistence/restart checks there, and drops only that temporary schema during cleanup. Existing schemas and tables are not reset. Run it only with a role allowed to create/drop schemas. It tests persistence across separately constructed orchestrators, not a real email provider; all provider calls remain mocked.

## Database troubleshooting

If the API returns HTTP 503 or Alembic reports a `DATABASE_URL` configuration error, check PostgreSQL service status, host/port, database name, role/password, and network access. Credentials are intentionally not guessed. The application does not change PostgreSQL authentication settings automatically.
