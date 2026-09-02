"""Local start refuses a database whose schema has drifted from the models.

A local database that reported the head revision but lacked a column the
models declare produced an internal server error on the dataset page, with
the cause visible only in a traceback. `alembic check` names the gap at
startup instead; this pins that `make dev` runs it after migrating.
"""

from __future__ import annotations

from pathlib import Path

_MAKEFILE = Path(__file__).resolve().parent.parent / "Makefile"


def _target_body(name: str) -> str:
    lines = _MAKEFILE.read_text().splitlines()
    body: list[str] = []
    inside = False
    for line in lines:
        if line.startswith(f"{name}:"):
            inside = True
            continue
        if inside:
            if line.startswith("\t"):
                body.append(line)
            elif line.strip():
                break
    return "\n".join(body)


def test_migrating_locally_also_checks_schema_drift() -> None:
    body = _target_body("db-migrate")
    assert "alembic upgrade head" in body
    assert "alembic check" in body, "make dev must fail on schema drift, not 500 on a page"
    assert body.index("alembic upgrade head") < body.index("alembic check"), (
        "drift is checked after migrating, or every pending migration reads as drift"
    )
