"""The ZHA Tools integration.

Provides a small collection of ZHA helper actions that automations can call:

* ``zha_tools.reconfigure`` -- run a ZHA device reconfigure, with retries;
* ``zha_tools.reinterview`` -- re-interview a device in place, with retries;
* ``zha_tools.rejoin`` -- ask a device to leave and immediately rejoin
  (a remote substitute for the pairing button -- see the danger notes).

Each action returns the resulting status to the caller.
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
import homeassistant.helpers.config_validation as cv

from .const import (
    ATTR_DEVICE_ID,
    CONF_CONFIRM,
    CONF_MAX_RETRIES,
    CONF_REJOIN_WAIT,
    CONF_RETRY_DELAY,
    CONF_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_REJOIN_WAIT,
    DEFAULT_RETRY_DELAY,
    DEFAULT_TIMEOUT,
    DOMAIN,
    SERVICE_RECONFIGURE,
    SERVICE_REINTERVIEW,
    SERVICE_REJOIN,
)
from .reconfigure import async_reconfigure_device
from .reinterview import async_reinterview_device
from .rejoin import async_rejoin_device

_LOGGER = logging.getLogger(__name__)

__version__ = "0.2.0"

# The reconfigure and re-interview actions share the same retry parameters;
# per-call overrides are optional and fall back to the config entry options.
_RETRY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(CONF_TIMEOUT): vol.All(vol.Coerce(float), vol.Range(min=1)),
        vol.Optional(CONF_MAX_RETRIES): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(CONF_RETRY_DELAY): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)

# ``confirm`` has no default and must be set to ``True``: this is a deliberate
# safety gate for the potentially device-stranding rejoin action.
REJOIN_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(CONF_CONFIRM): cv.boolean,
        vol.Optional(CONF_REJOIN_WAIT): vol.All(vol.Coerce(float), vol.Range(min=0)),
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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ZHA Tools from a config entry.

    Registers the ``reconfigure``, ``reinterview`` and ``rejoin`` actions. The
    single config entry's options supply the default timeout and retry behaviour
    shared by reconfigure and re-interview; each can be overridden per call.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being set up.

    Returns:
        ``True`` once the actions are registered.
    """

    async def handle_reconfigure(call: ServiceCall) -> ServiceResponse:
        """Handle a reconfigure call and return the resulting status."""
        device_id = call.data[ATTR_DEVICE_ID]
        timeout, max_retries, retry_delay = _retry_params(call, entry.options)
        _LOGGER.debug(
            "Service %s.%s called for device %s (timeout=%s, max_retries=%s, "
            "retry_delay=%s)",
            DOMAIN,
            SERVICE_RECONFIGURE,
            device_id,
            timeout,
            max_retries,
            retry_delay,
        )
        result = await async_reconfigure_device(
            hass,
            device_id,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        _LOGGER.debug(
            "Service %s.%s for device %s returning %s",
            DOMAIN,
            SERVICE_RECONFIGURE,
            device_id,
            result,
        )
        return result

    async def handle_reinterview(call: ServiceCall) -> ServiceResponse:
        """Handle a re-interview call and return the resulting status."""
        device_id = call.data[ATTR_DEVICE_ID]
        timeout, max_retries, retry_delay = _retry_params(call, entry.options)
        _LOGGER.debug(
            "Service %s.%s called for device %s (timeout=%s, max_retries=%s, "
            "retry_delay=%s)",
            DOMAIN,
            SERVICE_REINTERVIEW,
            device_id,
            timeout,
            max_retries,
            retry_delay,
        )
        result = await async_reinterview_device(
            hass,
            device_id,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        _LOGGER.debug(
            "Service %s.%s for device %s returning %s",
            DOMAIN,
            SERVICE_REINTERVIEW,
            device_id,
            result,
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

    hass.services.async_register(
        DOMAIN,
        SERVICE_RECONFIGURE,
        handle_reconfigure,
        schema=_RETRY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REINTERVIEW,
        handle_reinterview,
        schema=_RETRY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REJOIN,
        handle_rejoin,
        schema=REJOIN_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    _LOGGER.debug(
        "Registered %s actions: %s, %s, %s",
        DOMAIN,
        SERVICE_RECONFIGURE,
        SERVICE_REINTERVIEW,
        SERVICE_REJOIN,
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
    for service in (SERVICE_RECONFIGURE, SERVICE_REINTERVIEW, SERVICE_REJOIN):
        hass.services.async_remove(DOMAIN, service)
    _LOGGER.debug("Removed %s actions", DOMAIN)
    return True
