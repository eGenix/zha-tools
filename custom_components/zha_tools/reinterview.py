"""Core re-interview logic for the ZHA Tools integration.

Re-interviews a ZHA device *in place* -- rediscovering its node descriptor,
endpoints and clusters -- without removing it from the Zigbee network. This is
the safe counterpart to :mod:`rejoin`: because the device never leaves, a
re-interview cannot strand a device that is hard to re-pair manually. The device
only needs to be reachable; the attempt is retried, like a reconfigure, until it
succeeds or the attempts run out.

Errors raised by the underlying ZHA / zigpy call are deliberately not caught and
mapped to a status -- they propagate so the caller sees the real failure rather
than a silently swallowed one. Only a per-attempt timeout and an unreachable
device are turned into reported statuses.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant

from .const import STATUS_COMPLETE, STATUS_TIMEOUT, STATUS_UNAVAILABLE

# Imported as module-level names so tests can patch them on this module.
from .zha_adapter import async_get_zha_device, async_trigger_reinterview

_LOGGER = logging.getLogger(__name__)


async def _async_reinterview_once(
    hass: HomeAssistant, device_id: str, timeout: float
) -> str:
    """Run a single re-interview attempt and return its status.

    Args:
        hass: The Home Assistant instance.
        device_id: The device registry id of the ZHA device to re-interview.
        timeout: Maximum number of seconds to wait for the attempt to finish.

    Returns:
        One of ``STATUS_COMPLETE``, ``STATUS_TIMEOUT`` or ``STATUS_UNAVAILABLE``.

    Raises:
        ServiceValidationError: If ``device_id`` is not a known ZHA device.
    """
    device = async_get_zha_device(hass, device_id)

    # A re-interview talks to the device, so an offline device cannot be done.
    if not device.available:
        _LOGGER.warning(
            "ZHA device %s is not available, reporting %s",
            device_id,
            STATUS_UNAVAILABLE,
        )
        return STATUS_UNAVAILABLE

    _LOGGER.debug(
        "Triggering ZHA re-interview of %s (timeout %ss)", device_id, timeout
    )
    try:
        await asyncio.wait_for(async_trigger_reinterview(device), timeout)
    except TimeoutError:
        _LOGGER.warning(
            "ZHA re-interview of %s did not finish within %ss, reporting %s",
            device_id,
            timeout,
            STATUS_TIMEOUT,
        )
        return STATUS_TIMEOUT

    _LOGGER.info(
        "ZHA re-interview of %s finished, reporting %s", device_id, STATUS_COMPLETE
    )
    return STATUS_COMPLETE


async def async_reinterview_device(
    hass: HomeAssistant,
    device_id: str,
    timeout: float,
    max_retries: int,
    retry_delay: float,
) -> dict:
    """Re-interview a ZHA device, retrying until it succeeds or attempts run out.

    The device is re-interviewed once, then retried up to ``max_retries`` more
    times as long as the result is anything other than ``complete``, waiting
    ``retry_delay`` seconds between attempts.

    Args:
        hass: The Home Assistant instance.
        device_id: The device registry id of the ZHA device to re-interview.
        timeout: Per-attempt timeout in seconds.
        max_retries: Maximum number of retries after the first attempt.
        retry_delay: Seconds to wait between attempts.

    Returns:
        A mapping with the final ``status`` and the number of ``attempts`` made.

    Raises:
        ServiceValidationError: If ``device_id`` is not a known ZHA device.
    """
    max_attempts = max_retries + 1
    _LOGGER.info(
        "Re-interviewing ZHA device %s (timeout=%ss, max_retries=%s, "
        "retry_delay=%ss)",
        device_id,
        timeout,
        max_retries,
        retry_delay,
    )

    attempts = 0
    status = STATUS_UNAVAILABLE
    while True:
        attempts += 1
        _LOGGER.debug(
            "ZHA re-interview of %s: starting attempt %d of %d",
            device_id,
            attempts,
            max_attempts,
        )
        status = await _async_reinterview_once(hass, device_id, timeout)
        _LOGGER.info(
            "ZHA re-interview of %s: attempt %d of %d returned %s",
            device_id,
            attempts,
            max_attempts,
            status,
        )
        if status == STATUS_COMPLETE or attempts > max_retries:
            break
        _LOGGER.debug(
            "ZHA re-interview of %s: status %s, retrying in %ss",
            device_id,
            status,
            retry_delay,
        )
        await asyncio.sleep(retry_delay)

    log = _LOGGER.info if status == STATUS_COMPLETE else _LOGGER.warning
    log(
        "ZHA re-interview of %s finished after %d attempt(s) with status %s",
        device_id,
        attempts,
        status,
    )
    return {"device_id": device_id, "status": status, "attempts": attempts}
