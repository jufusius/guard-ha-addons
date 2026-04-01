"""S-therm Remote integration for Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from datetime import timedelta

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD, CONF_INSTALLATION_ID, CONF_COMPONENT_ID
from .stherm_client import SthermClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.CLIMATE, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up S-therm from a config entry."""
    client = SthermClient(
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        installation_id=entry.data[CONF_INSTALLATION_ID],
    )

    #CC- Nastavit component_id z uloženého config entry
    if CONF_COMPONENT_ID in entry.data:
        client.component_id = entry.data[CONF_COMPONENT_ID]

    try:
        await client.async_setup()
    except Exception as err:
        _LOGGER.error("S-therm: Setup failed: %s", err)
        raise

    async def async_update_data():
        """Fetch data from heat pump."""
        try:
            if not client._connected:
                await client.authenticate()
                await client.connect_mqtt()
            return await client.get_values()
        except Exception as err:
            raise UpdateFailed(f"S-therm update failed: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=60),
    )

    #CC- Initial data already fetched in async_setup
    coordinator.async_set_updated_data(client.values)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload S-therm config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        data["client"].disconnect()
    return unload_ok
