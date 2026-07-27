"""The dataset adapter-export route surfaces metaseed's plugin exporters.

Gating is the security-relevant part: a format must be offered for the dataset's
profile, or the route must refuse -- otherwise a hand-typed URL runs an exporter
against a profile it was never meant for.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from metaseed_hub.ui.routes.dataset.editor import (
    _adapter_export_options,
    dataset_export_adapter,
)


def test_export_options_come_from_the_metaseed_registry() -> None:
    # One PRIDE export: submission.px and its SDRF are parts of one submission,
    # so they arrive in a single archive rather than as rival buttons.
    assert {o["key"] for o in _adapter_export_options("pride")} == {"pride"}
    # A profile with no adapter exporters offers none.
    assert _adapter_export_options("darwin-core") == []


@pytest.mark.asyncio
async def test_unknown_format_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        await dataset_export_adapter("d1", "nonsense", Mock(), Mock())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_format_not_offered_for_the_profile_is_rejected() -> None:
    dataset = Mock(profile="darwin-core", version="1.0", name="d")
    with patch(
        "metaseed_hub.ui.routes.dataset.editor.get_dataset_for_user",
        new_callable=AsyncMock,
        return_value=dataset,
    ):
        with pytest.raises(HTTPException) as exc:
            # metabolights export exists, but not for a darwin-core dataset.
            await dataset_export_adapter("d1", "metabolights", Mock(), Mock())
    assert exc.value.status_code == 404
