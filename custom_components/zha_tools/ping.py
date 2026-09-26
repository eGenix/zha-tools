"""Core ping logic for the ZHA Tools integration.

Checks whether a ZHA device can be reached, and tries to re-establish contact
when it cannot. Each attempt reads an attribute from the device, bypassing all
caches. Unlike the other actions, a ping is also sent to a device ZHA currently
marks unavailable: once ZHA has given up on a device it stops contacting it and
waits for the device to transmit on its own, so an active ping is what brings
such a device back. A reply updates the device's ``last_seen``, and ZHA's own
availability checker then marks the device available again, usually within a
minute.

After the first failed attempt, the device's network (NWK) address is looked up
once by its IEEE address, so a device which rejoined under a new address without
the coordinator noticing can be found again. If the radio fails to deliver a
ping, zigpy resends it with a forced route discovery.

Errors raised by the underlying ZHA / zigpy calls are deliberately not caught
and mapped to a status -- they propagate so the caller sees the real failure
rather than a silently swallowed one. Only a missing reply and an undeliverable
request are turned into reported statuses.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant

from .const import STATUS_COMPLETE, STATUS_TIMEOUT, STATUS_UNAVAILABLE

# Imported as module-level names so tests can patch them on this module.
from .zha_adapter import (
    DeviceUnreachableError,
    async_get_zha_device,
    async_trigger_address_refresh,
    async_trigger_ping,
)

_LOGGER = logging.getLogger(__name__)


async def _async_ping_once(hass: HomeAssistant, device_id: str, timeout: float) -> str:
    """Run a single ping attempt and return its status.

    Args:
        hass: The Home Assistant instance.
        device_id: The device registry id of the ZHA device to ping.
        timeout: Maximum number of seconds to wait for the reply.

    Returns:
        One of ``STATUS_COMPLETE``, ``STATUS_TIMEOUT`` or ``STATUS_UNAVAILABLE``.

    Raises:
        ServiceValidationError: If ``device_id`` is not a known ZHA device.
    """
    device = async_get_zha_device(hass, device_id)
    was_available = device.available

    _LOGGER.debug(
        "Pinging ZHA device %s (timeout %ss, ZHA marks it %s)",
        device_id,
        timeout,
        "available" if was_available else "unavailable",
    )
    try:
        await asyncio.wait_for(async_trigger_ping(device, timeout), timeout)
    except TimeoutError:
        _LOGGER.warning(
            "ZHA device %s did not answer the ping within %ss, reporting %s",
            device_id,
            timeout,
            STATUS_TIMEOUT,
        )
        return STATUS_TIMEOUT
    except DeviceUnreachableError as err:
        _LOGGER.warning(
            "Ping to ZHA device %s could not be delivered (%s), reporting %s",
            device_id,
            err,
            STATUS_UNAVAILABLE,
        )
        return STATUS_UNAVAILABLE

    if was_available:
        _LOGGER.info("ZHA device %s answered the ping", device_id)
    else:
        _LOGGER.info(
            "ZHA device %s answered the ping; ZHA will mark it available again "
            "at its next availability check",
            device_id,
        )
    return STATUS_COMPLETE


async def _async_refresh_address(hass: HomeAssistant, device_id: str) -> None:
    """Ask the network for the current NWK address of a ZHA device.

    Args:
        hass: The Home Assistant instance.
        device_id: The device registry id of the ZHA device.

    Raises:
        ServiceValidationError: If ``device_id`` is not a known ZHA device.
    """
    device = async_get_zha_device(hass, device_id)
    _LOGGER.debug("Looking up the current network address of ZHA device %s", device_id)
    await async_trigger_address_refresh(device)


async def async_ping_device(
    hass: HomeAssistant,
    device_id: str,
    timeout: float,
    max_retries: int,
    retry_delay: float,
) -> dict:
    """Ping a ZHA device, retrying until it answers or attempts run out.

    The device is pinged once, then retried up to ``max_retries`` more times as
    long as the result is anything other than ``complete``, waiting
    ``retry_delay`` seconds between attempts. After the first failed attempt,
    the device's network address is looked up once, before the retry delay.

    Args:
        hass: The Home Assistant instance.
        device_id: The device registry id of the ZHA device to ping.
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
        "Pinging ZHA device %s (timeout=%ss, max_retries=%s, retry_delay=%ss)",
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
            "Ping of %s: starting attempt %d of %d",
            device_id,
            attempts,
            max_attempts,
        )
        status = await _async_ping_once(hass, device_id, timeout)
        _LOGGER.info(
            "Ping of %s: attempt %d of %d returned %s",
            device_id,
            attempts,
            max_attempts,
            status,
        )
        if status == STATUS_COMPLETE or attempts > max_retries:
            break
        if attempts == 1:
            # Sent before the retry delay, so the answer has time to arrive
            # before the next attempt.
            await _async_refresh_address(hass, device_id)
        _LOGGER.debug(
            "Ping of %s: status %s, retrying in %ss",
            device_id,
            status,
            retry_delay,
        )
        await asyncio.sleep(retry_delay)

    log = _LOGGER.info if status == STATUS_COMPLETE else _LOGGER.warning
    log(
        "Ping of %s finished after %d attempt(s) with status %s",
        device_id,
        attempts,
        status,
    )
    return {"device_id": device_id, "status": status, "attempts": attempts}
