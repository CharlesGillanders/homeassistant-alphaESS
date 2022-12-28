# homeassistant-alphaESS
![Project Stage](https://img.shields.io/badge/project%20stage-in%20production-green.svg?style=for-the-badge)
![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)


Monitor your energy generation, storage, and usage data using an unofficial API from Alpha ESS

## Installation using HACS

1. Use [HACS](https://hacs.xyz/docs/setup/download), in `HACS > Integrations > Hamburger Menu > Custom Repositories add https://github.com/CharlesGillanders/homeassistant-alphaESS with category set to integration.
2. in `HACS > Integrations > Explore & Add Repositories` search for "alphaess". 
3. Restart Home Assistant.
4. Enable Advanced Mode using Profile (click on your username at the bottom of the navigation column) -> Advanced Mode -> On
5. Log out of HomeAssistant and back in again
6. In the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Alpha ESS".
7. You will be prompted for the username and password for your account on the Alpha ESS website/app

## Manual Installation

1. Make a custom_components/alphaess folder in your Home Assistant file system.
2. Copy all of the files and folders from this repositary into that custom_components/alphaess folder
3. Restart Home Assistant
4. Enable Advanced Mode using Profile (click on your username at the bottom of the navigation column) -> Advanced Mode -> On
5. Log out of HomeAssistant and back in again
6. Setup this integration for your Alpha ESS energy storage system in Home Assistant via `Configuration -> Integrations -> Add -> Alpha ESS`
7. You will be prompted for the username and password for your account on the Alpha ESS website/app

## Services

This project allows you to use the following services in Home Assistant:<br>

### Alpha ESS: Set Battery Charge<br>
 
  This service call allows you to set the grid charge settings for your system. <br>
  Times are not validated and must be compatible with the Alpha values. <br>
  Data needed:<br>
    - serial = The serial of your system. <br>
    - enabled = True or False <br>
    - cp1start = Charging Period 1 Start Time <br>
    - cp1end = Charging Period 1 End Time <br>
    - cp2start = Charging Period 2 Start Time <br>
    - cp2end = Charging Period 2 End Time <br>

example:
```yaml
service: alphaess.setbatterycharge
data:
  serial: AA123456789
  enabled: True
  cp1start: "01:00"
  cp1end: "04:00"
  cp2start: "13:00"
  cp2end: "16:00"
  chargestopsoc: 100
```

### Alpha ESS: Set Battery Discharge<br>
 
  This service call allows you to set the battery discharge settings for your system. <br>
  Times are not validated and must be compatible with the Alpha values. <br>
  Data needed:<br>
    - serial = The serial of your system. <br>
    - enabled = True or False <br>
    - dp1start = Discharging Period 1 Start Time <br>
    - dp1end = Discharging Period 1 End Time <br>
    - dp2start = Discharging Period 2 Start Time <br>
    - dp2end = Discharging Period 2 End Time <br>

example:
```yaml
service: alphaess.setbatterydischarge
data:
  serial: AA123456789
  enabled: True
  dp1start: "01:00"
  dp1end: "04:00"
  dp2start: "13:00"
  dp2end: "16:00"
  dischargecutoffsoc: 10
```
