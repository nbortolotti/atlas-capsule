import datetime
import json
import logging
import os
from typing import Any, Dict, List, Optional
import hashlib
import hmac
from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()
logger = logging.getLogger(__name__)

Base = declarative_base()


def hash_password(password: str) -> str:
    """Hashes a password with SHA256 and a random salt."""
    import secrets
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return f"{salt}:{dk.hex()}"


def verify_password(stored_password: str, provided_password: str) -> bool:
    """Verifies a plain password against the stored salt:hash string."""
    try:
        salt, expected_hash = stored_password.split(":")
        dk = hashlib.pbkdf2_hmac("sha256", provided_password.encode("utf-8"), salt.encode("utf-8"), 100000)
        return hmac.compare_digest(dk.hex(), expected_hash)
    except Exception:
        return False


class AuditLog(Base):
    __tablename__ = "agent_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(128), index=True, nullable=False)
    thread_id = Column(String(128), index=True, nullable=False)
    sender = Column(String(255), nullable=False)
    subject = Column(String(500), nullable=True)
    risk_level = Column(String(32), nullable=False)
    action_taken = Column(String(64), nullable=False)
    reasons = Column(Text, nullable=True)
    draft_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "sender": self.sender,
            "subject": self.subject,
            "risk_level": self.risk_level,
            "action_taken": self.action_taken,
            "reasons": self.reasons,
            "draft_id": self.draft_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AgentTrajectoryStep(Base):
    __tablename__ = "agent_trajectory_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(128), index=True, nullable=False)
    step_type = Column(String(64), nullable=False)  # thought, tool_call, tool_result, response
    content = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        meta = {}
        if self.metadata_json:
            try:
                meta = json.loads(self.metadata_json)
            except Exception:
                meta = {"raw": self.metadata_json}
        return {
            "id": self.id,
            "session_id": self.session_id,
            "step_type": self.step_type,
            "content": self.content,
            "metadata": meta,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SecurityRule(Base):
    __tablename__ = "security_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_type = Column(String(64), nullable=False)  # blocked_sender, allowed_domain, blacklisted_keyword
    value = Column(String(255), nullable=False, unique=True)
    description = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "rule_type": self.rule_type,
            "value": self.value,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ServiceLock(Base):
    """Represents supervision lock configuration for external connectors/services (Principio del Candado)."""
    __tablename__ = "service_locks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    service_name = Column(String(64), nullable=False, unique=True, index=True)  # e.g. "gmail", "slack"
    is_supervision_enabled = Column(Boolean, default=True, nullable=False)  # True = lock is active (drafts/supervision required)
    mode = Column(String(32), default="ALL", nullable=False)  # "ALL" (applies to everyone) or "SPECIFIC" (only supervised_users list)
    description = Column(String(500), nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "service_name": self.service_name,
            "is_supervision_enabled": self.is_supervision_enabled,
            "mode": self.mode,
            "description": self.description,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SupervisedUser(Base):
    """Users/senders explicitly monitored or exempted under the Lock Principle."""
    __tablename__ = "supervised_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    service_name = Column(String(64), nullable=False, index=True)  # e.g. "gmail"
    identifier = Column(String(255), nullable=False)  # e.g. "user@example.com" or domain "@domain.com"
    description = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "service_name": self.service_name,
            "identifier": self.identifier,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class User(Base):
    """Admin and console users with authentication."""
    __tablename__ = "admin_users_auth"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(128), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(32), default="admin", nullable=False)  # "admin", "viewer", etc.
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TokenUsageLog(Base):
    """Logs token consumption and estimated cost per invocation."""
    __tablename__ = "token_usage_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(128), index=True, nullable=False)
    service_name = Column(String(64), nullable=False)  # "gmail", "chat", "simulator"
    model = Column(String(128), nullable=False)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    candidate_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    estimated_cost_usd = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "service_name": self.service_name,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "candidate_tokens": self.candidate_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CapsuleConfig(Base):
    """Dynamic operational settings: active model, budget limit, token limit, etc."""
    __tablename__ = "capsule_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(64), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False)
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "value": self.value,
            "description": self.description,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class MessageReaction(Base):
    """Stores user feedback reactions (thumbs up/down, emojis) on assistant messages or space items."""
    __tablename__ = "message_reactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(255), index=True, nullable=False)
    space_id = Column(String(128), index=True, nullable=False)
    emoji = Column(String(32), nullable=False)  # e.g. 👍, 👎, ❤️
    sentiment = Column(String(32), default="POSITIVE", nullable=False)  # POSITIVE, NEGATIVE, NEUTRAL
    user_email = Column(String(255), nullable=False)
    user_name = Column(String(255), nullable=True)
    action = Column(String(32), default="CREATED", nullable=False)  # CREATED or DELETED
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "space_id": self.space_id,
            "emoji": self.emoji,
            "sentiment": self.sentiment,
            "user_email": self.user_email,
            "user_name": self.user_name,
            "action": self.action,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ProcessedMessageClaim(Base):
    """Distributed atomic claim lock across container instances to prevent duplicate replies."""
    __tablename__ = "processed_message_claims"

    message_id = Column(String(255), primary_key=True)
    service_name = Column(String(64), nullable=False)  # "chat", "gmail"
    claimed_by_instance = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "service_name": self.service_name,
            "claimed_by_instance": self.claimed_by_instance,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class DatabaseManager:
    """Manages Cloud SQL MySQL connection pooling and schema initialization."""

    def __init__(self):
        self.enabled = os.environ.get("USE_CLOUDSQL", "true").lower() in ("true", "1", "yes")
        self.engine = None
        self.SessionLocal = None

        if self.enabled:
            self._init_db()

    def _init_db(self):
        user = os.environ.get("DB_USER", "root")
        password = os.environ.get("DB_PASSWORD", "")
        db_name = os.environ.get("DB_NAME", "atlas_capsule")
        conn_name = os.environ.get("DB_CONNECTION_NAME")
        host = os.environ.get("DB_HOST", "127.0.0.1")
        port = os.environ.get("DB_PORT", "3307")

        if os.environ.get("K_SERVICE"):  # Running in Cloud Run environment
            unix_socket_path = f"/cloudsql/{conn_name}"
            db_url = f"mysql+pymysql://{user}:{password}@/{db_name}?unix_socket={unix_socket_path}&charset=utf8mb4"
        else:
            db_url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{db_name}?charset=utf8mb4"

        try:
            self.engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=1800)
            Base.metadata.create_all(bind=self.engine)
            self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
            logger.info("Connected to Cloud SQL / MySQL and created tables.")
            self._seed_default_rules()
        except Exception as e:
            logger.error(f"Failed to connect to MySQL database: {e}")
            self.enabled = False

    def _seed_default_rules(self):
        """Seeds default security rules and default service locks if tables are empty."""
        if not self.SessionLocal:
            return
        session = self.SessionLocal()
        try:
            count = session.query(SecurityRule).count()
            if count == 0:
                defaults = [
                    SecurityRule(rule_type="blacklisted_keyword", value="password", description="Security credential"),
                    SecurityRule(rule_type="blacklisted_keyword", value="transferencia", description="Financial transfer"),
                    SecurityRule(rule_type="blacklisted_keyword", value="clave de acceso", description="Access token/key"),
                    SecurityRule(rule_type="blacklisted_keyword", value="reset", description="Account reset request"),
                    SecurityRule(rule_type="blocked_sender", value="spammer@malicious.com", description="Known spam source"),
                    SecurityRule(rule_type="blocked_sender", value="no-reply@", description="Automated broadcast bot"),
                    SecurityRule(rule_type="allowed_domain", value="*", description="Allow all domains by default"),
                    SecurityRule(rule_type="trusted_sender", value=os.environ.get("DEFAULT_ADMIN_EMAIL", "admin@example.com"), description="Trusted sender: auto-reply allowed"),
                    SecurityRule(rule_type="admin_user", value=os.environ.get("DEFAULT_ADMIN_EMAIL", "admin@example.com"), description="Administrator: can teach and modify behavior memory"),
                ]
                session.add_all(defaults)
                session.commit()
                logger.info("Seeded initial security rules into Cloud SQL.")

            # Seed default ServiceLock for Gmail if not exists
            lock_count = session.query(ServiceLock).filter(ServiceLock.service_name == "gmail").count()
            if lock_count == 0:
                gmail_lock = ServiceLock(
                    service_name="gmail",
                    is_supervision_enabled=True,
                    mode="ALL",
                    description="Supervisión activa para Gmail (Principio del Candado). Por defecto requiere borrador/revisión.",
                )
                session.add(gmail_lock)
                session.commit()
                logger.info("Seeded default Gmail ServiceLock into Cloud SQL.")

            # Seed default ServiceLock for Google Chat if not exists
            chat_lock_count = session.query(ServiceLock).filter(ServiceLock.service_name == "chat").count()
            if chat_lock_count == 0:
                chat_lock = ServiceLock(
                    service_name="chat",
                    is_supervision_enabled=True,
                    mode="ALL",
                    description="Supervisión activa para Google Chat (Principio del Candado). Por defecto requiere borrador/revisión.",
                )
                session.add(chat_lock)
                session.commit()
                logger.info("Seeded default Google Chat ServiceLock into Cloud SQL.")

            # Seed default admin user if not exists
            default_admin_user = os.environ.get("ADMIN_DEFAULT_USERNAME", "admin")
            admin_user = session.query(User).filter(User.username == default_admin_user).first()
            if not admin_user:
                default_admin_pass = os.environ.get("ADMIN_DEFAULT_PASSWORD", "changeme_capsule_secret_123!")
                new_admin = User(
                    username=default_admin_user,
                    hashed_password=hash_password(default_admin_pass),
                    role="admin",
                    is_active=True,
                )
                session.add(new_admin)
                session.commit()
                logger.info(f"Seeded default admin user {default_admin_user} into database.")

            # Seed default capsule config (active model, limits, etc.)
            default_model = os.environ.get("AGENT_MODEL", "gemini-2.5-flash-lite")
            cfg_defaults = [
                ("active_model", default_model, "Selected active conversational model"),
                ("max_tokens_limit", "1000000", "Maximum accumulated token limit before capsule pauses autonomy"),
                ("max_cost_limit_usd", "5.00", "Maximum accumulated cost limit in USD before capsule pauses autonomy"),
                ("is_limit_enforced", "true", "Whether token/cost limits are actively enforced by policy gate"),
                ("limit_reached_message", "He alcanzado mi límite operativo asignado de consumo y no puedo generar respuestas autónomas en este momento. Mi supervisor humano ha sido notificado.", "Standard response when consumption limit is reached"),
            ]
            for key, val, desc in cfg_defaults:
                exists = session.query(CapsuleConfig).filter(CapsuleConfig.key == key).first()
                if not exists:
                    session.add(CapsuleConfig(key=key, value=val, description=desc))
            session.commit()
            logger.info("Seeded default capsule configurations into Cloud SQL.")
        except Exception as e:
            logger.error(f"Failed to seed defaults: {e}")
            session.rollback()
        finally:
            session.close()

    def log_action(
        self,
        message_id: str,
        thread_id: str,
        sender: str,
        subject: str,
        risk_level: str,
        action_taken: str,
        reasons: str,
        draft_id: Optional[str] = None,
    ):
        if not self.enabled or not self.SessionLocal:
            return

        session = self.SessionLocal()
        try:
            log_entry = AuditLog(
                message_id=message_id,
                thread_id=thread_id,
                sender=sender,
                subject=subject,
                risk_level=risk_level,
                action_taken=action_taken,
                reasons=reasons,
                draft_id=draft_id,
            )
            session.add(log_entry)
            session.commit()
        except Exception as e:
            logger.error(f"Failed to write audit log to MySQL: {e}")
            session.rollback()
        finally:
            session.close()

    def is_message_processed(self, message_id: str) -> bool:
        """Checks if a message_id has already been processed and recorded in the audit log or claimed."""
        if not self.enabled or not self.SessionLocal or not message_id:
            return False
        session = self.SessionLocal()
        try:
            exists_audit = session.query(AuditLog.id).filter(AuditLog.message_id == message_id).first()
            if exists_audit is not None:
                return True
            exists_claim = session.query(ProcessedMessageClaim.message_id).filter(ProcessedMessageClaim.message_id == message_id).first()
            return exists_claim is not None
        except Exception as e:
            logger.error(f"Error checking if message {message_id} is processed: {e}")
            return False
        finally:
            session.close()

    def claim_message(self, message_id: str, service_name: str = "chat", instance_id: Optional[str] = None) -> bool:
        """
        Atomically claims a message in Cloud SQL before processing starts.
        Returns True if the claim was successfully acquired (this instance should process it).
        Returns False if the message was already claimed or processed by another instance.
        """
        if not self.enabled or not self.SessionLocal or not message_id:
            return True  # If DB disabled, fallback to local memory dedup
        session = self.SessionLocal()
        try:
            # Check audit log first
            if session.query(AuditLog.id).filter(AuditLog.message_id == message_id).first():
                return False

            claim = ProcessedMessageClaim(
                message_id=message_id,
                service_name=service_name,
                claimed_by_instance=instance_id or os.environ.get("HOSTNAME", "local"),
            )
            session.add(claim)
            session.commit()
            return True
        except Exception as e:
            # IntegrityError or duplicate key means another replica won the race
            session.rollback()
            logger.info(f"Message {message_id} already claimed or in-flight in another instance: {e}")
            return False
        finally:
            session.close()

    def get_processed_message_ids(self, limit: int = 1000) -> set:
        """Retrieves a set of recently processed message IDs from the audit log."""
        if not self.enabled or not self.SessionLocal:
            return set()
        session = self.SessionLocal()
        try:
            rows = (
                session.query(AuditLog.message_id)
                .order_by(AuditLog.id.desc())
                .limit(limit)
                .all()
            )
            return {r[0] for r in rows if r[0]}
        except Exception as e:
            logger.error(f"Error fetching processed message IDs: {e}")
            return set()
        finally:
            session.close()

    def log_trajectory(
        self,
        session_id: str,
        step_type: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        if not self.enabled or not self.SessionLocal:
            return

        session = self.SessionLocal()
        try:
            step = AgentTrajectoryStep(
                session_id=session_id,
                step_type=step_type,
                content=content,
                metadata_json=json.dumps(metadata) if metadata else None,
            )
            session.add(step)
            session.commit()
        except Exception as e:
            logger.error(f"Failed to record trajectory step: {e}")
            session.rollback()
        finally:
            session.close()

    def get_audit_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.enabled or not self.SessionLocal:
            return []
        session = self.SessionLocal()
        try:
            rows = session.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
            return [r.to_dict() for r in rows]
        finally:
            session.close()

    def get_trajectories(self, session_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if not self.enabled or not self.SessionLocal:
            return []
        session = self.SessionLocal()
        try:
            q = session.query(AgentTrajectoryStep)
            if session_id:
                q = q.filter(AgentTrajectoryStep.session_id == session_id)
            rows = q.order_by(AgentTrajectoryStep.id.desc()).limit(limit).all()
            return [r.to_dict() for r in rows]
        finally:
            session.close()

    def get_security_rules(self) -> List[Dict[str, Any]]:
        if not self.enabled or not self.SessionLocal:
            return []
        session = self.SessionLocal()
        try:
            rows = session.query(SecurityRule).order_by(SecurityRule.id.asc()).all()
            return [r.to_dict() for r in rows]
        finally:
            session.close()

    def add_security_rule(self, rule_type: str, value: str, description: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not self.enabled or not self.SessionLocal:
            return None
        session = self.SessionLocal()
        try:
            rule = SecurityRule(rule_type=rule_type, value=value, description=description, is_active=True)
            session.add(rule)
            session.commit()
            return rule.to_dict()
        except Exception as e:
            logger.error(f"Error adding rule: {e}")
            session.rollback()
            return None
        finally:
            session.close()

    def delete_security_rule(self, rule_id: int) -> bool:
        if not self.enabled or not self.SessionLocal:
            return False
        session = self.SessionLocal()
        try:
            rule = session.query(SecurityRule).filter(SecurityRule.id == rule_id).first()
            if rule:
                session.delete(rule)
                session.commit()
                return True
            return False
        except Exception as e:
            logger.error(f"Error deleting rule: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    # --- Lock Principle (Principio del Candado) CRUD ---

    def get_service_locks(self) -> List[Dict[str, Any]]:
        """Returns all registered service lock configurations."""
        if not self.enabled or not self.SessionLocal:
            return []
        session = self.SessionLocal()
        try:
            locks = session.query(ServiceLock).order_by(ServiceLock.service_name.asc()).all()
            return [l.to_dict() for l in locks]
        finally:
            session.close()

    def get_service_lock(self, service_name: str) -> Optional[Dict[str, Any]]:
        """Returns a specific service lock configuration by service name."""
        if not self.enabled or not self.SessionLocal:
            return None
        session = self.SessionLocal()
        try:
            lock = session.query(ServiceLock).filter(ServiceLock.service_name == service_name.lower()).first()
            return lock.to_dict() if lock else None
        finally:
            session.close()

    def set_service_lock(
        self,
        service_name: str,
        is_supervision_enabled: bool,
        mode: str = "ALL",
        description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Creates or updates a service supervision lock."""
        if not self.enabled or not self.SessionLocal:
            return None
        session = self.SessionLocal()
        try:
            lock = session.query(ServiceLock).filter(ServiceLock.service_name == service_name.lower()).first()
            if lock:
                lock.is_supervision_enabled = is_supervision_enabled
                lock.mode = mode.upper()
                if description is not None:
                    lock.description = description
                lock.updated_at = datetime.datetime.utcnow()
            else:
                lock = ServiceLock(
                    service_name=service_name.lower(),
                    is_supervision_enabled=is_supervision_enabled,
                    mode=mode.upper(),
                    description=description,
                )
                session.add(lock)
            session.commit()
            return lock.to_dict()
        except Exception as e:
            logger.error(f"Error upserting service lock for {service_name}: {e}")
            session.rollback()
            return None
        finally:
            session.close()

    def delete_service_lock(self, service_name: str) -> bool:
        """Deletes a service lock configuration."""
        if not self.enabled or not self.SessionLocal:
            return False
        session = self.SessionLocal()
        try:
            lock = session.query(ServiceLock).filter(ServiceLock.service_name == service_name.lower()).first()
            if lock:
                session.delete(lock)
                session.commit()
                return True
            return False
        except Exception as e:
            logger.error(f"Error deleting service lock: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def get_supervised_users(self, service_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns the list of supervised/controlled users under the Lock Principle."""
        if not self.enabled or not self.SessionLocal:
            return []
        session = self.SessionLocal()
        try:
            q = session.query(SupervisedUser)
            if service_name:
                q = q.filter(SupervisedUser.service_name == service_name.lower())
            rows = q.order_by(SupervisedUser.id.asc()).all()
            return [r.to_dict() for r in rows]
        finally:
            session.close()

    def add_supervised_user(
        self,
        service_name: str,
        identifier: str,
        description: Optional[str] = None,
        is_active: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Adds a user/domain to the lock principle list."""
        if not self.enabled or not self.SessionLocal:
            return None
        session = self.SessionLocal()
        try:
            item = SupervisedUser(
                service_name=service_name.lower(),
                identifier=identifier.strip().lower(),
                description=description,
                is_active=is_active,
            )
            session.add(item)
            session.commit()
            return item.to_dict()
        except Exception as e:
            logger.error(f"Error adding supervised user: {e}")
            session.rollback()
            return None
        finally:
            session.close()

    def update_supervised_user(
        self,
        user_id: int,
        is_active: Optional[bool] = None,
        description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Updates status or description of a supervised user."""
        if not self.enabled or not self.SessionLocal:
            return None
        session = self.SessionLocal()
        try:
            item = session.query(SupervisedUser).filter(SupervisedUser.id == user_id).first()
            if not item:
                return None
            if is_active is not None:
                item.is_active = is_active
            if description is not None:
                item.description = description
            session.commit()
            return item.to_dict()
        except Exception as e:
            logger.error(f"Error updating supervised user {user_id}: {e}")
            session.rollback()
            return None
        finally:
            session.close()

    def delete_supervised_user(self, user_id: int) -> bool:
        """Deletes a supervised user entry."""
        if not self.enabled or not self.SessionLocal:
            return False
        session = self.SessionLocal()
        try:
            item = session.query(SupervisedUser).filter(SupervisedUser.id == user_id).first()
            if item:
                session.delete(item)
                session.commit()
                return True
            return False
        except Exception as e:
            logger.error(f"Error deleting supervised user {user_id}: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    # --- User Authentication & Management ---

    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Validates credentials against admin_users_auth table."""
        if not self.enabled or not self.SessionLocal:
            return None
        session = self.SessionLocal()
        try:
            user = session.query(User).filter(User.username == username.strip(), User.is_active == True).first()
            if user and verify_password(user.hashed_password, password):
                return user.to_dict()
            return None
        finally:
            session.close()

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Retrieves user profile by username."""
        if not self.enabled or not self.SessionLocal:
            return None
        session = self.SessionLocal()
        try:
            user = session.query(User).filter(User.username == username.strip()).first()
            return user.to_dict() if user else None
        finally:
            session.close()

    def create_user(self, username: str, password: str, role: str = "admin") -> Optional[Dict[str, Any]]:
        """Creates a new user with hashed password."""
        if not self.enabled or not self.SessionLocal:
            return None
        session = self.SessionLocal()
        try:
            existing = session.query(User).filter(User.username == username.strip()).first()
            if existing:
                return None
            user = User(
                username=username.strip(),
                hashed_password=hash_password(password),
                role=role,
                is_active=True,
            )
            session.add(user)
            session.commit()
            return user.to_dict()
        except Exception as e:
            logger.error(f"Error creating user {username}: {e}")
            session.rollback()
            return None
        finally:
            session.close()

    def update_user_password(self, username: str, new_password: str) -> bool:
        """Updates user password."""
        if not self.enabled or not self.SessionLocal:
            return False
        session = self.SessionLocal()
        try:
            user = session.query(User).filter(User.username == username.strip()).first()
            if not user:
                return False
            user.hashed_password = hash_password(new_password)
            session.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating password for {username}: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def list_users(self) -> List[Dict[str, Any]]:
        """Lists all registered users (without passwords)."""
        if not self.enabled or not self.SessionLocal:
            return []
        session = self.SessionLocal()
        try:
            users = session.query(User).order_by(User.id.asc()).all()
            return [u.to_dict() for u in users]
        finally:
            session.close()

    # --- Token Usage & Limits Management ---

    def log_token_usage(
        self,
        session_id: str,
        service_name: str,
        model: str,
        prompt_tokens: int,
        candidate_tokens: int,
        total_tokens: int,
        estimated_cost_usd: float,
    ) -> Optional[Dict[str, Any]]:
        """Records token metrics and estimated cost in MySQL."""
        if not self.enabled or not self.SessionLocal:
            return None
        session = self.SessionLocal()
        try:
            log_item = TokenUsageLog(
                session_id=session_id,
                service_name=service_name,
                model=model,
                prompt_tokens=prompt_tokens,
                candidate_tokens=candidate_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=estimated_cost_usd,
            )
            session.add(log_item)
            session.commit()
            return log_item.to_dict()
        except Exception as e:
            logger.error(f"Error logging token usage: {e}")
            session.rollback()
            return None
        finally:
            session.close()

    def get_token_usage_stats(self) -> Dict[str, Any]:
        """Calculates total accumulated tokens, total cost, breakdown by model and by service."""
        default_stats = {
            "total_tokens": 0,
            "prompt_tokens": 0,
            "candidate_tokens": 0,
            "total_cost_usd": 0.0,
            "invocations_count": 0,
            "by_model": {},
            "by_service": {},
        }
        if not self.enabled or not self.SessionLocal:
            return default_stats

        session = self.SessionLocal()
        try:
            logs = session.query(TokenUsageLog).all()
            total_toks = 0
            prompt_toks = 0
            cand_toks = 0
            total_cost = 0.0
            by_model: Dict[str, Dict[str, Any]] = {}
            by_service: Dict[str, Dict[str, Any]] = {}

            for item in logs:
                total_toks += item.total_tokens
                prompt_toks += item.prompt_tokens
                cand_toks += item.candidate_tokens
                total_cost += item.estimated_cost_usd

                # Model breakdown
                m = item.model or "unknown"
                if m not in by_model:
                    by_model[m] = {"tokens": 0, "cost_usd": 0.0, "invocations": 0}
                by_model[m]["tokens"] += item.total_tokens
                by_model[m]["cost_usd"] = round(by_model[m]["cost_usd"] + item.estimated_cost_usd, 6)
                by_model[m]["invocations"] += 1

                # Service breakdown
                s = item.service_name or "unknown"
                if s not in by_service:
                    by_service[s] = {"tokens": 0, "cost_usd": 0.0, "invocations": 0}
                by_service[s]["tokens"] += item.total_tokens
                by_service[s]["cost_usd"] = round(by_service[s]["cost_usd"] + item.estimated_cost_usd, 6)
                by_service[s]["invocations"] += 1

            return {
                "total_tokens": total_toks,
                "prompt_tokens": prompt_toks,
                "candidate_tokens": cand_toks,
                "total_cost_usd": round(total_cost, 6),
                "invocations_count": len(logs),
                "by_model": by_model,
                "by_service": by_service,
            }
        except Exception as e:
            logger.error(f"Error querying token usage stats: {e}")
            return default_stats
        finally:
            session.close()

    def get_token_usage_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns recent token usage log entries."""
        if not self.enabled or not self.SessionLocal:
            return []
        session = self.SessionLocal()
        try:
            rows = session.query(TokenUsageLog).order_by(TokenUsageLog.id.desc()).limit(limit).all()
            return [r.to_dict() for r in rows]
        finally:
            session.close()

    def get_capsule_config(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieves a configuration value by key."""
        if not self.enabled or not self.SessionLocal:
            return default
        session = self.SessionLocal()
        try:
            cfg = session.query(CapsuleConfig).filter(CapsuleConfig.key == key).first()
            return cfg.value if cfg else default
        finally:
            session.close()

    def get_all_capsule_configs(self) -> Dict[str, str]:
        """Returns all configuration key-values as a dictionary."""
        if not self.enabled or not self.SessionLocal:
            return {}
        session = self.SessionLocal()
        try:
            rows = session.query(CapsuleConfig).all()
            return {r.key: r.value for r in rows}
        finally:
            session.close()

    def set_capsule_config(self, key: str, value: str, description: Optional[str] = None) -> bool:
        """Sets or updates a configuration key."""
        if not self.enabled or not self.SessionLocal:
            return False
        session = self.SessionLocal()
        try:
            cfg = session.query(CapsuleConfig).filter(CapsuleConfig.key == key).first()
            if cfg:
                cfg.value = str(value)
                if description:
                    cfg.description = description
                cfg.updated_at = datetime.datetime.utcnow()
            else:
                cfg = CapsuleConfig(key=key, value=str(value), description=description)
                session.add(cfg)
            session.commit()
            return True
        except Exception as e:
            logger.error(f"Error setting capsule config {key}: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    # --- Message Reactions / Sentiment Feedback Management ---

    def log_reaction(
        self,
        message_id: str,
        space_id: str,
        emoji: str,
        user_email: str,
        user_name: Optional[str] = None,
        action: str = "CREATED",
        sentiment: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Stores or updates a reaction event in the database."""
        if not self.enabled or not self.SessionLocal:
            return None

        # Determine sentiment if not specified
        if not sentiment:
            positive_emojis = {"👍", "+1", ":+1:", ":thumbsup:", "❤️", "❤", ":heart:", "🎉", "👏", "🔥", "😊", "🚀", "🙌"}
            negative_emojis = {"👎", "-1", ":-1:", ":thumbsdown:", "😕", "❌", "😢", "😡", "💔"}
            if emoji in positive_emojis or any(p in emoji.lower() for p in ["+1", "thumbsup", "heart", "smile"]):
                sentiment = "POSITIVE"
            elif emoji in negative_emojis or any(n in emoji.lower() for n in ["-1", "thumbsdown", "dislike", "angry"]):
                sentiment = "NEGATIVE"
            else:
                sentiment = "NEUTRAL"

        session = self.SessionLocal()
        try:
            reaction_entry = MessageReaction(
                message_id=message_id,
                space_id=space_id,
                emoji=emoji,
                sentiment=sentiment,
                user_email=user_email,
                user_name=user_name,
                action=action,
            )
            session.add(reaction_entry)
            session.commit()
            return reaction_entry.to_dict()
        except Exception as e:
            logger.error(f"Error logging reaction to database: {e}")
            session.rollback()
            return None
        finally:
            session.close()

    def get_reactions(self, message_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves recent user reactions."""
        if not self.enabled or not self.SessionLocal:
            return []
        session = self.SessionLocal()
        try:
            q = session.query(MessageReaction)
            if message_id:
                q = q.filter(MessageReaction.message_id == message_id)
            rows = q.order_by(MessageReaction.id.desc()).limit(limit).all()
            return [r.to_dict() for r in rows]
        finally:
            session.close()

    def get_reactions_summary(self) -> Dict[str, Any]:
        """Provides an aggregated sentiment summary across all logged reactions."""
        if not self.enabled or not self.SessionLocal:
            return {"total": 0, "positive": 0, "negative": 0, "neutral": 0, "recent": []}
        session = self.SessionLocal()
        try:
            total = session.query(MessageReaction).filter(MessageReaction.action == "CREATED").count()
            positive = session.query(MessageReaction).filter(MessageReaction.action == "CREATED", MessageReaction.sentiment == "POSITIVE").count()
            negative = session.query(MessageReaction).filter(MessageReaction.action == "CREATED", MessageReaction.sentiment == "NEGATIVE").count()
            neutral = total - positive - negative
            recent = [r.to_dict() for r in session.query(MessageReaction).order_by(MessageReaction.id.desc()).limit(15).all()]
            return {
                "total": total,
                "positive": positive,
                "negative": negative,
                "neutral": max(0, neutral),
                "recent": recent,
            }
        finally:
            session.close()




