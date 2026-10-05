import datetime
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import uuid
import yaml
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def get_configured_timezone() -> ZoneInfo:
    """Returns the local timezone configured in TIMEZONE env, defaulting to Europe/Zurich."""
    tz_name = os.environ.get("TIMEZONE", "Europe/Zurich")
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


class AutomationItem:
    """Represents a scheduled automation task."""

    def __init__(
        self,
        id: str,
        name: str,
        schedule_type: str,  # "cron", "daily", "weekly", "interval"
        time_spec: str,      # e.g. "08:00", "monday 08:00", or interval in minutes
        action: str,         # e.g. "chat_message", "calendar_review", "email_draft"
        prompt: str,         # Prompt/instruction for the synthetic identity
        target: Optional[str] = None,  # Space ID, email address, or DM
        channel: str = "chat",
        enabled: bool = True,
        last_run: Optional[str] = None,
        created_at: Optional[str] = None,
        created_by: Optional[str] = None,
        calendar_ids: Optional[List[str]] = None,
    ):
        self.id = id
        self.name = name
        self.schedule_type = schedule_type
        self.time_spec = time_spec
        self.action = action
        self.prompt = prompt
        self.target = target
        self.channel = channel
        self.enabled = enabled
        self.last_run = last_run
        self.created_at = created_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.created_by = created_by or "system"
        self.calendar_ids = calendar_ids or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "schedule_type": self.schedule_type,
            "time_spec": self.time_spec,
            "action": self.action,
            "prompt": self.prompt,
            "target": self.target,
            "channel": self.channel,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "calendar_ids": self.calendar_ids,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutomationItem":
        return cls(
            id=data.get("id") or str(uuid.uuid4())[:8],
            name=data.get("name", "Automatización"),
            schedule_type=data.get("schedule_type", "daily"),
            time_spec=data.get("time_spec", "08:00"),
            action=data.get("action", "chat_message"),
            prompt=data.get("prompt", ""),
            target=data.get("target"),
            channel=data.get("channel", "chat"),
            enabled=data.get("enabled", True),
            last_run=data.get("last_run"),
            created_at=data.get("created_at"),
            created_by=data.get("created_by"),
            calendar_ids=data.get("calendar_ids", []),
        )

    def is_due(self, now: Optional[datetime.datetime] = None) -> bool:
        """Evaluates whether this automation is due to run at current time in configured timezone."""
        if not self.enabled:
            return False

        tz = get_configured_timezone()
        if now is None:
            now = datetime.datetime.now(tz)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=tz)
        else:
            now = now.astimezone(tz)

        # Parse last run date in local timezone
        last_run_local = None
        if self.last_run:
            try:
                lr = datetime.datetime.fromisoformat(self.last_run)
                if lr.tzinfo is None:
                    lr = lr.replace(tzinfo=datetime.timezone.utc)
                last_run_local = lr.astimezone(tz)
            except Exception:
                pass

        current_time_str = now.strftime("%H:%M")
        current_day_str = now.strftime("%A").lower()

        # 1. Interval in minutes (e.g. "30", "60", "interval 15")
        if self.schedule_type == "interval":
            interval_mins = 60
            clean_spec = re.sub(r"[^\d]", "", self.time_spec)
            if clean_spec:
                interval_mins = max(1, int(clean_spec))
            if not last_run_local:
                return True
            elapsed_seconds = (now - last_run_local).total_seconds()
            return elapsed_seconds >= (interval_mins * 60)

        # 2. Daily automation (e.g. "07:30" or "daily 07:30")
        if self.schedule_type == "daily":
            # Extract HH:MM
            m = re.search(r"(\d{1,2}):(\d{2})", self.time_spec)
            if not m:
                return False
            sched_hour = int(m.group(1))
            sched_minute = int(m.group(2))
            sched_time_str = f"{sched_hour:02d}:{sched_minute:02d}"

            # If already executed today, do not repeat
            if last_run_local and last_run_local.date() == now.date():
                return False

            # Is due if current time has reached or passed the scheduled time on current date
            sched_dt = now.replace(hour=sched_hour, minute=sched_minute, second=0, microsecond=0)
            return now >= sched_dt

        # 3. Weekly automation (e.g. "monday 08:00")
        if self.schedule_type == "weekly":
            m_time = re.search(r"(\d{1,2}):(\d{2})", self.time_spec)
            if not m_time:
                return False
            sched_hour = int(m_time.group(1))
            sched_minute = int(m_time.group(2))
            sched_time_str = f"{sched_hour:02d}:{sched_minute:02d}"

            # Check day of week
            days_map = {
                "monday": 0, "lunes": 0,
                "tuesday": 1, "martes": 1,
                "wednesday": 2, "miercoles": 2, "miércoles": 2,
                "thursday": 3, "jueves": 3,
                "friday": 4, "viernes": 4,
                "saturday": 5, "sabado": 5, "sábado": 5,
                "sunday": 6, "domingo": 6,
            }
            spec_lower = self.time_spec.lower()
            target_weekday = None
            for d_name, w_idx in days_map.items():
                if d_name in spec_lower:
                    target_weekday = w_idx
                    break

            if target_weekday is not None and now.weekday() != target_weekday:
                return False

            # Check if executed today
            if last_run_local and last_run_local.date() == now.date():
                return False

            sched_dt = now.replace(hour=sched_hour, minute=sched_minute, second=0, microsecond=0)
            return now >= sched_dt

        return False


class AutomationEngine:
    """Manages automation definitions persisted to memory (YAML) and synchronized to GCS."""

    def __init__(self, memory_dir: str, gcs_manager: Optional[Any] = None):
        self.memory_dir = Path(memory_dir)
        self.gcs_manager = gcs_manager
        self.file_path = self.memory_dir / "automations.yaml"
        self.calendar_config_path = self.memory_dir / "calendar_config.yaml"
        self._automations: List[AutomationItem] = []
        self._running_locks: set = set()
        self._load()

    def get_selected_calendars(self) -> List[Dict[str, str]]:
        """Returns list of selected calendars [{"id": "...", "label": "..."}]."""
        if not self.calendar_config_path.exists():
            return []
        try:
            with open(self.calendar_config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                return data.get("selected_calendars", [])
        except Exception as e:
            logger.error(f"Failed to read calendar config from {self.calendar_config_path}: {e}")
            return []

    def set_selected_calendars(self, calendars: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Updates selected calendars list and saves to YAML / GCS."""
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            data = {"selected_calendars": calendars}
            with open(self.calendar_config_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            if self.gcs_manager:
                self.gcs_manager.sync_to_gcs()
            return calendars
        except Exception as e:
            logger.error(f"Failed to save calendar config to {self.calendar_config_path}: {e}")
            return []

    def acquire_execution_lock(self, automation_id: str) -> bool:
        """Acquires lock for executing automation to avoid duplicate simultaneous executions."""
        if automation_id in self._running_locks:
            return False
        self._running_locks.add(automation_id)
        return True

    def release_execution_lock(self, automation_id: str) -> None:
        """Releases the in-flight execution lock."""
        self._running_locks.discard(automation_id)

    def _load(self) -> None:
        """Loads automations from YAML file, or seeds with defaults if not present."""
        if not self.file_path.exists():
            # Initial default seed
            default_items = [
                AutomationItem(
                    id="weekly-family-motivation",
                    name="Mensaje de Ánimo Semanal",
                    schedule_type="weekly",
                    time_spec="monday 08:00",
                    action="chat_message",
                    prompt="Genera un saludo cálido, alegre y motivador para comenzar la semana con la mejor energía para la familia.",
                    channel="chat",
                    enabled=True,
                ),
                AutomationItem(
                    id="daily-calendar-review",
                    name="Revisión Diaria de Calendario",
                    schedule_type="daily",
                    time_spec="07:30",
                    action="calendar_review",
                    prompt="Revisa los eventos programados para hoy en el calendario familiar y resume la agenda de forma clara y organizada.",
                    channel="chat",
                    enabled=True,
                ),
            ]
            self._automations = default_items
            self._save()
            return

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                items = data.get("automations", [])
                self._automations = [AutomationItem.from_dict(it) for it in items]
        except Exception as e:
            logger.error(f"Failed to read automations file {self.file_path}: {e}")
            self._automations = []

    def _save(self) -> None:
        """Saves current automations to YAML and triggers sync to GCS if available."""
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            data = {"automations": [it.to_dict() for it in self._automations]}
            with open(self.file_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            if self.gcs_manager:
                self.gcs_manager.sync_to_gcs()
        except Exception as e:
            logger.error(f"Failed to save automations to {self.file_path}: {e}")

    def list_automations(self) -> List[Dict[str, Any]]:
        return [it.to_dict() for it in self._automations]

    def get_automation(self, automation_id: str) -> Optional[AutomationItem]:
        for it in self._automations:
            if it.id == automation_id:
                return it
        return None

    def upsert_automation(self, item: AutomationItem) -> AutomationItem:
        for idx, it in enumerate(self._automations):
            if it.id == item.id:
                self._automations[idx] = item
                self._save()
                return item
        self._automations.append(item)
        self._save()
        return item

    def delete_automation(self, automation_id: str) -> bool:
        initial_len = len(self._automations)
        self._automations = [it for it in self._automations if it.id != automation_id]
        if len(self._automations) < initial_len:
            self._save()
            return True
        return False

    def toggle_automation(self, automation_id: str, enabled: bool) -> Optional[AutomationItem]:
        item = self.get_automation(automation_id)
        if item:
            item.enabled = enabled
            self._save()
            return item
        return None

    def mark_executed(self, automation_id: str) -> None:
        item = self.get_automation(automation_id)
        if item:
            item.last_run = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._save()

    def get_due_automations(self, now: Optional[datetime.datetime] = None) -> List[AutomationItem]:
        """Returns all enabled automations that are due to execute at the given time."""
        due: List[AutomationItem] = []
        for it in self._automations:
            try:
                if it.is_due(now=now):
                    due.append(it)
            except Exception as e:
                logger.error(f"Error evaluating is_due for automation {it.id}: {e}")
        return due

    def parse_automation_tag(self, text: str, sender: str) -> Optional[AutomationItem]:
        """Parses a natural language instruction tagged with [automation] or [automatizacion]."""
        # Example tag: [automation] Los lunes a las 8:00 enviar saludo familiar
        clean_text = re.sub(r"^\[(?:automation|automatizacion)\]\s*", "", text, flags=re.IGNORECASE).strip()
        if not clean_text:
            return None

        # Basic schedule heuristics
        lower = clean_text.lower()
        schedule_type = "daily"
        time_spec = "08:00"

        # Check for days of week
        days_map = {
            "lunes": "monday",
            "martes": "tuesday",
            "miercoles": "wednesday",
            "miércoles": "wednesday",
            "jueves": "thursday",
            "viernes": "friday",
            "sabado": "saturday",
            "sábado": "saturday",
            "domingo": "sunday",
        }
        for day_es, day_en in days_map.items():
            if day_es in lower:
                schedule_type = "weekly"
                # look for time
                match_time = re.search(r"(\d{1,2}):(\d{2})", lower)
                if match_time:
                    time_spec = f"{day_en} {match_time.group(1).zfill(2)}:{match_time.group(2)}"
                else:
                    time_spec = f"{day_en} 08:00"
                break

        if schedule_type == "daily":
            match_time = re.search(r"(\d{1,2}):(\d{2})", lower)
            if match_time:
                time_spec = f"{match_time.group(1).zfill(2)}:{match_time.group(2)}"

        auto_id = f"auto-{str(uuid.uuid4())[:6]}"
        item = AutomationItem(
            id=auto_id,
            name=clean_text[:40] + ("..." if len(clean_text) > 40 else ""),
            schedule_type=schedule_type,
            time_spec=time_spec,
            action="calendar_review" if "calendario" in lower else "chat_message",
            prompt=clean_text,
            channel="chat",
            enabled=True,
            created_by=sender,
        )
        self.upsert_automation(item)
        return item
