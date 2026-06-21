"""Tests for the config and options flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zha_tools.const import (
    CONF_MAX_RETRIES,
    CONF_RETRY_DELAY,
    CONF_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_DELAY,
    DEFAULT_TIMEOUT,
    DOMAIN,
)


async def test_user_flow_creates_entry_with_defaults(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "ZHA Tools"
    assert result["options"] == {
        CONF_TIMEOUT: DEFAULT_TIMEOUT,
        CONF_MAX_RETRIES: DEFAULT_MAX_RETRIES,
        CONF_RETRY_DELAY: DEFAULT_RETRY_DELAY,
    }


async def test_single_instance_only(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] in ("already_configured", "single_instance_allowed")


async def test_options_flow_updates_defaults(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={},
        options={
            CONF_TIMEOUT: DEFAULT_TIMEOUT,
            CONF_MAX_RETRIES: DEFAULT_MAX_RETRIES,
            CONF_RETRY_DELAY: DEFAULT_RETRY_DELAY,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_TIMEOUT: 20,
            CONF_MAX_RETRIES: 2,
            CONF_RETRY_DELAY: 1,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_TIMEOUT] == 20
    assert entry.options[CONF_MAX_RETRIES] == 2
    assert entry.options[CONF_RETRY_DELAY] == 1
