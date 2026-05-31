# Plum Claims Orchestration Platform

A production-grade, multi-agent health insurance claims adjudication platform with sub-second API latency, asynchronous processing, robust error degradation, and total mathematical explainability. The frontend UI/UX is built to mirror the design language and premium branding aesthetics of **Plum (https://www.plumhq.com/)**.

---

## 🌟 Key Capabilities

1.  **7-Agent Pipeline**: Specialized sequential workers (Document Classifier, Document Validator, Document Parser, Cross-Document Validator, Policy Rules Evaluator, Claim Adjudicator, Fraud Detector).
2.  **Asynchronous Task Queue**: Transitioned from a blocking synchronous pipeline to a background task flow using **Celery & Redis**.
3.  **Idempotency & Concurrency Guards**:
    - **API-Level Idempotency**: Enforces `X-Idempotency-Key` headers using Redis to safely handle frontend retry submissions.
    - **Distributed Lock**: Acquires a Redis lock on `member_id + date + category` during task processing to prevent double-submit payout races.
4.  **Celery Message Payload Optimization**: Intercepts upload base64 strings and saves file data to disk at `uploads/{claim_id}` prior to enqueueing, keeping Redis broker message sizes under 10KB.
5.  **Decision Gate — Human Review Queue**: Escales suspicious or warning-flagged claims (e.g. name fuzzy mismatch, branded pharmacy drugs, missing dental reports) to `/review`, pausing updates until a human auditor approves (with optional amount override) or denies the claim with mandatory notes.
6.  **Full Policy Rules Alignment**: Dynamic loading from `policy_terms.json` to evaluate annual/per-claim limits, waiting periods, dependency coverage, covered procedures, and alternative medicine registrations.
7.  **Database-Driven Fraud Check**: Fraud detector queries past claim records from PostgreSQL to verify same-day and monthly claims limits.

---

## 🏛️ System Architecture

```
                  ┌────────────────────────────────────────┐
                  │            Next.js Frontend            │
                  │            (Port 3000 / UI)            │
                  └───────┬────────────────────────▲───────┘
                          │                        │
             POST /claims │ (REST API)             │ GET /claims/[id] (2s Poll)
             (Idempotency)│                        │ (Awaiting Review status)
                          ▼                        │
                  ┌────────────────────────────────┴───────┐
                  │            FastAPI Backend             │
                  │              (Port 8000)               │
                  └───────┬────────────────────────┬───────┘
                          │                        │
         Enqueue Celery   │                        │ SQLAlchemy Async
         Task with        │                        │ (PostgreSQL/Supabase)
         URL/File Path    ▼                        ▼
                  ┌──────────────┐         ┌──────────────┐
                  │ Redis Broker │         │  Postgres DB │
                  │  & Locks     │         │  (Port 5432) │
                  └───────┬──────┘         └───────▲──────┘
                          │                        │
                          │ Celery Workers         │ Transaction Pool
                          ▼                        │ (asyncpg)
                  ┌────────────────────────────────┴───────┐
                  │            Celery Worker               │
                  │         (7-Agent Pipeline)             │
                  └────────────────────────────────────────┘
```

---

## 🛠️ Installation & Setup

### Prerequisites
*   Node.js (v18+)
*   Docker & Docker Compose
*   Python (3.11 or 3.12 recommended - *only required if running locally without Docker*)

### ⚡ Method A: Fully Local Containerized Setup (Recommended for Quick Start)
Launches the entire stack including a **local PostgreSQL database** — no external accounts needed:

1. Create a `.env` configuration file in `backend/`:
   ```bash
   cp backend/.env.example backend/.env
   ```
2. Configure AI credentials in `backend/.env` (e.g., `GOOGLE_API_KEY`). Leave `DATABASE_URL` as default.
3. From the root directory, start all services:
   ```bash
   docker compose up --build -d
   ```
This boots **Postgres + Redis + FastAPI + Celery Worker** with hot-reloading enabled. Any local code edits instantly reflect inside the containers!

---

### ☁️ Method B: Cloud Database Setup (Supabase / Production)
If you have a **Supabase** (or other cloud PostgreSQL) account and want to use it instead of a local database:

1. Create a `.env` configuration file in `backend/`:
   ```bash
   cp backend/.env.example backend/.env
   ```
2. In `backend/.env`, set `DATABASE_URL` to your Supabase connection string:
   ```env
   DATABASE_URL=postgresql+asyncpg://postgres:<password>@db.<project-id>.supabase.co:5432/postgres
   ```
3. Use the **production compose file** which skips the local Postgres container:
   ```bash
   docker compose -f docker-compose.prod.yml up --build -d
   ```
This boots only **Redis + FastAPI + Celery Worker** — the database is served by Supabase in the cloud.

---

### 🪵 Method C: Local Python Environment Setup
If you prefer running the Python servers directly on your machine instead of Docker:

1. **Start Database & Broker Setup (Docker)**
   Start only PostgreSQL 15 and Redis containers:
   ```bash
   docker compose up db redis -d
   ```

2. **Backend Setup**
   Navigate to the `backend/` directory, create a virtual environment, and install dependencies:
   ```bash
   cd backend
   uv venv venv-uv --python 3.12
   source venv-uv/bin/activate
   uv pip install -r requirements.txt
   ```
   Create your `.env` configuration file:
   ```bash
   cp .env.example .env
   ```
   Launch the FastAPI application:
   ```bash
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

3. **Celery Worker Setup**
   Open a new terminal window, navigate to `backend/`, activate your venv, and run:
   ```bash
   source venv-uv/bin/activate
   python -m celery -A app.tasks.celery_app worker --loglevel=info
   ```

---

### 4. Frontend Setup
Navigate to the `frontend/` directory, install dependencies, and start the development server:
```bash
cd frontend
npm install
npm run dev -- --port 3000
```
Open `http://localhost:3000` in your web browser.

---

## 🧪 Verification & Testing

### 1. Automated Test Suite (All Unit & Integration Tests)
To run the full unit and integration test suite (includes tests for Celery payload offloading, distributed locking, failover clients, and decimal calculations):
```bash
cd backend
source venv-uv/bin/activate
pytest
```

### 2. Evaluation Suite (Verify All 20 Cases)
To run the evaluation suite verifying all 20 test cases:
```bash
cd backend
source venv-uv/bin/activate
python test_eval.py
```
**Expected Outcome**: `Final Score: 20/20 (Pass Rate 100%)`.

Alternatively, navigate to the **Evaluation Suite** (`/eval`) on the Next.js frontend to run and inspect all 20 test case execution traces inline in real-time.
