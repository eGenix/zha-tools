"""Shared pytest fixtures for the ZHA Tools tests."""

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load ``custom_components`` during tests (required by the test harness)."""
    yield
