"""Constants for Enchufado integration."""
import os

DOMAIN = "enchufado"

CONF_DATADIS_USER = "datadis_user"
CONF_DATADIS_PASSWORD = "datadis_password"
CONF_CUPS = "cups"
CONF_DISTRIBUTOR_CODE = "distributor_code"
CONF_POINT_TYPE = "point_type"
CONF_AUTHORIZED_NIF = "authorized_nif"
CONF_POWER_HIGH = "power_high"
CONF_POWER_LOW = "power_low"
CONF_ZIP_CODE = "zip_code"
CONF_BILLS_NUMBER = "bills_number"

CONSUMPTION_STATISTIC_ID = f"{DOMAIN}:consumption"
CONSUMPTION_STATISTIC_NAME = "Consumo eléctrico PVPC"
COST_STATISTIC_ID = f"{DOMAIN}:cost"
COST_STATISTIC_NAME = "Coste eléctrico PVPC"
CURRENT_BILL_STATE = f"{DOMAIN}.current_bill"

USER_FILES_PATH = f"/config/custom_components/{DOMAIN}/user_files"
ENERGY_FILE = f"{USER_FILES_PATH}/energy_data.csv"
BILLING_PERIODS_FILE = f"{USER_FILES_PATH}/billing_periods.csv"
