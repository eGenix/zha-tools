"""Tests for the reliable action wrapper.

The wrapped action is a test service registered on the Home Assistant test
instance, failing a given number of times before it succeeds. the wrapper's sleep
and clock are replaced by a fake clock, so the backoff schedule and
the overall timeout are checked without real waiting.
"""

from __future__ import annotations

import asyncio

import pytest
import voluptuous as vol

from homeassistant.core import Context, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zha_tools import retry_action
from custom_components.zha_tools.const import (
    DOMAIN,
    SERVICE_RETRY_ACTION,
    STATUS_COMPLETE,
    STATUS_TIMEOUT,
)
from custom_components.zha_tools.retry_action import async_retry_action

PARAMS = {
    "timeout": 600,
    "attempt_timeout": 30,
    "retry_delay": 5,
    "backoff": 1.5,
    "max_delay": 20,
}


class FakeClock:
    """A clock which only moves when the wrapper sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(retry_action, "_now", fake.time)
    monkeypatch.setattr(retry_action, "_sleep", fake.sleep)
    return fake


def _register_flaky(hass, failures, error=HomeAssistantError("no answer")):
    """Register ``test.flaky``, failing ``failures`` times; return its calls."""
    calls: list[ServiceCall] = []

    async def handler(call: ServiceCall) -> None:
        calls.append(call)
        if len(calls) <= failures:
            raise error

    hass.services.async_register("test", "flaky", handler)
    return calls


async def test_succeeds_after_retries_with_backoff(hass, clock):
    calls = _register_flaky(hass, failures=5)

    result = await async_retry_action(hass, "test.flaky", {"position": 0}, {}, **PARAMS)

    assert result == {
        "action": "test.flaky",
        "device_name": None,
        "status": STATUS_COMPLETE,
        "attempts": 6,
        "elapsed": 60.6,  # 5 + 7.5 + 11.25 + 16.875 + 20, rounded
        "error": None,
    }
    # Slow exponential backoff, capped at max_delay.
    assert clock.sleeps == [5, 7.5, 11.25, 16.875, 20]
    assert all(call.data == {"position": 0} for call in calls)


async def test_first_attempt_success_does_not_wait(hass, clock):
    _register_flaky(hass, failures=0)

    result = await async_retry_action(hass, "test.flaky", {}, {}, **PARAMS)

    assert result["status"] == STATUS_COMPLETE
    assert result["attempts"] == 1
    assert clock.sleeps == []


async def test_gives_up_after_overall_timeout(hass, clock):
    _register_flaky(hass, failures=1000)

    result = await async_retry_action(
        hass, "test.flaky", {}, {}, **{**PARAMS, "timeout": 60}
    )

    assert result["status"] == STATUS_TIMEOUT
    assert result["error"] == "no answer"
    # After 40.625s the next 20s delay no longer fits; the last wait is cut
    # short so a final attempt still gets 5s before the deadline.
    assert clock.sleeps == [5, 7.5, 11.25, 16.875, 14.375]
    assert result["attempts"] == 6


async def test_hanging_attempts_time_out_and_are_retried(hass, clock):
    attempts = 0

    async def handler(call: ServiceCall) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # Real sleep: the attempt timeout uses the event loop's clock.
            await asyncio.Event().wait()

    hass.services.async_register("test", "hang", handler)

    result = await async_retry_action(
        hass, "test.hang", {}, {}, **{**PARAMS, "attempt_timeout": 0.05}
    )

    assert result["status"] == STATUS_COMPLETE
    assert result["attempts"] == 2


@pytest.mark.parametrize(
    "error",
    [ServiceValidationError("bad"), vol.Invalid("bad data")],
    ids=["validation", "schema"],
)
async def test_invalid_calls_are_not_retried(hass, clock, error):
    calls = _register_flaky(hass, failures=1000, error=error)

    with pytest.raises(type(error)):
        await async_retry_action(hass, "test.flaky", {}, {}, **PARAMS)
    assert len(calls) == 1


async def test_unknown_action_is_not_retried(hass, clock):
    with pytest.raises(ServiceValidationError):
        await async_retry_action(hass, "test.missing", {}, {}, **PARAMS)
    assert clock.sleeps == []


async def test_unexpected_errors_propagate(hass, clock):
    calls = _register_flaky(hass, failures=1000, error=RuntimeError("bug"))

    with pytest.raises(RuntimeError, match="bug"):
        await async_retry_action(hass, "test.flaky", {}, {}, **PARAMS)
    assert len(calls) == 1


async def test_reports_target_device_names(hass, clock):
    calls = _register_flaky(hass, failures=0)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_ZIGBEE, "00:11:22:33:44:55:66:77")},
        name="Living room blind",
    )
    er.async_get(hass).async_get_or_create(
        "cover",
        "zha",
        "blind-1",
        suggested_object_id="living_room_blind",
        device_id=device.id,
    )
    hass.states.async_set("cover.living_room_blind", "open")

    result = await async_retry_action(
        hass, "test.flaky", {}, {"entity_id": "cover.living_room_blind"}, **PARAMS
    )

    assert result["device_name"] == "Living room blind"
    assert calls[0].data["entity_id"] == "cover.living_room_blind"


async def test_service_call_uses_defaults(hass, clock):
    """The registered action fills in the defaults and returns the result."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    calls = _register_flaky(hass, failures=2)

    result = await hass.services.async_call(
        DOMAIN,
        SERVICE_RETRY_ACTION,
        {"action": "test.flaky", "data": {"position": 10}},
        blocking=True,
        return_response=True,
    )

    assert result["status"] == STATUS_COMPLETE
    assert result["attempts"] == 3
    assert clock.sleeps == [5, 7.5]
    assert calls[-1].data == {"position": 10}


async def test_metadata_is_passed_in_the_action_data(hass, clock):
    calls = _register_flaky(hass, failures=1)

    await async_retry_action(
        hass,
        "test.flaky",
        {"position": 10},
        {},
        **PARAMS,
        metadata={"source": "ui"},
    )

    assert len(calls) == 2
    assert all(
        call.data == {"position": 10, "metadata": {"source": "ui"}} for call in calls
    )


async def test_empty_metadata_is_passed_too(hass, clock):
    calls = _register_flaky(hass, failures=0)

    await async_retry_action(hass, "test.flaky", {}, {}, **PARAMS, metadata={})

    assert calls[0].data == {"metadata": {}}


async def test_metadata_conflicting_with_data_is_refused(hass, clock):
    calls = _register_flaky(hass, failures=0)

    with pytest.raises(ServiceValidationError):
        await async_retry_action(
            hass,
            "test.flaky",
            {"metadata": {"a": 1}},
            {},
            **PARAMS,
            metadata={"b": 2},
        )

    assert calls == []


async def test_service_call_passes_metadata(hass, clock):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    calls = _register_flaky(hass, failures=0)

    result = await hass.services.async_call(
        DOMAIN,
        SERVICE_RETRY_ACTION,
        {
            "action": "test.flaky",
            "data": {"position": 10},
            "metadata": {"source": "ui"},
        },
        blocking=True,
        return_response=True,
    )

    assert result["status"] == STATUS_COMPLETE
    assert calls[0].data == {"position": 10, "metadata": {"source": "ui"}}


async def test_service_call_rejects_bad_action_name(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RETRY_ACTION,
            {"action": "not an action"},
            blocking=True,
            return_response=True,
        )


def _register_blind(hass, state):
    """Register a cover entity on a named device with the given state."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_ZIGBEE, "00:11:22:33:44:55:66:77")},
        name="Living room blind",
    )
    er.async_get(hass).async_get_or_create(
        "cover",
        "zha",
        "blind-1",
        suggested_object_id="living_room_blind",
        device_id=device.id,
    )
    if state is not None:
        hass.states.async_set("cover.living_room_blind", state)
    return device.id


async def test_unavailable_entity_is_not_called_but_retried(hass, clock):
    """HA would skip an unavailable entity and report success; we must not."""
    calls = _register_flaky(hass, failures=0)
    _register_blind(hass, "unavailable")

    async def come_back(delay):
        clock.sleeps.append(delay)
        clock.now += delay
        if len(clock.sleeps) == 2:
            hass.states.async_set("cover.living_room_blind", "open")

    retry_action._sleep = come_back
    try:
        result = await async_retry_action(
            hass, "test.flaky", {}, {"entity_id": "cover.living_room_blind"}, **PARAMS
        )
    finally:
        retry_action._sleep = clock.sleep

    assert result["status"] == STATUS_COMPLETE
    assert result["attempts"] == 3
    assert len(calls) == 1


async def test_device_target_checks_its_entities_of_the_action_domain(hass, clock):
    _register_flaky(hass, failures=0)
    device_id = _register_blind(hass, None)  # registered, no state yet

    result = await async_retry_action(
        hass, "cover.flaky", {}, {"device_id": device_id}, **{**PARAMS, "timeout": 10}
    )

    assert result["status"] == STATUS_TIMEOUT
    assert result["error"] == "unavailable: cover.living_room_blind"


async def test_unknown_entity_is_not_retried(hass, clock):
    calls = _register_flaky(hass, failures=0)

    with pytest.raises(ServiceValidationError, match="Unknown entity"):
        await async_retry_action(
            hass, "test.flaky", {}, {"entity_id": "cover.nope"}, **PARAMS
        )
    assert calls == []


async def test_comma_separated_entity_ids_are_checked(hass, clock):
    calls = _register_flaky(hass, failures=0)
    hass.states.async_set("cover.a", "open")
    hass.states.async_set("cover.b", "unavailable")

    result = await async_retry_action(
        hass,
        "test.flaky",
        {"entity_id": "cover.a, cover.b"},
        {},
        **{**PARAMS, "timeout": 10},
    )

    assert result["status"] == STATUS_TIMEOUT
    assert result["error"] == "unavailable: cover.b"
    assert calls == []


async def test_wrapping_zha_tools_actions_is_refused(hass, clock):
    with pytest.raises(ServiceValidationError, match="cannot wrap"):
        await async_retry_action(hass, f"{DOMAIN}.ping", {}, {}, **PARAMS)


async def test_timeout_raised_by_the_action_is_reported_as_such(hass, clock):
    _register_flaky(hass, failures=1, error=TimeoutError("radio busy"))

    result = await async_retry_action(hass, "test.flaky", {}, {}, **PARAMS)

    assert result["status"] == STATUS_COMPLETE
    assert result["attempts"] == 2


async def test_delay_never_exceeds_max_delay(hass, clock):
    _register_flaky(hass, failures=2)

    await async_retry_action(
        hass, "test.flaky", {}, {}, **{**PARAMS, "retry_delay": 30, "max_delay": 10}
    )

    assert clock.sleeps == [10, 10]


async def test_caller_context_is_passed_on(hass, clock):
    calls = _register_flaky(hass, failures=1)
    context = Context(user_id="user-1")

    await async_retry_action(hass, "test.flaky", {}, {}, **PARAMS, context=context)

    assert all(call.context is context for call in calls)


@pytest.mark.parametrize(
    "data",
    [
        {"action": "test.flaky", "timeout": "inf"},
        {"action": "test.flaky", "timeout": 100000},
        {"action": "test.flaky", "retry_delay": 0.1},
        {"action": "test.flaky", "target": {"entity_id": "not an entity"}},
        {"action": "test.flaky", "target": {"bogus": "x"}},
        {"action": "test.flaky", "metadata": "not a mapping"},
    ],
    ids=[
        "inf",
        "too-long",
        "too-fast",
        "bad-entity",
        "unknown-target-key",
        "bad-metadata",
    ],
)
async def test_service_call_rejects_unbounded_or_bad_values(hass, data):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, SERVICE_RETRY_ACTION, data, blocking=True, return_response=True
        )


async def test_debug_log_does_not_contain_action_data(hass, clock, caplog):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    _register_flaky(hass, failures=0)

    with caplog.at_level("DEBUG"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RETRY_ACTION,
            {
                "action": "test.flaky",
                "data": {"code": "s3cret"},
                "metadata": {"note": "hidden"},
            },
            blocking=True,
            return_response=True,
        )

    assert "s3cret" not in caplog.text
    assert "hidden" not in caplog.text
