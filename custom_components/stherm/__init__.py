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
    #CC- Lazy import — stherm_client importuje pycognito/boto3/paho
    from .stherm_client import SthermClient

    client = SthermClient(
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        installation_id=entry.data[CONF_INSTALLATION_ID],
    )

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
                #CC- Reconnect — vše blocking, musí do executoru
                loop = hass.loop
                await loop.run_in_executor(None, client._blocking_authenticate_and_store)
                await loop.run_in_executor(None, client._blocking_mqtt_connect)
                for _ in range(20):
                    if client._connected:
                        break
                    import asyncio
                    await asyncio.sleep(0.5)
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload S-therm config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        data["client"].disconnect()
    return unload_ok
