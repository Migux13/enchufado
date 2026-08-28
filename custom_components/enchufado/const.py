"""Constants for Enchufado integration."""
DOMAIN = "enchufado"

# Config entry keys
CONF_DATADIS_USER = "datadis_user"
CONF_DATADIS_PASSWORD = "datadis_password"
CONF_CUPS = "cups"
CONF_DISTRIBUTOR_CODE = "distributor_code"
CONF_POINT_TYPE = "point_type"
CONF_AUTHORIZED_NIF = "authorized_nif"
CONF_POWER_HIGH = "power_high"
CONF_POWER_LOW = "power_low"
CONF_ZIP_CODE = "zip_code"
CONF_ESIOS_TOKEN = "esios_token"

# Statistics
CONSUMPTION_STATISTIC_ID = f"{DOMAIN}:consumption"
CONSUMPTION_STATISTIC_NAME = "Consumo eléctrico PVPC"
COST_STATISTIC_ID = f"{DOMAIN}:cost"
COST_STATISTIC_NAME = "Coste eléctrico PVPC"
BILL_STATISTIC_ID = f"{DOMAIN}:bill"
BILL_STATISTIC_NAME = "Factura eléctrica simulada"
CURRENT_BILL_STATE = f"{DOMAIN}.current_bill"

# Data file names — resolved to hass.config.path(DOMAIN)/<name> at runtime,
# since the config directory isn't always /config (e.g. venv/source installs).
ENERGY_FILENAME = "energy_data.csv"
BILLING_PERIODS_FILENAME = "billing_periods.csv"
