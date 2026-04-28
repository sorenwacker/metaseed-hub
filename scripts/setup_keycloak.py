#!/usr/bin/env python3
"""Configure Keycloak realm and client for metaseed-hub."""

import httpx

KEYCLOAK_URL = "http://localhost:7080"
ADMIN_USER = "admin"
ADMIN_PASS = "admin"
REALM_NAME = "metaseed"
CLIENT_ID = "metaseed-hub"


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


def create_realm(token: str) -> None:
    """Create the metaseed realm."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Check if realm exists
    response = httpx.get(f"{KEYCLOAK_URL}/admin/realms/{REALM_NAME}", headers=headers)
    if response.status_code == 200:
        print(f"Realm '{REALM_NAME}' already exists")
        return

    # Create realm
    realm_config = {
        "realm": REALM_NAME,
        "enabled": True,
        "registrationAllowed": True,
        "registrationEmailAsUsername": True,
        "resetPasswordAllowed": True,
        "loginWithEmailAllowed": True,
        "duplicateEmailsAllowed": False,
        "sslRequired": "none",  # For dev only
    }

    response = httpx.post(
        f"{KEYCLOAK_URL}/admin/realms",
        headers=headers,
        json=realm_config,
    )
    response.raise_for_status()
    print(f"Created realm '{REALM_NAME}'")


def create_client(token: str) -> str:
    """Create the metaseed-hub client and return client secret."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Check if client exists
    response = httpx.get(
        f"{KEYCLOAK_URL}/admin/realms/{REALM_NAME}/clients",
        headers=headers,
        params={"clientId": CLIENT_ID},
    )
    response.raise_for_status()
    clients = response.json()

    if clients:
        client_uuid = clients[0]["id"]
        print(f"Client '{CLIENT_ID}' already exists")
    else:
        # Create client
        client_config = {
            "clientId": CLIENT_ID,
            "enabled": True,
            "publicClient": False,
            "clientAuthenticatorType": "client-secret",
            "redirectUris": [
                "http://localhost:7001/*",
                "http://localhost:7300/*",
            ],
            "webOrigins": [
                "http://localhost:7001",
                "http://localhost:7300",
            ],
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": True,
            "serviceAccountsEnabled": False,
            "protocol": "openid-connect",
        }

        response = httpx.post(
            f"{KEYCLOAK_URL}/admin/realms/{REALM_NAME}/clients",
            headers=headers,
            json=client_config,
        )
        response.raise_for_status()
        print(f"Created client '{CLIENT_ID}'")

        # Get the client UUID
        response = httpx.get(
            f"{KEYCLOAK_URL}/admin/realms/{REALM_NAME}/clients",
            headers=headers,
            params={"clientId": CLIENT_ID},
        )
        response.raise_for_status()
        client_uuid = response.json()[0]["id"]

    # Get client secret
    response = httpx.get(
        f"{KEYCLOAK_URL}/admin/realms/{REALM_NAME}/clients/{client_uuid}/client-secret",
        headers=headers,
    )
    response.raise_for_status()
    secret = response.json().get("value", "")

    if not secret:
        # Generate new secret
        response = httpx.post(
            f"{KEYCLOAK_URL}/admin/realms/{REALM_NAME}/clients/{client_uuid}/client-secret",
            headers=headers,
        )
        response.raise_for_status()
        secret = response.json().get("value", "")

    return secret


def main() -> None:
    """Configure Keycloak."""
    print("Configuring Keycloak...")

    token = get_admin_token()
    print("Got admin token")

    create_realm(token)
    secret = create_client(token)

    print("\n" + "=" * 50)
    print("Keycloak configured successfully!")
    print("=" * 50)
    print(f"\nRealm: {REALM_NAME}")
    print(f"Client ID: {CLIENT_ID}")
    print(f"Client Secret: {secret}")
    print(f"\nKeycloak URL: {KEYCLOAK_URL}")
    print(f"Login page: {KEYCLOAK_URL}/realms/{REALM_NAME}/account")
    print(
        f"Register: {KEYCLOAK_URL}/realms/{REALM_NAME}/protocol/openid-connect/registrations?client_id={CLIENT_ID}&response_type=code&redirect_uri=http://localhost:7001/"
    )
    print("\nAdd to .env:")
    print(f"KEYCLOAK_CLIENT_SECRET={secret}")


if __name__ == "__main__":
    main()
