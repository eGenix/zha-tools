"""Tests for the core reconfigure logic and status mapping.

ZHA is fully mocked: ``async_get_zha_device`` returns a fake device and
``async_trigger_reconfigure`` is replaced by a coroutine that emits the same
dispatcher events ZHA would, so the real collect-and-decide path is exercised.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.zha_tools.const import (
    SIGNAL_DEVICE_RECONFIGURE_EVENT,
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
)
from custom_components.zha_tools.reconfigure import async_reconfigure_device

# --- event payload helpers (shape mirrors what ZHA forwards) ----------------


def _bind(success: bool) -> dict:
    return {"type": "zha_channel_bind", "zha_channel_msg_data": {"success": success}}


def _report(status: str) -> dict:
    return {
        "type": "zha_channel_configure_reporting",
        "zha_channel_msg_data": {"attributes": {"battery": {"status": status}}},
    }


def _done() -> dict:
    return {"type": "zha_channel_cfg_done"}


def _make_trigger(hass, events, *, sleep=0.0, drop_device=None):
    """Build a fake ``async_trigger_reconfigure`` emitting ``events``.

    Args:
        hass: The Home Assistant instance used to send dispatcher events.
        events: The sequence of event payloads to emit.
        sleep: Optional delay before emitting, to simulate a slow attempt.
        drop_device: If given, mark this device unavailable after emitting.
    """

    async def _trigger(device):
        if sleep:
            await asyncio.sleep(sleep)
        for event in events:
            async_dispatcher_send(hass, SIGNAL_DEVICE_RECONFIGURE_EVENT, event)
        if drop_device is not None:
            drop_device.available = False

    return _trigger


def _patch(device, trigger):
    """Patch the ZHA adapter functions used by the reconfigure module."""
    return (
        patch(
            "custom_components.zha_tools.reconfigure.async_get_zha_device",
            return_value=device,
        ),
        patch(
            "custom_components.zha_tools.reconfigure.async_trigger_reconfigure",
            new=trigger,
        ),
    )


async def _run(hass, device, trigger, **kwargs):
    params = {"timeout": 5, "max_retries": 0, "retry_delay": 0}
    params.update(kwargs)
    get_patch, trigger_patch = _patch(device, trigger)
    with get_patch, trigger_patch:
        return await async_reconfigure_device(hass, "dev1", **params)


# --- happy path and finished-but-failed cases -------------------------------


async def test_complete_when_bind_and_report_succeed(hass):
    device = SimpleNamespace(available=True)
    trigger = _make_trigger(hass, [_bind(True), _report("SUCCESS"), _done()])
    result = await _run(hass, device, trigger)
    assert result == {"device_id": "dev1", "status": STATUS_COMPLETE, "attempts": 1}


async def test_incomplete_when_reporting_fails(hass):
    device = SimpleNamespace(available=True)
    trigger = _make_trigger(hass, [_bind(True), _report("FAILURE"), _done()])
    result = await _run(hass, device, trigger)
    assert result["status"] == STATUS_INCOMPLETE


async def test_incomplete_when_binding_fails(hass):
    device = SimpleNamespace(available=True)
    trigger = _make_trigger(hass, [_bind(False), _report("SUCCESS"), _done()])
    result = await _run(hass, device, trigger)
    assert result["status"] == STATUS_INCOMPLETE


async def test_complete_when_at_least_one_bind_and_report_succeed(hass):
    """Per the spec, one successful binding and one reporting is enough.

    A flaky device where some clusters fail still counts as complete as long as
    at least one binding and one configure-reporting step succeeded.
    """
    device = SimpleNamespace(available=True)
    trigger = _make_trigger(
        hass,
        [
            _bind(False),
            _bind(True),
            _report("FAILURE"),
            _report("SUCCESS"),
            _done(),
        ],
    )
    result = await _run(hass, device, trigger)
    assert result["status"] == STATUS_COMPLETE


async def test_incomplete_when_no_steps_reported(hass):
    """A device that reports no binding/reporting is incomplete.

    Without at least one binding and one reporting reported ok, the spec
    requires the result to be reported as incomplete.
    """
    device = SimpleNamespace(available=True)
    trigger = _make_trigger(hass, [_done()])
    result = await _run(hass, device, trigger)
    assert result["status"] == STATUS_INCOMPLETE


# --- failure modes ----------------------------------------------------------


async def test_timeout_when_attempt_does_not_finish(hass):
    device = SimpleNamespace(available=True)
    trigger = _make_trigger(hass, [_done()], sleep=0.3)
    result = await _run(hass, device, trigger, timeout=0.05)
    assert result["status"] == STATUS_TIMEOUT


async def test_unavailable_when_device_offline_upfront(hass):
    device = SimpleNamespace(available=False)
    trigger = _make_trigger(hass, [_done()])
    result = await _run(hass, device, trigger)
    assert result["status"] == STATUS_UNAVAILABLE


async def test_unavailable_when_device_drops_during_attempt(hass):
    device = SimpleNamespace(available=True)
    trigger = _make_trigger(hass, [_bind(False), _done()], drop_device=device)
    result = await _run(hass, device, trigger)
    assert result["status"] == STATUS_UNAVAILABLE


async def test_unavailable_on_timeout_when_offline(hass):
    device = SimpleNamespace(available=True)

    async def _trigger(dev):
        dev.available = False
        await asyncio.sleep(0.3)

    result = await _run(hass, device, _trigger, timeout=0.05)
    assert result["status"] == STATUS_UNAVAILABLE


async def test_unknown_device_raises(hass):
    def _raise(hass_, device_id):
        raise ServiceValidationError("nope")

    with patch(
        "custom_components.zha_tools.reconfigure.async_get_zha_device",
        side_effect=_raise,
    ):
        with pytest.raises(ServiceValidationError):
            await async_reconfigure_device(
                hass, "bad", timeout=1, max_retries=0, retry_delay=0
            )


# --- retry behaviour --------------------------------------------------------


async def test_retries_until_complete(hass):
    device = SimpleNamespace(available=True)
    calls = {"n": 0}

    async def _trigger(dev):
        calls["n"] += 1
        if calls["n"] == 1:
            events = [_bind(False), _done()]  # incomplete first
        else:
            events = [_bind(True), _report("SUCCESS"), _done()]  # complete
        for event in events:
            async_dispatcher_send(hass, SIGNAL_DEVICE_RECONFIGURE_EVENT, event)

    result = await _run(hass, device, _trigger, max_retries=3, retry_delay=0)
    assert result["status"] == STATUS_COMPLETE
    assert result["attempts"] == 2


async def test_retries_exhausted_returns_last_status(hass):
    device = SimpleNamespace(available=True)
    trigger = _make_trigger(hass, [_bind(False), _done()])  # always incomplete
    result = await _run(hass, device, trigger, max_retries=2, retry_delay=0)
    assert result["status"] == STATUS_INCOMPLETE
    assert result["attempts"] == 3  # 1 initial + 2 retries


async def test_no_retry_when_first_attempt_completes(hass):
    device = SimpleNamespace(available=True)
    trigger = _make_trigger(hass, [_bind(True), _report("SUCCESS"), _done()])
    result = await _run(hass, device, trigger, max_retries=5, retry_delay=0)
    assert result["attempts"] == 1


# --- logging ----------------------------------------------------------------


async def test_logs_zha_reporting_and_outcome(hass, caplog):
    """Per-step ZHA results and the outcome summary are logged."""
    device = SimpleNamespace(available=True)
    events = [
        {
            "type": "zha_channel_bind",
            "zha_channel_msg_data": {"cluster_name": "OnOff", "success": True},
        },
        {
            "type": "zha_channel_configure_reporting",
            "zha_channel_msg_data": {
                "cluster_name": "OnOff",
                "attributes": {"on_off": {"status": "FAILURE"}},
            },
        },
        _done(),
    ]
    trigger = _make_trigger(hass, events)
    with caplog.at_level(logging.DEBUG, logger="custom_components.zha_tools"):
        await _run(hass, device, trigger)

    # The detail of what ZHA reported back is visible.
    assert "bind of cluster OnOff succeeded" in caplog.text
    assert "configure-reporting of OnOff.on_off reported FAILURE" in caplog.text
    # The outcome summary names the failing step and the reported status.
    assert "failed reporting: OnOff.on_off" in caplog.text
    assert "incomplete" in caplog.text
