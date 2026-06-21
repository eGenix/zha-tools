"""Config and options flow for the ZHA Tools integration.

The integration has no connection settings; it is a single-instance service
provider. The config flow just creates the entry, and the options flow lets the
user edit the default timeout and retry behaviour used by the action.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_MAX_RETRIES,
    CONF_RETRY_DELAY,
    CONF_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_DELAY,
    DEFAULT_TIMEOUT,
    DOMAIN,
)

# Default options stored on the entry when it is first created.
DEFAULT_OPTIONS = {
    CONF_TIMEOUT: DEFAULT_TIMEOUT,
    CONF_MAX_RETRIES: DEFAULT_MAX_RETRIES,
    CONF_RETRY_DELAY: DEFAULT_RETRY_DELAY,
}


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    """Build the options form schema, pre-filled from ``options``."""
    return vol.Schema(
        {
            vol.Required(
                CONF_TIMEOUT,
                default=options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=300, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_MAX_RETRIES,
                default=options.get(CONF_MAX_RETRIES, DEFAULT_MAX_RETRIES),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=100, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_RETRY_DELAY,
                default=options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=300, step=1, mode=NumberSelectorMode.BOX
                )
            ),
        }
    )


class ZhaToolsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial config flow for ZHA Tools."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the single config entry.

        Args:
            user_input: Unused; the integration has no setup parameters.

        Returns:
            The flow result creating the entry, or aborting if one exists.
        """
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="ZHA Tools", data={}, options=dict(DEFAULT_OPTIONS)
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowWithReload:
        """Return the options flow handler."""
        return ZhaToolsOptionsFlow()


class ZhaToolsOptionsFlow(OptionsFlowWithReload):
    """Handle editing the default timeout and retry behaviour."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and process the options form.

        Args:
            user_input: The submitted values, or ``None`` to show the form.

        Returns:
            The form result, or the entry update once submitted.
        """
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(self.config_entry.options)
        )
