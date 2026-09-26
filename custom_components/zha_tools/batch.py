"""Run a ZHA Tools action across all configured ZHA devices.

The reconfigure, re-interview and ping actions can be pointed at every device
ZHA manages instead of at a single one. This module holds the shared machinery for
that: it looks up the devices (all of them except the coordinator), works
through them one at a time -- the next device is only started once the previous
one has finished, successfully or not -- and returns a per-device result list
plus a short summary.

Only one batch run may be active at a time. A batch can keep a Zigbee network
busy for a long while, and two of them talking to the same devices in parallel
would only make the results worse, so a second batched call is refused up front
rather than queued.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .const import DATA_BATCH_RUNNING, DOMAIN, STATUS_COMPLETE, STATUS_ERROR

# Imported as a module-level name so tests can patch it on this module.
from .zha_adapter import async_get_zha_device_ids

_LOGGER = logging.getLogger(__name__)

# A single-device action: ``(hass, device_id, **params) -> result mapping``.
# ``async_reconfigure_device``, ``async_reinterview_device`` and
# ``async_ping_device`` all match.
DeviceAction = Callable[..., Awaitable[dict]]


@contextmanager
def _only_one_batch(hass: HomeAssistant, action_name: str) -> Iterator[None]:
    """Mark a batch run as active for the duration of the block.

    Args:
        hass: The Home Assistant instance.
        action_name: Name of the action being run, used in messages.

    Yields:
        Nothing; the block runs as the one active batch.

    Raises:
        ServiceValidationError: If another batch run is already active.
    """
    data = hass.data.setdefault(DOMAIN, {})
    if DATA_BATCH_RUNNING in data:
        running = data[DATA_BATCH_RUNNING]
        _LOGGER.warning(
            "Refusing to run %s on all devices: a batched %s run is still in "
            "progress",
            action_name,
            running,
        )
        raise ServiceValidationError(
            f"A batched {DOMAIN}.{running} run is still in progress; only one "
            f"batched action can run at a time"
        )
    data[DATA_BATCH_RUNNING] = action_name
    try:
        yield
    finally:
        del data[DATA_BATCH_RUNNING]


async def async_run_on_all_devices(
    hass: HomeAssistant,
    action: DeviceAction,
    action_name: str,
    **params: object,
) -> dict:
    """Run a single-device action on every ZHA device except the coordinator.

    The devices are processed one after another: the next device is only
    started once the previous one has finished, no matter how it turned out.
    An error raised for one device does not abort the run either -- it is
    logged with its traceback and reported as that device's result, so the
    remaining devices still get their turn.

    Args:
        hass: The Home Assistant instance.
        action: The single-device coroutine function to run, called as
            ``action(hass, device_id, **params)``.
        action_name: Name of the action, used in log and error messages.
        **params: Extra keyword arguments passed on to ``action``.

    Returns:
        A mapping with the per-device results in ``devices`` and the counts
        ``total``, ``complete`` and ``failed``.

    Raises:
        ServiceValidationError: If another batch run is already active, or if
            the ZHA integration is not set up.
    """
    with _only_one_batch(hass, action_name):
        device_ids = async_get_zha_device_ids(hass)
        total = len(device_ids)
        _LOGGER.info(
            "Running %s on all %d ZHA device(s) except the coordinator",
            action_name,
            total,
        )

        results = []
        for number, device_id in enumerate(device_ids, start=1):
            _LOGGER.info(
                "Batched %s: device %d of %d (%s)",
                action_name,
                number,
                total,
                device_id,
            )
            try:
                result = await action(hass, device_id, **params)
            except Exception as err:
                # The whole point of a batch run is that one bad device does
                # not stop the rest. The error is not swallowed: it is logged
                # with its traceback and handed back to the caller as this
                # device's result.
                _LOGGER.exception(
                    "Batched %s: device %s (%d of %d) raised an error",
                    action_name,
                    device_id,
                    number,
                    total,
                )
                result = {
                    "device_id": device_id,
                    "status": STATUS_ERROR,
                    "error": str(err),
                }
            results.append(result)

        complete = sum(1 for result in results if result["status"] == STATUS_COMPLETE)
        failed = total - complete
        log = _LOGGER.info if failed == 0 else _LOGGER.warning
        log(
            "Batched %s finished: %d of %d device(s) complete, %d not complete",
            action_name,
            complete,
            total,
            failed,
        )
        return {
            "devices": results,
            "total": total,
            "complete": complete,
            "failed": failed,
        }
