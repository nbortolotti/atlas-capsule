import datetime
import html
import logging
from typing import Any, Dict, List, Optional, Set
from bs4 import BeautifulSoup
from googleapiclient.discovery import Resource, build

from src.models.message import ChatAttachment, ChatItem, ReactionItem

logger = logging.getLogger(__name__)


class GoogleChatConnector:
    """Wrapper for the Google Chat API and Webhook event handling with safe text sanitization."""

    def __init__(self, credentials=None, identity_profile: Optional[Dict[str, Any]] = None):
        self.credentials = credentials
        self.identity_profile = identity_profile or {}
        self._service: Optional[Resource] = None
        self._spaces_cache: Dict[str, Dict[str, Any]] = {}

    @property
    def service(self) -> Optional[Resource]:
        if not self._service and self.credentials:
            try:
                self._service = build("chat", "v1", credentials=self.credentials)
            except Exception as e:
                logger.warning(f"Could not build Google Chat API service: {e}")
        return self._service

    def get_space(self, space_name: str) -> Optional[Dict[str, Any]]:
        """Retrieves and caches space metadata from Google Chat API."""
        if not space_name or space_name == "spaces/default":
            return None
        if space_name in self._spaces_cache:
            return self._spaces_cache[space_name]

        if self.service:
            try:
                space_info = self.service.spaces().get(name=space_name).execute()
                self._spaces_cache[space_name] = space_info
                return space_info
            except Exception as e:
                logger.debug(f"Could not retrieve space info for {space_name}: {e}")
        return None

    def find_default_space(self) -> Optional[str]:
        """Discovers the most appropriate active space (e.g. collaborative SPACE or active DM) for sending automated messages or notifications."""
        if not self.service:
            return None
        try:
            res = self.service.spaces().list().execute()
            spaces = res.get("spaces", [])
            if not spaces:
                return None

            # Prioritize collaborative spaces (e.g. Family Hub / General) over DMs
            collaborative_spaces = []
            dm_spaces = []
            for sp in spaces:
                name = sp.get("name")
                if not name or not name.startswith("spaces/"):
                    continue
                raw_type = (sp.get("spaceType") or sp.get("type") or "").upper()
                is_dm = bool(sp.get("singleUserBotDm")) or raw_type in ["DM", "DIRECT_MESSAGE"]
                if is_dm:
                    dm_spaces.append(name)
                else:
                    collaborative_spaces.append(name)

            if collaborative_spaces:
                chosen = collaborative_spaces[0]
                logger.info(f"Discovered default collaborative space: {chosen}")
                return chosen
            if dm_spaces:
                chosen = dm_spaces[0]
                logger.info(f"Discovered default DM space: {chosen}")
                return chosen
        except Exception as e:
            logger.warning(f"Error discovering default Google Chat space: {e}")
        return None

    @staticmethod
    def sanitize_message_text(raw_text: str) -> str:
        """Strips harmful tags or scripts, keeping clean conversational text."""
        if not raw_text:
            return ""
        soup = BeautifulSoup(raw_text, "html.parser")
        for tag in soup(["script", "style", "noscript", "iframe", "object", "embed"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines()]
        cleaned = "\n".join([line for line in lines if line])
        return html.unescape(cleaned)

    @staticmethod
    def format_for_google_chat(text: str) -> str:
        """Adapts and polishes standard Markdown for Google Chat rendering.
        - Converts standard Markdown bold (**bold**) to Google Chat bold (*bold*).
        - Converts Markdown bullet points (* item or - item) into clean bullet symbols (• item).
        - Fixes composite bullet bolding (* **Item:** -> • *Item:*).
        - Strips markdown headers (# Header -> *Header*).
        - Normalizes multiple blank lines to keep chat bubbles compact and legible.
        """
        if not text:
            return ""

        import re

        result = text

        # 1. Convert headers (# Title) to bold Google Chat text (*Title*)
        result = re.sub(r"^(?:#{1,6})\s+(.+)$", r"*\1*", result, flags=re.MULTILINE)

        # 2. Fix composite bullet bold patterns like '* **Item:**' or '- **Item:**' -> '• *Item:*'
        result = re.sub(r"^[ \t]*[\*\-]\s+\*\*(.+?)\*\*", r"• *\1*", result, flags=re.MULTILINE)

        # 3. Convert Markdown bullets ('* ' or '- ') at start of line to clean unicode bullet ('• ')
        result = re.sub(r"^[ \t]*[\*\-]\s+", r"• ", result, flags=re.MULTILINE)

        # 4. Convert remaining Markdown bold (**text**) to Google Chat bold (*text*)
        result = re.sub(r"\*\*(.+?)\*\*", r"*\1*", result)

        # 5. Clean up triple or more consecutive newlines down to double newlines
        result = re.sub(r"\n{3,}", "\n\n", result)

        return result.strip()

    def is_identity_mentioned(self, message: Dict[str, Any], raw_text: str) -> bool:
        """Determines if the synthetic identity was tagged/mentioned in the message:
        - Annotation USER_MENTION matching identity email, name, or BOT
        - Text explicitly mentions '@Atlas', '@Capsule', or the identity's email/name.
        """
        # Note: Do NOT blindly return True for argumentText, because the Google Chat API list()
        # endpoint sets argumentText == text for ALL messages regardless of mentions!

        # Check annotations
        annotations = message.get("annotations", [])
        for a in annotations:
            if a.get("type") == "USER_MENTION":
                mentioned_user = a.get("userMention", {}).get("user", {})
                user_email = (mentioned_user.get("email") or "").lower()
                user_name = (mentioned_user.get("displayName") or "").lower()
                user_type = mentioned_user.get("type")

                # Match identity email or name if available
                ident_email = self.identity_profile.get("identity", {}).get("email", "").lower()
                ident_name = self.identity_profile.get("identity", {}).get("name", "").lower()
                if (ident_email and ident_email in user_email) or (ident_name and ident_name in user_name):
                    return True
                # Match keywords "atlas" or "capsule" in mentioned user name/email
                if "atlas" in user_name or "capsule" in user_name or "atlas" in user_email or "capsule" in user_email:
                    return True
                # If Google Chat explicitly marks it as BOT
                if user_type == "BOT":
                    return True

        # Check text-based patterns (require explicit @ tag, never match casual text)
        if raw_text:
            low_text = raw_text.lower()
            ident_email = self.identity_profile.get("identity", {}).get("email", "").lower()
            ident_user = ident_email.split("@")[0] if "@" in ident_email else ""
            ident_name = self.identity_profile.get("identity", {}).get("name", "").lower()

            target_tokens = ["@atlas", "@capsule", "@bot", "@assistant"]
            if ident_name:
                target_tokens.extend([f"@{ident_name}", ident_name])
            if ident_user:
                target_tokens.extend([f"@{ident_user}", ident_user])

            for token in target_tokens:
                if token in low_text:
                    return True

        return False

    def _should_process_message(self, is_direct_message: bool, has_bot_tag: bool, space_name: str, space_type: str, raw_text: str) -> bool:
        """Centralized decision whether a message should be processed.
        - Direct messages are always processed.
        - In shared spaces (non‑DM), process only when the bot is explicitly mentioned.
        Logs the decision for debugging.
        """
        if is_direct_message:
            return True
        if not has_bot_tag:
            logger.info(f"Skipping untagged message in space {space_name} (type: {space_type}). Text preview: '{raw_text[:40]}'")
            return False
        return True
    def parse_webhook_event(self, event_payload: Dict[str, Any]) -> Optional[ChatItem]:
        """Parses an incoming Google Chat HTTP webhook payload (MESSAGE or ADDED_TO_SPACE event)."""
        try:
            logger.info(f"Incoming Google Chat webhook event: {event_payload}")
            event_type = event_payload.get("type", "MESSAGE")
            message = event_payload.get("message", {})
            user = event_payload.get("user") or message.get("sender", {})
            # Merge space objects from top-level event, nested event, and message payload
            space_from_event = event_payload.get("space") or {}
            space_from_msg = message.get("space") or {}
            nested_event = event_payload.get("event") or {}
            space_from_nested = nested_event.get("space") or nested_event.get("message", {}).get("space") or {}
            space = {**space_from_nested, **space_from_msg, **space_from_event}
            thread = message.get("thread", {})

            # Extract user email or fallback to display name / user resource name
            display_name = user.get("displayName") or user.get("name") or "User"
            sender_email = (
                user.get("email")
                or (f"{display_name.lower().replace(' ', '.')}@chat.google.com" if display_name else None)
                or user.get("name")
                or "anonymous@chat.google.com"
            )

            ident_email = self.identity_profile.get("identity", {}).get("email", "").lower()
            ident_name = self.identity_profile.get("identity", {}).get("name", "").lower()

            # Ignore messages sent by the synthetic identity itself
            if ident_email and ident_email in sender_email.lower():
                logger.debug(f"Ignoring self-sent message from {sender_email}")
                return None
            if ident_name and display_name and ident_name in display_name.lower():
                logger.debug(f"Ignoring self-sent message from {display_name}")
                return None

            # Determine space type; default to SPACE (non-DM) unless explicitly DM
            # Check all Google Chat API v1 markers for 1:1 direct messages
            raw_space_type = (
                space.get("spaceType")
                or space.get("type")
                or space_from_msg.get("spaceType")
                or space_from_msg.get("type")
                or space_from_event.get("spaceType")
                or space_from_event.get("type")
                or ""
            ).upper()
            is_single_user_bot_dm = bool(
                space.get("singleUserBotDm")
                or space_from_msg.get("singleUserBotDm")
                or space_from_event.get("singleUserBotDm")
                or space_from_nested.get("singleUserBotDm")
            )
            is_direct_message = (
                is_single_user_bot_dm
                or raw_space_type in ["DM", "DIRECT_MESSAGE"]
                or "DM" in raw_space_type
                or "DIRECT" in raw_space_type
            )

            space_name = space.get("name") or "spaces/default"
            if not is_direct_message and not raw_space_type and space_name != "spaces/default":
                cached_space = self.get_space(space_name)
                if cached_space:
                    cached_type = (cached_space.get("spaceType") or cached_space.get("type") or "").upper()
                    if cached_space.get("singleUserBotDm") or cached_type in ["DM", "DIRECT_MESSAGE"] or "DM" in cached_type:
                        is_direct_message = True
                        raw_space_type = cached_type or "DIRECT_MESSAGE"

            space_type = "DM" if is_direct_message else (raw_space_type or "SPACE")
            logger.info(f"Chat space '{space_name}' evaluated: type={space_type}, is_direct_message={is_direct_message}, singleUserBotDm={is_single_user_bot_dm}")

            # If it is a group/space, only respond if explicitly mentioned or invited
            if event_type == "ADDED_TO_SPACE":
                raw_text = (
                    f"Hello! You were just added to the space '{space.get('displayName', 'New Space')}'. "
                    f"Please introduce yourself cordially as the synthetic identity."
                )
            else:
                raw_text = message.get("text") or message.get("argumentText") or ""
                has_bot_tag = self.is_identity_mentioned(message, raw_text)

                # In spaces (non-DM), require an explicit mention to the bot
                if not self._should_process_message(is_direct_message, has_bot_tag, space_name, space_type, raw_text):
                    return None

                # Clean leading bot @mentions or <users/...> tokens from the message body
                if "@" in raw_text or "<users/" in raw_text:
                    import re
                    ident_tokens = ["atlas capsule", "atlas", "capsule", "assistant", "bot"]
                    ident_name = self.identity_profile.get("identity", {}).get("name", "").lower()
                    if ident_name and ident_name not in ident_tokens:
                        ident_tokens.append(ident_name)
                    # Sort longest token first so composite mentions (e.g. '@atlas capsule') match before single words
                    ident_tokens.sort(key=len, reverse=True)
                    pattern = r"^@(?:" + "|".join(re.escape(t) for t in ident_tokens) + r")\b\s*"
                    raw_text = re.sub(pattern, "", raw_text, flags=re.IGNORECASE).strip()
                    raw_text = re.sub(r"^<users/[^>]+>\s*", "", raw_text).strip()

            clean_body = self.sanitize_message_text(raw_text)

            msg_id = message.get("name") or f"chat_{event_payload.get('eventTime', 'now')}"
            space_id = space.get("name") or "spaces/default"
            thread_id = thread.get("name") or space_id

            # Parse any attachments (photos / images uploaded by the user)
            raw_attachments = message.get("attachment") or message.get("attachments") or []
            chat_attachments: List[ChatAttachment] = []
            image_bytes_list: List[bytes] = []

            for att in raw_attachments:
                content_type = att.get("contentType") or "image/jpeg"
                content_name = att.get("contentName") or "attachment"
                att_name = att.get("name")  # e.g. spaces/.../messages/.../attachments/...
                dl_url = att.get("downloadUri") or att.get("thumbnailUri")

                chat_attachments.append(
                    ChatAttachment(
                        name=att_name,
                        content_name=content_name,
                        content_type=content_type,
                        download_url=dl_url,
                        source=att.get("source"),
                    )
                )

                # If it's an image, attempt authenticated download if service/credentials available
                if "image" in content_type.lower():
                    img_data = self.download_attachment(att)
                    if img_data:
                        image_bytes_list.append(img_data)
                        logger.info(f"Successfully downloaded image attachment {content_name} ({len(img_data)} bytes)")

            return ChatItem(
                message_id=msg_id,
                space_id=space_id,
                thread_id=thread_id,
                sender=sender_email,
                sender_name=display_name,
                clean_body=clean_body,
                service_name="chat",
                is_direct_message=is_direct_message,
                attachments=chat_attachments,
                image_bytes_list=image_bytes_list,
            )
        except Exception as e:
            logger.error(f"Error parsing Google Chat webhook event: {e}")
            return None

    def download_attachment(self, attachment_info: Dict[str, Any]) -> Optional[bytes]:
        """Downloads attachment bytes from Google Chat media service or downloadUri."""
        try:
            import requests

            # 1. Direct downloadUri using OAuth token if available
            token = None
            if self.credentials:
                if hasattr(self.credentials, "token") and self.credentials.token:
                    token = self.credentials.token
                elif hasattr(self.credentials, "get_access_token"):
                    token = self.credentials.get_access_token().access_token

            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            download_url = attachment_info.get("downloadUri")
            if download_url:
                resp = requests.get(download_url, headers=headers, timeout=15)
                if resp.status_code == 200 and resp.content:
                    return resp.content
                logger.debug(f"Direct downloadUri returned status {resp.status_code}, trying media API...")

            # 2. Try Google Chat API media().download()
            att_name = attachment_info.get("name")
            if self.service and att_name:
                try:
                    media_req = self.service.media().download_media(resourceName=att_name)
                    import io
                    from googleapiclient.http import MediaIoBaseDownload
                    fh = io.BytesIO()
                    downloader = MediaIoBaseDownload(fh, media_req)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()
                    return fh.getvalue()
                except Exception as inner_e:
                    logger.debug(f"Media download failed for {att_name}: {inner_e}")

        except Exception as e:
            logger.warning(f"Could not download attachment: {e}")
        return None

    def parse_reaction_event(self, event_payload: Dict[str, Any]) -> Optional[ReactionItem]:
        """Parses a Google Chat reaction event (e.g. from Google Workspace Events or reaction webhook)."""
        try:
            event_type = event_payload.get("type", "") or event_payload.get("eventType", "")
            # Typical event types: "google.workspace.chat.reaction.v1.created", "REACTION", "MESSAGE_REACTION"
            reaction_data = event_payload.get("reaction") or event_payload.get("event", {}).get("reaction", {}) or event_payload.get("data", {}).get("reaction", {})
            if not reaction_data and "reaction" not in event_type.lower():
                return None

            user = event_payload.get("user") or reaction_data.get("user") or event_payload.get("event", {}).get("user") or {}
            user_email = user.get("email") or user.get("name") or "unknown@chat.google.com"
            user_name = user.get("displayName") or user_email

            # Extract emoji
            emoji_obj = reaction_data.get("emoji", {})
            unicode_emoji = emoji_obj.get("unicode") or emoji_obj.get("customEmoji", {}).get("uid") or reaction_data.get("emoji") or "👍"

            # Extract target message and space
            message_name = reaction_data.get("message") or reaction_data.get("messageName") or event_payload.get("message", {}).get("name") or ""
            space_name = event_payload.get("space", {}).get("name") or reaction_data.get("space") or event_payload.get("event", {}).get("space", {}).get("name") or ""

            # Check action (created vs deleted)
            action = "DELETED" if "deleted" in event_type.lower() else "CREATED"

            # Derive sentiment
            sentiment = self.derive_emoji_sentiment(unicode_emoji)

            return ReactionItem(
                reaction_name=reaction_data.get("name"),
                emoji=unicode_emoji,
                space_id=space_name,
                message_id=message_name,
                user_email=user_email,
                user_name=user_name,
                action=action,
                sentiment=sentiment,
            )
        except Exception as e:
            logger.error(f"Error parsing reaction event: {e}")
            return None

    def fetch_new_chat_messages(
        self,
        processed_msg_ids: set,
        min_create_time: Optional[datetime.datetime] = None,
        on_bot_message_detected: Optional[Any] = None,
    ) -> List[ChatItem]:
        """Fetches recent incoming messages across accessible spaces/DMs that haven't been processed yet."""
        if not self.service:
            return []

        items: List[ChatItem] = []
        try:
            spaces_res = self.service.spaces().list().execute()
            spaces = spaces_res.get("spaces", [])

            for space in spaces:
                space_name = space.get("name")
                if not space_name:
                    continue

                # In Google Chat API v1, spaces without type or spaceType are collaborative spaces (SPACE), not DMs
                raw_space_type = (space.get("spaceType") or space.get("type") or "").upper()
                is_single_user_bot_dm = bool(space.get("singleUserBotDm"))
                is_direct_message = (
                    is_single_user_bot_dm
                    or raw_space_type in ["DM", "DIRECT_MESSAGE"]
                    or space.get("type") == "DM"
                    or space.get("spaceType") == "DIRECT_MESSAGE"
                )
                space_type = "DM" if is_direct_message else (raw_space_type or "SPACE")

                try:
                    # List recent messages in space sorted by createTime descending to always fetch the latest messages
                    msg_res = self.service.spaces().messages().list(
                        parent=space_name,
                        pageSize=20,
                        orderBy="createTime desc",
                    ).execute()
                    raw_messages = msg_res.get("messages", [])
                    # Reverse so we process oldest to newest among the recent batch
                    messages = list(reversed(raw_messages))

                    for m in messages:
                        msg_id = m.get("name")
                        if not msg_id:
                            continue

                        sender_obj = m.get("sender", {})
                        sender_email = sender_obj.get("email") or sender_obj.get("name") or "unknown@chat.google.com"
                        sender_name = sender_obj.get("displayName") or sender_email

                        ident_email = self.identity_profile.get("identity", {}).get("email", "").lower()
                        ident_name = self.identity_profile.get("identity", {}).get("name", "").lower()

                        # Check if message was sent by the synthetic identity itself
                        is_bot_message = (
                            (ident_email and ident_email in sender_email.lower())
                            or (ident_name and sender_name and ident_name in sender_name.lower())
                        )
                        if is_bot_message:
                            processed_msg_ids.add(msg_id)
                            # Invoke callback if provided to inspect user reactions on bot responses
                            if on_bot_message_detected:
                                try:
                                    on_bot_message_detected(msg_id, space_name)
                                except Exception as cb_err:
                                    logger.debug(f"Error in on_bot_message_detected callback for {msg_id}: {cb_err}")
                            continue

                        if msg_id in processed_msg_ids:
                            continue

                        raw_text = m.get("text") or m.get("argumentText") or ""
                        has_bot_tag = self.is_identity_mentioned(m, raw_text)

                        # Filter out messages created before min_create_time to avoid re-processing historical messages on restarts
                        if min_create_time and m.get("createTime"):
                            try:
                                msg_time_str = m["createTime"].replace("Z", "+00:00")
                                msg_time = datetime.datetime.fromisoformat(msg_time_str)
                                if min_create_time.tzinfo is None and msg_time.tzinfo is not None:
                                    min_create_time_tz = min_create_time.replace(tzinfo=datetime.timezone.utc)
                                else:
                                    min_create_time_tz = min_create_time
                                if msg_time < min_create_time_tz:
                                    processed_msg_ids.add(msg_id)
                                    continue
                            except Exception as te:
                                logger.debug(f"Could not parse message createTime {m.get('createTime')}: {te}")

                        # In shared spaces, only process if the identity was tagged/mentioned
                        if not self._should_process_message(is_direct_message, has_bot_tag, space_name, space_type, raw_text):
                            processed_msg_ids.add(msg_id)
                            continue

                        if "@" in raw_text or "<users/" in raw_text:
                            import re
                            ident_tokens = ["atlas capsule", "atlas", "capsule", "assistant", "bot"]
                            ident_name = self.identity_profile.get("identity", {}).get("name", "").lower()
                            if ident_name and ident_name not in ident_tokens:
                                ident_tokens.append(ident_name)
                            ident_tokens.sort(key=len, reverse=True)
                            pattern = r"^@(?:" + "|".join(re.escape(t) for t in ident_tokens) + r")\b\s*"
                            raw_text = re.sub(pattern, "", raw_text, flags=re.IGNORECASE).strip()
                            raw_text = re.sub(r"^<users/[^>]+>\s*", "", raw_text).strip()
                            raw_text = re.sub(pattern, "", raw_text, flags=re.IGNORECASE).strip()

                        clean_body = self.sanitize_message_text(raw_text)

                        thread_obj = m.get("thread", {})
                        thread_id = thread_obj.get("name") or space_name

                        items.append(
                            ChatItem(
                                message_id=msg_id,
                                space_id=space_name,
                                thread_id=thread_id,
                                sender=sender_email,
                                sender_name=sender_name,
                                clean_body=clean_body,
                                service_name="chat",
                                is_direct_message=is_direct_message,
                            )
                        )
                except Exception as inner_e:
                    logger.debug(f"Could not list messages for space {space_name}: {inner_e}")
                    continue

        except Exception as e:
            logger.warning(f"Error fetching Google Chat messages: {e}")

        return items

    def send_message(self, space_id: str, text: str, thread_id: Optional[str] = None) -> Optional[str]:
        """Sends a message to a Google Chat space via Chat API."""
        if not self.service:
            logger.info(f"Google Chat API service not initialized. Simulating send to space {space_id}: {text[:60]}")
            return f"simulated_chat_msg_{hash(text)}"

        try:
            formatted_text = self.format_for_google_chat(text)
            body = {"text": formatted_text}
            kwargs = {"parent": space_id, "body": body}
            if thread_id and thread_id != space_id:
                body["thread"] = {"name": thread_id}
                kwargs["messageReplyOption"] = "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"

            res = self.service.spaces().messages().create(**kwargs).execute()
            msg_name = res.get("name")
            logger.info(f"Sent Google Chat message to {space_id} with name {msg_name}")
            return msg_name
        except Exception as e:
            logger.error(f"Error sending message to Google Chat: {e}")
            return None

    @staticmethod
    def derive_emoji_sentiment(emoji_str: str) -> str:
        """Determines if an emoji or emoji code represents positive, negative, or neutral sentiment."""
        if not emoji_str:
            return "NEUTRAL"
        positive_emojis = {"👍", "+1", ":+1:", ":thumbsup:", "❤️", "❤", ":heart:", "🎉", "👏", "🔥", "😊", "🚀", "🙌"}
        negative_emojis = {"👎", "-1", ":-1:", ":thumbsdown:", "😕", "❌", "😢", "😡", "💔"}
        if emoji_str in positive_emojis or any(p in emoji_str.lower() for p in ["+1", "thumbsup", "heart", "smile"]):
            return "POSITIVE"
        elif emoji_str in negative_emojis or any(n in emoji_str.lower() for n in ["-1", "thumbsdown", "dislike", "angry"]):
            return "NEGATIVE"
        return "NEUTRAL"

    def add_reaction(self, message_id: str, emoji_unicode: str = "👀") -> Optional[str]:
        """Adds an emoji reaction to a Google Chat message (e.g. eyes 👀 to indicate processing).
        
        Args:
            message_id: Resource name of the message (e.g. 'spaces/AAAA/messages/BBBB').
            emoji_unicode: Unicode emoji character, default '👀'.
            
        Returns:
            Resource name of the created reaction or None.
        """
        if not self.service:
            logger.info(f"Google Chat API service not initialized. Simulating reaction '{emoji_unicode}' on {message_id}")
            return f"simulated_reaction_{emoji_unicode}_{message_id}"

        try:
            body = {
                "emoji": {
                    "unicode": emoji_unicode
                }
            }
            res = self.service.spaces().messages().reactions().create(
                parent=message_id,
                body=body
            ).execute()
            reaction_name = res.get("name")
            logger.info(f"Successfully added reaction '{emoji_unicode}' to message {message_id} (reaction: {reaction_name})")
            return reaction_name
        except Exception as e:
            logger.warning(f"Could not add reaction '{emoji_unicode}' to message {message_id}: {e}")
            return None

    def list_reactions(self, message_id: str) -> List[Dict[str, Any]]:
        """Lists user emoji reactions attached to a specific Google Chat message.
        
        Args:
            message_id: Resource name of the message (e.g. 'spaces/AAAA/messages/BBBB').
            
        Returns:
            List of raw reaction dictionaries from Google Chat API.
        """
        if not self.service:
            return []

        try:
            res = self.service.spaces().messages().reactions().list(
                parent=message_id,
                pageSize=50
            ).execute()
            return res.get("reactions", [])
        except Exception as e:
            logger.debug(f"Could not list reactions for message {message_id}: {e}")
            return []

    def get_thread_history(
        self,
        space_id: str,
        thread_id: Optional[str] = None,
        current_message_id: Optional[str] = None,
        limit: int = 8,
    ) -> str:
        """Fetches prior conversational history in the current thread or space to provide context to the agent.
        
        Args:
            space_id: Resource name of the space (e.g. 'spaces/AAAA').
            thread_id: Optional resource name of the thread (e.g. 'spaces/AAAA/threads/BBBB').
            current_message_id: Resource name of the current message being processed (to exclude it from history).
            limit: Maximum number of prior messages to include in the conversation history context.
            
        Returns:
            A formatted multi-line string representing the chronological dialogue of the thread, or empty string.
        """
        if not self.service or not space_id or space_id == "spaces/default":
            return ""

        try:
            # Query recent messages in space sorted by createTime desc
            list_kwargs = {
                "parent": space_id,
                "pageSize": 25,
                "orderBy": "createTime desc",
            }

            # In Google Chat API, filter can optionally be applied on thread.name if present
            # Note: Not all workspace editions support filter syntax consistently, so we fetch recent and filter in Python.
            msg_res = self.service.spaces().messages().list(**list_kwargs).execute()
            raw_messages = msg_res.get("messages", [])

            history_items = []
            for m in raw_messages:
                m_id = m.get("name")
                if current_message_id and m_id == current_message_id:
                    continue

                # Match thread if thread_id is defined and different from space_id
                if thread_id and thread_id != space_id:
                    msg_thread = m.get("thread", {}).get("name")
                    if msg_thread and msg_thread != thread_id:
                        continue

                sender_obj = m.get("sender", {})
                sender_name = sender_obj.get("displayName") or sender_obj.get("name") or "User"
                text = m.get("text") or m.get("argumentText") or ""
                clean_text = self.sanitize_message_text(text)
                if not clean_text:
                    continue

                ident_name = self.identity_profile.get("identity", {}).get("name", "").lower()
                ident_email = self.identity_profile.get("identity", {}).get("email", "").lower()
                sender_email = (sender_obj.get("email") or "").lower()

                # Determine if sender was the capsule or human
                is_bot = (
                    (ident_email and ident_email in sender_email)
                    or (ident_name and ident_name in sender_name.lower())
                    or sender_obj.get("type") == "BOT"
                )
                role_label = self.identity_profile.get("identity", {}).get("name", "Companion (You)") if is_bot else sender_name

                # Clean leading bot mentions from user text in history
                if "@" in clean_text:
                    import re
                    ident_tokens = ["atlas capsule", "atlas", "capsule", "assistant", "bot"]
                    if ident_name and ident_name not in ident_tokens:
                        ident_tokens.append(ident_name)
                    ident_tokens.sort(key=len, reverse=True)
                    pattern = r"^@(?:" + "|".join(re.escape(t) for t in ident_tokens) + r")\b\s*"
                    clean_text = re.sub(pattern, "", clean_text, flags=re.IGNORECASE).strip()

                history_items.append((m.get("createTime", ""), f"{role_label}: {clean_text}"))
                if len(history_items) >= limit:
                    break

            if not history_items:
                return ""

            # Reverse so chronological order (oldest to newest) is restored for prompt readability
            history_items.reverse()
            formatted_history = "\n".join(entry[1] for entry in history_items)
            return formatted_history
        except Exception as e:
            logger.warning(f"Could not retrieve Google Chat thread history for space {space_id} thread {thread_id}: {e}")
            return ""


