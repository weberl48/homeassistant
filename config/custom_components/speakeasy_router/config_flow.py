"""Config flow for speakeasy_router."""
from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class SpeakeasyRouterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the single setup step."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Speakeasy Router", data={})
