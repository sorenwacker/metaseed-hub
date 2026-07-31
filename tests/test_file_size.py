"""The project's file-size convention, enforced.

No source or test file may exceed 1000 lines. The rule existed only as a
convention, so it drifted unnoticed until two test modules passed 1100 lines;
a module that large stops being navigable and hides what it covers. This test
is the gate: it names every file over the limit with its line count, so the fix
is obvious from the failure alone.
"""

from __future__ import annotations

from pathlib import Path

MAX_LINES = 1000

_ROOT = Path(__file__).resolve().parent.parent
_CHECKED_DIRECTORIES = ("src", "tests")


def _line_count(path: Path) -> int:
    """Number of lines in a file, counted as ``wc -l`` does.

    Args:
        path: File to count.

    Returns:
        The number of newline-terminated lines.
    """
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def _python_files() -> list[Path]:
    """Every ``.py`` file under the checked directories.

    Returns:
        Paths to check, excluding caches and virtual environments.
    """
    files: list[Path] = []
    for directory in _CHECKED_DIRECTORIES:
        for path in sorted((_ROOT / directory).rglob("*.py")):
            if any(part in {"__pycache__", ".venv"} for part in path.parts):
                continue
            files.append(path)
    return files


def test_no_python_file_exceeds_the_line_limit() -> None:
    """A file over the limit must fail the suite, named and measured."""
    oversized = [
        (path.relative_to(_ROOT), count)
        for path in _python_files()
        if (count := _line_count(path)) > MAX_LINES
    ]

    assert not oversized, "files over {} lines:\n{}".format(
        MAX_LINES,
        "\n".join(f"  {path}: {count} lines" for path, count in sorted(oversized)),
    )
