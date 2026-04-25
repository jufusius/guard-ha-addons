"""S-therm Remote integration for Home Assistant."""

import asyncio
import logging
import subprocess

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from datetime import timedelta

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD, CONF_INSTALLATION_ID, CONF_COMPONENT_ID

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.CLIMATE, Platform.SWITCH]

#CC- Requirements z manifest.json odstraněny (RPi4 timeout na kompilaci)
#CC- Instalace probíhá ručně v executoru při prvním setupu
REQUIRED_PACKAGES = ["pycognito>=2024.12.0", "paho-mqtt>=2.0.0"]


def _ensure_deps():
    """Install missing dependencies in background thread (blocking OK here)."""
    for pkg in REQUIRED_PACKAGES:
        name = pkg.split(">=")[0].split("==")[0].replace("-", "_")
        try:
            __import__(name)
        except ImportError:
            _LOGGER.warning("S-therm: installing missing dep %s ...", pkg)
            subprocess.check_call(["pip", "install", "--quiet", pkg])
            _LOGGER.warning("S-therm: %s installed OK", pkg)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up S-therm from a config entry."""
    _LOGGER.warning("S-therm: === async_setup_entry START ===")

    try:
        #CC- Nainstalovat deps v executoru (blocking pip install)
        await asyncio.get_running_loop().run_in_executor(None, _ensure_deps)

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
            await client.get_values()
            #CC- Vracet KOPII dict — coordinator porovnává old vs new referenci
            #CC- Bez kopie je old_data is new_data (stejný mutable dict) → žádný update
            return dict(client.values)
        except Exception as err:
            raise UpdateFailed(f"S-therm update failed: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=60),
    )

    coordinator.async_set_updated_data(dict(client.values))

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
