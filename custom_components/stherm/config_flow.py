"""Config flow for S-therm integration."""

import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_INSTALLATION_ID,
    CONF_INSTALLATION_NAME,
    CONF_COMPONENT_ID,
)
from .stherm_client import SthermClient

_LOGGER = logging.getLogger(__name__)


class SthermConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for S-therm."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str = ""
        self._password: str = ""
        self._installations: list[dict] = []
        self._client: SthermClient | None = None

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle user step — login credentials."""
        errors = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            try:
                #CC- Ověřit credentials + získat seznam instalací
                self._client = SthermClient(self._username, self._password, "")
                await self._client.authenticate()
                await self._client.connect_mqtt()

                #CC- Pokud úspěch, rovnou zkusit discovery
                # Pro zjednodušení: použijeme ID z config flow
                return await self.async_step_installation()

            except Exception as err:
                _LOGGER.error("S-therm config flow: auth failed: %s", err)
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
            description_placeholders={
                "app_name": "S-therm Remote",
            },
        )

    async def async_step_installation(self, user_input=None) -> FlowResult:
        """Handle installation selection step."""
        errors = {}

        if user_input is not None:
            installation_id = user_input[CONF_INSTALLATION_ID]

            try:
                #CC- Reconnect s vybranou instalací a discover komponentu
                client = SthermClient(self._username, self._password, installation_id)
                await client.async_setup()

                #CC- Unique ID = installation ID
                await self.async_set_unique_id(installation_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"S-therm {user_input.get(CONF_INSTALLATION_NAME, installation_id[:8])}",
                    data={
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                        CONF_INSTALLATION_ID: installation_id,
                        CONF_INSTALLATION_NAME: user_input.get(CONF_INSTALLATION_NAME, ""),
                        CONF_COMPONENT_ID: client.component_id,
                    },
                )
            except Exception as err:
                _LOGGER.error("S-therm config flow: setup failed: %s", err)
                errors["base"] = "cannot_connect"

        #CC- Pro jednoduchost: uživatel zadá installation ID ručně
        #CC- (v budoucnu: REST API get-installation-list pro dropdown)
        return self.async_show_form(
            step_id="installation",
            data_schema=vol.Schema({
                vol.Required(CONF_INSTALLATION_ID): str,
                vol.Optional(CONF_INSTALLATION_NAME, default=""): str,
            }),
            errors=errors,
            description_placeholders={
                "hint": "Najdete v S-therm Remote appce → Nastavení instalace",
            },
        )
