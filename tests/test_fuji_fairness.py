"""Opt-in FAIRness regression check through F-UJI (metaseed#31).

F-UJI is a third-party assessor that harvests a dataset URL and scores the
FsF metrics. It needs a running F-UJI service and a dataset URL it can
REACH — which, under the hub's authentication, means a URL the deployment
has made visible. Both arrive by environment variable, so this stays out of
the core suite and runs only where the pieces exist:

    FUJI_URL=http://localhost:1071 FUJI_TARGET=https://hub.example/datasets/x \\
        uv run pytest tests/test_fuji_fairness.py

Run F-UJI locally with its published image:

    docker run -d -p 1071:1071 ghcr.io/pangaea-data-publisher/fuji

The baseline asserts the score does not REGRESS, not that it is good: the
harvestable exposure (embedded JSON-LD, content negotiation, signposting)
is what the score reads, and this check exists to notice when a change
quietly removes one of those signals.
"""

from __future__ import annotations

import os

import httpx
import pytest

FUJI_URL = os.environ.get("FUJI_URL")
FUJI_TARGET = os.environ.get("FUJI_TARGET")

#: FsF score (percent) below which the check fails. Set from the first real
#: measurement of a deployment; a placeholder floor until then.
BASELINE_PERCENT = float(os.environ.get("FUJI_BASELINE", "20"))

pytestmark = pytest.mark.skipif(
    not (FUJI_URL and FUJI_TARGET),
    reason="opt-in: set FUJI_URL and FUJI_TARGET to run the F-UJI check",
)


def test_the_fair_score_does_not_regress() -> None:
    response = httpx.post(
        f"{FUJI_URL}/fuji/api/v1/evaluate",
        json={"object_identifier": FUJI_TARGET, "test_debug": False},
        auth=("marvel", "wonderwoman"),  # F-UJI's published default basic auth
        timeout=300,
    )
    response.raise_for_status()
    summary = response.json().get("summary", {})
    score = summary.get("score_percent", {}).get("FAIR")
    assert score is not None, f"no FAIR score in F-UJI response: {summary}"
    assert score >= BASELINE_PERCENT, (
        f"FAIR score {score}% fell below the {BASELINE_PERCENT}% baseline — "
        "a harvestability signal probably went missing"
    )
