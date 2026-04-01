"""Sensor platform for S-therm integration."""

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MANUFACTURER,
    TEMPERATURE_SENSORS,
    EXTRA_SENSORS,
    UNIT_STATES,
    CONF_INSTALLATION_NAME,
    CONF_COMPONENT_ID,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up S-therm sensors from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]

    entities = []

    #CC- Teplotní senzory
    for param_code, (key, name, icon) in TEMPERATURE_SENSORS.items():
        entities.append(
            SthermTemperatureSensor(coordinator, client, entry, param_code, key, name, icon)
        )

    #CC- Další senzory (frekvence, hodiny, starty)
    for param_code, (key, name, icon, unit, device_class) in EXTRA_SENSORS.items():
        entities.append(
            SthermGenericSensor(coordinator, client, entry, param_code, key, name, icon, unit, device_class)
        )

    #CC- Status senzor
    entities.append(SthermStatusSensor(coordinator, client, entry))

    async_add_entities(entities)


class SthermBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for S-therm sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, client, entry, param_code, key, name) -> None:
        super().__init__(coordinator)
        self._client = client
        self._param_code = param_code
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        install_name = entry.data.get(CONF_INSTALLATION_NAME, "S-therm")

        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"S-therm {install_name}",
            "manufacturer": MANUFACTURER,
            "model": entry.data.get(CONF_COMPONENT_ID, "Heat Pump"),
            "sw_version": "1.0",
        }

    @property
    def _values(self) -> dict:
        return self._client.values


class SthermTemperatureSensor(SthermBaseSensor):
    """Temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, client, entry, param_code, key, name, icon) -> None:
        super().__init__(coordinator, client, entry, param_code, key, name)
        self._attr_icon = icon

    @property
    def native_value(self) -> float | None:
        vals = self._values.get(self._param_code)
        if vals and len(vals) > 0:
            return vals[0]
        return None


class SthermGenericSensor(SthermBaseSensor):
    """Generic sensor (frequency, hours, counter)."""

    def __init__(self, coordinator, client, entry, param_code, key, name, icon, unit, device_class) -> None:
        super().__init__(coordinator, client, entry, param_code, key, name)
        self._attr_icon = icon
        if unit:
            self._attr_native_unit_of_measurement = unit
        if device_class:
            self._attr_device_class = SensorDeviceClass(device_class)
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        vals = self._values.get(self._param_code)
        if vals and len(vals) > 0:
            return vals[0]
        return None


class SthermStatusSensor(CoordinatorEntity, SensorEntity):
    """Operating status sensor."""

    _attr_has_entity_name = True
    _attr_name = "Stav"
    _attr_icon = "mdi:heat-pump"

    def __init__(self, coordinator, client, entry) -> None:
        super().__init__(coordinator)
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_status"
        install_name = entry.data.get(CONF_INSTALLATION_NAME, "S-therm")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"S-therm {install_name}",
            "manufacturer": MANUFACTURER,
        }

    @property
    def native_value(self) -> str:
        vals = self._client.values.get("h2")
        if vals and len(vals) > 0:
            return UNIT_STATES.get(int(vals[0]), f"unknown_{vals[0]}")
        return "unavailable"

    @property
    def extra_state_attributes(self) -> dict:
        v = self._client.values
        attrs = {}

        def _get(code):
            vals = v.get(code)
            return vals[0] if vals and len(vals) > 0 else None

        if (sp := _get("h10")) is not None:
            attrs["setpoint_topeni"] = sp
        if (sp := _get("h13")) is not None:
            attrs["setpoint_tuv"] = sp
        if (sp := _get("h9")) is not None:
            attrs["setpoint_pokojova"] = sp
        if (freq := _get("h143")) is not None:
            attrs["frekvence_kompresoru"] = freq
        if (hrs := _get("h42")) is not None:
            attrs["provozni_hodiny"] = hrs

        #CC- Coil statusy
        for code, label in [("c29", "rezim"), ("c17", "ohrivac_tuv"),
                            ("c27", "ohrivac_topeni"), ("c33", "tichy_rezim"),
                            ("c19", "ekvitermni")]:
            val = _get(code)
            if val is not None:
                attrs[label] = "on" if val == 1 else "off"

        return attrs
