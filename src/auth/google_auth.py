import logging
from typing import Optional
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    # Gmail scopes
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    # Google Chat scopes
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/chat.messages.create",
    "https://www.googleapis.com/auth/chat.messages.reactions.create",
    "https://www.googleapis.com/auth/chat.messages.reactions",
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.spaces",
    # Google Calendar scopes
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
]

# Backward compatibility alias
GMAIL_SCOPES = SCOPES



class GoogleAuthManager:
    """Manages Google OAuth2 credentials lifecycle using a dedicated refresh token."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        token_uri: str = "https://oauth2.googleapis.com/token",
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.token_uri = token_uri
        self._credentials: Optional[Credentials] = None

    def get_credentials(self) -> Credentials:
        """Retrieves or refreshes the OAuth2 credentials."""
        if not self._credentials:
            self._credentials = Credentials(
                token=None,
                refresh_token=self.refresh_token,
                token_uri=self.token_uri,
                client_id=self.client_id,
                client_secret=self.client_secret,
            )

        if not self._credentials.valid:
            logger.info("Refreshing Google OAuth2 credentials...")
            request = Request()
            self._credentials.refresh(request)
            logger.info("Google OAuth2 credentials refreshed successfully.")

        return self._credentials

    def get_access_token(self) -> str:
        """Helper to get a valid access token string directly."""
        creds = self.get_credentials()
        return creds.token
