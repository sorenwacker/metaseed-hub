#!/usr/bin/env python3
"""Configure Keycloak realm from JSON config file."""

import json
from pathlib import Path

import httpx

KEYCLOAK_URL = "http://localhost:7080"
ADMIN_USER = "admin"
ADMIN_PASS = "admin"
REALM_CONFIG = Path(__file__).parent.parent / "keycloak" / "metaseed-realm.json"


def get_admin_token() -> str:
    """Get admin access token."""
    response = httpx.post(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        data={
            "username": ADMIN_USER,
            "password": ADMIN_PASS,
            "grant_type": "password",
            "client_id": "admin-cli",
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


def import_realm(token: str, realm_config: dict) -> None:
    """Import realm from config."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    realm_name = realm_config["realm"]

    # Check if realm exists
    response = httpx.get(f"{KEYCLOAK_URL}/admin/realms/{realm_name}", headers=headers)
    if response.status_code == 200:
        print(f"Realm '{realm_name}' already exists, deleting for fresh import...")
        response = httpx.delete(f"{KEYCLOAK_URL}/admin/realms/{realm_name}", headers=headers)
        response.raise_for_status()
        print(f"Deleted realm '{realm_name}'")

    # Import realm from JSON
    response = httpx.post(
        f"{KEYCLOAK_URL}/admin/realms",
        headers=headers,
        json=realm_config,
    )
    response.raise_for_status()
    print(f"Imported realm '{realm_name}' from {REALM_CONFIG.name}")


def main() -> None:
    """Configure Keycloak from JSON config."""
    print("Configuring Keycloak...")

    if not REALM_CONFIG.exists():
        print(f"Error: Config file not found: {REALM_CONFIG}")
        return

    realm_config = json.loads(REALM_CONFIG.read_text())
    realm_name = realm_config["realm"]

    token = get_admin_token()
    print("Got admin token")

    import_realm(token, realm_config)

    # Extract client secret from config
    client_secret = None
    for client in realm_config.get("clients", []):
        if client.get("clientId") == "metaseed-hub":
            client_secret = client.get("secret")
            break

    print("\n" + "=" * 50)
    print("Keycloak configured successfully!")
    print("=" * 50)
    print(f"\nRealm: {realm_name}")
    print("Client ID: metaseed-hub")
    if client_secret:
        print(f"Client Secret: {client_secret}")
    print(f"\nKeycloak URL: {KEYCLOAK_URL}")
    print(f"Admin Console: {KEYCLOAK_URL}/admin/master/console/")

    # Print users from config
    users = realm_config.get("users", [])
    if users:
        print("\nConfigured users:")
        for user in users:
            password = None
            for cred in user.get("credentials", []):
                if cred.get("type") == "password":
                    password = cred.get("value")
                    break
            print(f"  - {user['username']} / {password}")

    if client_secret:
        print(f"\nAdd to .env:\nOIDC_CLIENT_SECRET={client_secret}")


if __name__ == "__main__":
    main()
