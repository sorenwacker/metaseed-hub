#!/usr/bin/env python3
"""Sync Keycloak users from realm config file.

This script ensures users defined in the realm JSON exist in Keycloak.
It uses direct access grants with a temporary admin user for API access.
"""

import json
import sys
from pathlib import Path

import httpx

KEYCLOAK_URL = "http://localhost:7080"
REALM_NAME = "metaseed"
CLIENT_ID = "metaseed-hub"
CLIENT_SECRET = "metaseed-hub-dev-secret"
REALM_CONFIG = Path(__file__).parent.parent / "docker" / "keycloak-realm.json"


def get_service_token() -> str | None:
    """Get access token using client credentials.

    Note: This requires serviceAccountsEnabled=true on the client,
    which we don't have. Fall back to using an existing admin user.
    """
    # Try with existing admin user from realm config
    response = httpx.post(
        f"{KEYCLOAK_URL}/realms/{REALM_NAME}/protocol/openid-connect/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "username": "admin",
            "password": "admin123",
            "grant_type": "password",
        },
        timeout=10,
    )
    if response.status_code == 200:
        return response.json()["access_token"]
    return None


def check_realm_exists() -> bool:
    """Check if the realm exists."""
    response = httpx.get(
        f"{KEYCLOAK_URL}/realms/{REALM_NAME}/.well-known/openid-configuration",
        timeout=10,
    )
    return response.status_code == 200


def get_existing_users(token: str) -> dict[str, dict]:
    """Get existing users from Keycloak."""
    headers = {"Authorization": f"Bearer {token}"}
    response = httpx.get(
        f"{KEYCLOAK_URL}/admin/realms/{REALM_NAME}/users",
        headers=headers,
        timeout=10,
    )
    if response.status_code != 200:
        return {}
    return {user["username"]: user for user in response.json()}


def create_user(token: str, user_config: dict) -> bool:
    """Create a user in Keycloak."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    user_data = {
        "username": user_config["username"],
        "email": user_config.get("email"),
        "emailVerified": user_config.get("emailVerified", False),
        "enabled": user_config.get("enabled", True),
        "firstName": user_config.get("firstName"),
        "lastName": user_config.get("lastName"),
    }

    response = httpx.post(
        f"{KEYCLOAK_URL}/admin/realms/{REALM_NAME}/users",
        headers=headers,
        json=user_data,
        timeout=10,
    )

    if response.status_code not in (201, 409):  # 409 = already exists
        print(f"  Failed to create user {user_config['username']}: {response.status_code}")
        return False

    # Set password if provided
    for cred in user_config.get("credentials", []):
        if cred.get("type") == "password":
            # Get user ID
            users_response = httpx.get(
                f"{KEYCLOAK_URL}/admin/realms/{REALM_NAME}/users",
                headers=headers,
                params={"username": user_config["username"], "exact": "true"},
                timeout=10,
            )
            if users_response.status_code == 200 and users_response.json():
                user_id = users_response.json()[0]["id"]
                pwd_response = httpx.put(
                    f"{KEYCLOAK_URL}/admin/realms/{REALM_NAME}/users/{user_id}/reset-password",
                    headers=headers,
                    json={
                        "type": "password",
                        "value": cred["value"],
                        "temporary": cred.get("temporary", False),
                    },
                    timeout=10,
                )
                if pwd_response.status_code != 204:
                    print(f"  Failed to set password for {user_config['username']}")
                    return False

    return True


def main() -> int:
    """Sync users from realm config to Keycloak."""
    print("Syncing Keycloak users...")

    if not REALM_CONFIG.exists():
        print(f"Config file not found: {REALM_CONFIG}")
        return 1

    if not check_realm_exists():
        print(f"Realm '{REALM_NAME}' not found. Waiting for Keycloak to import it...")
        return 0  # Not an error - realm will be imported by Keycloak on first start

    realm_config = json.loads(REALM_CONFIG.read_text())
    config_users = realm_config.get("users", [])

    if not config_users:
        print("No users defined in config")
        return 0

    token = get_service_token()
    if not token:
        print("Could not authenticate. Users may not exist yet (first run).")
        print("If users are missing, run: make reset && make dev")
        return 0  # Don't fail - this is expected on first run

    existing_users = get_existing_users(token)

    created = 0
    skipped = 0

    for user_config in config_users:
        username = user_config["username"]
        if username in existing_users:
            print(f"  {username}: exists")
            skipped += 1
        else:
            if create_user(token, user_config):
                print(f"  {username}: created")
                created += 1
            else:
                print(f"  {username}: failed")

    print(f"\nUsers synced: {created} created, {skipped} existing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
