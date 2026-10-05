import pytest
from unittest.mock import MagicMock
from src.connectors.calendar_client import GoogleCalendarConnector
from src.connectors.chat_client import GoogleChatConnector
from src.engine.automations import AutomationEngine, AutomationItem


def test_calendar_connector_format_summary():
    """Verify that GoogleCalendarConnector formats events into clean markdown for agent memory."""
    connector = GoogleCalendarConnector(credentials=None)
    mock_events = [
        {
            "id": "ev1",
            "summary": "Cena Familiar",
            "start": "2026-09-28T20:00:00Z",
            "end": "2026-09-28T22:00:00Z",
            "location": "Casa",
            "description": "Traer postre",
            "attendees": ["nicolas@example.com"],
        },
        {
            "id": "ev2",
            "summary": "Dentista",
            "start": "2026-09-29T10:00:00Z",
            "location": "Clinica Central",
        }
    ]
    summary = connector.format_events_summary(mock_events, calendar_label="Calendario Familiar")
    assert "### Calendario Familiar (Próximos 2 eventos):" in summary
    assert "Cena Familiar" in summary
    assert "Casa" in summary
    assert "Traer postre" in summary
    assert "Dentista" in summary


def test_automations_engine_crud(tmp_path):
    """Verify CRUD lifecycle of AutomationEngine without database dependency."""
    engine = AutomationEngine(memory_dir=str(tmp_path))
    initial = engine.list_automations()
    assert len(initial) >= 2  # default seeds

    # Upsert new automation
    new_auto = AutomationItem(
        id="test-monday",
        name="Prueba Lunes",
        schedule_type="weekly",
        time_spec="monday 08:30",
        action="chat_message",
        prompt="Feliz lunes familia",
    )
    engine.upsert_automation(new_auto)
    items = engine.list_automations()
    assert any(it["id"] == "test-monday" for it in items)

    # Toggle
    toggled = engine.toggle_automation("test-monday", False)
    assert toggled.enabled is False

    # Mark executed
    engine.mark_executed("test-monday")
    item = engine.get_automation("test-monday")
    assert item.last_run is not None

    # Delete
    deleted = engine.delete_automation("test-monday")
    assert deleted is True
    assert engine.get_automation("test-monday") is None


def test_automations_parse_tags(tmp_path):
    """Verify parsing natural language directives with [automation] tags."""
    engine = AutomationEngine(memory_dir=str(tmp_path))

    # Parse weekly automation with day and time
    item = engine.parse_automation_tag(
        "[automation] Los lunes a las 09:00 enviar saludo de animo",
        sender="admin@example.com"
    )
    assert item is not None
    assert item.schedule_type == "weekly"
    assert "monday 09:00" in item.time_spec
    assert "saludo de animo" in item.prompt

    # Parse daily automation
    item2 = engine.parse_automation_tag(
        "[automatizacion] Todos los dias a las 07:15 revisar el calendario familiar",
        sender="admin@example.com"
    )
    assert item2 is not None
    assert item2.schedule_type == "daily"
    assert item2.action == "calendar_review"
    assert item2.time_spec == "07:15"


def test_calendar_and_automations_endpoints():
    """Verify FastAPI endpoints for calendar and automations."""
    from fastapi.testclient import TestClient
    from src.main import app

    client = TestClient(app)

    # 1. Test Cloud Scheduler trigger endpoint (public)
    res_run = client.post("/api/automations/run")
    assert res_run.status_code == 200
    data = res_run.json()
    assert data["status"] == "triggered"
    assert "count" in data

    # 2. Test get calendar events endpoint with dummy credentials
    res_cal = client.get("/api/calendar/events", headers={"X-Admin-Username": "admin", "X-Admin-Password": "dummy"})
    # Endpoint returns 200 or 503 if db disabled in isolated test
    assert res_cal.status_code in [200, 503]


def test_automation_is_due_evaluation(tmp_path):
    """Verify that is_due correctly evaluates daily and weekly automations with timezone support."""
    import datetime
    from zoneinfo import ZoneInfo
    from src.engine.automations import AutomationEngine, AutomationItem

    tz = ZoneInfo("Europe/Madrid")
    engine = AutomationEngine(memory_dir=str(tmp_path))

    # Daily automation scheduled at 07:30
    daily_item = AutomationItem(
        id="test-daily",
        name="Prueba Diaria",
        schedule_type="daily",
        time_spec="07:30",
        action="calendar_review",
        prompt="Revisar agenda",
        enabled=True,
    )

    # 1. Before scheduled time (07:15) -> Should NOT be due
    time_before = datetime.datetime(2026, 9, 29, 7, 15, tzinfo=tz)
    assert daily_item.is_due(now=time_before) is False

    # 2. At or after scheduled time (07:35) -> Should be due
    time_after = datetime.datetime(2026, 9, 29, 7, 35, tzinfo=tz)
    assert daily_item.is_due(now=time_after) is True

    # 3. If already executed today -> Should NOT be due again
    daily_item.last_run = time_after.isoformat()
    assert daily_item.is_due(now=time_after) is False

    # 4. Next day at 07:40 -> Should be due again
    next_day = datetime.datetime(2026, 9, 30, 7, 40, tzinfo=tz)
    assert daily_item.is_due(now=next_day) is True

    # 5. Weekly item on wrong weekday -> Should NOT be due
    weekly_item = AutomationItem(
        id="test-weekly",
        name="Prueba Lunes",
        schedule_type="weekly",
        time_spec="monday 08:00",
        action="chat_message",
        prompt="Feliz lunes",
        enabled=True,
    )
    # 2026-09-30 is Wednesday (weekday 2)
    assert weekly_item.is_due(now=next_day) is False

    # Monday 2026-09-28 at 08:15 -> Should be due
    monday_time = datetime.datetime(2026, 9, 28, 8, 15, tzinfo=tz)
    assert weekly_item.is_due(now=monday_time) is True


def test_chat_connector_find_default_space():
    """Verify that GoogleChatConnector can discover collaborative space from spaces.list()."""
    connector = GoogleChatConnector(credentials=None)
    mock_service = MagicMock()
    mock_spaces_api = MagicMock()
    mock_service.spaces.return_value = mock_spaces_api
    connector._service = mock_service

    # Case 1: Has collaborative space and DM -> should prefer collaborative space
    mock_spaces_api.list.return_value.execute.return_value = {
        "spaces": [
            {"name": "spaces/dm_user1", "spaceType": "DIRECT_MESSAGE", "singleUserBotDm": True},
            {"name": "spaces/family_collab", "spaceType": "SPACE", "displayName": "Family Space"},
        ]
    }
    found = connector.find_default_space()
    assert found == "spaces/family_collab"

    # Case 2: Only DMs available -> should fallback to first DM
    mock_spaces_api.list.return_value.execute.return_value = {
        "spaces": [
            {"name": "spaces/dm_user2", "spaceType": "DM"},
        ]
    }
    found_dm = connector.find_default_space()
    assert found_dm == "spaces/dm_user2"

    # Case 3: Empty spaces -> returns None
    mock_spaces_api.list.return_value.execute.return_value = {"spaces": []}
    assert connector.find_default_space() is None


def test_multi_calendar_merging_and_config(tmp_path):
    """Verify multi-calendar events aggregation, calendar config save, and execution locks."""
    # 1. Multi-calendar aggregation test
    connector = GoogleCalendarConnector(credentials=None)
    connector.get_upcoming_events = MagicMock(side_effect=[
        [{"id": "ev-fam-1", "summary": "Patin sobre hielo", "start": "2026-10-07T14:00:00Z"}],
        [{"id": "ev-pers-1", "summary": "Reunión Trabajo", "start": "2026-10-06T09:00:00Z"}],
    ])

    cals = [{"id": "family@group.calendar.google.com", "label": "Familia"}, {"id": "primary", "label": "Personal"}]
    merged = connector.get_events_from_multiple_calendars(cals, days_ahead=7)
    assert len(merged) == 2
    # Verify sorted chronologically
    assert merged[0]["id"] == "ev-pers-1"
    assert merged[0]["calendar_label"] == "Personal"
    assert merged[1]["id"] == "ev-fam-1"
    assert merged[1]["calendar_label"] == "Familia"

    formatted = connector.format_events_summary(merged, calendar_label="Calendarios Consolidados")
    assert "[Personal]" in formatted
    assert "[Familia]" in formatted

    # 2. Calendar config persistence in AutomationEngine
    engine = AutomationEngine(memory_dir=str(tmp_path))
    assert engine.get_selected_calendars() == []
    engine.set_selected_calendars(cals)
    saved = engine.get_selected_calendars()
    assert len(saved) == 2
    assert saved[0]["id"] == "family@group.calendar.google.com"

    # 3. Execution lock test
    assert engine.acquire_execution_lock("auto-1") is True
    assert engine.acquire_execution_lock("auto-1") is False  # Already acquired
    engine.release_execution_lock("auto-1")
    assert engine.acquire_execution_lock("auto-1") is True


