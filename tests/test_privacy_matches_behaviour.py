"""What the privacy page promises is what the code does.

The page states how long recorded errors are kept. That number lives in
`metaseed_hub.errors.RETENTION`, and nothing stopped the two drifting: changing
the retention would have left the page making a promise the code no longer
kept, which is the kind of statement people rely on.
"""

from __future__ import annotations

from pathlib import Path

from metaseed_hub.backup import RetentionPolicy
from metaseed_hub.errors import RETENTION

PRIVACY = Path("src/metaseed_hub/ui/templates/privacy.html")


def test_the_privacy_page_states_the_retention_the_code_enforces() -> None:
    stated = f"{RETENTION.days} days"
    page = PRIVACY.read_text()
    assert stated in page, (
        f"the privacy page does not say errors are kept for {stated}, which is "
        "what metaseed_hub.errors.RETENTION enforces"
    )


def test_the_page_says_errors_are_removed_automatically() -> None:
    """Removal is automatic; a page implying someone deletes them by hand would
    misdescribe how it works."""
    assert "removed automatically" in PRIVACY.read_text()


def test_the_page_states_how_long_backups_hold_a_deleted_account() -> None:
    """Deleting an account clears the live system at once, but the dumps keep a
    copy until the oldest tier ages out. Someone exercising a right to erasure
    is owed that number, and it is the backup policy's, not a guess."""
    policy = RetentionPolicy()
    page = PRIVACY.read_text()

    assert f"{policy.monthly} months" in page, (
        f"the page does not say data can persist in backups for "
        f"{policy.monthly} months, which is what RetentionPolicy keeps"
    )
    for tier in (policy.last, policy.daily, policy.weekly):
        assert str(tier) in page, f"the page does not state the {tier}-dump tier"
