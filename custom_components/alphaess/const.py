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
ALPHA_POST_REQUEST_RESTRICTION = timedelta(seconds=30)
THROTTLE_MULTIPLIER = 0
INVERTER_COUNT = 0
INVERTER_LIST = []

KNOWN_INVERTERS = ["Storion-S5", "SMILE5-INV", "VT1000", "SMILE-T10-HV-INV"]  # List of known inverters
KNOWN_CHARGERS = ["SMILE-EVCT11"]
# Set blacklist for certain inverters from certain sensors
INVERTER_SETTING_BLACKLIST = [
    "VT1000"]  # Blacklist sensors for setting discharge/charge amount and sending discharge and charge amount
LIMITED_INVERTER_SENSOR_LIST = [
    "Storion-S5"]  # Blacklist sensors for showing data relating to getlastpowerdata and other data points

# Inverters who do not support "getlastpowerdata"
LOWER_INVERTER_API_CALL_LIST = ["Storion-S5"]

LOCAL_API_INVERTER_BLACKLIST = []

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

EV_CHARGER_STATE_KEYS = {
    1: "available",
    2: "preparing",
    3: "charging",
    4: "suspended_evse",
    5: "suspended_ev",
    6: "finishing",
    9: "faulted"
}

TCP_STATUS_KEYS = {
    0: "connected_ok",
    -1: "initialization",
    -2: "not_connected_router",
    -3: "dns_lookup_error",
    -4: "connect_fail",
    -5: "signal_too_weak",
    -6: "failed_register_base_station",
    -7: "sim_card_not_inserted",
    -8: "not_bound_plant",
    -9: "key_error",
    -10: "sn_error",
    -11: "communication_timeout",
    -12: "communication_abort_server",
    -13: "server_address_error"
}

WIFI_STATUS_KEYS = {
    0: "connection_idle",
    1: "connecting",
    2: "password_error",
    3: "ap_not_found",
    4: "connect_fail",
    5: "connected_ok"
    # All other values default to unknown_error
}

ETHERNET_STATUS_KEYS = {
    0: "link_up",
    # All other values default to link_down
}

FOUR_G_STATUS_KEYS = {
    0: "ok",
    -1: "initialization",
    -2: "connected_fail",
    -3: "connected_lost",
    -4: "connected_fail"
    # All other values default to unknown_error
}


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
