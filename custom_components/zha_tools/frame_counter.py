"""Core frame counter logic for the ZHA Tools integration.

Reads the incoming network (NWK) frame counter which the coordinator stores for
a ZHA device. This helps track down the devices behind rising
``NWK_FRAME_COUNTER_FAILURE`` (and, indirectly, ``NWK_DECRYPTION_FAILURE``)
counts on the coordinator, which the radio firmware only reports as totals.

NWK security is applied hop by hop, so the coordinator only keeps a frame
counter for its direct neighbors; every other device is reported as
``not_neighbor``. The counter is the highest one the coordinator has accepted
from the device so far. A device that keeps transmitting (its ``last_seen``
moves on) while this counter stays the same is having its frames dropped --
typically because it reset its own outgoing counter, e.g. after a power cut or
firmware update.

The read only queries the coordinator's radio; nothing is sent over the air.
It is a single, quick read, so it is not retried. Frame counter reads are never
logged -- neither the values nor the fact that a read took place; the result is
only returned to the caller. Errors raised by the underlying calls are not
caught and mapped to a status -- they propagate so the caller sees the real
failure.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import STATUS_COMPLETE, STATUS_NOT_NEIGHBOR
from .helpers import device_name, iso_timestamp

# Imported as module-level names so tests can patch them on this module.
from .zha_adapter import async_get_neighbor_frame_counter, async_get_zha_device


async def async_read_frame_counter(hass: HomeAssistant, device_id: str) -> dict:
    """Read the coordinator's incoming NWK frame counter for a ZHA device.

    Args:
        hass: The Home Assistant instance.
        device_id: The device registry id of the ZHA device.

    Returns:
        A mapping with ``device_id``, the device's display name in
        ``device_name``, the device's ``ieee`` address, the
        ``status`` (``complete``, or ``not_neighbor`` if the coordinator stores
        no counter for the device), the ``frame_counter`` (``None`` for
        non-neighbors) and ``last_seen``, the time the coordinator last heard
        from the device as an ISO 8601 UTC timestamp (``None`` if never).

    Raises:
        ServiceValidationError: If ``device_id`` is not a known ZHA device.
        HomeAssistantError: If the radio cannot report frame counters.
    """
    # Deliberately nothing is logged here (see the module docs).
    device = async_get_zha_device(hass, device_id, quiet=True)
    frame_counter = await async_get_neighbor_frame_counter(device)
    status = STATUS_NOT_NEIGHBOR if frame_counter is None else STATUS_COMPLETE
    return {
        "device_id": device_id,
        "device_name": device_name(hass, device_id),
        "ieee": str(device.ieee),
        "status": status,
        "frame_counter": frame_counter,
        "last_seen": iso_timestamp(device.last_seen),
    }
