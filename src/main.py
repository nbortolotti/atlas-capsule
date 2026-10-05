import asyncio
from contextlib import asynccontextmanager
import datetime
import logging
import os
import sys
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

load_dotenv()

from src.auth.google_auth import GoogleAuthManager
from src.connectors.calendar_client import GoogleCalendarConnector
from src.connectors.chat_client import GoogleChatConnector
from src.connectors.gmail_client import GmailConnector
from src.db.database import DatabaseManager
from src.engine.agent import SyntheticIdentityAgent
from src.engine.automations import AutomationEngine, AutomationItem
from src.engine.policies import PolicyEngine
from src.engine.pricing import AVAILABLE_MODELS, MODEL_PRICING_MAP
from src.engine.skills import SkillEngine, SkillItem
from src.memory.gcs_storage import GCSStorageManager
from src.models.message import ActionType, ChatItem, EmailItem, ReactionItem
from src.version import __version__, get_version_info

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("capsule-main")


class RuleCreateRequest(BaseModel):
    rule_type: str
    value: str
    description: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    identity: Dict[str, Any]


class TestMessageRequest(BaseModel):
    sender: str
    subject: Optional[str] = "No Subject"
    body: str
    service_name: str = "gmail"  # "gmail" or "chat"
    space_id: Optional[str] = "spaces/DEMO_SPACE"
    is_direct_message: Optional[bool] = None
    image_b64: Optional[str] = None  # Optional base64 encoded image string
    image_mime: Optional[str] = "image/jpeg"
    thread_history: Optional[str] = None


class ReactionRequest(BaseModel):
    message_id: str
    space_id: Optional[str] = "spaces/DEMO_SPACE"
    emoji: str  # e.g. 👍, 👎, ❤️
    user_email: str
    user_name: Optional[str] = None
    action: str = "CREATED"



class LockConfigRequest(BaseModel):
    service_name: str = "gmail"
    is_supervision_enabled: bool = True
    mode: str = "ALL"  # "ALL" or "SPECIFIC"
    description: Optional[str] = None


class SupervisedUserCreateRequest(BaseModel):
    service_name: str = "gmail"
    identifier: str
    description: Optional[str] = None
    is_active: bool = True


class SupervisedUserUpdateRequest(BaseModel):
    is_active: Optional[bool] = None
    description: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    username: str
    current_password: str
    new_password: str


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "admin"


class ModelSelectionRequest(BaseModel):
    model_id: str


class BudgetLimitsRequest(BaseModel):
    max_tokens_limit: int
    max_cost_limit_usd: float
    is_limit_enforced: bool
    limit_reached_message: Optional[str] = None
    max_replies_per_hour: Optional[int] = 30


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    content: str
    enabled: bool = True


class SkillUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    enabled: Optional[bool] = None


class SkillToggleRequest(BaseModel):
    enabled: bool



class CapsuleService:
    def __init__(self):
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
        refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

        self.auth_manager = GoogleAuthManager(client_id, client_secret, refresh_token)
        self.gmail = None
        self.chat = None
        self.calendar = None
        if client_id and client_secret and refresh_token and client_id != "xxxx.apps.googleusercontent.com":
            try:
                creds = self.auth_manager.get_credentials()
                self.gmail = GmailConnector(creds)
                self.chat = GoogleChatConnector(creds)
                self.calendar = GoogleCalendarConnector(creds)
            except Exception as e:
                logger.warning(f"Could not initialize Google connectors: {e}")
        else:
            logger.warning("Google OAuth credentials missing or dummy in environment.")

        self.db = DatabaseManager()
        self.policy_engine = PolicyEngine(db_manager=self.db)
        base_app_data = os.environ.get("APP_DATA_DIR", "/tmp/atlas_brain")
        identity_name = "default"
        try:
            from pathlib import Path
            import yaml
            prof_file = Path("config/identity_profile.yaml")
            if prof_file.exists():
                with open(prof_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    identity_name = data.get("identity", {}).get("name") or data.get("identity", {}).get("email") or "default"
        except Exception:
            pass

        self.gcs_storage = GCSStorageManager(local_base_dir=base_app_data, identity_slug=identity_name)
        # Hydrate local persistent memory from GCS at boot
        try:
            self.gcs_storage.sync_from_gcs()
        except Exception as e:
            logger.warning(f"Could not hydrate memory from GCS at boot: {e}")

        self.app_data_dir = str(self.gcs_storage.local_base_dir)
        self.skills = SkillEngine(memory_dir=self.app_data_dir, gcs_manager=self.gcs_storage)
        self.agent = SyntheticIdentityAgent(app_data_dir=self.app_data_dir, db_manager=self.db, skills_engine=self.skills)
        self.automations = AutomationEngine(memory_dir=self.app_data_dir, gcs_manager=self.gcs_storage)
        if self.chat and hasattr(self.agent, "profile"):
            self.chat.identity_profile = self.agent.profile
        self.poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "10"))
        self._last_calendar_sync = None
        self._last_active_chat_space = os.environ.get("DEFAULT_CHAT_SPACE")
        self._is_running = True
        self.start_time = datetime.datetime.now(datetime.timezone.utc)
        # Pre-seed in-memory sets from database to avoid re-processing messages after re-deployments
        self._processed_chat_ids: set = set()
        self._processed_email_ids: set = set()
        self._chat_dedup_lock = asyncio.Lock()  # Prevents TOCTOU race between webhook and poller
        self._email_dedup_lock = asyncio.Lock()
        if self.db and self.db.enabled:
            try:
                historical_ids = self.db.get_processed_message_ids(limit=2000)
                self._processed_chat_ids.update(historical_ids)
                self._processed_email_ids.update(historical_ids)
                logger.info(f"Loaded {len(self._processed_chat_ids)} historical processed message IDs from database.")
            except Exception as e:
                logger.warning(f"Could not load processed message IDs from DB: {e}")

    async def process_single_email(self, msg: EmailItem, force_simulated_draft: bool = False):
        """Processes an email through the full pipeline: policy -> antigravity -> draft / log."""
        # 0. Deduplication check: prevent multiple responses to already processed emails
        async with self._email_dedup_lock:
            if msg.message_id in self._processed_email_ids:
                logger.info(f"Skipping already processed email {msg.message_id} (found in memory set).")
                return {"allowed": False, "policy": {"allowed": False, "reasons": ["Already processed"]}, "draft": None}

            if self.db and self.db.is_message_processed(msg.message_id):
                self._processed_email_ids.add(msg.message_id)
                logger.info(f"Skipping already processed email {msg.message_id} (found in database audit log).")
                return {"allowed": False, "policy": {"allowed": False, "reasons": ["Already processed"]}, "draft": None}

            # Claim this email atomically in distributed Cloud SQL across container instances
            if self.db and not self.db.claim_message(msg.message_id, service_name="gmail"):
                self._processed_email_ids.add(msg.message_id)
                logger.info(f"Skipping email {msg.message_id} (already claimed by another container instance).")
                return {"allowed": False, "policy": {"allowed": False, "reasons": ["Already claimed by another instance"]}, "draft": None}

            # Claim this email atomically in local memory
            self._processed_email_ids.add(msg.message_id)

        session_id = f"thread_{msg.thread_id}_{msg.message_id}"
        logger.info(f"Processing message {msg.message_id} from {msg.sender} (Subject: {msg.subject})")

        # 1. Deterministic Policy Gate
        policy_res = self.policy_engine.evaluate(msg)

        if not policy_res.allowed:
            logger.warning(f"Message {msg.message_id} blocked: {policy_res.risk_level.value} - {policy_res.reasons}")
            self.db.log_action(
                message_id=msg.message_id,
                thread_id=msg.thread_id,
                sender=msg.sender,
                subject=msg.subject,
                risk_level=policy_res.risk_level.value,
                action_taken=policy_res.recommended_action.value,
                reasons="; ".join(policy_res.reasons),
            )
            if self.gmail and policy_res.recommended_action == ActionType.IGNORE:
                self.gmail.mark_as_read(msg.message_id)

            is_limit_block = any("Límite de consumo alcanzado" in r for r in policy_res.reasons)
            limit_draft = None
            if is_limit_block:
                limit_msg = self.db.get_capsule_config(
                    "limit_reached_message",
                    "He alcanzado mi límite operativo asignado de consumo y no puedo generar respuestas autónomas en este momento. Mi supervisor humano ha sido notificado."
                ) if self.db and self.db.enabled else "He alcanzado mi límite operativo asignado de consumo."
                sig = self.agent.get_signature_for_service("email")
                full_limit_body = f"Estimado/a,\n\n{limit_msg}\n\n{sig}"
                limit_draft = {
                    "thread_id": msg.thread_id,
                    "to": msg.sender,
                    "subject": msg.subject or "Límite de consumo alcanzado",
                    "draft_body": full_limit_body,
                    "action_taken": ActionType.CREATE_DRAFT.value,
                    "draft_id": f"limit_draft_{msg.message_id}",
                    "explanation": "Consumo pausado por exceder límite operativo",
                }
                if self.gmail and not force_simulated_draft:
                    self.gmail.create_draft(
                        thread_id=msg.thread_id,
                        to=msg.sender,
                        subject=msg.subject or "Revisión pendiente",
                        body=full_limit_body,
                    )
                    self.gmail.mark_as_read(msg.message_id)

            return {
                "allowed": False,
                "policy": policy_res.model_dump(),
                "draft": limit_draft,
            }

        # 2. Antigravity Agent Reasoning & Response Synthesis
        logger.info(f"Reasoning with Antigravity Agent for message {msg.message_id}...")
        draft_res = await self.agent.generate_draft_response(msg)

        # 3. Action Execution (Autonomous Send or Draft Gate)
        action_type = policy_res.recommended_action
        action_id = None

        if self.gmail and not force_simulated_draft:
            if action_type == ActionType.SEND:
                action_id = self.gmail.send_reply(
                    thread_id=draft_res.thread_id,
                    to=draft_res.to,
                    subject=draft_res.subject,
                    body=draft_res.draft_body,
                )
                if action_id:
                    logger.info(f"Autonomously sent email response with ID: {action_id}")
                    self.policy_engine.record_action()
                    self.gmail.mark_as_read(msg.message_id)
            else:
                action_id = self.gmail.create_draft(
                    thread_id=draft_res.thread_id,
                    to=draft_res.to,
                    subject=draft_res.subject,
                    body=draft_res.draft_body,
                )
                if action_id:
                    logger.info(f"Draft created with ID: {action_id}")
                    self.policy_engine.record_action()
                    self.gmail.mark_as_read(msg.message_id)
        else:
            action_prefix = "simulated_send" if action_type == ActionType.SEND else "simulated_draft"
            action_id = f"{action_prefix}_{msg.message_id}"
            self.policy_engine.record_action()

        draft_res.draft_id = action_id
        draft_res.action_taken = action_type

        # 4. Log Action to Cloud SQL
        self.db.log_action(
            message_id=msg.message_id,
            thread_id=msg.thread_id,
            sender=msg.sender,
            subject=msg.subject,
            risk_level=policy_res.risk_level.value,
            action_taken=action_type.value,
            reasons="; ".join(policy_res.reasons),
            draft_id=action_id,
        )

        return {
            "allowed": True,
            "policy": policy_res.model_dump(),
            "draft": draft_res.model_dump(),
        }

    async def process_single_chat_message(self, msg: ChatItem, force_simulated: bool = False, from_webhook: bool = False):
        """Processes an incoming Google Chat message through the policy gate, behavioral learning, and response synthesis."""
        # 0. Atomic deduplication check with lock to prevent TOCTOU race between webhook and poller
        async with self._chat_dedup_lock:
            if msg.message_id in self._processed_chat_ids:
                logger.info(f"Skipping already processed chat message {msg.message_id} (found in memory set).")
                return {"allowed": False, "policy": {"allowed": False, "reasons": ["Already processed"]}, "draft": None, "learned": False}

            if self.db and self.db.is_message_processed(msg.message_id):
                self._processed_chat_ids.add(msg.message_id)
                logger.info(f"Skipping already processed chat message {msg.message_id} (found in database audit log).")
                return {"allowed": False, "policy": {"allowed": False, "reasons": ["Already processed"]}, "draft": None, "learned": False}

            # Claim this chat message atomically in distributed Cloud SQL across container instances
            if self.db and not self.db.claim_message(msg.message_id, service_name="chat"):
                self._processed_chat_ids.add(msg.message_id)
                logger.info(f"Skipping chat message {msg.message_id} (already claimed by another container instance).")
                return {"allowed": False, "policy": {"allowed": False, "reasons": ["Already claimed by another instance"]}, "draft": None, "learned": False}

            # Claim this message atomically in local memory — no other coroutine can pass the check now
            self._processed_chat_ids.add(msg.message_id)

        logger.info(f"Processing chat message {msg.message_id} from {msg.sender} (Space: {msg.space_id}, DM: {msg.is_direct_message})")

        # 1. Deterministic Policy Gate (includes consumption check for 'chat' service)
        policy_res = self.policy_engine.evaluate(msg, service_name="chat")

        if not policy_res.allowed:
            logger.warning(f"Chat message {msg.message_id} blocked or dropped: {policy_res.risk_level.value} - {policy_res.reasons}")
            self.db.log_action(
                message_id=msg.message_id,
                thread_id=msg.thread_id or msg.space_id,
                sender=msg.sender,
                subject=f"Chat in {msg.space_id}",
                risk_level=policy_res.risk_level.value,
                action_taken=policy_res.recommended_action.value,
                reasons="; ".join(policy_res.reasons),
            )
            is_limit_block = any("Límite de consumo alcanzado" in r for r in policy_res.reasons)
            limit_draft = None
            if is_limit_block:
                limit_msg = self.db.get_capsule_config(
                    "limit_reached_message",
                    "He alcanzado mi límite operativo asignado de consumo y no puedo generar respuestas autónomas en este momento. Mi supervisor humano ha sido notificado."
                ) if self.db and self.db.enabled else "He alcanzado mi límite operativo asignado de consumo."
                sig = self.agent.get_signature_for_service("chat")
                full_limit_body = f"{limit_msg}\n\n{sig}"
                limit_draft = {
                    "thread_id": msg.thread_id or msg.space_id,
                    "to": msg.sender,
                    "subject": f"Chat in {msg.space_id}",
                    "draft_body": full_limit_body,
                    "action_taken": ActionType.CREATE_DRAFT.value,
                    "draft_id": f"limit_chat_{msg.message_id}",
                    "explanation": "Consumo pausado por exceder límite operativo",
                }
                if self.chat and not force_simulated and not from_webhook:
                    # In chat polling, if limit reached, optionally inform the space/user
                    try:
                        self.chat.send_message(space_id=msg.space_id, text=full_limit_body, thread_id=msg.thread_id)
                    except Exception as ce:
                        logger.warning(f"Could not send limit message to chat space: {ce}")

            return {
                "allowed": False,
                "policy": policy_res.model_dump(),
                "draft": limit_draft,
                "learned": False,
            }

        # Remember the last active space for automated notifications if not generic
        if msg.space_id and msg.space_id.startswith("spaces/") and msg.space_id != "spaces/default" and not msg.space_id.startswith("spaces/simulated"):
            self._last_active_chat_space = msg.space_id

        # 2. Check if admin sent a behavior guideline directive (e.g. "[Learn]", "LEARN:", "REGLA:", "[Rule]")
        learned = False
        import re
        raw_clean_text = msg.clean_body.strip()
        # Strip any leading mention tags (e.g. "@Atlas", "@Capsule", etc.)
        text_without_mention = re.sub(r"^@[\w\s.-]+(?:\s+|$)", "", raw_clean_text).strip()
        if not text_without_mention:
            text_without_mention = raw_clean_text

        # Support directive tags at the start, at the end, or inside brackets:
        # e.g., "[learn] do X", "[rule] do X", "[automation] do X", "[calendar] do X"
        directive_pattern = re.compile(
            r"\[(learn|regla|rule|instruccion|instruction|automation|automatizacion|calendar|calendario)\]|^(?:learn|regla|rule|instruccion|instruction|automation|automatizacion|calendar|calendario):",
            re.IGNORECASE
        )
        directive_match = directive_pattern.search(text_without_mention)
        is_directive = bool(directive_match)

        if self.policy_engine.is_admin(msg.sender, sender_name=getattr(msg, "sender_name", None)) and is_directive:
            matched_tag = directive_match.group(1) or (directive_match.group(0).split(":")[0].replace("[", "").replace("]", ""))
            matched_tag = matched_tag.lower()
            directive_body = directive_pattern.sub("", text_without_mention).strip()
            if directive_body.startswith('"') and directive_body.endswith('"') and len(directive_body) > 2:
                directive_body = directive_body[1:-1].strip()

            if matched_tag in ["automation", "automatizacion"]:
                auto_item = self.automations.parse_automation_tag(text_without_mention, sender=msg.sender)
                if auto_item:
                    logger.info(f"Created automation '{auto_item.name}' via chat directive from {msg.sender}")
                    learned = True
            elif matched_tag in ["calendar", "calendario"]:
                # Associate calendar ID and trigger sync
                cal_id = directive_body.strip()
                if cal_id:
                    self.agent.append_behavior_guideline(directive=f"Calendario familiar principal vinculado: {cal_id}", admin_sender=msg.sender)
                    if self.calendar:
                        events = self.calendar.get_upcoming_events(calendar_id=cal_id, days_ahead=7)
                        summary = self.calendar.format_events_summary(events, calendar_label=f"Calendario Familiar ({cal_id})")
                        self.agent.update_calendar_context(summary)
                    self.gcs_storage.sync_to_gcs()
                    learned = True
                    logger.info(f"Associated calendar {cal_id} via chat directive from {msg.sender}")
            else:
                if directive_body:
                    self.agent.append_behavior_guideline(directive=directive_body, admin_sender=msg.sender)
                    self.gcs_storage.sync_to_gcs()
                    learned = True
                    logger.info(f"Admin {msg.sender} updated synthetic identity behavioral memory with directive: {directive_body}")

        # 3. Add 'processing' emoji reaction (👀) to acknowledge receipt and reduce user anxiety
        if self.chat and not force_simulated and msg.message_id and not msg.message_id.startswith("simulated"):
            try:
                # Add reaction asynchronously / without blocking reasoning failure
                self.chat.add_reaction(msg.message_id, emoji_unicode="👀")
            except Exception as rx_err:
                logger.debug(f"Could not add processing reaction to message {msg.message_id}: {rx_err}")

        # 4. Thread Conversational Context Hydration
        if self.chat and not msg.thread_history and not force_simulated and msg.space_id:
            try:
                history_text = self.chat.get_thread_history(
                    space_id=msg.space_id,
                    thread_id=msg.thread_id,
                    current_message_id=msg.message_id,
                    limit=8,
                )
                if history_text:
                    msg.thread_history = history_text
                    logger.info(f"Hydrated thread history for message {msg.message_id} ({len(history_text)} chars)")
            except Exception as hist_err:
                logger.debug(f"Could not hydrate thread history for {msg.message_id}: {hist_err}")

        # 5. Antigravity Agent Reasoning & Response Synthesis
        logger.info(f"Reasoning with Antigravity Agent for chat message {msg.message_id} (DM: {msg.is_direct_message}, has_history: {bool(msg.thread_history)})...")
        draft_res = await self.agent.generate_draft_response(msg)
        if draft_res and draft_res.draft_body:
            draft_res.draft_body = GoogleChatConnector.format_for_google_chat(draft_res.draft_body)

        # 5. Action Execution (Autonomous Send or Supervised Draft/Notification)
        action_type = policy_res.recommended_action
        action_id = None

        if from_webhook:
            # Synchronous delivery via webhook HTTP response:
            # Google Chat immediately displays the JSON body {"text": ...} returned by the webhook endpoint.
            action_prefix = "chat_webhook_send" if action_type == ActionType.SEND else "supervised_chat_draft"
            action_id = f"{action_prefix}_{msg.message_id}"
            if action_type == ActionType.SEND:
                logger.info(f"Prepared synchronous Google Chat webhook response: {action_id}")
            else:
                logger.info(f"Google Chat webhook message held for human supervision: {action_id}")
            self.policy_engine.record_action()
        elif self.chat and not force_simulated:
            if action_type == ActionType.SEND:
                res = self.chat.send_message(
                    space_id=msg.space_id,
                    text=draft_res.draft_body,
                    thread_id=msg.thread_id,
                )
                action_id = res.get("name") if isinstance(res, dict) else res
                if action_id:
                    logger.info(f"Autonomously sent Google Chat response: {action_id}")
                    self.policy_engine.record_action()
            else:
                # In Google Chat, supervision mode records a draft/pending action rather than posting publicly
                action_id = f"supervised_chat_draft_{msg.message_id}"
                logger.info(f"Google Chat message held for human supervision: {action_id}")
                self.policy_engine.record_action()
        else:
            action_prefix = "simulated_chat_send" if action_type == ActionType.SEND else "simulated_chat_draft"
            action_id = f"{action_prefix}_{msg.message_id}"
            self.policy_engine.record_action()

        draft_res.draft_id = action_id
        draft_res.action_taken = action_type

        # 5. Log Action to Cloud SQL
        self.db.log_action(
            message_id=msg.message_id,
            thread_id=msg.thread_id or msg.space_id,
            sender=msg.sender,
            subject=f"Chat in {msg.space_id}",
            risk_level=policy_res.risk_level.value,
            action_taken=action_type.value,
            reasons="; ".join(policy_res.reasons),
            draft_id=action_id,
        )

        return {
            "allowed": True,
            "policy": policy_res.model_dump(),
            "draft": draft_res.model_dump(),
            "learned": learned,
        }

    async def process_pending_emails(self):
        """Worker cycle for Gmail polling."""
        if not self.gmail:
            return

        self.gcs_storage.sync_from_gcs()
        unread_messages = self.gmail.fetch_unread_messages()
        logger.info(f"Retrieved {len(unread_messages)} unread messages.")

        for msg in unread_messages:
            await self.process_single_email(msg)

        self.gcs_storage.sync_to_gcs()

    def _sync_message_reactions(self, message_id: str, space_id: str):
        """Polls user reactions on a bot message and records them into database and trajectory."""
        if not self.chat or not self.db or not self.db.enabled:
            return

        try:
            reactions = self.chat.list_reactions(message_id=message_id)
            for r in reactions:
                # Reaction structure: name, emoji (unicode or customEmoji), user
                user = r.get("user", {})
                user_email = user.get("email") or user.get("name") or "unknown@chat.google.com"
                user_name = user.get("displayName") or user_email
                
                # Ignore reactions added by the bot itself (e.g. '👀' processing indicator)
                ident_email = self.agent.profile.get("identity", {}).get("email", "").lower()
                ident_name = self.agent.profile.get("identity", {}).get("name", "").lower()
                if (ident_email and ident_email in user_email.lower()) or (ident_name and ident_name in user_name.lower()):
                    continue

                emoji_obj = r.get("emoji", {})
                emoji_str = emoji_obj.get("unicode") or emoji_obj.get("customEmoji", {}).get("uid") or r.get("emoji") or "👍"
                if emoji_str == "👀":
                    continue

                sentiment = GoogleChatConnector.derive_emoji_sentiment(emoji_str)
                reaction_key = f"{message_id}_{user_email}_{emoji_str}"
                if reaction_key in getattr(self, "_processed_reaction_keys", set()):
                    continue

                self.db.log_reaction(
                    message_id=message_id,
                    space_id=space_id,
                    emoji=emoji_str,
                    user_email=user_email,
                    user_name=user_name,
                    action="CREATED",
                    sentiment=sentiment,
                )
                session_id = f"thread_{message_id}"
                self.db.log_trajectory(
                    session_id=session_id,
                    step_type="user_reaction",
                    content=f"Google Chat User {user_name} reacted with {emoji_str} ({sentiment})",
                    metadata={
                        "message_id": message_id,
                        "space_id": space_id,
                        "emoji": emoji_str,
                        "sentiment": sentiment,
                        "user_email": user_email,
                    },
                )
                if not hasattr(self, "_processed_reaction_keys"):
                    self._processed_reaction_keys = set()
                self._processed_reaction_keys.add(reaction_key)
                logger.info(f"Recorded user reaction {emoji_str} ({sentiment}) on bot message {message_id} from {user_name}")
        except Exception as e:
            logger.debug(f"Could not sync reactions for bot message {message_id}: {e}")

    async def process_pending_chat_messages(self):
        """Worker cycle for Google Chat polling."""
        if not self.chat:
            return

        try:
            bot_email = self.agent.profile.get("identity", {}).get("email", "").lower()
            # Poll messages created up to CHAT_LOOKBACK_MINUTES (default 360m / 6h) before startup.
            # Multi-tier deduplication (_processed_chat_ids and Cloud SQL audit log) ensures idempotency.
            lookback_minutes = int(os.environ.get("CHAT_LOOKBACK_MINUTES", "360"))
            min_create_time = self.start_time - datetime.timedelta(minutes=lookback_minutes)
            new_chat_messages = self.chat.fetch_new_chat_messages(
                processed_msg_ids=self._processed_chat_ids,
                min_create_time=min_create_time,
                on_bot_message_detected=self._sync_message_reactions,
            )
            for msg in new_chat_messages:
                # Avoid self-reply loops if bot sent the message
                if bot_email and bot_email in msg.sender.lower():
                    self._processed_chat_ids.add(msg.message_id)
                    continue
                # Pre-register in dedup set before any async work to block concurrent webhook/poller processing
                if msg.message_id in self._processed_chat_ids:
                    continue
                logger.info(f"Detected new incoming Chat message from {msg.sender} in {msg.space_id}")
                await self.process_single_chat_message(msg)
        except Exception as e:
            logger.debug(f"Chat polling check: {e}")

    async def sync_calendar_context(self, force: bool = False):
        """Synchronizes events from all user-selected calendars (or auto-detected family/primary) into memory."""
        now = datetime.datetime.now(datetime.timezone.utc)
        if not force and self._last_calendar_sync:
            # Sync once every hour
            if (now - self._last_calendar_sync).total_seconds() < 3600:
                return

        if not self.calendar:
            return

        try:
            available_cals = self.calendar.list_calendars()
            cal_map = {c.get("id"): c.get("summary") for c in available_cals if c.get("id")}

            # Check user configured calendars in Capsule memory
            selected_configs = self.automations.get_selected_calendars()
            active_cal_configs = []

            if selected_configs:
                for sc in selected_configs:
                    cid = sc.get("id")
                    if cid:
                        active_cal_configs.append({
                            "id": cid,
                            "label": sc.get("label") or cal_map.get(cid) or cid
                        })
            else:
                # Default heuristics: find family or primary
                chosen_id = "primary"
                chosen_label = "Calendario Principal"
                for c in available_cals:
                    summary_lower = (c.get("summary") or "").lower()
                    if "famil" in summary_lower or "casa" in summary_lower:
                        chosen_id = c.get("id")
                        chosen_label = c.get("summary") or "Familia"
                        break
                active_cal_configs.append({"id": chosen_id, "label": chosen_label})

            events = self.calendar.get_events_from_multiple_calendars(active_cal_configs, days_ahead=14)
            labels_str = ", ".join([c["label"] for c in active_cal_configs])
            summary_md = self.calendar.format_events_summary(events, calendar_label=f"Calendarios Activos ({labels_str})")
            self.agent.update_calendar_context(summary_md)
            self._last_calendar_sync = now
            logger.info(f"Synchronized {len(events)} calendar events from {len(active_cal_configs)} calendars into memory.")
        except Exception as e:
            logger.warning(f"Calendar sync check failed: {e}")

    async def execute_automation(self, item: AutomationItem) -> Dict[str, Any]:
        """Executes a single automation task autonomously with duplicate execution protection."""
        if not self.automations.acquire_execution_lock(item.id):
            logger.warning(f"Automation {item.id} ({item.name}) is already executing in another task. Skipping duplicate.")
            return {"automation_id": item.id, "status": "skipped", "message": "Already executing"}

        try:
            logger.info(f"Executing automation: {item.name} ({item.id})")
            # Ensure calendar context is up to date if action involves calendar
            if item.action == "calendar_review" or "calendario" in item.prompt.lower():
                await self.sync_calendar_context(force=True)

            # Resolve target space for Google Chat if not explicitly set
            target_space = item.target
            if not target_space or not target_space.startswith("spaces/"):
                target_space = getattr(self, "_last_active_chat_space", None) or os.environ.get("DEFAULT_CHAT_SPACE")
                if not target_space and self.chat:
                    discovered = self.chat.find_default_space()
                    if discovered:
                        target_space = discovered
                        self._last_active_chat_space = discovered
                        logger.info(f"Auto-resolved default Google Chat space for automation: {target_space}")

            # Build prompt for the identity
            sim_msg = ChatItem(
                message_id=f"auto_{item.id}_{int(datetime.datetime.now().timestamp())}",
                space_id=target_space or "spaces/family_hub",
                thread_id=None,
                sender=f"automation:{item.id}",
                sender_name="Programador de Tareas",
                text=item.prompt,
                clean_body=item.prompt,
                is_direct_message=False,
            )

            draft_res = await self.agent.generate_draft_response(sim_msg)
            formatted_body = draft_res.draft_body
            if item.channel == "chat":
                formatted_body = GoogleChatConnector.format_for_google_chat(draft_res.draft_body)
                if self.chat and target_space and target_space.startswith("spaces/"):
                    try:
                        self.chat.send_message(space_id=target_space, text=formatted_body)
                        logger.info(f"Sent automation message to chat space {target_space}")
                    except Exception as e:
                        logger.warning(f"Could not send automated message to chat {target_space}: {e}")
                else:
                    logger.info(f"Automation generated message (no chat target space configured): {formatted_body[:60]}...")

            self.automations.mark_executed(item.id)
            return {
                "automation_id": item.id,
                "status": "success",
                "message": formatted_body,
                "target": target_space,
                "executed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        finally:
            self.automations.release_execution_lock(item.id)

    async def process_due_automations(self):
        """Checks for automations that are due based on local time and triggers execution."""
        due_items = self.automations.get_due_automations()
        if due_items:
            logger.info(f"Detected {len(due_items)} due automation(s) to execute.")
            for item in due_items:
                try:
                    await self.execute_automation(item)
                except Exception as e:
                    logger.error(f"Failed to execute due automation {item.id}: {e}", exc_info=True)

    async def run_worker_loop(self):
        logger.info(f"Starting Capsule Worker loop with interval of {self.poll_interval}s...")
        while self._is_running:
            try:
                await self.process_pending_emails()
                await self.process_pending_chat_messages()
                await self.sync_calendar_context()
                await self.process_due_automations()
            except Exception as e:
                logger.error(f"Error in Capsule Worker loop: {e}", exc_info=True)

            await asyncio.sleep(self.poll_interval)


service = CapsuleService()
worker_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker_task
    # 1. Start worker loop for periodic fallback polling
    worker_task = asyncio.create_task(service.run_worker_loop())

    # 2. Register real-time Cloud Pub/Sub watch for Gmail if configured
    pubsub_topic = os.environ.get("PUBSUB_TOPIC_NAME", "projects/pelagic-bison-317014/topics/atlas-gmail-notifications")
    if service.gmail and pubsub_topic:
        try:
            logger.info(f"Registering Gmail watch on Cloud Pub/Sub topic: {pubsub_topic}")
            service.gmail.watch_mailbox(topic_name=pubsub_topic)
        except Exception as e:
            logger.warning(f"Failed to register Gmail watch on startup: {e}")

    yield
    service._is_running = False
    if worker_task:
        worker_task.cancel()



app = FastAPI(
    title="Synthetic Identity Capsule (Atlas)",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_db_authentication(request: Request, call_next):
    """Enforces authentication against admin_users_auth table in database for protected endpoints."""
    path = request.url.path

    # Public whitelist: static assets, login endpoint, external webhooks, health check, version, cloud scheduler trigger
    public_exact = {
        "/",
        "/api/health",
        "/api/version",
        "/api/auth/login",
        "/api/chat/webhook",
        "/webhooks/chat",
        "/webhook/trigger",
        "/api/pubsub/gmail",
        "/api/automations/run",
    }
    is_public = (
        path in public_exact
        or path.startswith("/assets")
        or (path.startswith("/api/chat/webhook"))
        or (path.startswith("/webhooks/"))
        or (path.startswith("/api/pubsub/"))
        or (path.startswith("/api/automations/run/"))
    )

    if is_public or request.method == "OPTIONS":
        return await call_next(request)

    # If database is disabled, deny access to protected endpoints
    if not service.db.enabled:
        return JSONResponse(status_code=503, content={"detail": "Database is required for authentication but disabled"})

    # Check credentials via custom headers (X-Admin-Username, X-Admin-Password) or HTTP Basic Auth
    username = request.headers.get("X-Admin-Username")
    password = request.headers.get("X-Admin-Password")

    if not username or not password:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Basic "):
            import base64
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                if ":" in decoded:
                    username, password = decoded.split(":", 1)
            except Exception:
                pass

    if not username or not password:
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required. Please log in with admin database credentials."},
            headers={"WWW-Authenticate": "Basic realm='Atlas Admin Console'"},
        )

    # Validate against Cloud SQL MySQL admin_users_auth table
    user = service.db.authenticate_user(username, password)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid admin database credentials"},
            headers={"WWW-Authenticate": "Basic realm='Atlas Admin Console'"},
        )

    # Attach authenticated user to request state
    request.state.user = user
    return await call_next(request)


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "service": "Synthetic Identity Capsule",
        "version": __version__,
        "identity": service.agent.profile.get("identity", {}).get("name", "Atlas"),
        "model": service.agent.get_active_model(),
        "db_connected": service.db.enabled,
        "gmail_configured": service.gmail is not None,
    }


@app.get("/api/version")
def get_version():
    """Returns deployment versioning, SemVer explanation, and release changelog."""
    return get_version_info()


@app.get("/api/identity")
def get_identity_profile():
    return service.agent.profile


@app.post("/api/identity")
def update_identity_profile(req: ProfileUpdateRequest):
    service.agent.save_profile(req.model_dump())
    new_name = req.identity.get("name") or req.identity.get("email") or "default"
    service.gcs_storage.set_identity(new_name)
    service.app_data_dir = str(service.gcs_storage.local_base_dir)
    service.agent.app_data_dir = service.app_data_dir
    return {"status": "updated", "profile": service.agent.profile, "memory_dir": service.app_data_dir}


@app.get("/api/policies")
def get_policies():
    service_locks = service.db.get_service_locks() if service.db.enabled else []
    supervised_users = service.db.get_supervised_users() if service.db.enabled else []
    return {
        "config_rules": service.policy_engine.rules,
        "effective_rules": service.policy_engine.get_effective_rules(),
        "database_rules": service.db.get_security_rules() if service.db.enabled else [],
        "service_locks": service_locks,
        "supervised_users": supervised_users,
    }


@app.post("/api/policies/rules")
def add_security_rule(req: RuleCreateRequest):
    rule = service.db.add_security_rule(req.rule_type, req.value, req.description)
    if not rule:
        raise HTTPException(status_code=400, detail="Failed to add rule or duplicate value")
    return {"status": "created", "rule": rule}


@app.delete("/api/policies/rules/{rule_id}")
def delete_security_rule(rule_id: int):
    ok = service.db.delete_security_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "deleted"}


# --- Admin Authentication Endpoints ---

@app.post("/api/auth/login")
def login(req: LoginRequest):
    """Authenticates admin user credentials against Cloud SQL MySQL."""
    if not service.db.enabled:
        raise HTTPException(status_code=503, detail="Database disabled")
    user = service.db.authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {
        "status": "authenticated",
        "user": user,
    }


@app.get("/api/auth/users")
def get_admin_users():
    """Lists registered console users."""
    if not service.db.enabled:
        return []
    return service.db.list_users()


@app.post("/api/auth/users")
def create_admin_user(req: UserCreateRequest):
    """Registers a new console user with password."""
    if not service.db.enabled:
        raise HTTPException(status_code=503, detail="Database disabled")
    created = service.db.create_user(req.username, req.password, req.role)
    if not created:
        raise HTTPException(status_code=400, detail="User already exists or could not be created")
    return {"status": "created", "user": created}


@app.post("/api/auth/password")
def change_password(req: PasswordChangeRequest):
    """Updates password for a user after verifying current password."""
    if not service.db.enabled:
        raise HTTPException(status_code=503, detail="Database disabled")
    auth = service.db.authenticate_user(req.username, req.current_password)
    if not auth:
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    ok = service.db.update_user_password(req.username, req.new_password)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update password")
    return {"status": "updated"}


# --- Lock Principle (Principio del Candado) CRUD Endpoints ---


@app.get("/api/locks")
def get_service_locks():
    """Retrieves all service locks and supervised users."""
    if not service.db.enabled:
        return {"service_locks": [], "supervised_users": []}
    return {
        "service_locks": service.db.get_service_locks(),
        "supervised_users": service.db.get_supervised_users(),
    }


@app.get("/api/locks/{service_name}")
def get_service_lock(service_name: str):
    """Retrieves the lock configuration and user list for a specific service (e.g. gmail)."""
    if not service.db.enabled:
        raise HTTPException(status_code=503, detail="Database disabled")
    lock = service.db.get_service_lock(service_name)
    users = service.db.get_supervised_users(service_name=service_name)
    return {
        "lock": lock,
        "users": users,
    }


@app.post("/api/locks")
def set_service_lock(req: LockConfigRequest):
    """Creates or updates a service supervision lock."""
    if not service.db.enabled:
        raise HTTPException(status_code=503, detail="Database disabled")
    lock = service.db.set_service_lock(
        service_name=req.service_name,
        is_supervision_enabled=req.is_supervision_enabled,
        mode=req.mode,
        description=req.description,
    )
    if not lock:
        raise HTTPException(status_code=400, detail="Failed to configure service lock")
    return {"status": "success", "lock": lock}


@app.delete("/api/locks/{service_name}")
def delete_service_lock(service_name: str):
    """Deletes a service lock configuration."""
    if not service.db.enabled:
        raise HTTPException(status_code=503, detail="Database disabled")
    ok = service.db.delete_service_lock(service_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Service lock not found")
    return {"status": "deleted"}


@app.get("/api/locks/{service_name}/users")
def get_supervised_users(service_name: str):
    """Gets supervised users for a specific service."""
    if not service.db.enabled:
        return []
    return service.db.get_supervised_users(service_name=service_name)


@app.post("/api/locks/{service_name}/users")
def add_supervised_user(service_name: str, req: SupervisedUserCreateRequest):
    """Adds a supervised user under the lock principle."""
    if not service.db.enabled:
        raise HTTPException(status_code=503, detail="Database disabled")
    user = service.db.add_supervised_user(
        service_name=service_name,
        identifier=req.identifier,
        description=req.description,
        is_active=req.is_active,
    )
    if not user:
        raise HTTPException(status_code=400, detail="Failed to add supervised user")
    return {"status": "created", "user": user}


@app.patch("/api/locks/users/{user_id}")
def update_supervised_user(user_id: int, req: SupervisedUserUpdateRequest):
    """Updates a supervised user entry."""
    if not service.db.enabled:
        raise HTTPException(status_code=503, detail="Database disabled")
    updated = service.db.update_supervised_user(
        user_id=user_id,
        is_active=req.is_active,
        description=req.description,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Supervised user not found")
    return {"status": "updated", "user": updated}


@app.delete("/api/locks/users/{user_id}")
def delete_supervised_user(user_id: int):
    """Deletes a supervised user entry."""
    if not service.db.enabled:
        raise HTTPException(status_code=503, detail="Database disabled")
    ok = service.db.delete_supervised_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Supervised user not found")
    return {"status": "deleted"}



@app.get("/api/audit-logs")
def get_audit_logs(limit: int = 50):
    return service.db.get_audit_logs(limit=limit)


@app.get("/api/trajectories")
def get_trajectories(session_id: Optional[str] = None, limit: int = 100):
    return service.db.get_trajectories(session_id=session_id, limit=limit)


# --- Reactions & Feedback Endpoints ---

@app.get("/api/reactions")
def get_reactions(message_id: Optional[str] = None, limit: int = 50):
    """Lists recent user reactions recorded across Chat or the simulator."""
    if not service.db.enabled:
        return []
    return service.db.get_reactions(message_id=message_id, limit=limit)


@app.get("/api/reactions/summary")
def get_reactions_summary():
    """Returns aggregated sentiment metrics (positive, negative, neutral) from reactions."""
    if not service.db.enabled:
        return {"total": 0, "positive": 0, "negative": 0, "neutral": 0, "recent": []}
    return service.db.get_reactions_summary()


@app.post("/api/reactions")
def add_reaction(req: ReactionRequest):
    """Records a reaction (e.g. thumbs up/down) directly from UI or webhook."""
    if not service.db.enabled:
        raise HTTPException(status_code=503, detail="Database disabled")

    recorded = service.db.log_reaction(
        message_id=req.message_id,
        space_id=req.space_id or "spaces/DEMO_SPACE",
        emoji=req.emoji,
        user_email=req.user_email,
        user_name=req.user_name,
        action=req.action,
    )

    # Also log as a trajectory step for full traceability
    session_id = f"thread_{req.message_id}"
    sentiment = recorded.get("sentiment", "POSITIVE") if recorded else "POSITIVE"
    service.db.log_trajectory(
        session_id=session_id,
        step_type="user_reaction",
        content=f"User {req.user_name or req.user_email} reacted with {req.emoji} (Sentiment: {sentiment}) to message {req.message_id}",
        metadata={
            "emoji": req.emoji,
            "sentiment": sentiment,
            "user_email": req.user_email,
            "action": req.action,
            "message_id": req.message_id,
        },
    )

    return {"status": "recorded", "reaction": recorded}



# --- Token Usage & Model Configuration Endpoints ---

@app.get("/api/tokens/stats")
def get_token_usage_stats():
    """Returns token consumption, calculated cost, breakdown by model and service, and limit config."""
    stats = service.db.get_token_usage_stats() if service.db.enabled else {
        "total_tokens": 0, "prompt_tokens": 0, "candidate_tokens": 0, "total_cost_usd": 0.0,
        "invocations_count": 0, "by_model": {}, "by_service": {}
    }
    active_model = service.agent.get_active_model()
    configs = service.db.get_all_capsule_configs() if service.db.enabled else {}

    return {
        "stats": stats,
        "active_model": active_model,
        "active_model_details": MODEL_PRICING_MAP.get(active_model),
        "limits": {
            "max_tokens_limit": int(configs.get("max_tokens_limit", 1000000)),
            "max_cost_limit_usd": float(configs.get("max_cost_limit_usd", 5.00)),
            "is_limit_enforced": configs.get("is_limit_enforced", "true").lower() in ("true", "1", "yes"),
            "limit_reached_message": configs.get("limit_reached_message", "He alcanzado mi límite operativo asignado de consumo y no puedo generar respuestas autónomas en este momento. Mi supervisor humano ha sido notificado."),
            "max_replies_per_hour": int(configs.get("max_replies_per_hour", 30)),
        }
    }


@app.get("/api/tokens/logs")
def get_token_usage_logs(limit: int = 50):
    """Returns recent token usage logs."""
    if not service.db.enabled:
        return []
    return service.db.get_token_usage_logs(limit=limit)


@app.get("/api/tokens/models")
def get_available_models():
    """Lists Gemini conversational models with pricing and capabilities."""
    active_model = service.agent.get_active_model()
    return {
        "active_model": active_model,
        "models": AVAILABLE_MODELS,
    }


@app.post("/api/tokens/model")
def set_active_model(req: ModelSelectionRequest):
    """Changes the active Gemini model for the capsule dynamically."""
    if not service.db.enabled:
        raise HTTPException(status_code=503, detail="Database disabled")
    if req.model_id not in MODEL_PRICING_MAP:
        raise HTTPException(status_code=400, detail=f"Model '{req.model_id}' is not in the conversational models catalog")

    ok = service.db.set_capsule_config("active_model", req.model_id, "Selected active conversational model")
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to persist model selection")
    return {
        "status": "updated",
        "active_model": req.model_id,
        "model_details": MODEL_PRICING_MAP.get(req.model_id),
    }


@app.post("/api/tokens/limits")
def update_budget_limits(req: BudgetLimitsRequest):
    """Updates operational token and cost budget limits."""
    if not service.db.enabled:
        raise HTTPException(status_code=503, detail="Database disabled")

    service.db.set_capsule_config("max_tokens_limit", str(req.max_tokens_limit), "Maximum accumulated token limit")
    service.db.set_capsule_config("max_cost_limit_usd", str(req.max_cost_limit_usd), "Maximum accumulated cost limit in USD")
    service.db.set_capsule_config("is_limit_enforced", "true" if req.is_limit_enforced else "false", "Enforce consumption limit")
    if req.limit_reached_message:
        service.db.set_capsule_config("limit_reached_message", req.limit_reached_message, "Standard response when consumption limit is reached")
    if req.max_replies_per_hour is not None:
        service.db.set_capsule_config("max_replies_per_hour", str(req.max_replies_per_hour), "Maximum autonomous replies per hour")

    return {"status": "updated"}


@app.post("/api/simulate/evaluate")
async def simulate_incoming_message(req: TestMessageRequest):
    """Allows testing policy classification and Antigravity reasoning directly from the UI for both Gmail and Chat."""
    svc = req.service_name.lower().strip() if req.service_name else "gmail"

    if svc == "chat":
        img_bytes_list: List[bytes] = []
        if req.image_b64:
            try:
                import base64
                b64_clean = req.image_b64
                if "," in b64_clean:
                    b64_clean = b64_clean.split(",", 1)[1]
                img_bytes_list.append(base64.b64decode(b64_clean))
            except Exception as e:
                logger.warning(f"Could not decode simulated image_b64: {e}")

        is_dm = req.is_direct_message
        if is_dm is None:
            space_lower = (req.space_id or "").lower()
            is_dm = "dm" in space_lower or "direct" in space_lower or space_lower == "spaces/dm"

        chat_msg = ChatItem(
            message_id=f"sim_chat_{int(asyncio.get_event_loop().time())}",
            space_id=req.space_id or "spaces/DEMO_SPACE",
            thread_id=f"sim_thread_{int(asyncio.get_event_loop().time())}",
            sender=req.sender,
            sender_name=req.sender.split("@")[0] if "@" in req.sender else req.sender,
            clean_body=req.body,
            service_name="chat",
            is_direct_message=is_dm,
            image_bytes_list=img_bytes_list,
            thread_history=req.thread_history,
        )
        result = await service.process_single_chat_message(chat_msg, force_simulated=True)
        return result

    # Default: Gmail
    msg = EmailItem(
        message_id=f"sim_{int(asyncio.get_event_loop().time())}",
        thread_id=f"sim_th_{int(asyncio.get_event_loop().time())}",
        sender=req.sender,
        recipient=service.agent.profile.get("identity", {}).get("email", "atlas.synthetic@gmail.com"),
        subject=req.subject or "No Subject",
        snippet=req.body[:80],
        clean_body=req.body,
        service_name="gmail",
    )
    result = await service.process_single_email(msg, force_simulated_draft=True)
    return result


@app.post("/api/chat/webhook")
@app.post("/webhooks/chat")
async def google_chat_webhook(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    """Webhook endpoint receiving events from Google Chat (messages and user reactions)."""
    connector = service.chat or GoogleChatConnector(credentials=None)

    # 1. Check if this is a reaction event (thumbs up/down)
    reaction_item = connector.parse_reaction_event(payload)
    if reaction_item:
        logger.info(f"Incoming reaction event: {reaction_item.emoji} from {reaction_item.user_email} on {reaction_item.message_id}")
        if service.db and service.db.enabled:
            service.db.log_reaction(
                message_id=reaction_item.message_id,
                space_id=reaction_item.space_id,
                emoji=reaction_item.emoji,
                user_email=reaction_item.user_email,
                user_name=reaction_item.user_name,
                action=reaction_item.action,
                sentiment=reaction_item.sentiment,
            )
            session_id = f"thread_{reaction_item.message_id}"
            service.db.log_trajectory(
                session_id=session_id,
                step_type="user_reaction",
                content=f"Google Chat User {reaction_item.user_name} reacted with {reaction_item.emoji} ({reaction_item.sentiment})",
                metadata=reaction_item.model_dump(),
            )
        return {}

    # 2. Otherwise parse as standard message
    chat_item = connector.parse_webhook_event(payload)

    if not chat_item:
        # Returning an empty dictionary tells Google Chat that no message should be posted
        # (e.g. untagged messages in collaborative group spaces)
        return {}

    # Pre-register message_id in dedup set so the poller skips it while the webhook is processing
    if chat_item.message_id in service._processed_chat_ids:
        logger.info(f"Webhook skipping already processed message {chat_item.message_id}")
        return {}

    # Process message through policy and AI agent with synchronous webhook response mode
    result = await service.process_single_chat_message(chat_item, from_webhook=True)
    draft = result.get("draft")
    policy = result.get("policy", {})
    action_type = policy.get("recommended_action")

    # If action was SEND and draft_body is present, return JSON with text for Google Chat
    if action_type == ActionType.SEND.value or action_type == ActionType.SEND:
        if draft and draft.get("draft_body"):
            logger.info(f"Delivering Google Chat webhook response to {chat_item.space_id} (DM: {chat_item.is_direct_message}): {draft.get('draft_body')[:80]}...")
            return {"text": draft.get("draft_body")}

    # If blocked by operational token limit, deliver the limit notification text to inform the user
    if not result.get("allowed") and draft and draft.get("draft_body") and "límite" in draft.get("draft_body", "").lower():
        logger.info(f"Delivering Google Chat consumption limit notification to {chat_item.space_id}")
        return {"text": draft.get("draft_body")}

    return {}


@app.post("/webhook/trigger")
async def trigger_cycle(background_tasks: BackgroundTasks):
    background_tasks.add_task(service.process_pending_emails)
    background_tasks.add_task(service.process_pending_chat_messages)
    return {"status": "triggered"}


@app.post("/api/pubsub/gmail")
async def gmail_pubsub_push_handler(request: Request, background_tasks: BackgroundTasks):
    """Handles real-time push events from Cloud Pub/Sub sent by Gmail API watch()."""
    try:
        body = await request.json()
        message = body.get("message", {})
        data_b64 = message.get("data")
        if data_b64:
            import base64
            import json
            decoded_json = json.loads(base64.b64decode(data_b64).decode("utf-8"))
            email_address = decoded_json.get("emailAddress")
            history_id = decoded_json.get("historyId")
            logger.info(f"Received Gmail Pub/Sub push notification for {email_address} (historyId: {history_id})")
        else:
            logger.info("Received Gmail Pub/Sub push notification without payload data.")
    except Exception as e:
        logger.warning(f"Could not parse Pub/Sub payload, proceeding to process emails: {e}")

    # Immediately process pending emails asynchronously upon receiving push notification
    background_tasks.add_task(service.process_pending_emails)
    return {"status": "received"}


# --- Calendar & Automations Endpoints ---

@app.get("/api/calendar/list")
async def get_available_calendars():
    """Lists all accessible Google Calendars for the account."""
    if not service.calendar:
        return {"calendars": []}
    cals = service.calendar.list_calendars()
    selected = service.automations.get_selected_calendars()
    selected_ids = {s.get("id") for s in selected}
    for c in cals:
        c["selected"] = c.get("id") in selected_ids
    return {"calendars": cals, "selected": selected}


@app.get("/api/calendar/config")
async def get_calendar_config():
    """Gets currently selected calendar configurations."""
    selected = service.automations.get_selected_calendars()
    return {"selected_calendars": selected}


@app.post("/api/calendar/config")
async def save_calendar_config(payload: Dict[str, Any]):
    """Saves selected calendars configuration and triggers sync."""
    selected = payload.get("selected_calendars", [])
    saved = service.automations.set_selected_calendars(selected)
    await service.sync_calendar_context(force=True)
    return {"status": "saved", "selected_calendars": saved}


@app.get("/api/calendar/events")
async def get_calendar_events(days: int = 14):
    """Retrieves upcoming calendar events across configured calendars and current memory summary."""
    events = []
    selected = service.automations.get_selected_calendars()
    if service.calendar:
        if selected:
            events = service.calendar.get_events_from_multiple_calendars(selected, days_ahead=days)
        else:
            # Fallback to auto-discovered family or primary
            cals = service.calendar.list_calendars()
            chosen_id = "primary"
            chosen_label = "Calendario Principal"
            for c in cals:
                summary_lower = (c.get("summary") or "").lower()
                if "famil" in summary_lower or "casa" in summary_lower:
                    chosen_id = c.get("id")
                    chosen_label = c.get("summary") or "Familia"
                    break
            events = service.calendar.get_events_from_multiple_calendars([{"id": chosen_id, "label": chosen_label}], days_ahead=days)

    cal_context = service.agent._load_calendar_context()
    return {
        "days": days,
        "count": len(events),
        "events": events,
        "cached_context": cal_context,
        "selected_calendars": selected,
        "last_sync": service._last_calendar_sync.isoformat() if service._last_calendar_sync else None,
    }


@app.post("/api/calendar/sync")
async def force_calendar_sync():
    """Manually forces a calendar sync across all configured calendars and memory update."""
    if not service.calendar:
        return {"status": "error", "message": "Google Calendar connector is not configured"}
    await service.sync_calendar_context(force=True)
    return {
        "status": "success",
        "selected_calendars": service.automations.get_selected_calendars(),
        "timestamp": service._last_calendar_sync.isoformat() if service._last_calendar_sync else None,
    }


@app.get("/api/automations")
async def list_automations():
    """Lists all configured automations."""
    return service.automations.list_automations()


@app.post("/api/automations")
async def upsert_automation(payload: Dict[str, Any]):
    """Creates or updates an automation."""
    item = AutomationItem.from_dict(payload)
    saved = service.automations.upsert_automation(item)
    return saved.to_dict()


@app.delete("/api/automations/{automation_id}")
async def delete_automation(automation_id: str):
    """Deletes an automation by ID."""
    deleted = service.automations.delete_automation(automation_id)
    return {"success": deleted, "id": automation_id}


@app.patch("/api/automations/{automation_id}/toggle")
async def toggle_automation(automation_id: str, payload: Dict[str, bool]):
    """Toggles active status of an automation."""
    enabled = payload.get("enabled", True)
    item = service.automations.toggle_automation(automation_id, enabled)
    if not item:
        raise HTTPException(status_code=404, detail="Automation not found")
    return item.to_dict()


@app.post("/api/automations/run/{automation_id}")
async def run_automation_now(automation_id: str):
    """Triggers an immediate execution of a specific automation."""
    item = service.automations.get_automation(automation_id)
    if not item:
        raise HTTPException(status_code=404, detail="Automation not found")
    res = await service.execute_automation(item)
    return res


@app.post("/api/automations/run")
async def run_scheduled_automations(background_tasks: BackgroundTasks, force: bool = False):
    """Webhook entrypoint for Cloud Scheduler or cron triggers to execute active automations.
    By default runs due automations; if force=true, triggers all active ones.
    """
    if force:
        items = [AutomationItem.from_dict(it) for it in service.automations.list_automations() if it.get("enabled")]
    else:
        items = service.automations.get_due_automations()

    executed = []
    for item in items:
        # Run in background to respond swiftly to Cloud Scheduler
        background_tasks.add_task(service.execute_automation, item)
        executed.append(item.id)
    return {"status": "triggered", "count": len(executed), "automations": executed}


# --- Skills & Habilidades Endpoints ---

@app.get("/api/skills")
def list_skills():
    """Lists all registered skills with metadata and enabled status."""
    return service.skills.list_skills()


@app.get("/api/skills/{skill_id}")
def get_skill_detail(skill_id: str):
    """Retrieves full skill details including SKILL.md markdown content."""
    skill = service.skills.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill.to_dict(include_content=True)


@app.post("/api/skills")
def create_skill(req: SkillCreateRequest):
    """Creates a new skill directory with SKILL.md and registers it."""
    created = service.skills.create_skill(
        name=req.name,
        description=req.description,
        content=req.content,
        enabled=req.enabled,
    )
    return created.to_dict(include_content=True)


@app.put("/api/skills/{skill_id}")
def update_skill(skill_id: str, req: SkillUpdateRequest):
    """Updates an existing skill's metadata and/or markdown body."""
    updated = service.skills.update_skill(
        skill_id=skill_id,
        name=req.name,
        description=req.description,
        content=req.content,
        enabled=req.enabled,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Skill not found")
    return updated.to_dict(include_content=True)


@app.patch("/api/skills/{skill_id}/toggle")
def toggle_skill(skill_id: str, req: SkillToggleRequest):
    """Toggles active state of a skill."""
    toggled = service.skills.toggle_skill(skill_id=skill_id, enabled=req.enabled)
    if not toggled:
        raise HTTPException(status_code=404, detail="Skill not found")
    return toggled.to_dict(include_content=False)


@app.delete("/api/skills/{skill_id}")
def delete_skill(skill_id: str):
    """Deletes a skill directory and unregisters it."""
    deleted = service.skills.delete_skill(skill_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"success": True, "id": skill_id}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, log_level="info")
