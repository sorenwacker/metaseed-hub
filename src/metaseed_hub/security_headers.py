"""The Content-Security-Policy, sent by the application itself.

nginx sets this header in production and nothing set it anywhere else, so
development, the test client and the Selenium suite all ran against pages with
no policy at all. Three violations reached the deployed site and stayed there,
each invisible to a green suite: `hx-headers` on `<body>`, an `hx-vals` button,
and eight `hx-on:` handlers -- every one compiled by htmx with `new Function`,
which the policy forbids. The last was the worst, because the request succeeded
and only the handler after it failed, so "Add Entity" saved the entity and
looked inert.

Sending it here makes every environment match the deployed one, so a violation
fails a test rather than reaching a person. nginx keeps its own copy: a policy
that survives the application being misconfigured is worth having twice, and
identical headers are enforced identically.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Kept identical to `ansible/roles/metaseed-hub/templates/nginx.conf.j2`.
#: `tests/test_the_production_csp_is_honoured.py` fails if the two drift.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'self'"
)


class ContentSecurityPolicyMiddleware:
    """Add the policy to every response that does not already carry one."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_policy(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                names = {name.lower() for name, _ in headers}
                if b"content-security-policy" not in names:
                    headers.append(
                        (
                            b"content-security-policy",
                            CONTENT_SECURITY_POLICY.encode("latin-1"),
                        )
                    )
            await send(message)

        await self.app(scope, receive, send_with_policy)
