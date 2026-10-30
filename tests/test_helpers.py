"""Tests for the device naming helper and its use in log messages."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.helpers import device_registry as dr

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zha_tools.batch import async_run_on_all_devices
from custom_components.zha_tools.const import DOMAIN, STATUS_TIMEOUT
from custom_components.zha_tools.helpers import device_label
from custom_components.zha_tools.ping import async_ping_device


def _register_device(hass, name, name_by_user=None):
    """Add a device to the device registry and return its id."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("zha", "00:11:22:33:44:55:66:77")},
        name=name,
    )
    if name_by_user is not None:
        device = registry.async_update_device(device.id, name_by_user=name_by_user)
    return device.id


async def test_label_uses_registry_name(hass):
    device_id = _register_device(hass, "IKEA bulb")
    assert device_label(hass, device_id) == f"IKEA bulb ({device_id})"


async def test_label_prefers_user_name(hass):
    device_id = _register_device(hass, "IKEA bulb", name_by_user="Kitchen light")
    assert device_label(hass, device_id) == f"Kitchen light ({device_id})"


async def test_label_falls_back_to_device_id(hass):
    assert device_label(hass, "unknown") == "unknown"


async def test_timeout_log_includes_device_name(hass, caplog):
    device_id = _register_device(hass, "Kitchen light")

    async def trigger(dev, timeout):
        raise TimeoutError

    with (
        patch(
            "custom_components.zha_tools.ping.async_get_zha_device",
            return_value=SimpleNamespace(available=True),
        ),
        patch("custom_components.zha_tools.ping.async_trigger_ping", new=trigger),
        caplog.at_level(logging.WARNING),
    ):
        result = await async_ping_device(
            hass, device_id, timeout=1, max_retries=0, retry_delay=0
        )

    assert result["status"] == STATUS_TIMEOUT
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings
    assert all("Kitchen light" in r.getMessage() for r in warnings)


async def test_batch_error_log_includes_device_name(hass, caplog):
    device_id = _register_device(hass, "Kitchen light")

    async def action(hass_, device_id_, **params):
        raise RuntimeError("boom")

    with (
        patch(
            "custom_components.zha_tools.batch.async_get_zha_device_ids",
            return_value=[device_id],
        ),
        caplog.at_level(logging.ERROR),
    ):
        await async_run_on_all_devices(hass, action, "ping")

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "Kitchen light" in errors[0].getMessage()
