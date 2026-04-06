"""Climate platform for S-therm integration."""

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, UNIT_STATES, CONF_INSTALLATION_NAME

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up S-therm climate entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]

    async_add_entities([
        SthermClimate(coordinator, client, entry),
    ])


class SthermClimate(CoordinatorEntity, ClimateEntity):
    """Climate entity for S-therm heat pump."""

    _attr_has_entity_name = True
    _attr_name = "Tepelné čerpadlo"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_min_temp = 20
    _attr_max_temp = 60
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF, HVACMode.AUTO]
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator, client, entry) -> None:
        super().__init__(coordinator)
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_climate"
        install_name = entry.data.get(CONF_INSTALLATION_NAME, "S-therm")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"S-therm {install_name}",
            "manufacturer": MANUFACTURER,
        }

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        v = (self.coordinator.data or {})
        #CC- h2: 1=heat, 2=dhw, 3=cool+dhw, 4=heat+dhw, 5=cool (profil GSH-140TRB2-3)
        h2 = v.get("h2", [4])[0] if v.get("h2") else 4
        c22 = v.get("c22", [0])[0] if v.get("c22") else 0

        if c22 == 1:  #CC- ekvitermní regulace aktivní
            return HVACMode.AUTO
        elif h2 in (1, 4):  #CC- 1=topení, 4=topení+TUV
            return HVACMode.HEAT
        elif h2 in (5, 3):  #CC- 5=chlazení, 3=chlazení+TUV
            return HVACMode.COOL
        else:  #CC- 2=TUV → auto
            return HVACMode.AUTO

    @property
    def current_temperature(self) -> float | None:
        """Return outdoor temperature."""
        vals = (self.coordinator.data or {}).get("h118")
        return vals[0] if vals else None

    @property
    def target_temperature(self) -> float | None:
        """Return heating setpoint."""
        vals = (self.coordinator.data or {}).get("h10")
        return vals[0] if vals else None

    @property
    def hvac_action(self) -> str | None:
        """Return current action."""
        v = (self.coordinator.data or {})
        freq = v.get("h143", [0])[0] if v.get("h143") else 0
        h2 = v.get("h2", [4])[0] if v.get("h2") else 4

        if freq > 0:
            #CC- h2: 1,4=topení, 5,3=chlazení, 2=dhw
            return "heating" if h2 in (1, 4) else "cooling"
        return "idle"

    @property
    def extra_state_attributes(self) -> dict:
        v = (self.coordinator.data or {})
        attrs = {}

        def _get(code):
            vals = v.get(code)
            return vals[0] if vals and len(vals) > 0 else None

        if (t := _get("h125")) is not None:
            attrs["inlet_temp"] = t
        if (t := _get("h127")) is not None:
            attrs["outlet_temp"] = t
        if (t := _get("h128")) is not None:
            attrs["dhw_temp"] = t
        if (freq := _get("h143")) is not None:
            attrs["compressor_hz"] = freq
        if (c22 := _get("c22")) is not None:
            attrs["equithermal"] = "on" if c22 == 1 else "off"

        return attrs

    async def async_set_temperature(self, **kwargs) -> None:
        """Set heating setpoint."""
        temp = kwargs.get("temperature")
        if temp is not None:
            if not await self._client.set_parameter("h10", float(temp)):
                raise HomeAssistantError("S-therm write failed for h10")
            await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode (limited — TČ režim je komplexní)."""
        if hvac_mode == HVACMode.OFF:
            #CC- Nelze vypnout TČ přes MQTT (h42 vyžaduje speciální hodnoty 170/85)
            _LOGGER.warning("S-therm: Turn off not supported via MQTT, use S-therm Remote app")
        elif hvac_mode == HVACMode.HEAT:
            #CC- Vypnout ekvitermu (c22=0) → přímé topení dle setpointu
            if not await self._client.set_parameter("c22", 0):
                raise HomeAssistantError("S-therm write failed for c22=0")
            await self.coordinator.async_request_refresh()
        elif hvac_mode == HVACMode.COOL:
            _LOGGER.warning("S-therm: Cooling mode switch is not implemented yet")
        elif hvac_mode == HVACMode.AUTO:
            #CC- Zapnout ekvitermu (c22=1) → automatická regulace dle venkovní teploty
            if not await self._client.set_parameter("c22", 1):
                raise HomeAssistantError("S-therm write failed for c22=1")
            await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
