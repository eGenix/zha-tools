"""Tests for the ping logic, its status mapping and the ZHA adapter ping helpers.

The ZHA adapter is mocked for the retry loop tests: ``async_get_zha_device``
returns a fake device, and ``async_trigger_ping`` and
``async_trigger_address_refresh`` are replaced by coroutines. The adapter
helpers themselves are tested against fake zigpy modules, since zigpy is not
installed in the test environment.
"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.zha_tools import zha_adapter
from custom_components.zha_tools.const import (
    STATUS_COMPLETE,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
)
from custom_components.zha_tools.ping import async_ping_device
from custom_components.zha_tools.zha_adapter import DeviceUnreachableError


async def _run(hass, device, trigger, refresh=None, **kwargs):
    """Run ``async_ping_device`` with the adapter functions patched."""
    params = {"timeout": 5, "max_retries": 0, "retry_delay": 0}
    params.update(kwargs)
    if refresh is None:
        refresh = AsyncMock()
    with (
        patch(
            "custom_components.zha_tools.ping.async_get_zha_device",
            return_value=device,
        ),
        patch("custom_components.zha_tools.ping.async_trigger_ping", new=trigger),
        patch(
            "custom_components.zha_tools.ping.async_trigger_address_refresh",
            new=refresh,
        ),
    ):
        return await async_ping_device(hass, "dev1", **params)


async def test_complete_when_device_answers(hass):
    device = SimpleNamespace(available=True)
    refresh = AsyncMock()

    async def trigger(dev, timeout):
        return None

    result = await _run(hass, device, trigger, refresh)
    assert result == {"device_id": "dev1", "status": STATUS_COMPLETE, "attempts": 1}
    refresh.assert_not_awaited()


async def test_pings_device_zha_marks_unavailable(hass):
    """Unlike the other actions, ping must still contact an offline device."""
    device = SimpleNamespace(available=False)
    sent = {"n": 0}

    async def trigger(dev, timeout):
        sent["n"] += 1

    result = await _run(hass, device, trigger)
    assert result["status"] == STATUS_COMPLETE
    assert sent["n"] == 1


async def test_timeout_is_passed_to_the_ping(hass):
    device = SimpleNamespace(available=True)
    seen = []

    async def trigger(dev, timeout):
        seen.append(timeout)

    await _run(hass, device, trigger, timeout=12.5)
    assert seen == [12.5]


async def test_timeout_when_device_does_not_answer(hass):
    device = SimpleNamespace(available=True)

    async def trigger(dev, timeout):
        await asyncio.sleep(0.3)

    result = await _run(hass, device, trigger, timeout=0.05)
    assert result["status"] == STATUS_TIMEOUT


async def test_timeout_raised_by_zigpy_maps_to_timeout(hass):
    device = SimpleNamespace(available=True)

    async def trigger(dev, timeout):
        raise TimeoutError

    result = await _run(hass, device, trigger)
    assert result["status"] == STATUS_TIMEOUT


async def test_unavailable_when_request_cannot_be_delivered(hass):
    device = SimpleNamespace(available=True)

    async def trigger(dev, timeout):
        raise DeviceUnreachableError("no route")

    result = await _run(hass, device, trigger)
    assert result["status"] == STATUS_UNAVAILABLE


async def test_retries_until_complete_and_refreshes_address_once(hass):
    device = SimpleNamespace(available=False)
    events = []

    async def trigger(dev, timeout):
        events.append("ping")
        if events.count("ping") < 3:
            raise TimeoutError

    async def refresh(dev):
        events.append("refresh")

    result = await _run(hass, device, trigger, refresh, max_retries=5)
    assert result["status"] == STATUS_COMPLETE
    assert result["attempts"] == 3
    # The address is refreshed once, right before the first retry.
    assert events == ["ping", "refresh", "ping", "ping"]


async def test_retries_exhausted_returns_last_status(hass):
    device = SimpleNamespace(available=True)
    refresh = AsyncMock()

    async def trigger(dev, timeout):
        raise DeviceUnreachableError("no route")

    result = await _run(hass, device, trigger, refresh, max_retries=2)
    assert result["status"] == STATUS_UNAVAILABLE
    assert result["attempts"] == 3  # 1 initial + 2 retries
    refresh.assert_awaited_once_with(device)


async def test_no_address_refresh_without_retries(hass):
    device = SimpleNamespace(available=True)
    refresh = AsyncMock()

    async def trigger(dev, timeout):
        raise TimeoutError

    result = await _run(hass, device, trigger, refresh, max_retries=0)
    assert result["status"] == STATUS_TIMEOUT
    refresh.assert_not_awaited()


async def test_unexpected_error_is_not_silenced(hass):
    """A real error from the ping must propagate, not be swallowed."""
    device = SimpleNamespace(available=True)

    async def trigger(dev, timeout):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await _run(hass, device, trigger)


async def test_unknown_device_raises(hass):
    with patch(
        "custom_components.zha_tools.ping.async_get_zha_device",
        side_effect=ServiceValidationError("nope"),
    ):
        with pytest.raises(ServiceValidationError):
            await async_ping_device(
                hass, "bad", timeout=1, max_retries=0, retry_delay=0
            )


# --- ZHA adapter ping helpers -------------------------------------------------


class FakeDeliveryError(Exception):
    """Stand-in for ``zigpy.exceptions.DeliveryError``."""


def _fake_zigpy(broadcast=None):
    """Return the fake zigpy modules the adapter imports, keyed by name."""
    zigpy = ModuleType("zigpy")
    exceptions = ModuleType("zigpy.exceptions")
    exceptions.DeliveryError = FakeDeliveryError
    zdo = ModuleType("zigpy.zdo")
    zdo.broadcast = broadcast if broadcast is not None else AsyncMock()
    zdo_types = ModuleType("zigpy.zdo.types")
    zdo_types.ZDOCmd = SimpleNamespace(NWK_addr_req="NWK_addr_req")
    zdo_types.AddrRequestType = SimpleNamespace(Single="Single")
    zigpy.exceptions = exceptions
    zigpy.zdo = zdo
    zdo.types = zdo_types
    return {
        "zigpy": zigpy,
        "zigpy.exceptions": exceptions,
        "zigpy.zdo": zdo,
        "zigpy.zdo.types": zdo_types,
    }


def _zha_device(endpoints):
    """Return a fake ZHA device wrapping a zigpy device with ``endpoints``."""
    return SimpleNamespace(
        name="Test device",
        ieee="00:11:22:33:44:55:66:77",
        device=SimpleNamespace(endpoints=endpoints),
        gateway=SimpleNamespace(application_controller="app"),
    )


def _endpoint(**clusters):
    return SimpleNamespace(in_clusters=dict(clusters))


async def test_adapter_ping_reads_basic_cluster_uncached():
    basic = SimpleNamespace(read_attributes=AsyncMock(return_value=({0: 3}, {})))
    zdo = SimpleNamespace()  # endpoint 0 is the ZDO, which has no ZCL clusters
    device = _zha_device(
        {0: zdo, 1: _endpoint(), 2: SimpleNamespace(in_clusters={0: basic})}
    )

    with patch.dict(sys.modules, _fake_zigpy()):
        await zha_adapter.async_trigger_ping(device, 15.0)

    basic.read_attributes.assert_awaited_once_with(
        [0x0000], allow_cache=False, timeout=15.0
    )


async def test_adapter_ping_translates_delivery_error():
    basic = SimpleNamespace(
        read_attributes=AsyncMock(side_effect=FakeDeliveryError("no route"))
    )
    device = _zha_device({1: SimpleNamespace(in_clusters={0: basic})})

    with patch.dict(sys.modules, _fake_zigpy()):
        with pytest.raises(DeviceUnreachableError):
            await zha_adapter.async_trigger_ping(device, 5.0)


async def test_adapter_ping_lets_timeout_through():
    basic = SimpleNamespace(read_attributes=AsyncMock(side_effect=TimeoutError))
    device = _zha_device({1: SimpleNamespace(in_clusters={0: basic})})

    with patch.dict(sys.modules, _fake_zigpy()):
        with pytest.raises(TimeoutError):
            await zha_adapter.async_trigger_ping(device, 5.0)


async def test_adapter_ping_without_basic_cluster_raises():
    device = _zha_device({1: _endpoint()})

    with patch.dict(sys.modules, _fake_zigpy()):
        with pytest.raises(HomeAssistantError):
            await zha_adapter.async_trigger_ping(device, 5.0)


async def test_adapter_address_refresh_broadcasts_nwk_addr_req():
    broadcast = AsyncMock()
    device = _zha_device({})

    with patch.dict(sys.modules, _fake_zigpy(broadcast)):
        await zha_adapter.async_trigger_address_refresh(device)

    broadcast.assert_awaited_once_with(
        "app", "NWK_addr_req", None, 0, device.ieee, "Single", 0
    )
