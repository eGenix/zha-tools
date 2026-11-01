"""Tests for the rejoin (leave-with-rejoin) logic.

The ZHA adapter is mocked: ``async_get_zha_device`` returns a fake device and
``async_trigger_rejoin`` is replaced by a coroutine. Rejoin is a one-shot,
potentially destructive operation, so these tests assert it is only ever sent to
a reachable device, is never retried, and reports its outcome honestly.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.zha_tools.const import (
    STATUS_COMPLETE,
    STATUS_REQUESTED,
    STATUS_UNAVAILABLE,
)
from custom_components.zha_tools.rejoin import async_rejoin_device


def _patch(device, trigger):
    """Patch the ZHA adapter functions used by the rejoin module."""
    return (
        patch(
            "custom_components.zha_tools.rejoin.async_get_zha_device",
            return_value=device,
        ),
        patch(
            "custom_components.zha_tools.rejoin.async_trigger_rejoin",
            new=trigger,
        ),
    )


async def _run(hass, device, trigger, **kwargs):
    params = {"rejoin_wait": 0, "confirm": True}
    params.update(kwargs)
    get_patch, trigger_patch = _patch(device, trigger)
    with get_patch, trigger_patch:
        return await async_rejoin_device(hass, "dev1", **params)


async def test_refuses_without_confirm(hass):
    """Without confirm=True the rejoin must refuse and send nothing."""
    device = SimpleNamespace(available=True)
    sent = {"n": 0}

    async def trigger(dev):
        sent["n"] += 1

    get_patch, trigger_patch = _patch(device, trigger)
    with get_patch, trigger_patch:
        with pytest.raises(ServiceValidationError):
            await async_rejoin_device(hass, "dev1", rejoin_wait=0, confirm=False)
    assert sent["n"] == 0  # nothing was sent to the device


async def test_requested_when_not_waiting(hass):
    device = SimpleNamespace(available=True)
    sent = {"n": 0}

    async def trigger(dev):
        sent["n"] += 1

    result = await _run(hass, device, trigger, rejoin_wait=0)
    assert result == {
        "device_id": "dev1",
        "device_name": None,
        "status": STATUS_REQUESTED,
    }
    assert sent["n"] == 1


async def test_unavailable_device_is_not_sent_a_leave(hass):
    device = SimpleNamespace(available=False)
    sent = {"n": 0}

    async def trigger(dev):
        sent["n"] += 1

    result = await _run(hass, device, trigger)
    assert result["status"] == STATUS_UNAVAILABLE
    assert sent["n"] == 0  # never send a leave to an offline device


async def _run_with_current(hass, current):
    """Run a waiting rejoin where ZHA holds ``current`` after the wait."""
    device = SimpleNamespace(available=True)

    async def trigger(dev):
        return None

    with patch(
        "custom_components.zha_tools.rejoin.async_get_current_device",
        return_value=current,
    ) as get_current:
        result = await _run(hass, device, trigger, rejoin_wait=0.05)
    get_current.assert_called_once_with(device)
    return result


async def test_complete_when_device_returns(hass):
    """ZHA re-created the device and heard from it after the leave."""
    current = SimpleNamespace(available=True, last_seen=time.time() + 60)
    result = await _run_with_current(hass, current)
    assert result["status"] == STATUS_COMPLETE


async def test_requested_when_device_absent_after_wait(hass):
    """ZHA dropped the device on leave and it has not rejoined."""
    result = await _run_with_current(hass, None)
    assert result["status"] == STATUS_REQUESTED


async def test_stale_device_object_does_not_confirm_the_rejoin(hass):
    """The pre-leave object stays "available"; it must not count as a rejoin."""
    stale = SimpleNamespace(available=True, last_seen=time.time() - 60)
    result = await _run_with_current(hass, stale)
    assert result["status"] == STATUS_REQUESTED


async def test_unexpected_error_is_not_silenced(hass):
    device = SimpleNamespace(available=True)

    async def trigger(dev):
        raise RuntimeError("delivery failed")

    get_patch, trigger_patch = _patch(device, trigger)
    with get_patch, trigger_patch:
        with pytest.raises(RuntimeError):
            await async_rejoin_device(hass, "dev1", rejoin_wait=0, confirm=True)


async def test_unknown_device_raises(hass):
    def _raise(hass_, device_id):
        raise ServiceValidationError("nope")

    with patch(
        "custom_components.zha_tools.rejoin.async_get_zha_device",
        side_effect=_raise,
    ):
        with pytest.raises(ServiceValidationError):
            await async_rejoin_device(hass, "bad", rejoin_wait=0, confirm=True)
