#!/usr/bin/env python3
"""
Helper script to authorize and obtain a Google OAuth Refresh Token
with both Gmail and Google Chat scopes for the Capsule Bot account.
"""

import os
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("Error: GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET not found in .env")
    exit(1)

SCOPES = [
    # Gmail Scopes
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    # Google Chat Scopes for direct user message interaction
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.spaces",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/chat.messages.create",
]

client_config = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

print("\n=== Atlas Capsule: Google OAuth Refresh Token Generator ===")
print("Starting local authorization server...")
print(f"Scopes to request: {len(SCOPES)}")
print("A browser window will open. Please log in with your Bot Google Workspace account.\n")


flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

print("\n Authorization Successful!")
print("=======================================================")
print("New REFRESH_TOKEN:")
print(creds.refresh_token)
print("=======================================================")
print("\nUpdate GOOGLE_REFRESH_TOKEN in your .env with this value.")
