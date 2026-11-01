"""Tests for setup, teardown and the registered service/actions."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol

from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError, Unauthorized

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import zha_tools
from custom_components.zha_tools.const import (
    CONF_MAX_RETRIES,
    CONF_RETRY_DELAY,
    CONF_TIMEOUT,
    DOMAIN,
    SERVICE_FRAME_COUNTER,
    SERVICE_PING,
    SERVICE_QUERY_ROUTE,
    SERVICE_RECONFIGURE,
    SERVICE_REINTERVIEW,
    SERVICE_REJOIN,
    SERVICE_RETRY_ACTION,
    STATUS_COMPLETE,
    STATUS_REQUESTED,
)

ALL_SERVICES = (
    SERVICE_RECONFIGURE,
    SERVICE_REINTERVIEW,
    SERVICE_REJOIN,
    SERVICE_PING,
    SERVICE_FRAME_COUNTER,
    SERVICE_QUERY_ROUTE,
    SERVICE_RETRY_ACTION,
)

# The read-only actions without retry parameters, with their handler function.
QUERY_ACTIONS = [
    (SERVICE_FRAME_COUNTER, "async_read_frame_counter"),
    (SERVICE_QUERY_ROUTE, "async_query_route"),
]

DEFAULT_OPTIONS = {CONF_TIMEOUT: 10, CONF_MAX_RETRIES: 10, CONF_RETRY_DELAY: 5}


async def _setup(hass, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={},
        options=options if options is not None else dict(DEFAULT_OPTIONS),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_services_registered_and_removed(hass):
    entry = await _setup(hass)
    for service in ALL_SERVICES:
        assert hass.services.has_service(DOMAIN, service)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    for service in ALL_SERVICES:
        assert not hass.services.has_service(DOMAIN, service)


@pytest.mark.parametrize(
    ("service", "action", "data"),
    [
        (SERVICE_RECONFIGURE, "async_reconfigure_device", {"device_id": "dev1"}),
        (SERVICE_REINTERVIEW, "async_reinterview_device", {"device_id": "dev1"}),
        (SERVICE_PING, "async_ping_device", {"device_id": "dev1"}),
        (SERVICE_FRAME_COUNTER, "async_read_frame_counter", {"device_id": "dev1"}),
        (SERVICE_QUERY_ROUTE, "async_query_route", {"device_id": "dev1"}),
        (
            SERVICE_REJOIN,
            "async_rejoin_device",
            {"device_id": "dev1", "confirm": True},
        ),
    ],
)
async def test_actions_run_without_response(hass, service, action, data):
    """The response is optional: callers need not set a response_variable."""
    await _setup(hass)
    with patch(
        f"custom_components.zha_tools.{action}",
        AsyncMock(return_value={"device_id": "dev1", "status": STATUS_COMPLETE}),
    ) as mock:
        result = await hass.services.async_call(DOMAIN, service, data, blocking=True)
    assert result is None
    mock.assert_awaited_once()


async def test_reinterview_returns_response(hass):
    await _setup(hass)
    expected = {"device_id": "dev1", "status": STATUS_COMPLETE, "attempts": 1}
    with patch(
        "custom_components.zha_tools.async_reinterview_device",
        AsyncMock(return_value=expected),
    ) as mock:
        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_REINTERVIEW,
            {"device_id": "dev1"},
            blocking=True,
            return_response=True,
        )
    assert result == expected
    # Shares the reconfigure defaults.
    assert mock.await_args.kwargs == {
        "timeout": 10.0,
        "max_retries": 10,
        "retry_delay": 5.0,
    }


async def test_ping_returns_response(hass):
    await _setup(hass)
    expected = {"device_id": "dev1", "status": STATUS_COMPLETE, "attempts": 1}
    with patch(
        "custom_components.zha_tools.async_ping_device",
        AsyncMock(return_value=expected),
    ) as mock:
        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_PING,
            {"device_id": "dev1", "max_retries": 3},
            blocking=True,
            return_response=True,
        )
    assert result == expected
    # Shares the reconfigure defaults, with per-call overrides.
    assert mock.await_args.kwargs == {
        "timeout": 10.0,
        "max_retries": 3,
        "retry_delay": 5.0,
    }


@pytest.mark.parametrize(("service", "action"), QUERY_ACTIONS)
async def test_query_action_returns_response(hass, service, action):
    await _setup(hass)
    expected = {
        "device_id": "dev1",
        "device_name": "Kitchen light",
        "ieee": "00:11:22:33:44:55:66:77",
        "status": STATUS_COMPLETE,
    }
    with patch(
        f"custom_components.zha_tools.{action}",
        AsyncMock(return_value=expected),
    ) as mock:
        result = await hass.services.async_call(
            DOMAIN,
            service,
            {"device_id": "dev1"},
            blocking=True,
            return_response=True,
        )
    assert result == expected
    assert mock.await_args.args[1] == "dev1"
    assert mock.await_args.kwargs == {}


@pytest.mark.parametrize(("service", "action"), QUERY_ACTIONS)
async def test_query_action_all_devices_runs_a_batch(hass, service, action):
    """The read-only batches pass no retry parameters."""
    await _setup(hass)
    expected = {"devices": [], "total": 0, "complete": 0, "failed": 0}
    with patch(
        "custom_components.zha_tools.async_run_on_all_devices",
        AsyncMock(return_value=expected),
    ) as mock:
        result = await hass.services.async_call(
            DOMAIN,
            service,
            {"all_devices": True},
            blocking=True,
            return_response=True,
        )
    assert result == expected
    args = mock.await_args.args
    assert args[1] is getattr(zha_tools, action)
    assert args[2] == service
    # No retry parameters; frame counter runs must not log anything.
    assert mock.await_args.kwargs == {"quiet": service == SERVICE_FRAME_COUNTER}


@pytest.mark.parametrize(
    "data",
    [
        {"device_id": "dev1", "all_devices": True},
        {},
        {"device_id": "dev1", "timeout": 5},
    ],
    ids=["both", "neither", "retry-option"],
)
@pytest.mark.parametrize(("service", "action"), QUERY_ACTIONS)
async def test_query_action_rejects_invalid_targets(hass, service, action, data):
    await _setup(hass)
    with patch(
        f"custom_components.zha_tools.{action}",
        AsyncMock(return_value={}),
    ) as mock:
        with pytest.raises(Exception):
            await hass.services.async_call(
                DOMAIN,
                service,
                data,
                blocking=True,
                return_response=True,
            )
    mock.assert_not_awaited()


async def test_rejoin_returns_response(hass):
    await _setup(hass)
    expected = {"device_id": "dev1", "status": STATUS_REQUESTED}
    with patch(
        "custom_components.zha_tools.async_rejoin_device",
        AsyncMock(return_value=expected),
    ) as mock:
        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_REJOIN,
            {"device_id": "dev1", "rejoin_wait": 5, "confirm": True},
            blocking=True,
            return_response=True,
        )
    assert result == expected
    assert mock.await_args.kwargs == {"rejoin_wait": 5.0, "confirm": True}


async def test_rejoin_requires_confirm_in_schema(hass):
    """Omitting confirm fails service validation before the action runs."""
    await _setup(hass)
    with patch(
        "custom_components.zha_tools.async_rejoin_device",
        AsyncMock(return_value={}),
    ) as mock:
        with pytest.raises(Exception):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REJOIN,
                {"device_id": "dev1"},
                blocking=True,
                return_response=True,
            )
    mock.assert_not_awaited()


async def test_service_returns_response(hass):
    await _setup(hass)
    expected = {"device_id": "dev1", "status": STATUS_COMPLETE, "attempts": 1}

    with patch(
        "custom_components.zha_tools.async_reconfigure_device",
        AsyncMock(return_value=expected),
    ):
        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_RECONFIGURE,
            {"device_id": "dev1"},
            blocking=True,
            return_response=True,
        )
    assert result == expected


async def test_service_uses_configured_defaults(hass):
    await _setup(hass)
    with patch(
        "custom_components.zha_tools.async_reconfigure_device",
        AsyncMock(return_value={}),
    ) as mock:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RECONFIGURE,
            {"device_id": "dev1"},
            blocking=True,
            return_response=True,
        )
    mock.assert_awaited_once()
    kwargs = mock.await_args.kwargs
    assert kwargs == {"timeout": 10.0, "max_retries": 10, "retry_delay": 5.0}
    assert mock.await_args.args[1] == "dev1"


async def test_service_call_overrides_defaults(hass):
    await _setup(hass)
    with patch(
        "custom_components.zha_tools.async_reconfigure_device",
        AsyncMock(return_value={}),
    ) as mock:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RECONFIGURE,
            {
                "device_id": "dev1",
                "timeout": 20,
                "max_retries": 3,
                "retry_delay": 1,
            },
            blocking=True,
            return_response=True,
        )
    kwargs = mock.await_args.kwargs
    assert kwargs == {"timeout": 20.0, "max_retries": 3, "retry_delay": 1.0}


async def test_service_requires_device_id(hass):
    await _setup(hass)
    with pytest.raises(Exception):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RECONFIGURE,
            {},
            blocking=True,
            return_response=True,
        )


@pytest.mark.parametrize(
    ("service", "action"),
    [
        (SERVICE_RECONFIGURE, "async_reconfigure_device"),
        (SERVICE_REINTERVIEW, "async_reinterview_device"),
        (SERVICE_PING, "async_ping_device"),
    ],
)
async def test_all_devices_runs_a_batch(hass, service, action):
    """``all_devices: true`` hands the action to the batch runner."""
    await _setup(hass)
    expected = {"devices": [], "total": 0, "complete": 0, "failed": 0}
    with patch(
        "custom_components.zha_tools.async_run_on_all_devices",
        AsyncMock(return_value=expected),
    ) as mock:
        result = await hass.services.async_call(
            DOMAIN,
            service,
            {"all_devices": True},
            blocking=True,
            return_response=True,
        )
    assert result == expected
    args = mock.await_args.args
    assert args[1] is getattr(zha_tools, action)
    assert args[2] == service
    assert mock.await_args.kwargs == {
        "timeout": 10.0,
        "max_retries": 10,
        "retry_delay": 5.0,
    }


async def test_all_devices_and_device_id_are_exclusive(hass):
    """Naming both a device and all devices is a validation error."""
    await _setup(hass)
    with patch(
        "custom_components.zha_tools.async_run_on_all_devices",
        AsyncMock(return_value={}),
    ) as batch_mock:
        with patch(
            "custom_components.zha_tools.async_reconfigure_device",
            AsyncMock(return_value={}),
        ) as single_mock:
            with pytest.raises(Exception):
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_RECONFIGURE,
                    {"device_id": "dev1", "all_devices": True},
                    blocking=True,
                    return_response=True,
                )
    batch_mock.assert_not_awaited()
    single_mock.assert_not_awaited()


async def test_all_devices_false_without_device_id_raises(hass):
    """``all_devices: false`` alone leaves nothing to work on."""
    await _setup(hass)
    with patch(
        "custom_components.zha_tools.async_reconfigure_device",
        AsyncMock(return_value={}),
    ) as mock:
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RECONFIGURE,
                {"all_devices": False},
                blocking=True,
                return_response=True,
            )
    mock.assert_not_awaited()


async def test_rejoin_does_not_accept_all_devices(hass):
    """The dangerous rejoin action is single-device only."""
    await _setup(hass)
    with patch(
        "custom_components.zha_tools.async_rejoin_device",
        AsyncMock(return_value={}),
    ) as mock:
        with pytest.raises(Exception):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REJOIN,
                {"all_devices": True, "confirm": True},
                blocking=True,
                return_response=True,
            )
    mock.assert_not_awaited()


@pytest.mark.parametrize(
    ("service", "data"),
    [
        (SERVICE_PING, {"device_id": "dev1", "timeout": "inf"}),
        (SERVICE_PING, {"device_id": "dev1", "max_retries": 1_000_000}),
        (SERVICE_RECONFIGURE, {"device_id": "dev1", "retry_delay": 301}),
        (SERVICE_REJOIN, {"device_id": "dev1", "confirm": True, "rejoin_wait": "inf"}),
    ],
    ids=["inf-timeout", "huge-retries", "long-delay", "inf-rejoin-wait"],
)
async def test_unbounded_values_are_rejected(hass, service, data):
    await _setup(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)


@pytest.mark.parametrize(
    ("service", "data"),
    [
        (SERVICE_RECONFIGURE, {"device_id": "dev1"}),
        (SERVICE_REINTERVIEW, {"device_id": "dev1"}),
        (SERVICE_PING, {"device_id": "dev1"}),
        (SERVICE_FRAME_COUNTER, {"device_id": "dev1"}),
        (SERVICE_QUERY_ROUTE, {"device_id": "dev1"}),
        (SERVICE_REJOIN, {"device_id": "dev1", "confirm": True}),
    ],
)
async def test_device_actions_are_admin_only(hass, hass_read_only_user, service, data):
    await _setup(hass)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            service,
            data,
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )


async def test_retry_action_is_open_to_non_admins(hass, hass_read_only_user):
    """The wrapper itself is not restricted; the wrapped action decides."""
    await _setup(hass)
    hass.services.async_register("test", "noop", AsyncMock(return_value=None))

    result = await hass.services.async_call(
        DOMAIN,
        SERVICE_RETRY_ACTION,
        {"action": "test.noop"},
        blocking=True,
        return_response=True,
        context=Context(user_id=hass_read_only_user.id),
    )
    assert result["status"] == STATUS_COMPLETE
