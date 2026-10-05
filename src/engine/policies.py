import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional
import yaml

from src.models.message import ActionType, EmailItem, PolicyEvaluation, RiskLevel

logger = logging.getLogger(__name__)


class PolicyEngine:
    """Deterministic security gate that classifies incoming emails and action risk."""

    def __init__(self, config_path: str = "config/policies.yaml", db_manager: Optional[Any] = None):
        self.config_path = config_path
        self.db = db_manager
        self.rules: Dict[str, Any] = self._load_rules()
        self._sent_timestamps: List[float] = []

    def _load_rules(self) -> Dict[str, Any]:
        p = Path(self.config_path)
        if not p.exists():
            return {
                "execution_rules": {
                    "default_action": "CREATE_DRAFT",
                    "allowed_domains": ["*"],
                    "blacklisted_keywords": ["password", "transferencia", "reset"],
                    "max_replies_per_hour": 10,
                }
            }
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def save_rules(self, updated_rules: Dict[str, Any]) -> None:
        """Persists updated policies back to the YAML file."""
        self.rules = updated_rules
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(updated_rules, f, default_flow_style=False, allow_unicode=True)

    def _is_rate_limited(self, max_limit: int = 10) -> bool:
        now = time.time()
        one_hour_ago = now - 3600
        self._sent_timestamps = [t for t in self._sent_timestamps if t > one_hour_ago]
        return len(self._sent_timestamps) >= max_limit

    def record_action(self) -> None:
        self._sent_timestamps.append(time.time())

    def get_effective_rules(self) -> Dict[str, Any]:
        """Combines YAML static config with active dynamic rules from Database."""
        rules = self.rules.get("execution_rules", {}).copy()
        allowed_domains = list(rules.get("allowed_domains", ["*"]))
        blacklisted_keywords = list(rules.get("blacklisted_keywords", []))
        blocked_senders = list(rules.get("blocked_senders", []))
        trusted_senders = list(rules.get("trusted_senders", []))
        admin_users = list(rules.get("admin_users", []))
        interaction_services = list(rules.get("interaction_enabled_services", ["gmail", "chat"]))

        if self.db and self.db.enabled:
            db_rules = self.db.get_security_rules()
            for r in db_rules:
                if not r.get("is_active", True):
                    continue
                rtype = r.get("rule_type")
                val = r.get("value")
                if rtype == "blacklisted_keyword" and val not in blacklisted_keywords:
                    blacklisted_keywords.append(val)
                elif rtype == "blocked_sender" and val not in blocked_senders:
                    blocked_senders.append(val)
                elif rtype == "allowed_domain" and val not in allowed_domains:
                    allowed_domains.append(val)
                elif rtype == "trusted_sender" and val not in trusted_senders:
                    trusted_senders.append(val)
                elif rtype == "admin_user" and val not in admin_users:
                    admin_users.append(val)

        rules["allowed_domains"] = allowed_domains
        rules["blacklisted_keywords"] = blacklisted_keywords
        rules["blocked_senders"] = blocked_senders
        rules["trusted_senders"] = trusted_senders
        rules["admin_users"] = admin_users
        rules["interaction_enabled_services"] = interaction_services

        if self.db and self.db.enabled:
            db_rate_limit = self.db.get_capsule_config("max_replies_per_hour")
            if db_rate_limit:
                try:
                    rules["max_replies_per_hour"] = int(db_rate_limit)
                except (ValueError, TypeError):
                    pass
        return rules

    def is_admin(self, sender: str, sender_name: Optional[str] = None) -> bool:
        """Checks if sender email or display name is in the authorized admin list."""
        rules = self.get_effective_rules()
        admin_users = rules.get("admin_users", [])
        candidates = [s.lower() for s in [sender, sender_name] if s]
        return any(
            any(adm.lower() in cand for adm in admin_users if adm)
            for cand in candidates
        )

    def evaluate(self, item: Any, service_name: Optional[str] = None) -> PolicyEvaluation:
        """Evaluates sender, service enablement, content risk, blacklisted keywords, and injection vectors."""
        reasons: List[str] = []
        rules = self.get_effective_rules()

        svc = service_name or getattr(item, "service_name", "gmail")
        interaction_services = rules.get("interaction_enabled_services", ["gmail", "chat"])

        # 0. Consumption Control Gate: Service enablement check
        if svc not in interaction_services:
            reasons.append(f"El servicio '{svc}' no está habilitado para interacción en la identidad sintética. Descartado para control de consumo.")
            return PolicyEvaluation(
                allowed=False,
                risk_level=RiskLevel.LOW,
                recommended_action=ActionType.IGNORE,
                reasons=reasons,
            )

        # 0.1 Token & Cost Operational Budget Limit Gate
        if self.db and self.db.enabled:
            is_enforced = str(self.db.get_capsule_config("is_limit_enforced", "true") or "true").lower() in ("true", "1", "yes")
            if is_enforced:
                try:
                    max_tokens = int(self.db.get_capsule_config("max_tokens_limit", "1000000"))
                    max_cost = float(self.db.get_capsule_config("max_cost_limit_usd", "5.00"))
                    stats = self.db.get_token_usage_stats()
                    curr_tokens = stats.get("total_tokens", 0)
                    curr_cost = stats.get("total_cost_usd", 0.0)

                    limit_msg = self.db.get_capsule_config(
                        "limit_reached_message",
                        "He alcanzado mi límite operativo asignado de consumo y no puedo generar respuestas autónomas en este momento. Mi supervisor humano ha sido notificado."
                    )

                    if (max_tokens > 0 and curr_tokens >= max_tokens) or (max_cost > 0 and curr_cost >= max_cost):
                        limit_reason = (
                            f"Límite de consumo alcanzado (Tokens: {curr_tokens}/{max_tokens}, Costo: ${curr_cost:.4f}/${max_cost:.2f} USD). "
                            f"{limit_msg}"
                        )
                        reasons.append(limit_reason)
                        return PolicyEvaluation(
                            allowed=False,
                            risk_level=RiskLevel.HIGH,
                            recommended_action=ActionType.CREATE_DRAFT,
                            reasons=reasons,
                        )
                except Exception as ex:
                    logger.warning(f"Error checking budget limits in policy engine: {ex}")

        sender = getattr(item, "sender", "")
        sender_name = getattr(item, "sender_name", "")
        clean_body = getattr(item, "clean_body", "")
        subject = getattr(item, "subject", "")

        allowed_domains = rules.get("allowed_domains", ["*"])
        blacklisted_keywords = rules.get("blacklisted_keywords", [])
        blocked_senders = rules.get("blocked_senders", [])
        trusted_senders = rules.get("trusted_senders", [])
        admin_users = rules.get("admin_users", [])
        max_replies = rules.get("max_replies_per_hour", 30)

        # 1. Rate limiting check (Admins are exempt from rate limiting to permit continuous operational control)
        is_sender_admin = self.is_admin(sender, sender_name=sender_name)
        if not is_sender_admin and self._is_rate_limited(max_replies):
            reasons.append(f"Rate limit exceeded ({max_replies} actions/hour).")
            return PolicyEvaluation(
                allowed=False,
                risk_level=RiskLevel.HIGH,
                recommended_action=ActionType.ESCALATE_TO_HUMAN,
                reasons=reasons,
            )

        # 2. Blocked senders check
        for blocked in blocked_senders:
            if blocked.lower() in sender.lower():
                reasons.append(f"Sender {sender} is in the blocked senders list.")
                return PolicyEvaluation(
                    allowed=False,
                    risk_level=RiskLevel.HIGH,
                    recommended_action=ActionType.IGNORE,
                    reasons=reasons,
                )

        # 3. Domain allowlist check
        if "*" not in allowed_domains and "@" in sender:
            sender_domain = sender.split("@")[-1].strip(">").lower()
            if sender_domain not in [d.lower() for d in allowed_domains]:
                reasons.append(f"Sender domain {sender_domain} is not in allowed domains.")
                return PolicyEvaluation(
                    allowed=False,
                    risk_level=RiskLevel.MEDIUM,
                    recommended_action=ActionType.ESCALATE_TO_HUMAN,
                    reasons=reasons,
                )

        # 4. Blacklisted keyword check in subject and body
        combined_text = f"{subject} {clean_body}".lower()
        for kw in blacklisted_keywords:
            if kw.lower() in combined_text:
                reasons.append(f"Contains high-risk or blacklisted keyword: '{kw}'")

        # 5. Prompt injection heuristic checks
        injection_patterns = [
            r"ignore\s+(all\s+)?(previous|prior)\s+instructions",
            r"system\s*prompt",
            r"you\s+are\s+now\s+in\s+developer\s+mode",
            r"reveal\s+(your\s+)?(keys|secrets|token)",
            r"sudo\s+",
        ]
        for pattern in injection_patterns:
            if re.search(pattern, combined_text, re.IGNORECASE):
                reasons.append(f"Potential indirect prompt injection detected: '{pattern}'")

        if reasons:
            return PolicyEvaluation(
                allowed=False,
                risk_level=RiskLevel.HIGH,
                recommended_action=ActionType.ESCALATE_TO_HUMAN,
                reasons=reasons,
            )

        # Check if sender or display name is in trusted senders list or admin users list
        sender_identifiers = [s.lower() for s in [sender, sender_name] if s]
        is_trusted = any(
            any(t.lower() in ident for t in trusted_senders if t)
            or any(a.lower() in ident for a in admin_users if a)
            for ident in sender_identifiers
        )

        # 6. Lock Principle evaluation (Principio del Candado)
        is_dm = getattr(item, "is_direct_message", False)

        if svc == "chat" and is_dm:
            # Mandatory rule: 1:1 Direct Messages must ALWAYS be responded to autonomously
            requires_supervision = False
            lock_reason = "1:1 Direct Message: Envío autónomo mandatorio (los mensajes directos 1:1 siempre deben ser respondidos)."
        elif svc == "chat" and is_trusted:
            # Trusted senders / Admins in Google Chat spaces should always receive autonomous responses
            requires_supervision = False
            lock_reason = f"Remitente de confianza ({sender_name or sender}) en Google Chat: Envío autónomo habilitado."
        else:
            # Check service supervision lock dynamically (service_name="gmail" or "chat")
            service_lock = None
            if self.db and self.db.enabled:
                service_lock = self.db.get_service_lock(svc)

            is_supervision_active = True
            lock_mode = "ALL"
            if service_lock:
                is_supervision_active = service_lock.get("is_supervision_enabled", True)
                lock_mode = service_lock.get("mode", "ALL").upper()

            requires_supervision = False

            if is_supervision_active:
                if lock_mode == "ALL":
                    # Supervision locked for everyone
                    requires_supervision = True
                    lock_reason = f"Principio del Candado: Supervisión activa para {svc.upper()} (Modo: TODOS). Requiere borrador y revisión humana."
                elif lock_mode == "SPECIFIC":
                    # Only apply lock to users listed in supervised_users
                    supervised_list = []
                    if self.db and self.db.enabled:
                        supervised_list = self.db.get_supervised_users(service_name=svc)

                    is_in_supervised_list = any(
                        u.get("is_active", True) and u.get("identifier", "").lower() in sender.lower()
                        for u in supervised_list
                    )
                    if is_in_supervised_list:
                        requires_supervision = True
                        lock_reason = f"Principio del Candado: Supervisión activa para el remitente {sender} en {svc.upper()} según lista específica."
                    else:
                        requires_supervision = False
                        lock_reason = f"Principio del Candado: Remitente {sender} exento de supervisión en {svc.upper()} según lista específica."
            else:
                # Supervision disabled: service lock is unlocked
                requires_supervision = False
                lock_reason = f"Principio del Candado: Supervisión DESHABILITADA para {svc.upper()}. Envíos autónomos permitidos."

        if requires_supervision:
            action = ActionType.CREATE_DRAFT
            action_reason = lock_reason
        else:
            action = ActionType.SEND
            action_reason = lock_reason if not is_trusted else f"Remitente de confianza ({sender_name or sender}). Envío autónomo habilitado."

        return PolicyEvaluation(
            allowed=True,
            risk_level=RiskLevel.LOW,
            recommended_action=action,
            reasons=[action_reason],
        )

