"""Thin adapter around the ZHA integration internals.

All access to ZHA / zigpy lives here so that the rest of the integration stays
decoupled from ZHA's non-public API (which changes between Home Assistant
releases) and so the heavy ZHA imports are only performed when actually needed.
This module is the single place tests need to mock to exercise the integration
without a running Zigbee network.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def async_get_zha_device(hass: HomeAssistant, device_id: str) -> object:
    """Return the ZHA device object for a Home Assistant ``device_id``.

    Args:
        hass: The Home Assistant instance.
        device_id: A device registry id which must belong to a ZHA device.

    Returns:
        The underlying ZHA device object. It exposes at least an ``available``
        property and an awaitable ``async_configure()`` method.

    Raises:
        ServiceValidationError: If the id is unknown or not a ZHA device.
    """
    # Imported lazily: pulling in ZHA drags in zigpy and the whole Zigbee stack,
    # which we want to avoid unless a reconfigure is actually requested.
    from homeassistant.components.zha.helpers import async_get_zha_device_proxy

    try:
        proxy = async_get_zha_device_proxy(hass, device_id)
    except (KeyError, ValueError, AttributeError) as err:
        raise ServiceValidationError(
            f"Device {device_id!r} is not a known {DOMAIN} target ZHA device"
        ) from err
    _LOGGER.debug(
        "Resolved device %s to ZHA device %s (available=%s)",
        device_id,
        getattr(proxy.device, "name", proxy.device),
        getattr(proxy.device, "available", "unknown"),
    )
    return proxy.device


async def async_trigger_reconfigure(device: object) -> None:
    """Trigger a reconfigure on a ZHA device.

    This runs the same binding and configure-reporting steps as the
    "Reconfigure device" button in the ZHA UI. Progress is reported by ZHA on
    the Home Assistant dispatcher (see ``SIGNAL_DEVICE_RECONFIGURE_EVENT``); the
    awaited call returns once configuration has finished. Per-cluster transport
    failures (e.g. an unresponsive device) are reported via those events rather
    than raised.

    Args:
        device: A ZHA device object as returned by ``async_get_zha_device``.
    """
    await device.async_configure()


async def async_trigger_reinterview(device: object) -> None:
    """Re-interview a ZHA device in place (rediscover endpoints and clusters).

    Goes through ZHA's gateway, which re-runs the zigpy device interview behind a
    shadow device and swaps the refreshed device in on success. The device is
    *not* removed from the network, so this cannot strand it.

    Args:
        device: A ZHA device object as returned by ``async_get_zha_device``.
    """
    await device.gateway.async_reinterview_device(device.ieee)


async def async_trigger_rejoin(device: object) -> None:
    """Ask a ZHA device to leave the network and immediately rejoin it.

    Sends a ZDO leave request with the rejoin flag set, via the zigpy controller
    application directly -- ZHA's own ``async_remove_device`` removes a device
    without setting the rejoin flag, which would make it leave permanently. The
    call returns once the request has been sent; whether the device actually
    rejoins is up to its firmware.

    Args:
        device: A ZHA device object as returned by ``async_get_zha_device``.
    """
    await device.gateway.application_controller.remove(device.ieee, rejoin=True)
