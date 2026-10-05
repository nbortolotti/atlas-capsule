import datetime
import pytest
from unittest.mock import MagicMock, patch
from src.connectors.chat_client import GoogleChatConnector
from src.models.message import ChatItem, ReactionItem


@pytest.fixture
def mock_profile():
    return {
        "identity": {
            "name": "Atlas Capsule",
            "email": "atlas.capsule@company.com",
            "signature": {
                "chat": "-- Atlas"
            }
        }
    }


@pytest.fixture
def chat_connector(mock_profile):
    return GoogleChatConnector(credentials=None, identity_profile=mock_profile)


# =====================================================================
# 1. 1:1 Direct Messages (DMs) Tests - Must ALWAYS be answered
# =====================================================================

def test_dm_detection_single_user_bot_dm(chat_connector):
    """Direct message with singleUserBotDm=True must be detected and processed without @mention."""
    payload = {
        "type": "MESSAGE",
        "space": {
            "name": "spaces/DM_USER_1",
            "singleUserBotDm": True,
            "spaceType": "DIRECT_MESSAGE",
        },
        "message": {
            "name": "spaces/DM_USER_1/messages/msg_101",
            "sender": {"displayName": "Nicolas", "email": "nicolas@company.com"},
            "text": "Hola, ¿cómo estás?",
        },
    }
    item = chat_connector.parse_webhook_event(payload)
    assert item is not None
    assert item.is_direct_message is True
    assert item.clean_body == "Hola, ¿cómo estás?"
    assert item.sender == "nicolas@company.com"
    assert item.space_id == "spaces/DM_USER_1"


def test_dm_detection_space_type_direct_message(chat_connector):
    """Space with spaceType='DIRECT_MESSAGE' must be recognized as 1:1 DM."""
    payload = {
        "type": "MESSAGE",
        "space": {
            "name": "spaces/DM_USER_2",
            "spaceType": "DIRECT_MESSAGE",
        },
        "message": {
            "name": "spaces/DM_USER_2/messages/msg_102",
            "sender": {"displayName": "Elena", "email": "elena@company.com"},
            "text": "Please provide an update on the project.",
        },
    }
    item = chat_connector.parse_webhook_event(payload)
    assert item is not None
    assert item.is_direct_message is True
    assert item.clean_body == "Please provide an update on the project."


def test_dm_detection_legacy_type_dm(chat_connector):
    """Space with legacy type='DM' must be recognized as 1:1 DM."""
    payload = {
        "type": "MESSAGE",
        "space": {
            "name": "spaces/DM_USER_3",
            "type": "DM",
        },
        "message": {
            "name": "spaces/DM_USER_3/messages/msg_103",
            "sender": {"displayName": "Carlos", "email": "carlos@company.com"},
            "text": "Buenos días",
        },
    }
    item = chat_connector.parse_webhook_event(payload)
    assert item is not None
    assert item.is_direct_message is True


def test_dm_detection_nested_space_in_message(chat_connector):
    """Space metadata inside message.space must be merged and recognized when top-level space is minimal."""
    payload = {
        "type": "MESSAGE",
        "space": {
            "name": "spaces/DM_NESTED",
        },
        "message": {
            "name": "spaces/DM_NESTED/messages/msg_104",
            "sender": {"displayName": "Maria", "email": "maria@company.com"},
            "text": "Necesito ayuda",
            "space": {
                "name": "spaces/DM_NESTED",
                "spaceType": "DIRECT_MESSAGE",
                "singleUserBotDm": True,
            },
        },
    }
    item = chat_connector.parse_webhook_event(payload)
    assert item is not None
    assert item.is_direct_message is True
    assert item.clean_body == "Necesito ayuda"


def test_dm_detection_api_cache_fallback(chat_connector):
    """When webhook payload lacks spaceType and singleUserBotDm, query get_space() and cache it."""
    payload = {
        "type": "MESSAGE",
        "space": {
            "name": "spaces/UNKNOWN_SPACE",
        },
        "message": {
            "name": "spaces/UNKNOWN_SPACE/messages/msg_105",
            "sender": {"displayName": "John", "email": "john@company.com"},
            "text": "Hello there",
        },
    }

    # Pre-seed space cache as a DIRECT_MESSAGE
    chat_connector._spaces_cache["spaces/UNKNOWN_SPACE"] = {
        "name": "spaces/UNKNOWN_SPACE",
        "spaceType": "DIRECT_MESSAGE",
        "singleUserBotDm": True,
    }

    item = chat_connector.parse_webhook_event(payload)
    assert item is not None
    assert item.is_direct_message is True


# =====================================================================
# 2. Group Spaces Tests - Only respond when explicitly tagged
# =====================================================================

def test_group_space_untagged_message_dropped(chat_connector):
    """Untagged messages exchanged in group spaces MUST be dropped (return None)."""
    payload = {
        "type": "MESSAGE",
        "space": {
            "name": "spaces/COLLAB_ROOM_1",
            "spaceType": "SPACE",
            "displayName": "Engineering Space",
            "singleUserBotDm": False,
        },
        "message": {
            "name": "spaces/COLLAB_ROOM_1/messages/msg_201",
            "sender": {"displayName": "Alice", "email": "alice@company.com"},
            "text": "Has anyone seen the pull request?",
        },
    }
    item = chat_connector.parse_webhook_event(payload)
    assert item is None


def test_group_space_tagged_with_bot_name(chat_connector):
    """Messages tagging the bot with @Atlas Capsule must be processed and mention stripped."""
    payload = {
        "type": "MESSAGE",
        "space": {
            "name": "spaces/COLLAB_ROOM_1",
            "spaceType": "SPACE",
            "displayName": "Engineering Space",
            "singleUserBotDm": False,
        },
        "message": {
            "name": "spaces/COLLAB_ROOM_1/messages/msg_202",
            "sender": {"displayName": "Alice", "email": "alice@company.com"},
            "text": "@Atlas Capsule ¿cuándo es la próxima reunión?",
        },
    }
    item = chat_connector.parse_webhook_event(payload)
    assert item is not None
    assert item.is_direct_message is False
    assert "¿cuándo es la próxima reunión?" in item.clean_body
    assert "@Atlas Capsule" not in item.clean_body


def test_group_space_tagged_with_alias(chat_connector):
    """Messages tagging aliases like @atlas or @capsule must be recognized."""
    for alias in ["@atlas", "@ATLAS", "@capsule", "@bot", "@assistant"]:
        payload = {
            "type": "MESSAGE",
            "space": {
                "name": "spaces/ROOM_OPS",
                "spaceType": "SPACE",
                "singleUserBotDm": False,
            },
            "message": {
                "name": f"spaces/ROOM_OPS/messages/msg_{alias}",
                "sender": {"displayName": "Bob", "email": "bob@company.com"},
                "text": f"{alias} status report please",
            },
        }
        item = chat_connector.parse_webhook_event(payload)
        assert item is not None, f"Failed to match alias {alias}"
        assert item.is_direct_message is False
        assert "status report please" in item.clean_body


def test_group_space_tagged_with_user_mention_annotation(chat_connector):
    """Messages with USER_MENTION annotations matching bot identity must be processed."""
    payload = {
        "type": "MESSAGE",
        "space": {
            "name": "spaces/ROOM_DEV",
            "spaceType": "SPACE",
            "singleUserBotDm": False,
        },
        "message": {
            "name": "spaces/ROOM_DEV/messages/msg_203",
            "sender": {"displayName": "Dev User", "email": "dev@company.com"},
            "text": "<users/1234567890> can you review this code?",
            "annotations": [
                {
                    "type": "USER_MENTION",
                    "userMention": {
                        "type": "MENTION",
                        "user": {
                            "name": "users/1234567890",
                            "displayName": "Atlas Capsule",
                            "type": "BOT",
                        },
                    },
                }
            ],
        },
    }
    item = chat_connector.parse_webhook_event(payload)
    assert item is not None
    assert item.is_direct_message is False
    assert "can you review this code?" in item.clean_body
    assert "<users/1234567890>" not in item.clean_body


def test_group_space_added_to_space_event(chat_connector):
    """ADDED_TO_SPACE event must generate the self-introduction prompt."""
    payload = {
        "type": "ADDED_TO_SPACE",
        "space": {
            "name": "spaces/ROOM_NEW",
            "spaceType": "SPACE",
            "displayName": "New Project Room",
            "singleUserBotDm": False,
        },
        "user": {
            "displayName": "Admin User",
            "email": "admin@company.com",
        },
    }
    item = chat_connector.parse_webhook_event(payload)
    assert item is not None
    assert "You were just added to the space" in item.clean_body
    assert "introduce yourself cordially" in item.clean_body


# =====================================================================
# 3. Multimodal Attachments & Photos Tests
# =====================================================================

def test_parse_webhook_image_attachments(chat_connector):
    """Image attachments in Google Chat webhook payload must be extracted into ChatAttachment."""
    payload = {
        "type": "MESSAGE",
        "space": {
            "name": "spaces/DM_PHOTO",
            "singleUserBotDm": True,
        },
        "message": {
            "name": "spaces/DM_PHOTO/messages/msg_photo_1",
            "sender": {"displayName": "User", "email": "user@company.com"},
            "text": "¿Qué ves en esta foto?",
            "attachment": [
                {
                    "name": "spaces/DM_PHOTO/messages/msg_photo_1/attachments/att_1",
                    "contentName": "family_photo.jpg",
                    "contentType": "image/jpeg",
                    "downloadUri": "https://chat.googleapis.com/v1/media/att_1?alt=media",
                    "source": "UPLOADED_CONTENT",
                }
            ],
        },
    }

    with patch.object(chat_connector, "download_attachment", return_value=b"fake_jpeg_bytes"):
        item = chat_connector.parse_webhook_event(payload)
        assert item is not None
        assert len(item.attachments) == 1
        assert item.attachments[0].content_name == "family_photo.jpg"
        assert item.attachments[0].content_type == "image/jpeg"
        assert len(item.image_bytes_list) == 1
        assert item.image_bytes_list[0] == b"fake_jpeg_bytes"


# =====================================================================
# 4. User Reactions Tests
# =====================================================================

def test_parse_reaction_event_positive(chat_connector):
    """Positive emojis (thumbs up, heart, party) must be mapped to sentiment POSITIVE."""
    for emoji in ["👍", "❤️", "🎉", "🔥", "🚀"]:
        payload = {
            "type": "google.workspace.chat.reaction.v1.created",
            "reaction": {
                "name": "spaces/SPACE_1/messages/MSG_1/reactions/REACT_1",
                "emoji": {"unicode": emoji},
                "user": {"displayName": "Nicolas", "email": "nicolas@company.com"},
            },
            "space": {"name": "spaces/SPACE_1"},
            "message": {"name": "spaces/SPACE_1/messages/MSG_1"},
        }
        item = chat_connector.parse_reaction_event(payload)
        assert item is not None
        assert item.emoji == emoji
        assert item.sentiment == "POSITIVE"
        assert item.action == "CREATED"
        assert item.user_email == "nicolas@company.com"


def test_parse_reaction_event_negative(chat_connector):
    """Negative emojis (thumbs down, dislike, angry) must be mapped to sentiment NEGATIVE."""
    for emoji in ["👎", "😕", "❌", "😡"]:
        payload = {
            "type": "REACTION",
            "reaction": {
                "name": "spaces/SPACE_1/messages/MSG_1/reactions/REACT_2",
                "emoji": {"unicode": emoji},
                "user": {"displayName": "User", "email": "user@company.com"},
            },
            "space": {"name": "spaces/SPACE_1"},
            "message": {"name": "spaces/SPACE_1/messages/MSG_1"},
        }
        item = chat_connector.parse_reaction_event(payload)
        assert item is not None
        assert item.emoji == emoji
        assert item.sentiment == "NEGATIVE"


def test_parse_reaction_event_deleted(chat_connector):
    """Reaction deletion event must set action to DELETED."""
    payload = {
        "type": "google.workspace.chat.reaction.v1.deleted",
        "reaction": {
            "name": "spaces/SPACE_1/messages/MSG_1/reactions/REACT_3",
            "emoji": {"unicode": "👍"},
            "user": {"email": "user@company.com"},
        },
        "space": {"name": "spaces/SPACE_1"},
        "message": {"name": "spaces/SPACE_1/messages/MSG_1"},
    }
    item = chat_connector.parse_reaction_event(payload)
    assert item is not None
    assert item.action == "DELETED"


# =====================================================================
# 5. Formatting Tests for Google Chat
# =====================================================================

def test_format_for_google_chat():
    """Verify Markdown transformations for Google Chat rendering."""
    raw = (
        "# Status Update\n\n"
        "Here are the items:\n"
        "* **Item 1:** High priority\n"
        "- **Item 2:** Medium priority\n"
        "This is **very important**.\n\n\n\n"
        "-- Atlas"
    )
    formatted = GoogleChatConnector.format_for_google_chat(raw)

    # Header converted to bold
    assert "*Status Update*" in formatted
    assert "#" not in formatted

    # Composite bullet points converted to unicode bullet
    assert "• *Item 1:* High priority" in formatted
    assert "• *Item 2:* Medium priority" in formatted

    # Standard Markdown bold converted to single asterisks
    assert "*very important*" in formatted
    assert "**" not in formatted

    # Extra newlines collapsed
    assert "\n\n\n" not in formatted


# =====================================================================
# 6. Self-Message Filtering Tests
# =====================================================================

def test_ignore_self_sent_messages(chat_connector):
    """Messages from the bot's own email or displayName must be ignored to prevent self-reply loops."""
    payload_email = {
        "type": "MESSAGE",
        "space": {"name": "spaces/DM_SELF", "singleUserBotDm": True},
        "message": {
            "name": "spaces/DM_SELF/messages/msg_self_1",
            "sender": {"email": "atlas.capsule@company.com", "displayName": "Atlas Capsule"},
            "text": "Autonomous self message",
        },
    }
    assert chat_connector.parse_webhook_event(payload_email) is None

    payload_name = {
        "type": "MESSAGE",
        "space": {"name": "spaces/DM_SELF", "singleUserBotDm": True},
        "message": {
            "name": "spaces/DM_SELF/messages/msg_self_2",
            "sender": {"displayName": "Atlas Capsule"},
            "text": "Autonomous self message",
        },
    }
    assert chat_connector.parse_webhook_event(payload_name) is None


# =====================================================================
# 7. Polling fetch_new_chat_messages Tests
# =====================================================================

def test_fetch_new_chat_messages_polling(mock_profile):
    """Verify background polling fetches only unread messages and adheres to DM/group rules."""
    connector = GoogleChatConnector(credentials=None, identity_profile=mock_profile)

    mock_service = MagicMock()
    connector._service = mock_service

    # Mock spaces().list().execute()
    mock_service.spaces().list().execute.return_value = {
        "spaces": [
            {
                "name": "spaces/DM_1",
                "singleUserBotDm": True,
                "spaceType": "DIRECT_MESSAGE",
            },
            {
                "name": "spaces/ROOM_1",
                "spaceType": "SPACE",
                "singleUserBotDm": False,
            }
        ]
    }

    # Mock spaces().messages().list() for both spaces
    def mock_messages_list(parent, **kwargs):
        mock_req = MagicMock()
        if parent == "spaces/DM_1":
            mock_req.execute.return_value = {
                "messages": [
                    {
                        "name": "spaces/DM_1/messages/m1",
                        "sender": {"email": "user1@company.com", "displayName": "User One"},
                        "text": "Direct message to bot",
                    }
                ]
            }
        else:
            mock_req.execute.return_value = {
                "messages": [
                    {
                        "name": "spaces/ROOM_1/messages/m2_untagged",
                        "sender": {"email": "user2@company.com", "displayName": "User Two"},
                        "text": "Untagged message to everyone",
                    },
                    {
                        "name": "spaces/ROOM_1/messages/m3_tagged",
                        "sender": {"email": "user3@company.com", "displayName": "User Three"},
                        "text": "@Atlas Capsule tagged in room",
                    },
                ]
            }
        return mock_req

    mock_service.spaces().messages().list.side_effect = mock_messages_list

    processed_ids = set()
    items = connector.fetch_new_chat_messages(processed_ids)

    # Must contain 2 items: m1 (DM) and m3_tagged (tagged in room). m2_untagged must be ignored!
    item_ids = [it.message_id for it in items]
    assert "spaces/DM_1/messages/m1" in item_ids
    assert "spaces/ROOM_1/messages/m3_tagged" in item_ids
    assert "spaces/ROOM_1/messages/m2_untagged" not in item_ids

    # Verify DM status
    dm_item = next(it for it in items if it.message_id == "spaces/DM_1/messages/m1")
    assert dm_item.is_direct_message is True

    group_item = next(it for it in items if it.message_id == "spaces/ROOM_1/messages/m3_tagged")
    assert group_item.is_direct_message is False
    assert "@Atlas Capsule" not in group_item.clean_body


def test_fetch_new_chat_messages_min_create_time_filter():
    """Messages created before min_create_time must be ignored and added to processed_msg_ids."""
    mock_service = MagicMock()
    connector = GoogleChatConnector(credentials=MagicMock(), identity_profile={"identity": {"email": "bot@company.com", "name": "Atlas"}})
    connector._service = mock_service

    mock_service.spaces().list().execute.return_value = {
        "spaces": [{"name": "spaces/DM_1", "singleUserBotDm": True}]
    }

    # Message m_old is from 2026-09-20, m_new is from 2026-09-27
    mock_service.spaces().messages().list().execute.return_value = {
        "messages": [
            {
                "name": "spaces/DM_1/messages/m_old",
                "createTime": "2026-09-20T10:00:00Z",
                "sender": {"email": "user@company.com", "displayName": "User"},
                "text": "Old message that should be ignored",
            },
            {
                "name": "spaces/DM_1/messages/m_new",
                "createTime": "2026-09-27T10:00:00Z",
                "sender": {"email": "user@company.com", "displayName": "User"},
                "text": "Fresh new message",
            },
        ]
    }

    processed_ids = set()
    cutoff_time = datetime.datetime(2026, 9, 27, 9, 0, 0, tzinfo=datetime.timezone.utc)
    items = connector.fetch_new_chat_messages(processed_ids, min_create_time=cutoff_time)

    assert len(items) == 1
    assert items[0].message_id == "spaces/DM_1/messages/m_new"
    assert "spaces/DM_1/messages/m_old" in processed_ids


def test_derive_emoji_sentiment():
    """Verify derive_emoji_sentiment classifies positive, negative, and neutral reactions."""
    assert GoogleChatConnector.derive_emoji_sentiment("👍") == "POSITIVE"
    assert GoogleChatConnector.derive_emoji_sentiment("+1") == "POSITIVE"
    assert GoogleChatConnector.derive_emoji_sentiment("❤️") == "POSITIVE"
    assert GoogleChatConnector.derive_emoji_sentiment("👎") == "NEGATIVE"
    assert GoogleChatConnector.derive_emoji_sentiment("-1") == "NEGATIVE"
    assert GoogleChatConnector.derive_emoji_sentiment("dislike") == "NEGATIVE"
    assert GoogleChatConnector.derive_emoji_sentiment("🤔") == "NEUTRAL"
    assert GoogleChatConnector.derive_emoji_sentiment("") == "NEUTRAL"


def test_list_reactions(chat_connector):
    """Verify list_reactions queries the Google Chat API reactions subresource."""
    mock_service = MagicMock()
    chat_connector._service = mock_service
    mock_service.spaces().messages().reactions().list().execute.return_value = {
        "reactions": [
            {
                "name": "spaces/SP1/messages/M1/reactions/R1",
                "emoji": {"unicode": "👍"},
                "user": {"name": "users/100", "displayName": "Nicolas", "email": "nicolas@company.com"},
            }
        ]
    }
    reactions = chat_connector.list_reactions("spaces/SP1/messages/M1")
    assert len(reactions) == 1
    assert reactions[0]["emoji"]["unicode"] == "👍"
    assert reactions[0]["user"]["displayName"] == "Nicolas"


def test_fetch_new_chat_messages_triggers_bot_message_callback():
    """Bot's own messages should trigger on_bot_message_detected callback for user reaction polling."""
    mock_service = MagicMock()
    connector = GoogleChatConnector(
        credentials=MagicMock(),
        identity_profile={"identity": {"email": "bot@company.com", "name": "Atlas Capsule"}},
    )
    connector._service = mock_service

    mock_service.spaces().list().execute.return_value = {
        "spaces": [{"name": "spaces/DM_1", "singleUserBotDm": True}]
    }

    mock_service.spaces().messages().list().execute.return_value = {
        "messages": [
            {
                "name": "spaces/DM_1/messages/bot_reply_1",
                "sender": {"email": "bot@company.com", "displayName": "Atlas Capsule"},
                "text": "Entendido Nicolas. Si surge algo estoy aquí para ayudarte.",
            }
        ]
    }

    detected_bot_messages = []

    def handle_bot_msg(msg_id, space_name):
        detected_bot_messages.append((msg_id, space_name))

    processed_ids = set()
    items = connector.fetch_new_chat_messages(
        processed_ids,
        on_bot_message_detected=handle_bot_msg,
    )

    # Bot message should not be returned as an incoming user task item
    assert len(items) == 0
    # But it must be captured in the bot callback so reactions can be synced
    assert len(detected_bot_messages) == 1
    assert detected_bot_messages[0] == ("spaces/DM_1/messages/bot_reply_1", "spaces/DM_1")
    assert "spaces/DM_1/messages/bot_reply_1" in processed_ids


def test_get_thread_history_formatting(chat_connector):
    """Test get_thread_history correctly filters, attributes, and formats chronological conversation history."""
    mock_service = MagicMock()
    chat_connector._service = mock_service

    mock_service.spaces().messages().list().execute.return_value = {
        "messages": [
            {
                "name": "spaces/SP1/messages/msg_newest",
                "createTime": "2026-10-03T09:10:00Z",
                "thread": {"name": "spaces/SP1/threads/th1"},
                "sender": {"displayName": "Nicolas", "email": "nicolas@company.com"},
                "text": "current question",
            },
            {
                "name": "spaces/SP1/messages/msg_2",
                "createTime": "2026-10-03T09:05:00Z",
                "thread": {"name": "spaces/SP1/threads/th1"},
                "sender": {"displayName": "Atlas Capsule", "email": "atlas.capsule@company.com", "type": "BOT"},
                "text": "Hola Nicolas, ¿en qué te ayudo?",
            },
            {
                "name": "spaces/SP1/messages/msg_1",
                "createTime": "2026-10-03T09:00:00Z",
                "thread": {"name": "spaces/SP1/threads/th1"},
                "sender": {"displayName": "Nicolas", "email": "nicolas@company.com"},
                "text": "@Atlas Capsule hola",
            },
        ]
    }

    history = chat_connector.get_thread_history(
        space_id="spaces/SP1",
        thread_id="spaces/SP1/threads/th1",
        current_message_id="spaces/SP1/messages/msg_newest",
        limit=5,
    )

    # Oldest first: msg_1 then msg_2, excluding msg_newest
    assert "Nicolas: hola" in history
    assert "Atlas Capsule: Hola Nicolas, ¿en qué te ayudo?" in history
    lines = history.strip().split("\n")
    assert len(lines) == 2
    assert lines[0] == "Nicolas: hola"
    assert lines[1] == "Atlas Capsule: Hola Nicolas, ¿en qué te ayudo?"


