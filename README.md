# AgricultureCloud

Crop decision support from soil and climate data.

A farmer enters seven readings (soil nitrogen, phosphorus, potassium and pH; temperature,
humidity and rainfall). AgricultureCloud recommends a crop, shows how confident the model is,
lists alternatives, compares every reading with what the crop typically needs, and saves the
analysis so readings can be tracked over time.

> **Advisory only.** The recommendation comes from a statistical model that sees seven numbers.
> It is one input to a decision, not a guarantee of any outcome. See [Known limitations](#known-limitations).

## Contents

- [Problem being solved](#problem-being-solved)
- [Features](#features)
- [Architecture](#architecture)
- [Technology stack](#technology-stack)
- [Directory structure](#directory-structure)
- [Installation](#installation)
- [Environment variables](#environment-variables)
- [MongoDB setup](#mongodb-setup)
- [Weather API setup](#weather-api-setup)
- [Running locally](#running-locally)
- [Running in production](#running-in-production)
- [Running migrations](#running-migrations)
- [Running backups](#running-backups) and [restoring](#restoring-backups)
- [Running tests](#running-tests)
- [API overview](#api-overview)
- [ML model information](#ml-model-information)
- [Known limitations](#known-limitations)
- [Security notes](#security-notes)
- [Future roadmap](#future-roadmap)

## Problem being solved

"Enter seven numbers, get a crop name" is not enough to act on. A grower also needs to know why
that crop, how sure the model is, what else could work, which readings are a problem, and what
could be improved. AgricultureCloud answers those questions and is explicit about what it does
not know.

## Features

- **Ranked recommendation** – best-matching crop out of 22, with model confidence and up to three alternatives.
- **Explanation** – each reading compared with the typical range for the recommended crop, a soil pH class, and general suggestions for readings that are out of range.
- **Two kinds of result, kept apart** – the *machine-learning* recommendation and the *rule-based* reading comparison are labelled separately everywhere (UI and API).
- **Reliability warnings** – inputs outside the range the model was trained on are accepted but flagged; low-confidence and multi-year crops carry cautions.
- **Weather lookup** – temperature and humidity fetched through the backend (the API key never reaches the browser). The server records which values came from the lookup.
- **History and trends** – every analysis is saved; paginated history, reopen any analysis, sparkline trends for N, P, K and pH, delete.
- **Accounts** – signup, login, logout, optional password reset by email. Only name, email and password are collected.
- **Operations** – health endpoint, structured logs with request IDs, audit trail, per-request timing, versioned migrations, backup and restore.

## Architecture

```
Browser ──► React app (frontend/)            one origin, no CORS
              │  fetch /api/*  (session cookie + CSRF header)
              ▼
        Flask JSON API (backend/)
              │
   Route ─► Validation ─► Service ─► Repository ─► MongoDB
                             ├──────► ML model (scikit-learn, loaded once)
                             └──────► Weather provider (server-side only)
```

- **Routes** (`backend/app/routes`) only parse the request, call a service and shape the response.
- **Validation** (`backend/app/utils/validators.py`) is server-side and assumes hostile input. Input limits live in one place, `backend/app/models/features.py`, and are served to the front end through `GET /api/config`, so browser and server validation cannot drift apart.
- **Services** (`backend/app/services`) hold the logic: prediction, crop reference data, rule-based soil interpretation, weather client, auth, audit, backup.
- **Repositories** (`backend/app/repositories`) are the only code that queries MongoDB. Every prediction query is scoped by the owner's user ID.
- **Same-origin by design.** In development Vite serves the pages and proxies `/api` to Flask. In production one reverse proxy fronts both, or Flask serves the compiled React files itself. Either way the browser sees one origin, so the session cookie stays `SameSite=Lax` and no CORS policy is needed.

Every API response uses one envelope:

```json
{ "success": true,  "data": { }, "message": "Prediction generated successfully" }
{ "success": false, "error": { "code": "INVALID_INPUT", "message": "…", "details": { } }, "request_id": "…" }
```

## Technology stack

| Layer | Technology |
|---|---|
| Front end | React 19, React Router 7, Vite 8, plain CSS (no UI framework, no CDN) |
| Back end | Python 3.12, Flask 3.1, Flask-WTF (CSRF), Flask-Limiter (rate limits), Waitress (WSGI) |
| Database | MongoDB 7 with PyMongo |
| ML | scikit-learn 1.6.1 soft-voting blend (ExtraTrees + logistic regression), loaded with joblib |
| Weather | OpenWeatherMap "current weather" API (optional) |
| Tests | pytest + mongomock (backend), Vitest + Testing Library (front end) |

## Directory structure

```
agriculture_website/
├── backend/                         Flask JSON API
│   ├── app/
│   │   ├── __init__.py              application factory
│   │   ├── config.py                development / testing / production settings
│   │   ├── db.py                    lazy MongoDB connection, collection names
│   │   ├── errors.py                error types + centralized handlers
│   │   ├── observability.py         request IDs and timing
│   │   ├── logging_config.py        JSON logs with secret redaction
│   │   ├── routes/                  auth, prediction, weather, health, main, frontend
│   │   ├── services/                prediction, crop, soil, weather, auth, audit, mail, backup
│   │   ├── repositories/            user, prediction, password reset, logs
│   │   ├── models/                  feature definitions, value objects
│   │   ├── utils/                   validators, responses, security, payload
│   │   └── data/crop_details.json   reference data for all 22 crops
│   ├── migrations/                  001–004 and the runner
│   ├── scripts/                     migrate, backup, restore, api_stats, smoke test, weather stub
│   ├── tests/                       unit / api / integration
│   ├── crop_recommendation_model.pkl
│   ├── run.py
│   ├── requirements.txt             runtime dependencies
│   ├── requirements-dev.txt         + test dependencies
│   └── .env.example
├── frontend/                        React app
│   ├── src/
│   │   ├── api/client.js            fetch wrapper, CSRF handling, ApiError
│   │   ├── context/                 auth and configuration providers
│   │   ├── components/              layout and shared UI
│   │   ├── pages/                   Landing, Auth, Advisor, Result, History, NotFound
│   │   ├── lib/                     validation and formatting helpers
│   │   ├── styles/app.css           design system
│   │   └── test/                    Vitest suites
│   ├── index.html
│   ├── vite.config.js
│   └── package.json
├── docs/API.md                      full API reference
├── docker-compose.yml               local MongoDB
└── README.md
```

## Installation

Requirements: Python 3.12, Node.js 20.19 or newer, and MongoDB 7 (or Docker).

```bash
git clone <repository-url>
cd agriculture_website
```

Backend:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
copy .env.example .env            # Windows   (macOS/Linux: cp .env.example .env)
```

Front end:

```bash
cd frontend
npm install
```

`scikit-learn` is pinned to 1.6.1, the version the bundled model was trained with. Do not change
it without retraining or re-exporting the model.

## Environment variables

Set these in `backend/.env` (never commit that file).

| Variable | Required | Purpose |
|---|---|---|
| `FLASK_ENV` | no | `development` (default), `testing` or `production` |
| `SECRET_KEY` | **production** | Signs sessions and CSRF tokens. Random, at least 32 characters. Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `MONGODB_URI` | **production** | Connection string; the database name is taken from its path. Development defaults to `mongodb://127.0.0.1:27017/agriculture_db` |
| `WEATHER_API_KEY` | no | OpenWeatherMap key. Without it the weather lookup is disabled and climate values are typed in |
| `AUTO_MIGRATE` | no | Apply pending migrations at startup. On in development, off in production |
| `APP_BASE_URL` | no | Public address of the site, used in password-reset links |
| `ALLOWED_HOSTS` | no | Comma-separated host names the site is served from (recommended in production) |
| `TRUSTED_PROXY_COUNT` | no | Number of reverse proxies in front of the app, for correct client IPs |
| `RATELIMIT_STORAGE_URI` | no | Shared rate-limit store when running several workers (e.g. `redis://…`). Default is in-memory |
| `MAIL_BACKEND` | no | `disabled`, `smtp`, or `console` (development only) – see [Security notes](#security-notes) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`, `MAIL_FROM` | with `smtp` | Mail server for password-reset email |
| `FRONTEND_DIST` | no | Folder of the compiled React app. Default `../frontend/dist` |
| `BACKUP_DIR` | no | Where backups are written. Default `backend/backups` |
| `LOG_LEVEL` | no | Default `INFO` |

Startup fails with a clear message if a required value is missing, too short, or still a
placeholder copied from `.env.example`. Optional services degrade instead: no weather key means
manual entry; no mail backend means the password-reset feature is absent.

## MongoDB setup

**Local, with Docker** (from the project root):

```bash
docker compose up -d
```

This starts MongoDB 7 on `127.0.0.1:27017` with data kept in a named volume. The default
`MONGODB_URI` already points at it.

**MongoDB Atlas:** create a cluster and a database user, allow your server's IP address, and set

```
MONGODB_URI=mongodb+srv://<username>:<password>@<cluster-host>/agriculture_db?retryWrites=true&w=majority
```

Collections (each exists for a reason):

| Collection | Purpose |
|---|---|
| `users` | accounts (name, email, password hash, session version) |
| `predictions` | every saved analysis |
| `password_reset_tokens` | hashed, single-use reset tokens (expire automatically) |
| `audit_logs` | security and business events |
| `api_logs` | per-request timing (expires after 14 days) |
| `migration_history` | which migrations ran, when, and with what result |
| `backups` | metadata about each backup |

## Weather API setup

1. Create a free account at <https://openweathermap.org/api> and copy your API key.
2. Put it in `backend/.env` as `WEATHER_API_KEY=…` and restart the backend.

New keys can take up to a couple of hours to activate; until then the provider rejects them and
the app reports the lookup as unavailable while manual entry keeps working.

The lookup fills in **temperature and humidity only**. Rainfall is deliberately not filled in:
the provider reports rain for the last hour, while the model expects typical rainfall for the
growing period.

To try the lookup without a key, run the local stand-in:

```bash
cd backend
python scripts/dev_weather_stub.py
```

and start the backend with `WEATHER_API_KEY=local-dev-key` and
`WEATHER_API_BASE_URL=http://127.0.0.1:5055/weather`.

## Running locally

Start three things: the database, the API, and the React dev server.

```bash
docker compose up -d
```

```bash
cd backend
python run.py
```

```bash
cd frontend
npm run dev
```

Open <http://localhost:5173>. The dev server forwards `/api` and `/health` to the backend on
port 5000. In development the backend applies database migrations automatically at startup.

To get sample data (a demo account and a dozen analyses) into a **development** database, run
this while the backend is up:

```bash
cd backend
python scripts/live_smoke_test.py
```

The demo credentials are in `backend/tests/fixtures/demo_user.json`.

## Running in production

```bash
cd frontend
npm run build
```

```bash
cd backend
python scripts/migrate.py up
```

```bash
cd backend
waitress-serve --listen=0.0.0.0:8000 run:app
```

With `frontend/dist` present, the backend serves the site and the API from one process. Set
`FLASK_ENV=production`, a real `SECRET_KEY`, `MONGODB_URI`, `ALLOWED_HOSTS` and `APP_BASE_URL`,
and put TLS in front (the production configuration marks the session cookie `Secure` and sends
HSTS). `python run.py` refuses to start in production because it is the development server.

## Running migrations

MongoDB has no schema, but data and indexes still change in controlled steps.

```bash
cd backend
python scripts/migrate.py status
```

```bash
python scripts/migrate.py up --dry-run
```

```bash
python scripts/migrate.py up
```

```bash
python scripts/migrate.py down --to 2 --yes
```

| Version | What it does | Reversible |
|---|---|---|
| 001 | Creates all indexes | yes |
| 002 | Normalizes legacy users (lower-case email, `password` → `password_hash`) | yes |
| 003 | Upgrades legacy predictions to the current shape | yes |
| 004 | Deletes government ID and phone number fields from users | **no** |

Every run is recorded in `migration_history`, logged, and protected by a lock so two processes
cannot migrate at once. A migration that would permanently delete existing data (004) is not
applied until you pass `--allow-irreversible`; take a backup first.

## Running backups

```bash
cd backend
python scripts/backup_database.py --note "before upgrade"
```

```bash
python scripts/backup_database.py --list
```

Each backup is a new directory under `BACKUP_DIR` containing one compressed file per collection
and a manifest with SHA-256 checksums and index definitions. Backups never overwrite each other,
and each attempt (including failures) is recorded in the `backups` collection.

Backups contain personal data (names, emails, password hashes). Keep the directory private, out
of version control (it is git-ignored), and encrypted when stored off the server. For large
databases prefer `mongodump` or your host's snapshot feature.

## Restoring backups

```bash
cd backend
python scripts/restore_database.py --backup-id <id> --verify-only
```

```bash
python scripts/restore_database.py --backup-id <id> --confirm <database-name>
```

```bash
python scripts/restore_database.py --backup-id <id> --confirm <database-name> --drop-existing
```

Checksums are verified before anything changes. You must confirm with the exact database name.
Collections that already hold data are not replaced unless you pass `--drop-existing`, in which
case a safety backup of the current data is taken first and each collection is swapped in
atomically. Every attempt is written to the audit log.

## Running tests

Backend (no services needed; uses an in-memory database):

```bash
cd backend
python -m pytest
```

Backend against a real MongoDB (also runs the query-plan tests):

```bash
cd backend
set TEST_MONGODB_URI=mongodb://127.0.0.1:27017
python -m pytest
```

Each test uses its own throwaway database named `agri_test_<random>`. The suite never reads
`MONGODB_URI`, so it cannot touch development or production data.

Front end:

```bash
cd frontend
npm test
```

End to end over real HTTP, against a running development server:

```bash
cd backend
python scripts/live_smoke_test.py
```

## API overview

Full reference: [docs/API.md](docs/API.md).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | – | Readiness: application, database, model, migrations |
| GET | `/health/live` | – | Liveness |
| GET | `/api/config` | – | Input definitions and feature flags |
| GET | `/api/csrf-token` | – | CSRF token for the session |
| GET | `/api/auth/me` | – | Current session |
| POST | `/api/auth/signup` | – | Create an account |
| POST | `/api/auth/login` | – | Log in |
| POST | `/api/auth/logout` | – | Log out |
| POST | `/api/auth/forgot-password` | – | Request a reset link |
| GET / POST | `/api/auth/reset-password/<token>` | – | Check a link / set a new password |
| GET | `/api/weather?city=` | yes | Current temperature and humidity |
| POST | `/api/predictions` | yes | Run and save an analysis |
| GET | `/api/predictions` | yes | Paginated history |
| GET | `/api/predictions/trends` | yes | Soil reading trends |
| GET | `/api/predictions/<id>` | yes | One analysis |
| DELETE | `/api/predictions/<id>` | yes | Delete an analysis |

## ML model information

- **Model (v2.0):** a soft-voting blend of ExtraTrees and logistic regression, trained on noise-augmented data by `backend/scripts/train_model.py`, stored with its provenance, metrics and an "unusual input" reference set in `backend/crop_recommendation_model.pkl`. It replaces the original 100-tree RandomForest; `docs/MODEL_CARD.md` gives the evidence, the metrics and what they do and do not mean.
- **Independent agronomic check.** Temperature and pH are also compared with the absolute limits published in FAO's EcoCrop database (`backend/app/data/ecocrop_limits.json`, built by `scripts/build_ecocrop_limits.py`, UK Open Government Licence v3.0). If the model recommends a crop the readings could not support at all, the result says so and is shown as unreliable. Rainfall is not checked: EcoCrop gives annual totals and the model's input is on a different scale.
- **Unusual combinations.** Readings that are each possible but together resemble nothing in the training data (very high rainfall with very low humidity, say) are detected and the result carries a warning that it is unreliable. Uniformly random inputs are flagged about 98% of the time; about 1% of real training rows are.
- **Inputs (7):** N, P, K, temperature (°C), humidity (%), pH, rainfall (mm).
- **Outputs (22 classes):** rice, maize, chickpea, kidneybeans, pigeonpeas, mothbeans, mungbean, blackgram, lentil, pomegranate, banana, mango, grapes, watermelon, muskmelon, apple, orange, papaya, coconut, cotton, jute, coffee.
- **Loading:** once per process. The file is a pickle, so it is loaded only if its SHA-256 matches the pinned value (`MODEL_SHA256`); then its classes and feature order are checked against the application. A model that fails any check is reported as unavailable rather than used.
- **Confidence** is the model's class probability (how strongly its trees agree). It is presented as "model confidence", never as a chance of success.
- **Reference ranges.** `backend/app/models/features.py` records, for each input, the span of values in the public *Crop Recommendation* dataset whose 22 labels the model predicts. The tests confirm every split threshold in the model lies inside those spans. Values beyond them are clamped to the edge before the model sees them; such inputs are accepted but flagged.
- **Per-crop typical ranges** in `backend/app/data/crop_details.json` are approximate reference values. The tests confirm the model predicts each crop at the midpoint of its ranges. They drive the rule-based comparison only.

## Known limitations

- The model uses only seven readings. It knows nothing about market prices, seed or labour availability, irrigation capacity, groundwater, pests and diseases, soil texture, crop rotation, local regulations, farmer budget or future weather. The UI states this on the landing page, the advisor and every result.
- **Nutrient units are undocumented.** The reference dataset describes N, P and K as relative soil content without a unit. Soil reports that use a different scale (for example kg/ha) will often fall outside the model's reference range; the app flags this rather than converting.
- **Rainfall meaning is undocumented** in the reference data (values span roughly 20–300 mm). The app asks for typical rainfall for the growing period and never fills it from the weather lookup.
- Weather values are a snapshot of current conditions, not a seasonal average.
- Some crops need conditions the model has no input for (winter chilling for apple, altitude and shade for coffee). Their results carry a caution.
- Crop reference text and season notes are general and India-centric.
- Rule-based suggestions are deliberately general and never give quantities.
- **Not tested here:** the live OpenWeatherMap service (no API key was available; all provider behaviours are covered with a stand-in and mocks) and delivery through a real SMTP server (covered with a mocked mail server).
- History uses offset pagination, which is fine for per-user lists but would need cursor pagination for very large histories.
- Backups read collections one after another, so they are not a point-in-time snapshot under concurrent writes.
- The default rate-limit store is in-memory: limits are per process and reset on restart. Use a shared store with several workers.
- The interface is English only.

## Security notes

- **Passwords** are hashed with scrypt (per-password salt). Plain passwords are never stored or logged.
- **Sessions** are signed cookies: `HttpOnly`, `SameSite=Lax`, `Secure` in production, 12-hour lifetime. The session is re-issued at login (no fixation) and checked against the database on every authenticated request, so a password reset or account deletion invalidates existing sessions immediately.
- **CSRF**: every POST and DELETE needs the `X-CSRFToken` header, and bodies must be JSON. Logout is POST-only.
- **No CORS**: the API is same-origin only.
- **Rate limits**: login 20/min per IP plus 8 failed attempts per 15 minutes per account; signup 20/hour; predictions 30/min and weather 20/min per user; password reset 5/hour per IP and 3/hour per address.
- **Login failures are generic**, and the server does the same password-hashing work whether or not the account exists. The reset-request response is identical for known and unknown addresses.
- **Input**: all values are type-checked server-side (operator objects such as `{"$ne": null}` never reach a query); numbers must be plain finite decimals within fixed limits; request bodies are capped at 64 KB.
- **Access control**: every prediction query includes the owner's ID. Another user's record returns 404.
- **Headers**: strict Content-Security-Policy (no inline scripts or styles, no third-party hosts), `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, HSTS in production. The React build contains no inline code.
- **Weather key** stays on the server and is sent only over HTTPS. Provider errors are logged by type only, because exception text contains the request URL with the key.
- **Model file** is checksum-verified before unpickling.
- **Errors**: clients never see tracebacks, driver messages or upstream responses; logs carry the request ID.
- **Logs** are JSON; fields that look sensitive are redacted. Failed-login identifiers are stored only as a keyed hash.
- **Government ID** – decision: *removed*. The original signup collected a government ID and phone number in plain text, but nothing used them. They are no longer collected, and migration 004 deletes stored values.
- **Password reset** uses single-use tokens stored as SHA-256 hashes with a 30-minute expiry. With `MAIL_BACKEND=disabled` (the production default) the feature is absent and its endpoints return 404, so the app never pretends to send email. `console` writes the link to the log and is rejected in production.
- **Secrets** live only in `backend/.env`, which is git-ignored.

## Future roadmap

The service boundaries are meant to let these be added without restructuring: market price
information, fertiliser recommendations, disease detection, yield estimation, crop rotation
advice, irrigation advice, regional languages, soil report upload, and government scheme
information. Also planned: account settings (change password, delete account), seasonal climate
data in place of a current-weather snapshot, and a retrained model with documented units.
