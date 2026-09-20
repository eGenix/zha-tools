"""Tests for running an action across all configured ZHA devices.

The device lookup is mocked, so the batch loop, the per-device error handling
and the "only one batch at a time" guard are exercised without a running Zigbee
network.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.zha_tools import zha_adapter
from custom_components.zha_tools.batch import async_run_on_all_devices
from custom_components.zha_tools.const import (
    STATUS_COMPLETE,
    STATUS_ERROR,
    STATUS_TIMEOUT,
)

DEVICE_IDS = ["dev1", "dev2", "dev3"]


def _patch_device_ids(device_ids=None):
    """Patch the device lookup used by the batch module."""
    return patch(
        "custom_components.zha_tools.batch.async_get_zha_device_ids",
        return_value=DEVICE_IDS if device_ids is None else device_ids,
    )


def _result(device_id, status=STATUS_COMPLETE, attempts=1):
    """Return a per-device result as the single-device actions produce it."""
    return {"device_id": device_id, "status": status, "attempts": attempts}


async def test_runs_all_devices_in_order(hass):
    visited = []

    async def action(hass_, device_id, **params):
        visited.append(device_id)
        return _result(device_id)

    with _patch_device_ids():
        result = await async_run_on_all_devices(hass, action, "reconfigure")

    assert visited == DEVICE_IDS
    assert result["devices"] == [_result(device_id) for device_id in DEVICE_IDS]
    assert result["total"] == 3
    assert result["complete"] == 3
    assert result["failed"] == 0


async def test_passes_parameters_through(hass):
    seen = []

    async def action(hass_, device_id, **params):
        seen.append(params)
        return _result(device_id)

    with _patch_device_ids(["dev1"]):
        await async_run_on_all_devices(
            hass,
            action,
            "reconfigure",
            timeout=7,
            max_retries=2,
            retry_delay=0,
        )

    assert seen == [{"timeout": 7, "max_retries": 2, "retry_delay": 0}]


async def test_continues_after_unsuccessful_device(hass):
    """A device that does not complete must not stop the run."""
    visited = []

    async def action(hass_, device_id, **params):
        visited.append(device_id)
        if device_id == "dev2":
            return _result(device_id, status=STATUS_TIMEOUT, attempts=3)
        return _result(device_id)

    with _patch_device_ids():
        result = await async_run_on_all_devices(hass, action, "reinterview")

    assert visited == DEVICE_IDS
    assert result["complete"] == 2
    assert result["failed"] == 1
    assert result["devices"][1]["status"] == STATUS_TIMEOUT


async def test_continues_after_device_error_and_reports_it(hass):
    """An error on one device is reported, and the run moves on."""
    visited = []

    async def action(hass_, device_id, **params):
        visited.append(device_id)
        if device_id == "dev2":
            raise RuntimeError("boom")
        return _result(device_id)

    with _patch_device_ids():
        result = await async_run_on_all_devices(hass, action, "reconfigure")

    assert visited == DEVICE_IDS
    failed = result["devices"][1]
    assert failed["device_id"] == "dev2"
    assert failed["status"] == STATUS_ERROR
    assert "boom" in failed["error"]
    assert result["complete"] == 2
    assert result["failed"] == 1


async def test_no_devices_is_not_an_error(hass):
    async def action(hass_, device_id, **params):
        raise AssertionError("must not be called")

    with _patch_device_ids([]):
        result = await async_run_on_all_devices(hass, action, "reconfigure")

    assert result == {"devices": [], "total": 0, "complete": 0, "failed": 0}


async def test_second_batch_is_refused_while_one_runs(hass):
    """Only one batched action may run at a time."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_action(hass_, device_id, **params):
        started.set()
        await release.wait()
        return _result(device_id)

    async def fast_action(hass_, device_id, **params):
        return _result(device_id)

    with _patch_device_ids(["dev1"]):
        first = asyncio.create_task(
            async_run_on_all_devices(hass, slow_action, "reconfigure")
        )
        await started.wait()

        with pytest.raises(ServiceValidationError):
            await async_run_on_all_devices(hass, fast_action, "reinterview")

        release.set()
        assert (await first)["complete"] == 1


async def test_batch_can_run_again_after_the_previous_one_finished(hass):
    async def action(hass_, device_id, **params):
        return _result(device_id)

    with _patch_device_ids(["dev1"]):
        await async_run_on_all_devices(hass, action, "reconfigure")
        result = await async_run_on_all_devices(hass, action, "reinterview")

    assert result["complete"] == 1


async def test_guard_is_released_when_the_device_lookup_fails(hass):
    """A failing device lookup must not leave the batch permanently blocked."""

    async def action(hass_, device_id, **params):
        return _result(device_id)

    with patch(
        "custom_components.zha_tools.batch.async_get_zha_device_ids",
        side_effect=ServiceValidationError("no gateway"),
    ):
        with pytest.raises(ServiceValidationError):
            await async_run_on_all_devices(hass, action, "reconfigure")

    with _patch_device_ids(["dev1"]):
        result = await async_run_on_all_devices(hass, action, "reconfigure")

    assert result["complete"] == 1


def _proxy(device_id, name, ieee, is_coordinator=False):
    """Return a stand-in for a ZHA device proxy."""
    return SimpleNamespace(
        device_id=device_id,
        device=SimpleNamespace(name=name, ieee=ieee, is_coordinator=is_coordinator),
    )


def test_coordinator_is_excluded_from_the_device_list():
    """The coordinator is never part of a batch run."""
    proxies = [
        _proxy("dev2", "Kitchen sensor", "00:11"),
        _proxy("coord", "Coordinator", "00:00", is_coordinator=True),
        _proxy("dev1", "Attic sensor", "00:22"),
    ]
    # Sorted by device name, so the run order is predictable.
    assert zha_adapter.selected_device_ids(proxies) == ["dev1", "dev2"]
