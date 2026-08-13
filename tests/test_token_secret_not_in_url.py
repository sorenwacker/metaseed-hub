"""A freshly minted token secret must never travel in a URL.

The create-token route redirected to `/hub/auth/profile?token=<secret>`, which
puts a live credential into the server access log, the browser history, and
anything else that records URLs. The secret now rides in a short-lived
encrypted cookie the profile page reads once and expires — cookies appear in
none of those places.
"""

from __future__ import annotations

from metaseed_hub.crypto import decrypt_secret, encrypt_secret
from metaseed_hub.ui.routes.auth import NEW_TOKEN_COOKIE, auth_create_token, auth_profile


class TestTheRoutesByContract:
    def test_the_redirect_carries_no_secret(self) -> None:
        """The URL is the leak; the route must not build one containing the
        token. Checked structurally so no auth stack is needed."""
        import inspect

        source = inspect.getsource(auth_create_token)

        assert "?token=" not in source
        assert "set_cookie" in source
        assert "NEW_TOKEN_COOKIE" in source

    def test_the_profile_page_reads_the_cookie_not_the_query(self) -> None:
        import inspect

        source = inspect.getsource(auth_profile)

        assert 'query_params.get("token")' not in source
        assert "NEW_TOKEN_COOKIE" in source
        assert "delete_cookie" in source

    def test_the_cookie_value_is_not_the_plain_secret(self) -> None:
        """Encrypted with the app's Fernet key and a TTL, so a cookie that
        somehow survives is an expired ciphertext, not a credential."""
        stored = encrypt_secret("mst_secret")

        assert stored != "mst_secret"
        assert decrypt_secret(stored) == "mst_secret"

    def test_the_cookie_has_a_name_worth_grepping_for(self) -> None:
        assert NEW_TOKEN_COOKIE == "hub_new_token"
