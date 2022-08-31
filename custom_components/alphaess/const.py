"""Constants for the Alpha ESS integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "alphaess"
PLATFORMS = [Platform.SENSOR]
SCAN_INTERVAL = timedelta(minutes=1)

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
