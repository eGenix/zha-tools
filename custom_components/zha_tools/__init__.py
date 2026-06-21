"""The ZHA Tools integration.

Provides a single ``zha_tools.reconfigure`` action that automations can
call to run a ZHA device reconfigure programmatically, with retries, and that
returns the resulting status.
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
    CONF_MAX_RETRIES,
    CONF_RETRY_DELAY,
    CONF_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_DELAY,
    DEFAULT_TIMEOUT,
    DOMAIN,
    SERVICE_RECONFIGURE,
)
from .reconfigure import async_reconfigure_device

_LOGGER = logging.getLogger(__name__)

__version__ = "0.1.0"

# Per-call overrides are optional; defaults come from the config entry options.
RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(CONF_TIMEOUT): vol.All(vol.Coerce(float), vol.Range(min=1)),
        vol.Optional(CONF_MAX_RETRIES): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(CONF_RETRY_DELAY): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ZHA Tools from a config entry.

    Registers the ``reconfigure`` action. The single config entry's options
    supply the default timeout and retry behaviour; each can be overridden per
    call.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being set up.

    Returns:
        ``True`` once the action is registered.
    """

    async def handle_reconfigure(call: ServiceCall) -> ServiceResponse:
        """Handle a reconfigure service call and return the resulting status."""
        options = entry.options
        device_id = call.data[ATTR_DEVICE_ID]
        timeout = call.data.get(
            CONF_TIMEOUT, options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        )
        max_retries = call.data.get(
            CONF_MAX_RETRIES, options.get(CONF_MAX_RETRIES, DEFAULT_MAX_RETRIES)
        )
        retry_delay = call.data.get(
            CONF_RETRY_DELAY, options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY)
        )
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
            timeout=float(timeout),
            max_retries=int(max_retries),
            retry_delay=float(retry_delay),
        )
        _LOGGER.debug(
            "Service %s.%s for device %s returning %s",
            DOMAIN,
            SERVICE_RECONFIGURE,
            device_id,
            result,
        )
        return result

    hass.services.async_register(
        DOMAIN,
        SERVICE_RECONFIGURE,
        handle_reconfigure,
        schema=RECONFIGURE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    _LOGGER.debug("Registered %s.%s action", DOMAIN, SERVICE_RECONFIGURE)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the config entry and remove the registered action.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being unloaded.

    Returns:
        ``True`` once the action is removed.
    """
    hass.services.async_remove(DOMAIN, SERVICE_RECONFIGURE)
    _LOGGER.debug("Removed %s.%s action", DOMAIN, SERVICE_RECONFIGURE)
    return True
