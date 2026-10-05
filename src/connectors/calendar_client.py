import datetime
import logging
from typing import Any, Dict, List, Optional
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)


class GoogleCalendarConnector:
    """Manages integration with Google Calendar API to retrieve upcoming events and calendars."""

    def __init__(self, credentials: Optional[Credentials] = None):
        self.credentials = credentials
        self._service = None
        if credentials:
            try:
                self._service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
            except Exception as e:
                logger.warning(f"Could not build Calendar service: {e}")

    @property
    def service(self):
        if not self._service and self.credentials:
            self._service = build("calendar", "v3", credentials=self.credentials, cache_discovery=False)
        return self._service

    def list_calendars(self) -> List[Dict[str, Any]]:
        """Lists user's accessible calendars."""
        if not self.service:
            logger.warning("Calendar service unavailable.")
            return []
        try:
            calendars_result = self.service.calendarList().list().execute()
            items = calendars_result.get("items", [])
            return [
                {
                    "id": item.get("id"),
                    "summary": item.get("summary"),
                    "description": item.get("description", ""),
                    "primary": item.get("primary", False),
                    "timeZone": item.get("timeZone"),
                }
                for item in items
            ]
        except Exception as e:
            logger.error(f"Error listing calendars: {e}")
            return []

    def get_upcoming_events(
        self,
        calendar_id: str = "primary",
        max_results: int = 25,
        days_ahead: int = 7,
    ) -> List[Dict[str, Any]]:
        """Retrieves upcoming events starting from now up to days_ahead."""
        if not self.service:
            logger.warning("Calendar service unavailable.")
            return []

        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            time_min = now.isoformat()
            time_max = (now + datetime.timedelta(days=days_ahead)).isoformat()

            events_result = (
                self.service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            items = events_result.get("items", [])
            events = []
            for item in items:
                start = item.get("start", {}).get("dateTime", item.get("start", {}).get("date"))
                end = item.get("end", {}).get("dateTime", item.get("end", {}).get("date"))
                events.append(
                    {
                        "id": item.get("id"),
                        "summary": item.get("summary", "Sin título"),
                        "description": item.get("description", ""),
                        "start": start,
                        "end": end,
                        "location": item.get("location", ""),
                        "htmlLink": item.get("htmlLink", ""),
                        "attendees": [
                            att.get("email")
                            for att in item.get("attendees", [])
                            if att.get("email")
                        ],
                    }
                )
            return events
        except Exception as e:
            logger.error(f"Error fetching calendar events for {calendar_id}: {e}")
            return []

    def get_events_from_multiple_calendars(
        self,
        calendar_configs: List[Dict[str, str]],  # [{"id": "...", "label": "Familia"}]
        max_results: int = 25,
        days_ahead: int = 7,
    ) -> List[Dict[str, Any]]:
        """Retrieves and merges upcoming events from multiple calendars, sorted chronologically."""
        all_events = []
        for cal in calendar_configs:
            cal_id = cal.get("id")
            cal_label = cal.get("label") or cal_id
            if not cal_id:
                continue
            evs = self.get_upcoming_events(calendar_id=cal_id, max_results=max_results, days_ahead=days_ahead)
            for ev in evs:
                ev_copy = dict(ev)
                ev_copy["calendar_id"] = cal_id
                ev_copy["calendar_label"] = cal_label
                all_events.append(ev_copy)

        # Sort combined events by start time
        def get_start_key(e: Dict[str, Any]) -> str:
            val = e.get("start") or ""
            return str(val)

        all_events.sort(key=get_start_key)
        return all_events

    def format_events_summary(self, events: List[Dict[str, Any]], calendar_label: str = "Calendario") -> str:
        """Formats a list of events into a clean markdown agenda summary for agent memory."""
        if not events:
            return f"### {calendar_label}\nNo hay eventos programados para los próximos días.\n"

        lines = [f"### {calendar_label} (Próximos {len(events)} eventos):"]
        for ev in events:
            summary = ev.get("summary", "Sin título")
            start = ev.get("start", "")
            cal_tag = f" [{ev.get('calendar_label')}]" if ev.get("calendar_label") else ""
            loc = f" (Lugar: {ev.get('location')})" if ev.get("location") else ""
            desc = f" - {ev.get('description')}" if ev.get("description") else ""
            lines.append(f"- **{start}**{cal_tag}: {summary}{loc}{desc}")
        return "\n".join(lines) + "\n"
