"""Core rejoin (leave-with-rejoin) logic for the ZHA Tools integration.

Asks a ZHA device to leave the Zigbee network and immediately rejoin it, by
sending a ZDO leave request with the *rejoin* flag set. This is a remote
substitute for physically pressing a device's pairing button -- useful for
installed devices whose button is hard to reach.

DANGER: this is a one-shot, potentially destructive operation. If the device's
firmware does not honour the rejoin flag it will leave the network and NOT come
back, and will then need a manual re-pair. For that reason the rejoin is:

* never retried (a second leave would only make matters worse);
* only ever sent to a device that is currently reachable; and
* logged at warning level before it is sent.

The outcome cannot be confirmed reliably: the call returns once the leave has
been sent, and -- if asked to wait -- reports ``complete`` only if the device is
reachable again afterwards, otherwise ``requested`` (it may still be rejoining,
or it may be gone).
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .const import STATUS_COMPLETE, STATUS_REQUESTED, STATUS_UNAVAILABLE

# Imported as module-level names so tests can patch them on this module.
from .zha_adapter import async_get_zha_device, async_trigger_rejoin

_LOGGER = logging.getLogger(__name__)


async def async_rejoin_device(
    hass: HomeAssistant, device_id: str, rejoin_wait: float, confirm: bool
) -> dict:
    """Ask a ZHA device to leave the network and immediately rejoin it.

    Args:
        hass: The Home Assistant instance.
        device_id: The device registry id of the ZHA device to rejoin.
        rejoin_wait: Seconds to wait for the device to come back before
            reporting. ``0`` returns immediately after sending the request
            without checking the outcome.
        confirm: Safety acknowledgement. Must be ``True``; the action refuses to
            do anything otherwise, so a mistyped automation cannot trigger a
            potentially device-stranding rejoin by accident.

    Returns:
        A mapping with ``device_id`` and the resulting ``status``:

        * ``unavailable`` -- the device was offline, so no leave was sent;
        * ``requested`` -- the leave-with-rejoin was sent but the device has not
          been confirmed back (it may still be rejoining, or may be gone);
        * ``complete`` -- the device was reachable again within ``rejoin_wait``.

    Raises:
        ServiceValidationError: If ``confirm`` is not ``True``, or if
            ``device_id`` is not a known ZHA device.
    """
    # Refuse before touching the device: this is the safety gate that keeps an
    # accidental call from sending a leave request.
    if confirm is not True:
        raise ServiceValidationError(
            f"Refusing to rejoin device {device_id!r}: set 'confirm' to true to "
            "acknowledge that the device may leave the network and need a manual "
            "re-pair."
        )

    device = async_get_zha_device(hass, device_id)

    # A leave request can only be delivered to a device that is currently on the
    # network; sending one to an offline device would do nothing.
    if not device.available:
        _LOGGER.warning(
            "ZHA device %s is not available; not sending a leave-with-rejoin, "
            "reporting %s",
            device_id,
            STATUS_UNAVAILABLE,
        )
        return {"device_id": device_id, "status": STATUS_UNAVAILABLE}

    _LOGGER.warning(
        "Sending leave-with-rejoin to ZHA device %s. If the device firmware "
        "ignores the rejoin flag it will leave the network and not return, "
        "requiring a manual re-pair.",
        device_id,
    )
    await async_trigger_rejoin(device)

    if rejoin_wait <= 0:
        _LOGGER.info(
            "Leave-with-rejoin sent to %s; not waiting for it to return, "
            "reporting %s",
            device_id,
            STATUS_REQUESTED,
        )
        return {"device_id": device_id, "status": STATUS_REQUESTED}

    _LOGGER.debug("Waiting up to %ss for %s to rejoin", rejoin_wait, device_id)
    await asyncio.sleep(rejoin_wait)
    if device.available:
        _LOGGER.info(
            "ZHA device %s is reachable again after the rejoin, reporting %s",
            device_id,
            STATUS_COMPLETE,
        )
        return {"device_id": device_id, "status": STATUS_COMPLETE}

    _LOGGER.warning(
        "ZHA device %s has not returned within %ss; it may still be rejoining or "
        "may need a manual re-pair, reporting %s",
        device_id,
        rejoin_wait,
        STATUS_REQUESTED,
    )
    return {"device_id": device_id, "status": STATUS_REQUESTED}
