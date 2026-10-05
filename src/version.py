import os
import subprocess
from typing import Any, Dict

# Semantic Version of the Synthetic Identity Capsule
VERSION_MAJOR = 0
VERSION_MINOR = 4
VERSION_PATCH = 3

__version__ = f"{VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_PATCH}"
APP_NAME = "Atlas Synthetic Identity Capsule"


def get_git_commit() -> str:
    """Returns short commit SHA from git or environment."""
    env_sha = os.environ.get("GIT_COMMIT_SHA") or os.environ.get("COMMIT_SHA") or os.environ.get("REVISION_ID")
    if env_sha:
        return env_sha[:7]
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL)
        return out.decode("utf-8").strip()
    except Exception:
        return "4ad856f"


def get_git_branch() -> str:
    """Returns active git branch name."""
    env_branch = os.environ.get("GIT_BRANCH") or os.environ.get("BRANCH_NAME")
    if env_branch:
        return env_branch
    try:
        out = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL)
        return out.decode("utf-8").strip()
    except Exception:
        return "main"


def get_build_date() -> str:
    """Returns ISO build/deploy timestamp."""
    return os.environ.get("BUILD_DATE", "2026-10-03T07:55:00Z")


def get_version_info() -> Dict[str, Any]:
    """Returns complete versioning manifest and semver explanation."""
    commit_sha = get_git_commit()
    branch = get_git_branch()
    environment = os.environ.get("ENVIRONMENT", "production")
    cloud_run_service = os.environ.get("K_SERVICE", "atlas-synthetic-capsule-prod")
    cloud_run_revision = os.environ.get("K_REVISION", f"{cloud_run_service}-00005-{commit_sha}")

    return {
        "app_name": APP_NAME,
        "version": __version__,
        "semver": {
            "major": VERSION_MAJOR,
            "minor": VERSION_MINOR,
            "patch": VERSION_PATCH,
            "level": "Patch (Thread conversational context & multi-calendar sync)",
            "diagram": (
                "  0   .   4   .   3\n"
                "  |       |       |\n"
                "  |       |       +---> PARCHE (Thread history / bug fixes / multi-calendar)\n"
                "  |       +-----------> MENOR (Nuevas funcionalidades compatibles)\n"
                "  +-------------------> MAYOR (Cambios incompatibles / rediseños)"
            ),
            "rules": [
                {
                    "level": "Z (Tercero)",
                    "name": "Parche",
                    "when": "Corrección de errores que no rompen nada y no añaden pantallas ni endpoints nuevos.",
                    "example": "Se arregla un error tipográfico o un fallo en el cálculo de impuestos / lookback.",
                    "next": f"0.{VERSION_MINOR}.{VERSION_PATCH} → 0.{VERSION_MINOR}.{VERSION_PATCH + 1}"
                },
                {
                    "level": "Y (Segundo)",
                    "name": "Menor",
                    "when": "Nuevas funcionalidades que se agregan manteniendo compatibilidad hacia atrás. Se reinicia el parche a 0.",
                    "example": "Se añade exportación a PDF, canal nuevo o panel de versiones.",
                    "next": f"0.{VERSION_MINOR}.{VERSION_PATCH} → 0.{VERSION_MINOR + 1}.0"
                },
                {
                    "level": "X (Primer)",
                    "name": "Mayor",
                    "when": "Cambios que rompen compatibilidad (breaking changes) o reescrituras profundas. Se reinician menor y parche a 0.",
                    "example": "Cambio completo de base de datos incompatible, o cambio total en la API pública.",
                    "next": f"{VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_PATCH} → {VERSION_MAJOR + 1}.0.0"
                }
            ]
        },
        "git": {
            "commit": commit_sha,
            "branch": branch,
        },
        "deployment": {
            "environment": environment,
            "platform": "Google Cloud Run" if os.environ.get("K_SERVICE") else "Local Runtime / Dev",
            "service": cloud_run_service,
            "revision": cloud_run_revision,
            "build_date": get_build_date(),
        },
        "changelog": [
            {
                "version": "0.4.3",
                "date": "2026-10-03",
                "type": "patch",
                "title": "Google Chat Thread Conversational Context Hydration",
                "summary": "Automatic conversational history retrieval for threads and spaces in Google Chat, injecting recent chronological context into the Antigravity Agent and Gemini prompt."
            },
            {
                "version": "0.4.2",
                "date": "2026-10-03",
                "type": "patch",
                "title": "Multi-Calendar Management UI & Automation Concurrency Lock",
                "summary": "Multi-calendar aggregation support with selection dialog in Capsule UI, automatic calendar config persistence, and execution concurrency lock to prevent duplicate runs between internal worker and Cloud Scheduler."
            },
            {
                "version": "0.4.1",
                "date": "2026-10-02",
                "type": "patch",
                "title": "Rate Limit & Budget Form Step Validation Fix",
                "summary": "Fixed HTML5 browser step validation on rate limit (step=1) and budget thresholds (step=any), preventing invalid step submission errors when saving limits."
            },
            {
                "version": "0.4.0",
                "date": "2026-10-02",
                "type": "minor",
                "title": "Skills Management & Dynamic Agent Capability Injection",
                "summary": "Full Skills CRUD console with activation toggle, SKILL.md Markdown editor, Antigravity SDK LocalAgentConfig(skills_paths=...) injection, system instructions prompt enrichment, and GCS persistence synchronization."
            },
            {
                "version": "0.3.3",
                "date": "2026-09-30",
                "type": "patch",
                "title": "Google Chat Space Auto-Discovery & GCS Boot Hydration",
                "summary": "Automatic space discovery via spaces().list(), GCS pre-boot memory hydration, and enhanced /api/automations/run execution trigger."
            },
            {
                "version": "0.3.2",
                "date": "2026-09-29",
                "type": "patch",
                "title": "Autonomous Scheduled Automations Dispatcher & Target Space Resolution",
                "summary": "Internal background worker cron scheduler evaluating daily/weekly tasks in local timezone, automatic chat space targeting, and Cloud Scheduler webhook trigger support."
            },
            {
                "version": "0.3.1",
                "date": "2026-09-28",
                "type": "patch",
                "title": "Distributed Atomic Message Claim Lock",
                "summary": "Cloud SQL-backed atomic claim table (processed_message_claims) preventing race conditions and message duplication/triplication across autoscaled Cloud Run container instances."
            },
            {
                "version": "0.3.0",
                "date": "2026-09-28",
                "type": "minor",
                "title": "Google Calendar Integration & Autonomous Scheduled Automations",
                "summary": "Google Calendar connector for memory injection, dynamic [calendar] and [automation] chat directives, YAML-backed automations engine without Cloud SQL, and Cloud Scheduler trigger endpoints."
            },
            {
                "version": "0.2.1",
                "date": "2026-09-28",
                "type": "patch",
                "title": "Visual Processing Feedback Reaction (Eyes 👀)",
                "summary": "Immediate visual acknowledgment with 👀 emoji reaction on user messages in Google Chat while the AI agent reasons and generates response."
            },
            {
                "version": "0.2.0",
                "date": "2026-09-28",
                "type": "minor",
                "title": "Trusted Sender Direct Dispatch & Configurable Lookback",
                "summary": "Autonomous SEND direct dispatch in Google Chat shared spaces for trusted senders, extended 6-hour startup lookback window, and visual SemVer versioning dashboard."
            },
            {
                "version": "0.1.4",
                "date": "2026-09-27",
                "type": "patch",
                "title": "Multimodal Image Interpretation & User Reaction Feedback",
                "summary": "Attachment photo download with Gemini multimodal parsing, and Google Chat reaction feedback."
            },
            {
                "version": "0.1.3",
                "date": "2026-09-26",
                "type": "patch",
                "title": "Chat and Email Deduplication Gate",
                "summary": "Atomic async locks and database pre-seeding to eliminate duplicate message replies."
            },
            {
                "version": "0.1.2",
                "date": "2026-09-25",
                "type": "patch",
                "title": "Token Tracking, Dynamic Models & Budget Limits",
                "summary": "Pricing engine per 1M tokens, dynamic hot-swapping of Gemini models, and operational budget gates."
            },
            {
                "version": "0.1.1",
                "date": "2026-09-25",
                "type": "patch",
                "title": "Admin Authentication & Access Protection",
                "summary": "PBKDF2 salted password hashing, admin_users_auth table, and console security gate."
            },
            {
                "version": "0.1.0",
                "date": "2026-09-24",
                "type": "minor",
                "title": "Initial Multi-Channel Synthetic Identity MVP",
                "summary": "Initial release with Gmail connector, Google Chat DM/Space mention filtering, and Principle of Lock."
            }
        ]
    }
