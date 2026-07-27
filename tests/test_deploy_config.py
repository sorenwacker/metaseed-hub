"""Gates on the deployment templates so security config cannot silently regress.

The security response headers live in the nginx template (applied by ansible),
not in the app, so a unit test cannot observe them at runtime. Assert instead
that the template still declares each one -- a template edit that drops a header
fails here rather than in production.
"""

from pathlib import Path

import pytest

_NGINX_TEMPLATE = (
    Path(__file__).resolve().parent.parent
    / "ansible"
    / "roles"
    / "metaseed-hub"
    / "templates"
    / "nginx.conf.j2"
)
_SERVICE_TEMPLATE = (
    Path(__file__).resolve().parent.parent
    / "ansible"
    / "roles"
    / "metaseed-hub"
    / "templates"
    / "metaseed-hub.service.j2"
)

_REQUIRED_HEADERS = [
    "Content-Security-Policy",
    "Strict-Transport-Security",
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
]


@pytest.mark.parametrize("header", _REQUIRED_HEADERS)
def test_nginx_declares_security_header(header: str) -> None:
    conf = _NGINX_TEMPLATE.read_text()
    assert f"add_header {header} " in conf, f"nginx template is missing {header}"


def test_nginx_hsts_has_long_max_age() -> None:
    conf = _NGINX_TEMPLATE.read_text()
    assert "max-age=31536000" in conf


def test_uvicorn_runs_with_proxy_headers() -> None:
    # Required so the app sees the real scheme behind the TLS-terminating proxy,
    # which the Secure-cookie and Origin-check logic depends on.
    unit = _SERVICE_TEMPLATE.read_text()
    assert "--proxy-headers" in unit
    assert "--forwarded-allow-ips" in unit


def test_csp_allows_no_external_script_origin() -> None:
    # Third-party scripts are self-hosted; the CSP must not re-open a CDN origin.
    conf = _NGINX_TEMPLATE.read_text()
    csp_line = next(line for line in conf.splitlines() if "Content-Security-Policy" in line)
    assert "unpkg" not in csp_line
    assert "http://" not in csp_line and "https://" not in csp_line


_HUB_TEMPLATES = (
    Path(__file__).resolve().parent.parent / "src" / "metaseed_hub" / "ui" / "templates"
)


_ROLE = Path(__file__).resolve().parent.parent / "ansible" / "roles" / "metaseed-hub"


def test_backup_timer_is_enabled_by_the_playbook() -> None:
    """Installing the units without enabling the timer yields no backups at all,
    which is invisible until a restore is needed."""
    tasks = (_ROLE / "tasks" / "main.yml").read_text()
    assert "metaseed-backup.service.j2" in tasks
    assert "metaseed-backup.timer.j2" in tasks
    assert "- metaseed-backup.timer" in tasks, "the backup timer is never enabled"


def test_backup_timer_catches_up_after_downtime() -> None:
    timer = (_ROLE / "templates" / "metaseed-backup.timer.j2").read_text()
    assert "Persistent=true" in timer, "a host down at the scheduled time skips the backup"


def test_backup_directory_is_not_world_readable() -> None:
    """Dumps contain every dataset in the hub."""
    tasks = (_ROLE / "tasks" / "main.yml").read_text()
    backup_task = tasks.split("Create backup directory", 1)[1].split("- name:", 1)[0]
    assert 'mode: "0700"' in backup_task


def test_no_hub_template_loads_a_cdn_script() -> None:
    """Hub templates must self-host JS, not reintroduce a supply-chain CDN origin."""
    offenders = [
        p.relative_to(_HUB_TEMPLATES).as_posix()
        for p in _HUB_TEMPLATES.rglob("*.html")
        if "unpkg.com" in p.read_text() or "cdn.jsdelivr" in p.read_text()
    ]
    assert not offenders, f"templates load an external CDN: {offenders}"
