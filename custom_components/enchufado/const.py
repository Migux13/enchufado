"""Constants for Enchufado integration."""
import os

DOMAIN = "enchufado"

CONSUMPTION_STATISTIC_ID = f"{DOMAIN}:consumption"
CONSUMPTION_STATISTIC_NAME = "Consumo eléctrico PVPC"
COST_STATISTIC_ID = f"{DOMAIN}:cost"
COST_STATISTIC_NAME = "Coste eléctrico PVPC"
CURRENT_BILL_STATE = f"{DOMAIN}.current_bill"

USER_FILES_PATH = f"/config/custom_components/{DOMAIN}/user_files"
ENERGY_FILE = f"{USER_FILES_PATH}/energy_data.csv"
BILLING_PERIODS_FILE = f"{USER_FILES_PATH}/billing_periods.csv"
