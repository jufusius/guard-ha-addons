"""Constants for S-therm integration."""

DOMAIN = "stherm"
MANUFACTURER = "Sinclair"

#CC- AWS Cognito + IoT config (z JS bundle s-thermremote.com)
COGNITO_USER_POOL_ID = "eu-central-1_que8JCEVH"
COGNITO_CLIENT_ID = "3qga0ntgrj7fblcgau24plp2h4"
COGNITO_IDENTITY_POOL_ID = "eu-central-1:d1f8d4ae-7401-4d69-b603-1e992f635253"
IOT_ENDPOINT = "a24t7r3f2r1nrr-ats.iot.eu-central-1.amazonaws.com"
AWS_REGION = "eu-central-1"
APP_ID = "274"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_INSTALLATION_ID = "installation_id"
CONF_INSTALLATION_NAME = "installation_name"
CONF_COMPONENT_ID = "component_id"

#CC- Parametry pro čtení (základní sada jako webová appka)
READ_PARAMS = [
    "h42", "h2", "c29", "h10", "h9", "h13", "h11", "c33", "h12", "h15",
    "c27", "h35", "h118", "h129", "c17", "h127", "h125", "h128", "h126",
    "h143", "h117", "c173", "c171", "c172", "h132",
    "c19",  #CC- režim chlazení/topení (NE ekvitermní!)
    "c22",  #CC- ekvitermní regulace (weather-dependent) — OVĚŘENO zápisem 2026-04-06
]

#CC- Mapování parametrů na lidské názvy
PARAM_NAMES = {
    "h118": "Venkovní teplota",
    "h117": "Venkovní průměr",
    "h125": "Vstup výměník",
    "h127": "Výstup výměník",
    "h128": "Nádrž TUV",
    "h126": "Za bivalentním zdrojem",
    "h129": "Sání kompresoru",
    "h143": "Frekvence kompresoru",
    "h142": "Frekvence kompresoru 2",
    "h42": "ON/OFF přepínač",  #CC- 170=ON (0xAA), 85=OFF (0x55)
    "h132": "Počet startů kompresoru",
    "h2": "Režim provozu",  #CC- 1=topení, 2=TUV, 3=chlaz+TUV, 4=top+TUV, 5=chlaz
    "h10": "Setpoint topení",
    "h13": "Setpoint TUV",
    "h9": "Setpoint pokojová",
    "h11": "Setpoint chlazení",
    "h12": "Setpoint ECO",
    "h15": "Bod bivalence",
    "h35": "Defrost režim",
    "c29": "S/bez TUV",
    "c17": "E-ohřívač TUV",
    "c27": "E-ohřívač topení",
    "c33": "Tichý režim",
    "c19": "Režim chlazení/topení",
    "c171": "Solární režim",
    "c172": "Chlazení povoleno",
    "c173": "Smart Grid",
    "c22": "Ekvitermní regulace",  #CC- OVĚŘENO dle profilu GSH-140TRB2-3
}

#CC- Provozní režim (h2) dle profilu GSH-140TRB2-3 (hodnoty 1-5)
UNIT_STATES = {
    1: "heating",
    2: "hot_water",
    3: "cooling_hot_water",
    4: "heating_hot_water",
    5: "cooling",
}

#CC- Teplotní senzory pro sensor platform
TEMPERATURE_SENSORS = {
    "h118": ("outdoor_temp", "Venkovní teplota", "mdi:thermometer"),
    "h125": ("hex_inlet_temp", "Vstup výměník", "mdi:thermometer-water"),
    "h127": ("hex_outlet_temp", "Výstup výměník", "mdi:thermometer-water"),
    "h128": ("dhw_tank_temp", "Nádrž TUV", "mdi:water-boiler"),
    "h126": ("bivalent_temp", "Za bivalentním zdrojem", "mdi:thermometer-lines"),
    "h129": ("suction_temp", "Sání kompresoru", "mdi:thermometer-low"),
    "h117": ("outdoor_avg_temp", "Venkovní průměr", "mdi:thermometer-average"),
}

#CC- Další senzory
EXTRA_SENSORS = {
    "h143": ("compressor_freq", "Frekvence kompresoru", "mdi:sine-wave", "Hz", "frequency"),
    "h42": ("operating_hours", "Provozní hodiny", "mdi:clock-outline", "h", None),
    "h132": ("compressor_starts", "Počet startů", "mdi:counter", None, None),
}
