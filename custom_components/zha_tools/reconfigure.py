"""Core reconfigure logic for the ZHA Tools integration.

This module orchestrates a single reconfigure attempt and the retry loop, and
maps the progress events emitted by ZHA onto the four reported statuses. It is
deliberately free of any direct ZHA imports: the actual device access goes
through :mod:`zha_adapter`, which keeps this logic easy to test with mocks.

Logging is intentionally verbose so it is clear from the Home Assistant log
what is being done and exactly what ZHA reports back for each binding and
configure-reporting step. Enable debug logging for the per-cluster / per-
attribute detail::

    logger:
      logs:
        custom_components.zha_tools: debug
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    SIGNAL_DEVICE_RECONFIGURE_EVENT,
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
    ZHA_ATTR_STATUS_SUCCESS,
    ZHA_EVENT_BIND,
    ZHA_EVENT_CFG_DONE,
    ZHA_EVENT_CFG_RPT,
    ZHA_EVENT_TYPE,
    ZHA_MSG_ATTR_STATUS,
    ZHA_MSG_ATTRIBUTES,
    ZHA_MSG_CLUSTER_ID,
    ZHA_MSG_CLUSTER_NAME,
    ZHA_MSG_DATA,
    ZHA_MSG_SUCCESS,
)

# Imported as module-level names so tests can patch them on this module.
from .zha_adapter import async_get_zha_device, async_trigger_reconfigure

_LOGGER = logging.getLogger(__name__)


class _ReconfigureCollector:
    """Collect ZHA reconfigure progress events for a single attempt.

    ZHA forwards binding and configure-reporting progress on the Home Assistant
    dispatcher. This collector records the per-cluster bind results and the
    per-attribute reporting results so the overall outcome can be decided once
    configuration has finished. Every event is logged as it arrives so the log
    shows exactly what ZHA reported back.
    """

    # The device id this collector is reporting for. Set in __init__.
    device_id: str | None = None
    # Per-cluster bind outcomes (True == bound successfully). Set in __init__.
    bind_results: list[bool] | None = None
    # Per-attribute configure-reporting outcomes (True == SUCCESS). Set in
    # __init__.
    report_results: list[bool] | None = None
    # Human-readable names of the bindings / reportings that failed, for
    # logging. Set in __init__.
    failed_binds: list[str] | None = None
    failed_reports: list[str] | None = None
    # Whether ZHA emitted its terminal "configuration done" event.
    done: bool = False

    def __init__(self, device_id: str) -> None:
        """Initialise the empty result collections.

        Args:
            device_id: The device the events relate to, used in log messages.
        """
        self.device_id = device_id
        self.bind_results = []
        self.report_results = []
        self.failed_binds = []
        self.failed_reports = []
        self.done = False

    @callback
    def handle_event(self, data: dict) -> None:
        """Handle a single reconfigure event forwarded by ZHA.

        Args:
            data: The event payload as forwarded on the dispatcher signal.
        """
        event_type = data[ZHA_EVENT_TYPE]
        if event_type == ZHA_EVENT_BIND:
            message = data[ZHA_MSG_DATA]
            success = bool(message[ZHA_MSG_SUCCESS])
            cluster = self._cluster_label(message)
            self.bind_results.append(success)
            if not success:
                self.failed_binds.append(cluster)
            _LOGGER.debug(
                "ZHA reconfigure of %s: bind of cluster %s %s",
                self.device_id,
                cluster,
                "succeeded" if success else "FAILED",
            )
        elif event_type == ZHA_EVENT_CFG_RPT:
            message = data[ZHA_MSG_DATA]
            cluster = self._cluster_label(message)
            for attr_name, attribute in message[ZHA_MSG_ATTRIBUTES].items():
                status = attribute[ZHA_MSG_ATTR_STATUS]
                success = status == ZHA_ATTR_STATUS_SUCCESS
                self.report_results.append(success)
                if not success:
                    self.failed_reports.append(f"{cluster}.{attr_name}")
                _LOGGER.debug(
                    "ZHA reconfigure of %s: configure-reporting of %s.%s "
                    "reported %s",
                    self.device_id,
                    cluster,
                    attr_name,
                    status,
                )
        elif event_type == ZHA_EVENT_CFG_DONE:
            self.done = True
            _LOGGER.debug(
                "ZHA reconfigure of %s: configuration done", self.device_id
            )
        # Other event types are not relevant for the outcome and are ignored.

    @staticmethod
    def _cluster_label(message: dict) -> str:
        """Return a readable cluster label from an event payload.

        The cluster name/id are descriptive only (used for logging), so missing
        fields fall back gracefully rather than failing the reconfigure.
        """
        if ZHA_MSG_CLUSTER_NAME in message:
            return str(message[ZHA_MSG_CLUSTER_NAME])
        if ZHA_MSG_CLUSTER_ID in message:
            return str(message[ZHA_MSG_CLUSTER_ID])
        return "unknown"

    @property
    def succeeded(self) -> bool:
        """Return whether the reconfigure counts as complete.

        Per the spec it is enough for at least one binding and at least one
        configure-reporting step to have been reported back as successful by
        ZHA; flaky devices where only some clusters succeed still count.
        """
        return any(self.bind_results) and any(self.report_results)

    def summary(self) -> str:
        """Return a one-line summary of what ZHA reported back."""
        binds_ok = sum(self.bind_results)
        reports_ok = sum(self.report_results)
        text = (
            f"{binds_ok}/{len(self.bind_results)} bindings ok, "
            f"{reports_ok}/{len(self.report_results)} reporting ok"
        )
        if self.failed_binds:
            text += f"; failed bindings: {', '.join(self.failed_binds)}"
        if self.failed_reports:
            text += f"; failed reporting: {', '.join(self.failed_reports)}"
        return text


async def _async_reconfigure_once(
    hass: HomeAssistant, device_id: str, timeout: float
) -> str:
    """Run a single reconfigure attempt and return its status.

    Args:
        hass: The Home Assistant instance.
        device_id: The device registry id of the ZHA device to reconfigure.
        timeout: Maximum number of seconds to wait for the attempt to finish.

    Returns:
        One of the ``STATUS_*`` constants describing the outcome.

    Raises:
        ServiceValidationError: If ``device_id`` is not a known ZHA device.
    """
    device = async_get_zha_device(hass, device_id)

    # A device that is already known to be offline cannot be reconfigured.
    if not device.available:
        _LOGGER.warning(
            "ZHA device %s is not available, reporting %s",
            device_id,
            STATUS_UNAVAILABLE,
        )
        return STATUS_UNAVAILABLE

    collector = _ReconfigureCollector(device_id)
    unsub = async_dispatcher_connect(
        hass, SIGNAL_DEVICE_RECONFIGURE_EVENT, collector.handle_event
    )
    _LOGGER.debug(
        "Triggering ZHA reconfigure of %s (timeout %ss)", device_id, timeout
    )
    try:
        await asyncio.wait_for(async_trigger_reconfigure(device), timeout)
    except TimeoutError:
        # The device may have dropped off the network mid-attempt; tell those
        # two cases apart so automations can react appropriately.
        if not device.available:
            _LOGGER.warning(
                "ZHA reconfigure of %s timed out after %ss and the device is "
                "no longer available, reporting %s (%s)",
                device_id,
                timeout,
                STATUS_UNAVAILABLE,
                collector.summary(),
            )
            return STATUS_UNAVAILABLE
        _LOGGER.warning(
            "ZHA reconfigure of %s did not finish within %ss, reporting %s "
            "(progress so far: %s)",
            device_id,
            timeout,
            STATUS_TIMEOUT,
            collector.summary(),
        )
        return STATUS_TIMEOUT
    finally:
        unsub()

    if not device.available:
        _LOGGER.warning(
            "ZHA reconfigure of %s finished but the device is no longer "
            "available, reporting %s (%s)",
            device_id,
            STATUS_UNAVAILABLE,
            collector.summary(),
        )
        return STATUS_UNAVAILABLE

    if collector.succeeded:
        _LOGGER.info(
            "ZHA reconfigure of %s finished: %s, reporting %s",
            device_id,
            collector.summary(),
            STATUS_COMPLETE,
        )
        return STATUS_COMPLETE

    _LOGGER.info(
        "ZHA reconfigure of %s finished without a successful binding and "
        "reporting: %s, reporting %s",
        device_id,
        collector.summary(),
        STATUS_INCOMPLETE,
    )
    return STATUS_INCOMPLETE


async def async_reconfigure_device(
    hass: HomeAssistant,
    device_id: str,
    timeout: float,
    max_retries: int,
    retry_delay: float,
) -> dict:
    """Reconfigure a ZHA device, retrying until it succeeds or attempts run out.

    The device is reconfigured once, then retried up to ``max_retries`` more
    times as long as the result is anything other than ``complete``, waiting
    ``retry_delay`` seconds between attempts.

    Args:
        hass: The Home Assistant instance.
        device_id: The device registry id of the ZHA device to reconfigure.
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
        "Reconfiguring ZHA device %s (timeout=%ss, max_retries=%s, "
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
            "ZHA reconfigure of %s: starting attempt %d of %d",
            device_id,
            attempts,
            max_attempts,
        )
        status = await _async_reconfigure_once(hass, device_id, timeout)
        _LOGGER.info(
            "ZHA reconfigure of %s: attempt %d of %d returned %s",
            device_id,
            attempts,
            max_attempts,
            status,
        )
        if status == STATUS_COMPLETE or attempts > max_retries:
            break
        _LOGGER.debug(
            "ZHA reconfigure of %s: status %s, retrying in %ss",
            device_id,
            status,
            retry_delay,
        )
        await asyncio.sleep(retry_delay)

    log = _LOGGER.info if status == STATUS_COMPLETE else _LOGGER.warning
    log(
        "ZHA reconfigure of %s finished after %d attempt(s) with status %s",
        device_id,
        attempts,
        status,
    )
    return {"device_id": device_id, "status": status, "attempts": attempts}
