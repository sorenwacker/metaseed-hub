"""Dependency updates are Renovate on the best-practices preset (see the
metaseed repository's test of the same name for why). The hub additionally
keeps image majors off and merges minor/patch updates itself."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_renovate_extends_best_practices_and_replaces_dependabot():
    config = json.loads((ROOT / "renovate.json").read_text())
    assert "config:best-practices" in config["extends"]
    rules = config["packageRules"]
    assert any(r.get("automerge") for r in rules)
    assert any(
        r.get("enabled") is False and "major" in r.get("matchUpdateTypes", []) for r in rules
    )
    assert not (ROOT / ".github" / "dependabot.yml").exists()
    assert not (ROOT / ".github" / "workflows" / "dependabot-auto-merge.yml").exists()
