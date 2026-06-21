"""Tests for the re-interview logic and status mapping.

The ZHA adapter is fully mocked: ``async_get_zha_device`` returns a fake device
and ``async_trigger_reinterview`` is replaced by a coroutine, so the retry loop
and status mapping are exercised without a running Zigbee network.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.zha_tools.const import (
    STATUS_COMPLETE,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
)
from custom_components.zha_tools.reinterview import async_reinterview_device


def _patch(device, trigger):
    """Patch the ZHA adapter functions used by the reinterview module."""
    return (
        patch(
            "custom_components.zha_tools.reinterview.async_get_zha_device",
            return_value=device,
        ),
        patch(
            "custom_components.zha_tools.reinterview.async_trigger_reinterview",
            new=trigger,
        ),
    )


async def _run(hass, device, trigger, **kwargs):
    params = {"timeout": 5, "max_retries": 0, "retry_delay": 0}
    params.update(kwargs)
    get_patch, trigger_patch = _patch(device, trigger)
    with get_patch, trigger_patch:
        return await async_reinterview_device(hass, "dev1", **params)


async def test_complete_when_reinterview_finishes(hass):
    device = SimpleNamespace(available=True)

    async def trigger(dev):
        return None

    result = await _run(hass, device, trigger)
    assert result == {"device_id": "dev1", "status": STATUS_COMPLETE, "attempts": 1}


async def test_unavailable_when_device_offline_upfront(hass):
    device = SimpleNamespace(available=False)
    sent = {"n": 0}

    async def trigger(dev):
        sent["n"] += 1

    result = await _run(hass, device, trigger)
    assert result["status"] == STATUS_UNAVAILABLE
    assert sent["n"] == 0  # never sent to an offline device


async def test_timeout_when_attempt_does_not_finish(hass):
    device = SimpleNamespace(available=True)

    async def trigger(dev):
        await asyncio.sleep(0.3)

    result = await _run(hass, device, trigger, timeout=0.05)
    assert result["status"] == STATUS_TIMEOUT


async def test_retries_until_complete(hass):
    device = SimpleNamespace(available=True)
    calls = {"n": 0}

    async def trigger(dev):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(0.3)  # first attempt times out

    result = await _run(hass, device, trigger, timeout=0.05, max_retries=3)
    assert result["status"] == STATUS_COMPLETE
    assert result["attempts"] == 2


async def test_retries_exhausted_returns_last_status(hass):
    device = SimpleNamespace(available=True)

    async def trigger(dev):
        await asyncio.sleep(0.3)  # always times out

    result = await _run(hass, device, trigger, timeout=0.05, max_retries=2)
    assert result["status"] == STATUS_TIMEOUT
    assert result["attempts"] == 3  # 1 initial + 2 retries


async def test_unexpected_error_is_not_silenced(hass):
    """A real error from the re-interview must propagate, not be swallowed."""
    device = SimpleNamespace(available=True)

    async def trigger(dev):
        raise RuntimeError("boom")

    get_patch, trigger_patch = _patch(device, trigger)
    with get_patch, trigger_patch:
        with pytest.raises(RuntimeError):
            await async_reinterview_device(
                hass, "dev1", timeout=5, max_retries=0, retry_delay=0
            )


async def test_unknown_device_raises(hass):
    def _raise(hass_, device_id):
        raise ServiceValidationError("nope")

    with patch(
        "custom_components.zha_tools.reinterview.async_get_zha_device",
        side_effect=_raise,
    ):
        with pytest.raises(ServiceValidationError):
            await async_reinterview_device(
                hass, "bad", timeout=1, max_retries=0, retry_delay=0
            )
