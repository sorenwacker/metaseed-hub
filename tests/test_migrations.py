def test_no_migration_drops_or_creates_an_anonymous_constraint() -> None:
    """`drop_constraint(None, ...)` cannot execute; anonymous FKs force it.

    Two downgrades were unrunnable this way — found only when someone needed
    to roll back. Every constraint a migration touches must be named.
    """
    import re
    from pathlib import Path

    versions = Path(__file__).parent.parent / "alembic" / "versions"
    offenders = []
    for path in sorted(versions.glob("*.py")):
        text = path.read_text()
        if re.search(r"drop_constraint\(\s*None", text):
            offenders.append(f"{path.name}: drop_constraint(None)")
        if re.search(r"create_foreign_key\(\s*None", text):
            offenders.append(f"{path.name}: create_foreign_key(None)")
    assert not offenders, offenders
