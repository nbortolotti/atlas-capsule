import base64
import email
from email.message import EmailMessage
import html
import logging
import re
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
from googleapiclient.discovery import Resource, build

from src.models.message import EmailItem

logger = logging.getLogger(__name__)


class GmailConnector:
    """Wrapper for the Gmail REST API with safe text sanitization."""

    def __init__(self, credentials):
        self.credentials = credentials
        self._service: Optional[Resource] = None

    @property
    def service(self) -> Resource:
        if not self._service:
            self._service = build("gmail", "v1", credentials=self.credentials)
        return self._service

    @staticmethod
    def sanitize_html(raw_html: str) -> str:
        """Strips scripts, styles, and extracts clean, safe text from HTML."""
        soup = BeautifulSoup(raw_html, "html.parser")
        for tag in soup(["script", "style", "noscript", "iframe", "object", "embed"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        # Collapse excessive whitespace
        lines = [line.strip() for line in text.splitlines()]
        cleaned = "\n".join([line for line in lines if line])
        return html.unescape(cleaned)

    def _extract_body_from_payload(self, payload: Dict[str, Any]) -> str:
        """Recursively parses Gmail payload parts to extract plain text or sanitized HTML."""
        body = ""
        mime_type = payload.get("mimeType", "")

        if "parts" in payload:
            for part in payload["parts"]:
                body += self._extract_body_from_payload(part) + "\n"
        else:
            data = payload.get("body", {}).get("data")
            if data:
                try:
                    decoded_bytes = base64.urlsafe_b64decode(data.encode("ASCII"))
                    decoded_str = decoded_bytes.decode("utf-8", errors="replace")
                    if mime_type == "text/html":
                        body = self.sanitize_html(decoded_str)
                    else:
                        body = decoded_str
                except Exception as e:
                    logger.warning(f"Failed to decode message part: {e}")

        return body.strip()

    def fetch_unread_messages(self, query: str = "label:INBOX is:unread", max_results: int = 10) -> List[EmailItem]:
        """Fetches unread inbox messages matching query."""
        try:
            results = (
                self.service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
            raw_messages = results.get("messages", [])
            items: List[EmailItem] = []

            for raw_msg in raw_messages:
                msg_id = raw_msg["id"]
                full_msg = (
                    self.service.users()
                    .messages()
                    .get(userId="me", id=msg_id, format="full")
                    .execute()
                )

                payload = full_msg.get("payload", {})
                headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

                sender = headers.get("from", "unknown")
                recipient = headers.get("to", "me")
                subject = headers.get("subject", "(No Subject)")
                snippet = full_msg.get("snippet", "")
                thread_id = full_msg.get("threadId", msg_id)
                labels = full_msg.get("labelIds", [])

                clean_body = self._extract_body_from_payload(payload)
                if not clean_body:
                    clean_body = snippet

                items.append(
                    EmailItem(
                        message_id=msg_id,
                        thread_id=thread_id,
                        sender=sender,
                        recipient=recipient,
                        subject=subject,
                        snippet=snippet,
                        clean_body=clean_body,
                        labels=labels,
                    )
                )

            return items
        except Exception as e:
            logger.error(f"Error fetching unread messages: {e}")
            return []

    def create_draft(self, thread_id: str, to: str, subject: str, body: str) -> Optional[str]:
        """Creates a draft email in the user's Gmail box, tied to the original thread."""
        try:
            message = EmailMessage()
            message.set_content(body)
            message["To"] = to
            # Prepend Re: if not present
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
            message["Subject"] = subject

            encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

            draft_body = {
                "message": {
                    "raw": encoded_message,
                    "threadId": thread_id,
                }
            }

            draft = (
                self.service.users()
                .drafts()
                .create(userId="me", body=draft_body)
                .execute()
            )

            draft_id = draft.get("id")
            logger.info(f"Created draft {draft_id} for thread {thread_id}")
            return draft_id
        except Exception as e:
            logger.error(f"Error creating draft in Gmail: {e}")
            return None

    def send_reply(self, thread_id: str, to: str, subject: str, body: str) -> Optional[str]:
        """Directly sends a reply email in the user's Gmail box, tied to the original thread."""
        try:
            message = EmailMessage()
            message.set_content(body)
            message["To"] = to
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
            message["Subject"] = subject

            encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

            send_body = {
                "raw": encoded_message,
                "threadId": thread_id,
            }

            sent = (
                self.service.users()
                .messages()
                .send(userId="me", body=send_body)
                .execute()
            )

            sent_id = sent.get("id")
            logger.info(f"Directly sent email {sent_id} to {to} for thread {thread_id}")
            return sent_id
        except Exception as e:
            logger.error(f"Error sending email in Gmail: {e}")
            return None

    def mark_as_read(self, message_id: str) -> bool:
        """Removes the UNREAD label from a message."""
        try:
            self.service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
            return True
        except Exception as e:
            logger.error(f"Error marking message {message_id} as read: {e}")
            return False

    def watch_mailbox(self, topic_name: str, label_ids: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """Registers a Cloud Pub/Sub topic to receive real-time push notifications from Gmail."""
        try:
            body: Dict[str, Any] = {
                "topicName": topic_name,
                "labelIds": label_ids or ["INBOX"],
            }
            res = self.service.users().watch(userId="me", body=body).execute()
            logger.info(f"Successfully registered Gmail watch on topic {topic_name}: {res}")
            return res
        except Exception as e:
            logger.error(f"Failed to register Gmail watch on topic {topic_name}: {e}")
            return None

    def stop_watch(self) -> bool:
        """Stops receiving push notifications for the user's mailbox."""
        try:
            self.service.users().stop(userId="me").execute()
            logger.info("Successfully stopped Gmail mailbox watch.")
            return True
        except Exception as e:
            logger.error(f"Failed to stop Gmail watch: {e}")
            return False


