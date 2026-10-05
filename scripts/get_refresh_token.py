#!/usr/bin/env python3
"""
OAuth2 Refresh Token Generator for Atlas Synthetic Identity Capsule.
Requests permissions for both Gmail and Google Chat APIs.

Usage:
    .venv/bin/python scripts/get_refresh_token.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure atlas root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.auth.google_auth import SCOPES

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Error: google-auth-oauthlib is required.")
    print("Please install it: .venv/bin/pip install google-auth-oauthlib")
    sys.exit(1)


def main():
    load_dotenv()

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("Error: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be defined in your .env file.")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8088/"],
        }
    }

    print("\n" + "=" * 60)
    print(" ATLAS SYNTHETIC IDENTITY: OAUTH2 REFRESH TOKEN GENERATOR")
    print("=" * 60)
    print(f"Client ID: {client_id[:20]}...")
    print("\nScopes to be requested:")
    for s in SCOPES:
        print(f"  - {s}")

    print("\nA browser window will open shortly to authenticate.")
    print("Please sign in with the Google account for the synthetic identity.")
    print("-" * 60)

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    if not creds.refresh_token:
        print("\n[WARNING]: No refresh token was returned.")
        print("Tip: If you've previously authorized this app, Google may omit the refresh token.")
        print("Try revoking permissions at https://myaccount.google.com/permissions and re-run.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print(" SUCCESS! NEW GOOGLE_REFRESH_TOKEN GENERATED")
    print("=" * 60)
    print("\nCopy and update this in your .env file:\n")
    print(f'GOOGLE_REFRESH_TOKEN="{creds.refresh_token}"')
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
