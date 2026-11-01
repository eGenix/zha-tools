"""The ZHA Tools integration.

Provides a small collection of ZHA helper actions that automations can call:

* ``zha_tools.reconfigure`` -- run a ZHA device reconfigure, with retries;
* ``zha_tools.reinterview`` -- re-interview a device in place, with retries;
* ``zha_tools.rejoin`` -- ask a device to leave and immediately rejoin
  (a remote substitute for the pairing button -- see the danger notes);
* ``zha_tools.ping`` -- check that a device answers, with retries, which also
  brings back devices ZHA has given up on;
* ``zha_tools.frame_counter`` -- read the incoming network frame counter the
  coordinator stores for a device, to track down frame counter failures;
* ``zha_tools.query_route`` -- report the known routes between the coordinator
  and a device, with each hop named;
* ``zha_tools.retry_action`` -- call any action, e.g. ``cover.close_cover``,
  repeatedly with a slow exponential backoff until it goes through.

Each action returns the resulting status to the caller. All device actions
except rejoin can either work on a single ``device_id`` or, with ``all_devices: true``,
on every ZHA device except the coordinator.
"""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .batch import DeviceAction, async_run_on_all_devices
from .const import (
    ATTR_DEVICE_ID,
    CONF_ACTION,
    CONF_ALL_DEVICES,
    CONF_ATTEMPT_TIMEOUT,
    CONF_BACKOFF,
    CONF_CONFIRM,
    CONF_DATA,
    CONF_MAX_DELAY,
    CONF_MAX_RETRIES,
    CONF_REJOIN_WAIT,
    CONF_RETRY_DELAY,
    CONF_TARGET,
    CONF_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_REJOIN_WAIT,
    DEFAULT_RETRY_ACTION_ATTEMPT_TIMEOUT,
    DEFAULT_RETRY_ACTION_BACKOFF,
    DEFAULT_RETRY_ACTION_DELAY,
    DEFAULT_RETRY_ACTION_MAX_DELAY,
    DEFAULT_RETRY_ACTION_TIMEOUT,
    DEFAULT_RETRY_DELAY,
    DEFAULT_TIMEOUT,
    DOMAIN,
    SERVICE_FRAME_COUNTER,
    SERVICE_PING,
    SERVICE_QUERY_ROUTE,
    SERVICE_RECONFIGURE,
    SERVICE_REINTERVIEW,
    SERVICE_REJOIN,
    SERVICE_RETRY_ACTION,
)
from .frame_counter import async_read_frame_counter
from .ping import async_ping_device
from .reconfigure import async_reconfigure_device
from .reinterview import async_reinterview_device
from .rejoin import async_rejoin_device
from .retry_action import async_retry_action
from .route import async_query_route

_LOGGER = logging.getLogger(__name__)

__version__ = "0.6.1"

# The reconfigure, re-interview and ping actions share the same retry parameters;
# per-call overrides are optional and fall back to the config entry options.
# The target is either a single ``device_id`` or ``all_devices: true`` -- the
# two are mutually exclusive, and one of them has to be given.
_RETRY_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Exclusive(ATTR_DEVICE_ID, "target"): cv.string,
            vol.Exclusive(CONF_ALL_DEVICES, "target"): cv.boolean,
            # Bounded like the options flow (which also rejects "inf"), so a
            # call cannot retry forever.
            vol.Optional(CONF_TIMEOUT): vol.All(
                vol.Coerce(float), vol.Range(min=1, max=300)
            ),
            vol.Optional(CONF_MAX_RETRIES): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=100)
            ),
            vol.Optional(CONF_RETRY_DELAY): vol.All(
                vol.Coerce(float), vol.Range(min=0, max=300)
            ),
        },
        cv.has_at_least_one_key(ATTR_DEVICE_ID, CONF_ALL_DEVICES),
    )
)

# The frame counter read and the route query are single, quick reads of the
# coordinator's state, so they take the same target as the retried actions but
# no retry parameters.
_QUERY_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Exclusive(ATTR_DEVICE_ID, "target"): cv.string,
            vol.Exclusive(CONF_ALL_DEVICES, "target"): cv.boolean,
        },
        cv.has_at_least_one_key(ATTR_DEVICE_ID, CONF_ALL_DEVICES),
    )
)

# ``confirm`` has no default and must be set to ``True``: this is a deliberate
# safety gate for the potentially device-stranding rejoin action.
REJOIN_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(CONF_CONFIRM): cv.boolean,
        vol.Optional(CONF_REJOIN_WAIT): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=600)
        ),
    }
)

# The reliable action wrapper has its own timing parameters, with defaults
# suited to keep trying for a long time while slowing down.
RETRY_ACTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACTION): cv.service,
        vol.Optional(CONF_DATA, default=dict): dict,
        vol.Optional(CONF_TARGET, default=dict): vol.Schema(
            cv.TARGET_SERVICE_FIELDS
        ),
        # All timing values are bounded (which also rejects "inf"), so a call
        # cannot keep retrying forever or flood the network with attempts.
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_RETRY_ACTION_TIMEOUT): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=86400)
        ),
        vol.Optional(
            CONF_ATTEMPT_TIMEOUT, default=DEFAULT_RETRY_ACTION_ATTEMPT_TIMEOUT
        ): vol.All(vol.Coerce(float), vol.Range(min=1, max=600)),
        vol.Optional(CONF_RETRY_DELAY, default=DEFAULT_RETRY_ACTION_DELAY): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=3600)
        ),
        vol.Optional(CONF_BACKOFF, default=DEFAULT_RETRY_ACTION_BACKOFF): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=10)
        ),
        vol.Optional(CONF_MAX_DELAY, default=DEFAULT_RETRY_ACTION_MAX_DELAY): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=3600)
        ),
    }
)


def _retry_params(call: ServiceCall, options: dict) -> tuple[float, int, float]:
    """Return ``(timeout, max_retries, retry_delay)`` for a retried action.

    Each value comes from the service call if given, otherwise from the config
    entry options, otherwise from the built-in defaults.
    """
    timeout = call.data.get(CONF_TIMEOUT, options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT))
    max_retries = call.data.get(
        CONF_MAX_RETRIES, options.get(CONF_MAX_RETRIES, DEFAULT_MAX_RETRIES)
    )
    retry_delay = call.data.get(
        CONF_RETRY_DELAY, options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY)
    )
    return float(timeout), int(max_retries), float(retry_delay)


async def _async_handle_retry_action(
    hass: HomeAssistant,
    entry: ConfigEntry,
    call: ServiceCall,
    service: str,
    action: DeviceAction,
) -> ServiceResponse:
    """Run a retried single-device action for a service call.

    Reconfigure, re-interview and ping only differ in the action they run, so
    they share this handler body: it resolves the retry parameters and then either
    runs the action on the single requested device or, for
    ``all_devices: true``, on every ZHA device except the coordinator.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry supplying the default retry parameters.
        call: The service call being handled.
        service: The service name, used in log and error messages.
        action: The single-device coroutine function to run.

    Returns:
        The single-device result, or the batch result for an all-devices run.

    Raises:
        ServiceValidationError: If the call names no device to work on, or if
            another batched run is already in progress.
    """
    all_devices = call.data.get(CONF_ALL_DEVICES, False)
    device_id = call.data.get(ATTR_DEVICE_ID)
    timeout, max_retries, retry_delay = _retry_params(call, entry.options)
    _LOGGER.debug(
        "Service %s.%s called for %s (timeout=%s, max_retries=%s, retry_delay=%s)",
        DOMAIN,
        service,
        "all devices" if all_devices else f"device {device_id}",
        timeout,
        max_retries,
        retry_delay,
    )
    if all_devices:
        result = await async_run_on_all_devices(
            hass,
            action,
            service,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
    elif device_id:
        result = await action(
            hass,
            device_id,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
    else:
        # ``all_devices: false`` without a device_id passes the schema but
        # leaves nothing to work on.
        raise ServiceValidationError(
            f"{DOMAIN}.{service} needs either a device_id or all_devices set "
            f"to true"
        )
    _LOGGER.debug("Service %s.%s returning %s", DOMAIN, service, result)
    return result


async def _async_handle_query_action(
    hass: HomeAssistant,
    call: ServiceCall,
    service: str,
    action: DeviceAction,
    quiet: bool = False,
) -> ServiceResponse:
    """Run a non-retried, read-only action for a service call.

    Frame counter and route query only differ in the action they run, so they
    share this handler body: it runs the action on the single requested device
    or, for ``all_devices: true``, on every ZHA device except the coordinator.

    Args:
        hass: The Home Assistant instance.
        call: The service call being handled.
        service: The service name, used in log and error messages.
        action: The single-device coroutine function to run, called as
            ``action(hass, device_id)``.
        quiet: Log nothing at all, for actions whose use must not show up in
            the log (frame counter).

    Returns:
        The single-device result, or the batch result for an all-devices run.

    Raises:
        ServiceValidationError: If the call names no device to work on, or if
            another batched run is already in progress.
    """
    all_devices = call.data.get(CONF_ALL_DEVICES, False)
    device_id = call.data.get(ATTR_DEVICE_ID)
    if not quiet:
        _LOGGER.debug(
            "Service %s.%s called for %s",
            DOMAIN,
            service,
            "all devices" if all_devices else f"device {device_id}",
        )
    if all_devices:
        result = await async_run_on_all_devices(hass, action, service, quiet=quiet)
    elif device_id:
        result = await action(hass, device_id)
    else:
        raise ServiceValidationError(
            f"{DOMAIN}.{service} needs either a device_id or all_devices set "
            f"to true"
        )
    if not quiet:
        # Only the status is logged; the actions log their own results.
        _LOGGER.debug(
            "Service %s.%s returning status %s",
            DOMAIN,
            service,
            result.get("status", f"{result.get('complete')}/{result.get('total')}"),
        )
    return result


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ZHA Tools from a config entry.

    Registers the ``reconfigure``, ``reinterview``, ``rejoin``, ``ping``,
    ``frame_counter``, ``query_route`` and ``retry_action`` actions. The single
    config entry's options supply the default timeout and retry behaviour shared
    by reconfigure, re-interview and ping; each can be overridden per call. All
    device actions except rejoin also accept ``all_devices: true`` instead of a
    ``device_id``, to work through every ZHA device except the coordinator.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being set up.

    Returns:
        ``True`` once the actions are registered.
    """

    async def handle_reconfigure(call: ServiceCall) -> ServiceResponse:
        """Handle a reconfigure call and return the resulting status."""
        return await _async_handle_retry_action(
            hass, entry, call, SERVICE_RECONFIGURE, async_reconfigure_device
        )

    async def handle_reinterview(call: ServiceCall) -> ServiceResponse:
        """Handle a re-interview call and return the resulting status."""
        return await _async_handle_retry_action(
            hass, entry, call, SERVICE_REINTERVIEW, async_reinterview_device
        )

    async def handle_ping(call: ServiceCall) -> ServiceResponse:
        """Handle a ping call and return the resulting status."""
        return await _async_handle_retry_action(
            hass, entry, call, SERVICE_PING, async_ping_device
        )

    async def handle_frame_counter(call: ServiceCall) -> ServiceResponse:
        """Handle a frame counter call and return the counter(s) read."""
        return await _async_handle_query_action(
            hass, call, SERVICE_FRAME_COUNTER, async_read_frame_counter, quiet=True
        )

    async def handle_query_route(call: ServiceCall) -> ServiceResponse:
        """Handle a route query call and return the route report(s)."""
        return await _async_handle_query_action(
            hass, call, SERVICE_QUERY_ROUTE, async_query_route
        )

    async def handle_retry_action(call: ServiceCall) -> ServiceResponse:
        """Handle a reliable action call and return its outcome."""
        # The wrapped action's data may hold secrets (e.g. lock codes), so
        # only its keys are logged.
        _LOGGER.debug(
            "Service %s.%s called for %s (target=%s, data keys=%s, timeout=%s, "
            "attempt_timeout=%s, retry_delay=%s, backoff=%s, max_delay=%s)",
            DOMAIN,
            SERVICE_RETRY_ACTION,
            call.data[CONF_ACTION],
            call.data[CONF_TARGET],
            sorted(call.data[CONF_DATA]),
            call.data[CONF_TIMEOUT],
            call.data[CONF_ATTEMPT_TIMEOUT],
            call.data[CONF_RETRY_DELAY],
            call.data[CONF_BACKOFF],
            call.data[CONF_MAX_DELAY],
        )
        result = await async_retry_action(
            hass,
            call.data[CONF_ACTION],
            call.data[CONF_DATA],
            call.data[CONF_TARGET],
            timeout=call.data[CONF_TIMEOUT],
            attempt_timeout=call.data[CONF_ATTEMPT_TIMEOUT],
            retry_delay=call.data[CONF_RETRY_DELAY],
            backoff=call.data[CONF_BACKOFF],
            max_delay=call.data[CONF_MAX_DELAY],
            context=call.context,
        )
        _LOGGER.debug(
            "Service %s.%s returning %s", DOMAIN, SERVICE_RETRY_ACTION, result
        )
        return result

    async def handle_rejoin(call: ServiceCall) -> ServiceResponse:
        """Handle a rejoin call and return the resulting status."""
        device_id = call.data[ATTR_DEVICE_ID]
        confirm = call.data[CONF_CONFIRM]
        rejoin_wait = float(call.data.get(CONF_REJOIN_WAIT, DEFAULT_REJOIN_WAIT))
        _LOGGER.debug(
            "Service %s.%s called for device %s (rejoin_wait=%s, confirm=%s)",
            DOMAIN,
            SERVICE_REJOIN,
            device_id,
            rejoin_wait,
            confirm,
        )
        result = await async_rejoin_device(
            hass, device_id, rejoin_wait=rejoin_wait, confirm=confirm
        )
        _LOGGER.debug(
            "Service %s.%s for device %s returning %s",
            DOMAIN,
            SERVICE_REJOIN,
            device_id,
            result,
        )
        return result

    # The device maintenance actions can disturb or reshape the Zigbee network
    # (rejoin even removes a device), and the read-only ones expose the network
    # layout, so only administrators may call them. Automations and scripts run
    # without a user and are not affected. retry_action is not restricted: the
    # wrapped action runs with the caller's own permissions.
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RECONFIGURE,
        handle_reconfigure,
        schema=_RETRY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_REINTERVIEW,
        handle_reinterview,
        schema=_RETRY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_PING,
        handle_ping,
        schema=_RETRY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_FRAME_COUNTER,
        handle_frame_counter,
        schema=_QUERY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_QUERY_ROUTE,
        handle_query_route,
        schema=_QUERY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RETRY_ACTION,
        handle_retry_action,
        schema=RETRY_ACTION_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_REJOIN,
        handle_rejoin,
        schema=REJOIN_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    _LOGGER.debug(
        "Registered %s actions: %s, %s, %s, %s, %s, %s, %s",
        DOMAIN,
        SERVICE_RECONFIGURE,
        SERVICE_REINTERVIEW,
        SERVICE_REJOIN,
        SERVICE_PING,
        SERVICE_FRAME_COUNTER,
        SERVICE_QUERY_ROUTE,
        SERVICE_RETRY_ACTION,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the config entry and remove the registered actions.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being unloaded.

    Returns:
        ``True`` once the actions are removed.
    """
    for service in (
        SERVICE_RECONFIGURE,
        SERVICE_REINTERVIEW,
        SERVICE_REJOIN,
        SERVICE_PING,
        SERVICE_FRAME_COUNTER,
        SERVICE_QUERY_ROUTE,
        SERVICE_RETRY_ACTION,
    ):
        hass.services.async_remove(DOMAIN, service)
    _LOGGER.debug("Removed %s actions", DOMAIN)
    return True
