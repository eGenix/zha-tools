"""Tests for setup, teardown and the registered service/actions."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.exceptions import ServiceValidationError

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import zha_tools
from custom_components.zha_tools.const import (
    CONF_MAX_RETRIES,
    CONF_RETRY_DELAY,
    CONF_TIMEOUT,
    DOMAIN,
    SERVICE_RECONFIGURE,
    SERVICE_REINTERVIEW,
    SERVICE_REJOIN,
    STATUS_COMPLETE,
    STATUS_REQUESTED,
)

ALL_SERVICES = (SERVICE_RECONFIGURE, SERVICE_REINTERVIEW, SERVICE_REJOIN)

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
