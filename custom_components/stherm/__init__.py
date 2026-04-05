"""S-therm Remote integration for Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from datetime import timedelta

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD, CONF_INSTALLATION_ID, CONF_COMPONENT_ID

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.CLIMATE, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up S-therm from a config entry."""
    _LOGGER.warning("S-therm: === async_setup_entry START ===")

    try:
        #CC- Lazy import — stherm_client importuje pycognito/boto3/paho
        _LOGGER.warning("S-therm: importing stherm_client...")
        from .stherm_client import SthermClient
        _LOGGER.warning("S-therm: import OK")

        client = SthermClient(
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            installation_id=entry.data[CONF_INSTALLATION_ID],
        )

        if CONF_COMPONENT_ID in entry.data:
            client.component_id = entry.data[CONF_COMPONENT_ID]

        _LOGGER.warning("S-therm: calling async_setup...")
        await client.async_setup()
        _LOGGER.warning("S-therm: async_setup OK, values=%d", len(client.values))

    except Exception as err:
        _LOGGER.error("S-therm: Setup FAILED: %s", err, exc_info=True)
        raise

    async def async_update_data():
        """Fetch data from heat pump."""
        try:
            if not client._connected:
                await client.async_setup()
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

    coordinator.async_set_updated_data(client.values)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    _LOGGER.warning("S-therm: forwarding platforms: %s", PLATFORMS)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.warning("S-therm: === async_setup_entry DONE ===")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload S-therm config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        data["client"].disconnect()
    return unload_ok
