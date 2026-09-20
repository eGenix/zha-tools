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


def selected_device_ids(device_proxies: list) -> list[str]:
    """Return the Home Assistant device ids of the given ZHA device proxies.

    The coordinator is left out: it is the radio itself, not a device that can
    be reconfigured or re-interviewed. The ids are returned sorted by device
    name so a batch run always works through the devices in the same,
    predictable order.

    Args:
        device_proxies: ZHA device proxies, each exposing a ``device_id`` and
            the underlying ZHA ``device``.

    Returns:
        The device registry ids of all non-coordinator devices.
    """
    proxies = [proxy for proxy in device_proxies if not proxy.device.is_coordinator]
    proxies.sort(key=lambda proxy: (str(proxy.device.name), str(proxy.device.ieee)))
    return [proxy.device_id for proxy in proxies]


def async_get_zha_device_ids(hass: HomeAssistant) -> list[str]:
    """Return the device ids of all ZHA devices except the coordinator.

    Args:
        hass: The Home Assistant instance.

    Returns:
        The device registry ids of every device managed by ZHA, minus the
        coordinator, sorted by device name.

    Raises:
        ServiceValidationError: If the ZHA integration is not set up.
    """
    # Imported lazily, for the same reason as in ``async_get_zha_device``.
    from homeassistant.components.zha.helpers import get_zha_gateway_proxy

    try:
        gateway_proxy = get_zha_gateway_proxy(hass)
    except ValueError as err:
        raise ServiceValidationError(
            "The ZHA integration is not set up, so its devices cannot be listed"
        ) from err

    device_ids = selected_device_ids(list(gateway_proxy.device_proxies.values()))
    _LOGGER.debug(
        "Found %d ZHA device(s) excluding the coordinator: %s",
        len(device_ids),
        ", ".join(device_ids),
    )
    return device_ids


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
