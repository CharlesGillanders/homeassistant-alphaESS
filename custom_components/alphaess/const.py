"""Constants for the Alpha ESS integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "alphaess"
PLATFORMS = [Platform.SENSOR]
SCAN_INTERVAL = timedelta(minutes=5)

NAME = "Alpha ESS"
VERSION = "0.0.6"
ISSUE_URL = "https://github.com/CharlesGillanders/homeassistant-alphaESS/issues"

STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""
