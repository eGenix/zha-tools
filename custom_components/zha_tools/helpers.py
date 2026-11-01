"""Small helpers shared by the ZHA Tools actions."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr


def _display_name(device: dr.DeviceEntry | None) -> str | None:
    """Return the display name of a device registry entry, or ``None``.

    The display name is the name given by the user, or else the device name.
    """
    if device is None:
        return None
    return device.name_by_user or device.name or None


def device_name(hass: HomeAssistant, device_id: str) -> str | None:
    """Return the display name of a device in Home Assistant.

    Args:
        hass: The Home Assistant instance.
        device_id: The device registry id of the device.

    Returns:
        The name given by the user, or else the device name, or ``None`` if the
        device is not in the registry or has no name.
    """
    return _display_name(dr.async_get(hass).async_get(device_id))


def device_name_by_ieee(hass: HomeAssistant, ieee: str) -> str | None:
    """Return the display name of a Zigbee device, looked up by IEEE address.

    ZHA registers each of its devices, including the coordinator, with a
    ``zigbee`` connection carrying the device's IEEE address.

    Args:
        hass: The Home Assistant instance.
        ieee: The device's IEEE address, as a ``xx:xx:...`` string.

    Returns:
        The device's display name, or ``None`` if Home Assistant does not know
        the device or it has no name.
    """
    device = dr.async_get(hass).async_get_device(
        connections={(dr.CONNECTION_ZIGBEE, ieee)}
    )
    return _display_name(device)


def device_label(hass: HomeAssistant, device_id: str) -> str:
    """Return a human-readable label for a device, for use in log messages.

    The label is the device's display name in Home Assistant (the name given
    by the user, or else the device name) followed by its device id, so log
    entries can be matched up with the device both by eye and by id.

    Args:
        hass: The Home Assistant instance.
        device_id: The device registry id of the device.

    Returns:
        ``"<display name> (<device_id>)"``, or just the ``device_id`` if the
        device is not in the registry or has no name.
    """
    name = device_name(hass, device_id)
    if not name:
        return device_id
    return f"{name} ({device_id})"


def iso_timestamp(timestamp: float | None) -> str | None:
    """Return a POSIX timestamp as an ISO 8601 UTC string, or ``None``.

    Args:
        timestamp: Seconds since the epoch, or ``None`` (e.g. a device that was
            never seen).

    Returns:
        The ISO 8601 representation, or ``None`` if ``timestamp`` is ``None``.
    """
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, UTC).isoformat()
