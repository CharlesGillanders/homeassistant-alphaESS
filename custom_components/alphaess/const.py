"""Constants for the Alpha ESS integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "alphaess"
PLATFORMS = [
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.NUMBER
]
SCAN_INTERVAL = timedelta(minutes=1)
ALPHA_POST_REQUEST_RESTRICTION = timedelta(minutes=10)
THROTTLE_MULTIPLIER = 1.5
INVERTER_COUNT = 0
INVERTER_LIST = []

KNOWN_INVERTERS = ["Storion-S5", "SMILE5-INV", "VT1000", "SMILE-T10-HV-INV"]  # List of known inverters
KNOWN_CHARGERS = ["SMILE-EVCT11"]
# Set blacklist for certain inverters from certain sensors
INVERTER_SETTING_BLACKLIST = ["VT1000"]  # Blacklist sensors for setting discharge/charge amount and sending discharge and charge amount
LIMITED_INVERTER_SENSOR_LIST = ["Storion-S5"]  # Blacklist sensors for showing data relating to getlastpowerdata and other data points

# Inverters who do not support "getlastpowerdata"
LOWER_INVERTER_API_CALL_LIST = ["Storion-S5"]

NAME = "Alpha ESS"
ISSUE_URL = "https://github.com/CharlesGillanders/homeassistant-alphaESS/issues"

STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""


def increment_inverter_count():
    global INVERTER_COUNT
    INVERTER_COUNT += 1


def get_inverter_count():
    return INVERTER_COUNT


# If no Storion-S5, make the throttle amount smaller
def set_throttle_count_lower():
    global THROTTLE_MULTIPLIER
    THROTTLE_MULTIPLIER = 1.25


def add_inverter_to_list(inverter):
    global INVERTER_LIST
    INVERTER_LIST.append(inverter)


def get_inverter_list():
    return INVERTER_LIST
