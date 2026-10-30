"""Small helpers shared by the ZHA Tools actions."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr


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
    device = dr.async_get(hass).async_get(device_id)
    name = device and (device.name_by_user or device.name)
    if not name:
        return device_id
    return f"{name} ({device_id})"
