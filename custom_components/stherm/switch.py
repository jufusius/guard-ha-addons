"""Switch platform for S-therm integration."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, CONF_INSTALLATION_NAME

_LOGGER = logging.getLogger(__name__)

#CC- Přepínače mapované na coil parametry (dle profilu GSH-140TRB2-3)
SWITCHES = [
    ("c22", "equithermal", "Ekvitermní regulace", "mdi:thermostat-auto"),
    ("c33", "quiet_mode", "Tichý režim", "mdi:volume-off"),
    ("c17", "dhw_heater", "E-ohřívač TUV", "mdi:water-boiler-alert"),
    ("c27", "heating_heater", "E-ohřívač topení", "mdi:radiator"),
    ("c171", "solar_mode", "Solární režim", "mdi:solar-power"),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up S-therm switches."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]

    async_add_entities([
        SthermSwitch(coordinator, client, entry, param_code, key, name, icon)
        for param_code, key, name, icon in SWITCHES
    ])


class SthermSwitch(CoordinatorEntity, SwitchEntity):
    """Switch for S-therm coil parameters."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, client, entry, param_code, key, name, icon) -> None:
        super().__init__(coordinator)
        self._client = client
        self._param_code = param_code
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        install_name = entry.data.get(CONF_INSTALLATION_NAME, "S-therm")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"S-therm {install_name}",
            "manufacturer": MANUFACTURER,
        }

    @property
    def is_on(self) -> bool | None:
        #CC- Číst z coordinator.data (kopie), ne z client.values (mutable reference)
        data = self.coordinator.data or {}
        vals = data.get(self._param_code)
        if vals and len(vals) > 0:
            return vals[0] == 1
        return None

    async def async_turn_on(self, **kwargs) -> None:
        _LOGGER.warning("S-therm switch: turn_on %s (param=%s)", self.entity_id, self._param_code)
        if not await self._client.set_parameter(self._param_code, 1):
            raise HomeAssistantError(f"S-therm write failed for {self._param_code}=1")
        #CC- Okamžitě aktualizovat lokální stav — zachovat plný tuple [val, min, max]
        current = self._client.values.get(self._param_code, [0, 0, 1])
        self._client.values[self._param_code] = [1] + current[1:]
        self.coordinator.async_set_updated_data(dict(self._client.values))

    async def async_turn_off(self, **kwargs) -> None:
        _LOGGER.warning("S-therm switch: turn_off %s (param=%s)", self.entity_id, self._param_code)
        if not await self._client.set_parameter(self._param_code, 0):
            raise HomeAssistantError(f"S-therm write failed for {self._param_code}=0")
        #CC- Okamžitě aktualizovat lokální stav — zachovat plný tuple [val, min, max]
        current = self._client.values.get(self._param_code, [1, 0, 1])
        self._client.values[self._param_code] = [0] + current[1:]
        self.coordinator.async_set_updated_data(dict(self._client.values))
