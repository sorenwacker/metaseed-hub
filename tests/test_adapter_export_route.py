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
    """What the sidebar offers is whatever the registry declares, so these track
    metaseed rather than being restated here."""
    # One PRIDE export: submission.px and its SDRF are parts of one submission,
    # so they arrive together in a single archive.
    everything = {"dcat", "seek", "ena", "pride", "brapi", "metabolights"}
    assert {o["key"] for o in _adapter_export_options("pride", features=everything)} == {
        "pride",
        "dcat",
    }
    # A profile with no repository adapter still gets the DCAT catalogue record,
    # which describes a dataset under any profile — but no other profile's exporter.
    assert {o["key"] for o in _adapter_export_options("darwin-core", features=everything)} == {
        "dcat"
    }


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


class TestExportsAreGatedByFeature:
    """Each adapter export requires the feature named by its key.

    The keys and the feature names are the same six strings, so membership of
    the plugin's group is what turns its export on -- for the buttons and for a
    hand-typed URL alike.
    """

    def test_options_offer_only_the_features_the_user_holds(self) -> None:
        offered = {o["key"] for o in _adapter_export_options("pride", features={"dcat"})}
        assert offered == {"dcat"}

    def test_no_features_means_no_export_options(self) -> None:
        assert _adapter_export_options("pride", features=set()) == []

    @pytest.mark.asyncio
    async def test_the_route_refuses_a_format_the_user_lacks(self) -> None:
        # The buttons being hidden is cosmetic; the route is the gate. 404, not
        # 403: an ungranted feature does not advertise its existence.
        dataset = Mock(profile="pride", version="1.0", name="d")
        with (
            patch(
                "metaseed_hub.ui.routes.dataset.editor.get_dataset_for_user",
                new_callable=AsyncMock,
                return_value=dataset,
            ),
            patch(
                "metaseed_hub.ui.routes.dataset.editor.user_feature_set",
                AsyncMock(return_value={"dcat"}),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await dataset_export_adapter("d1", "pride", Mock(), Mock())
        assert exc.value.status_code == 404
