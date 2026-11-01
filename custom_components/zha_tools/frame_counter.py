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
It is a single, quick read, so it is not retried. The counter value itself is
only returned to the caller, never written to the log. The log entry for a read
is a warning when the counter is above ``FRAME_COUNTER_WARNING_THRESHOLD`` and an
error above ``FRAME_COUNTER_ERROR_THRESHOLD``, so high counters still stand out.
Errors raised by the underlying calls are not caught and mapped to a status --
they propagate so the caller sees the real failure.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .const import (
    FRAME_COUNTER_ERROR_THRESHOLD,
    FRAME_COUNTER_WARNING_THRESHOLD,
    STATUS_COMPLETE,
    STATUS_NOT_NEIGHBOR,
)
from .helpers import device_label, device_name, iso_timestamp

# Imported as module-level names so tests can patch them on this module.
from .zha_adapter import async_get_neighbor_frame_counter, async_get_zha_device

_LOGGER = logging.getLogger(__name__)


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
    name = device_label(hass, device_id)
    device = async_get_zha_device(hass, device_id)
    frame_counter = await async_get_neighbor_frame_counter(device)
    last_seen = iso_timestamp(device.last_seen)

    if frame_counter is None:
        status = STATUS_NOT_NEIGHBOR
        _LOGGER.info(
            "ZHA device %s (%s) is not a neighbor of the coordinator; no frame "
            "counter stored, reporting %s",
            name,
            device.ieee,
            status,
        )
    else:
        status = STATUS_COMPLETE
        # The value is deliberately left out of the log; only the log level
        # tells how high it is.
        if frame_counter > FRAME_COUNTER_ERROR_THRESHOLD:
            log, level = _LOGGER.error, f"above {FRAME_COUNTER_ERROR_THRESHOLD}"
        elif frame_counter > FRAME_COUNTER_WARNING_THRESHOLD:
            log, level = _LOGGER.warning, f"above {FRAME_COUNTER_WARNING_THRESHOLD}"
        else:
            log, level = _LOGGER.info, "normal"
        log(
            "ZHA device %s (%s): incoming NWK frame counter read (%s), last seen %s",
            name,
            device.ieee,
            level,
            last_seen,
        )
    return {
        "device_id": device_id,
        "device_name": device_name(hass, device_id),
        "ieee": str(device.ieee),
        "status": status,
        "frame_counter": frame_counter,
        "last_seen": last_seen,
    }
