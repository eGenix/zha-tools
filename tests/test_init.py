"""Tests for setup, teardown and the reconfigure service/action."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zha_tools.const import (
    CONF_MAX_RETRIES,
    CONF_RETRY_DELAY,
    CONF_TIMEOUT,
    DOMAIN,
    SERVICE_RECONFIGURE,
    STATUS_COMPLETE,
)

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


async def test_service_registered_and_removed(hass):
    entry = await _setup(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_RECONFIGURE)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, SERVICE_RECONFIGURE)


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
