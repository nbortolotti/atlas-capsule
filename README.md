# Atlas: Synthetic Identity Capsule (Open Source SDK)

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![Google Antigravity SDK](https://img.shields.io/badge/Google%20Antigravity-SDK-4285F4.svg)](https://cloud.google.com)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

**Atlas** is an autonomous **Synthetic Identity Capsule** powered by the **Google Antigravity SDK** (`google-antigravity`) and Gemini models. It provides a governed, secure, and extensible operational harness for synthetic autonomous assistants across real-time communication channels (Google Chat, Gmail, and Google Calendar).

Atlas incorporates deterministic policy gates, granular service supervision locks, modular skill management, multi-calendar automations, and resilient long-term memory synchronization.

This repository contains the **clean, open-source backend core** designed for standalone deployment or integration without proprietary frontend dependencies or personal identity data.

---

## 🌟 Key Features

1. **Google Antigravity SDK Integration (`google-antigravity`)**:
   - High-order reasoning loops and persistent identity memory hydration via `LocalAgentConfig`.
   - Contextual prompt injection incorporating active identity profiles, behavioral directives, and specialized skills.
   - Built-in resilient fallback to direct Gemini client API (`_generate_with_genai_client`) if the Antigravity harness encounters network limits or timeout constraints.

2. **Autonomous Communication Connectors**:
   - **Google Chat**: Supports both webhook endpoints and polling loops, interactive thread context tracking, bot tag detection (`@Atlas`, `@Capsule`, `@bot`), and acknowledgement emoji indicators (`👀`).
   - **Gmail**: Inbound message evaluation, deterministic draft creation vs. autonomous delivery, and thread classification.
   - **Google Calendar**: Multi-calendar aggregation and live synchronization into conversational agent memory.

3. **Multi-Calendar Scheduled Automations Engine**:
   - Scheduled task routines (daily summaries, agenda reviews, weekly reminders) evaluated with local timezone awareness.
   - In-memory concurrency locks (`acquire_execution_lock` / `release_execution_lock`) preventing race conditions between the background worker loop and external triggers (e.g., Google Cloud Scheduler).

4. **Security & Human-in-the-Loop Governance ("Lock Principle")**:
   - **Deterministic Policy Gate**: Inbound message validation, prompt injection protection heuristics, blacklisted keywords, and configurable sender/domain allowlists.
   - **Service Supervision Locks**: Granular control over autonomous message delivery (`ALL` for mandatory human review/draft, `SPECIFIC` for designated supervised users, or fully unlocked autonomous sending).
   - **Budget & Consumption Safeguards**: Automated enforcement of hourly rate limits, total token caps, and USD budget boundaries.

5. **Dynamic Skills Architecture**:
   - Modular skill packages adhering to the standard Antigravity `SKILL.md` specification with YAML frontmatter.
   - Hot-reloadable skill state (activate, pause, inspect, create, or delete) via REST API without restarting containers or modifying code.

6. **Relational & Cloud Storage Memory**:
   - Relational database persistence (MySQL / Google Cloud SQL) for complete message audit trajectories, service lock rules, and administrative authentication.
   - Google Cloud Storage (GCS) synchronization for identity memory partitions, skills, and configuration backups.

---

## 📁 Repository Structure

```text
atlas-open-capsule/
├── config/
│   ├── identity_profile.yaml    # Public agent persona & signatures template
│   └── policies.yaml            # Deterministic policy rules and domain allowlists
├── src/
│   ├── auth/                    # Google OAuth2 credentials and token refresh
│   ├── connectors/              # Google Chat, Gmail, and Google Calendar connectors
│   ├── db/                      # SQLAlchemy database schemas, audit logs, and locks
│   ├── engine/                  # Antigravity agent harness, policy gate, skills, automations
│   ├── memory/                  # Google Cloud Storage (GCS) persistence manager
│   ├── models/                  # Pydantic data schemas and action enums
│   ├── main.py                  # FastAPI application entry point and worker lifecycle
│   └── version.py               # SemVer metadata and deployment diagnostics
├── tests/                       # Pytest test suite (63+ automated test cases)
├── scripts/                     # Helper CLI scripts (OAuth refresh token generation)
├── .agents/skills/              # Default seed skills for initial hydration
├── Dockerfile                   # Cloud Run / Docker container configuration
└── requirements.txt             # Python dependencies
```

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.11+
- Virtual environment tool (`venv`)
- Google Cloud project with Gemini API enabled (and optionally Gmail/Chat/Calendar APIs)

### 2. Installation

```bash
# Clone the repository
git clone https://github.com/your-org/atlas-open-capsule.git
cd atlas-open-capsule

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration

Create a `.env` file from the provided template:

```bash
cp .env.example .env
```

Key environment variables:
- `GEMINI_API_KEY`: API key for Gemini / Google Antigravity SDK.
- `AGENT_MODEL`: Active model identifier (e.g. `gemini-2.5-flash-lite`, `gemini-2.5-pro`).
- `DEFAULT_ADMIN_EMAIL`: Administrator email (default: `admin@example.com`).
- `ADMIN_DEFAULT_USERNAME` & `ADMIN_DEFAULT_PASSWORD`: Initial administrative credentials for protected endpoints.
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`: OAuth credentials for Gmail and Google Chat.
- `DB_USER`, `DB_PASS`, `DB_NAME`, `DB_HOST`, `DB_PORT`: Relational database connection parameters.
- `GCS_BUCKET_NAME`: Google Cloud Storage bucket for long-term memory synchronization.

### 4. Running the Application

```bash
# Start the FastAPI capsule service
uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload
```

Interactive OpenAPI documentation is available at:
`http://localhost:8080/docs`

---

## 🔌 Core REST API Endpoints

| Category | Method | Path | Description |
| :--- | :--- | :--- | :--- |
| **System** | `GET` | `/api/health` | Service health status and active version |
| **System** | `GET` | `/api/version` | Semantic version details, commit SHA, and platform info |
| **Auth** | `POST` | `/api/auth/login` | Administrator authentication |
| **Webhooks** | `POST` | `/api/chat/webhook` | Synchronous Google Chat webhook receiver |
| **Automations** | `POST` | `/api/automations/run` | Cloud Scheduler automation execution trigger |
| **Skills** | `GET`, `POST` | `/api/skills` | List registered skills or create a new skill |
| **Skills** | `PATCH` | `/api/skills/{id}/toggle` | Toggle skill active/paused state |
| **Supervision**| `GET`, `POST` | `/api/locks` | Inspect and configure service supervision locks |
| **Calendar** | `GET`, `POST` | `/api/calendar/config` | Manage synchronized Google Calendar sources |

---

## 🧪 Running Automated Tests

Atlas includes a full automated test suite verifying policy gates, webhook event parsing, service locks, and skill hydration:

```bash
PYTHONPATH=. pytest tests -v
```

---

## 🐳 Docker Deployment

Build and run the container locally:

```bash
# Build the container image
docker build -t atlas-open-capsule .

# Run container on port 8080
docker run -p 8080:8080 --env-file .env atlas-open-capsule
```

For Google Cloud Run:
```bash
gcloud run deploy atlas-capsule \
  --source . \
  --region us-central1 \
  --allow-unauthenticated
```

---

## 🛡️ License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.
