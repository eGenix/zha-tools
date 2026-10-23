"""Tests for the frame counter logic and its ZHA adapter helper.

The ZHA adapter is mocked for the logic tests: ``async_get_zha_device`` returns
a fake device and ``async_get_neighbor_frame_counter`` is replaced by a
coroutine. The adapter helper itself is tested against a fake bellows EZSP
object, since bellows is not installed in the test environment.
"""

from __future__ import annotations

import enum
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.zha_tools import zha_adapter
from custom_components.zha_tools.const import STATUS_COMPLETE, STATUS_NOT_NEIGHBOR
from custom_components.zha_tools.frame_counter import async_read_frame_counter

IEEE = "00:11:22:33:44:55:66:77"


class FakeEmberStatus(enum.IntEnum):
    """Stand-in for bellows' ``EmberStatus`` (EZSP < 14)."""

    SUCCESS = 0x00
    NOT_FOUND = 0x03
    INVALID_CALL = 0x70


class FakeSlStatus(enum.IntEnum):
    """Stand-in for bellows' ``sl_Status`` (EZSP >= 14)."""

    OK = 0x0000
    NOT_FOUND = 0x002D


async def _run(hass, device, read):
    """Run ``async_read_frame_counter`` with the adapter functions patched."""
    with (
        patch(
            "custom_components.zha_tools.frame_counter.async_get_zha_device",
            return_value=device,
        ),
        patch(
            "custom_components.zha_tools.frame_counter."
            "async_get_neighbor_frame_counter",
            new=read,
        ),
    ):
        return await async_read_frame_counter(hass, "dev1")


async def test_complete_when_device_is_a_neighbor(hass):
    device = SimpleNamespace(ieee=IEEE, last_seen=1_700_000_000.0)
    read = AsyncMock(return_value=4711)

    result = await _run(hass, device, read)

    assert result == {
        "device_id": "dev1",
        "ieee": IEEE,
        "status": STATUS_COMPLETE,
        "frame_counter": 4711,
        "last_seen": "2023-11-14T22:13:20+00:00",
    }
    read.assert_awaited_once_with(device)


async def test_not_neighbor_when_no_counter_is_stored(hass):
    device = SimpleNamespace(ieee=IEEE, last_seen=None)
    read = AsyncMock(return_value=None)

    result = await _run(hass, device, read)

    assert result == {
        "device_id": "dev1",
        "ieee": IEEE,
        "status": STATUS_NOT_NEIGHBOR,
        "frame_counter": None,
        "last_seen": None,
    }


async def test_errors_propagate(hass):
    """Unexpected adapter errors are not mapped to a status."""
    device = SimpleNamespace(ieee=IEEE, last_seen=None)
    read = AsyncMock(side_effect=HomeAssistantError("boom"))

    with pytest.raises(HomeAssistantError, match="boom"):
        await _run(hass, device, read)


# --- adapter -------------------------------------------------------------


def _device(ezsp):
    """Return a fake ZHA device whose controller carries the given EZSP."""
    app = SimpleNamespace()
    if ezsp is not None:
        app._ezsp = ezsp
    return SimpleNamespace(
        ieee=IEEE,
        name="Lamp",
        gateway=SimpleNamespace(application_controller=app),
    )


def _ezsp(version, response):
    return SimpleNamespace(
        ezsp_version=version,
        getNeighborFrameCounter=AsyncMock(return_value=response),
    )


@pytest.mark.parametrize(
    "status", [FakeEmberStatus.SUCCESS, FakeSlStatus.OK], ids=["ember", "sl"]
)
async def test_adapter_returns_counter(status):
    ezsp = _ezsp(8, [status, 1234])

    counter = await zha_adapter.async_get_neighbor_frame_counter(_device(ezsp))

    assert counter == 1234
    ezsp.getNeighborFrameCounter.assert_awaited_once_with(eui64=IEEE)


@pytest.mark.parametrize(
    "status",
    [FakeEmberStatus.NOT_FOUND, FakeSlStatus.NOT_FOUND],
    ids=["ember", "sl"],
)
async def test_adapter_returns_none_for_non_neighbors(status):
    ezsp = _ezsp(14, [status, 0])

    assert await zha_adapter.async_get_neighbor_frame_counter(_device(ezsp)) is None


async def test_adapter_raises_on_other_status():
    ezsp = _ezsp(8, [FakeEmberStatus.INVALID_CALL, 0])

    with pytest.raises(HomeAssistantError, match="INVALID_CALL"):
        await zha_adapter.async_get_neighbor_frame_counter(_device(ezsp))


async def test_adapter_rejects_non_ezsp_radios():
    with pytest.raises(HomeAssistantError, match="EZSP"):
        await zha_adapter.async_get_neighbor_frame_counter(_device(None))


async def test_adapter_rejects_old_ezsp_versions():
    ezsp = _ezsp(7, [FakeEmberStatus.SUCCESS, 1])

    with pytest.raises(HomeAssistantError, match="version 8"):
        await zha_adapter.async_get_neighbor_frame_counter(_device(ezsp))
    ezsp.getNeighborFrameCounter.assert_not_awaited()
