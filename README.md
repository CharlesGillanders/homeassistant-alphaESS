# homeassistant-alphaESS
![Project Stage](https://img.shields.io/badge/project%20stage-in%20production-green.svg?style=for-the-badge)
![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)


Monitor your energy generation, storage, and usage data using the official Open API from Alpha ESS

## AlphaESS OpenAPI

In November 2023, AlphaESS introduced a new web API for their own web client and developers were officially encouraged to migrate to using the AlphaESS Open API published at [https://open.alphaess.com/](https://open.alphaess.com/).  This component has been updated to use that Open API.  Anyone wanting to use this component in Home Assistant will first need to register their own inverter with the AlphaESS Open API developer portal.

1. Navigate to [https://open.alphaess.com/](https://open.alphaess.com/) and chose the option to register an account.
2. Once registered and logged in follow the instructions in your inverters manual to find your inverter SN and CheckCode, See example [here](https://imgur.com/a/Xm5t1s0)
3. Add your inverter to the developer portal using your SN and CheckCode

## Modifying existing installs to use the new OpenAPI

If you had previously been using this custom component in Home Assistant you will need to change to use the new authentication mechanism required by the AlphaESS OpenAPI.  

1. First upgrade the HomeAssistant component to at least version 0.4.0 and then restart your HomeAssistant
2. In HomeAssistant navigate to Settings / Devices & Services / AlphaESS
3. Look for the pane labeled Integration entries and click on the "3 dots" menu to the right of your existing AlphaESS service.
4. Delete the existing AlphaESS service.
5. Click Add Entry to add a new AlphaESS service
6. Provide the AppID and AppSecret for your account on the Alpha ESS OpenAPI developer portal.
7. The new service will be created keeping the same entity/device names as before.


## Installation using HACS

1. Use [HACS](https://hacs.xyz/docs/setup/download), in `HACS > Integrations > Hamburger Menu > Custom Repositories add https://github.com/CharlesGillanders/homeassistant-alphaESS with category set to integration.
2. in `HACS > Integrations > Explore & Add Repositories` search for "alphaess". 
3. Restart Home Assistant.
4. Enable Advanced Mode using Profile (click on your username at the bottom of the navigation column) -> Advanced Mode -> On
5. Log out of HomeAssistant and back in again
6. In the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Alpha ESS".
7. You will be prompted for the AppID and AppSecret for your account on the Alpha ESS OpenAPI developer portal.

## Manual Installation

1. Make a custom_components/alphaess folder in your Home Assistant file system.
2. Copy all the files and folders from this repository into the custom_components/alphaess folder
3. Restart Home Assistant
4. Enable Advanced Mode using Profile (click on your username at the bottom of the navigation column) -> Advanced Mode -> On
5. Log out of HomeAssistant and back in again
6. Setup this integration for your Alpha ESS energy storage system in Home Assistant via `Configuration -> Integrations -> Add -> Alpha ESS`
7. You will be prompted for the AppID and AppSecret for your account on the Alpha ESS OpenAPI developer portal.



## Alpha ESS: GUI based Set Battery Charge/Discharge Times information<br>

This is currently in early testing and comes with some caveats enforced by the OpenAPI and AlphaESS, This includes;
- Charging can only be set once every 10 minutes 
- Discharging can only be set once every 10 minutes
- The reset button calls both set Charging and set Discharging

Some values are set by default, this includes 
- updateChargeConfigInfo: gridcharge = 1 
- updateDisChargeConfigInfo: ctrDis = 0

Setting the bathighcap and batusecap only save it to hass data (making it persistent across restarts)
and will only be applied when the respective button (or the reset button) is pressed

A error will be placed in the logs 

The current charge config, discharge config and charging range will only update once the API is re-called (can be up to 1 min)

## Issues with registering systems to the AlphaESS OpenAPI

There has been a few issues regarding registering systems to the AlphaESS OpenAPI.  The following are some of the issues that have been reported and how to resolve them.

### Issue: Unable to register system to AlphaESS OpenAPI (not receiving verification code) 

If you are unable to register your system to the AlphaESS OpenAPI because you are not receiving the verification code, you can try the following steps to resolve the issue:
1. Access the current postman collection library for the AlphaESS OpenAPI [here](https://www.postman.com/poshy163/alphaess/collection/tsy43t1/alphaess-open-api?action=share&creator=11219653) (you will need to fork the collection)
2. Clicking on the root of the list of API calls (should be called AlphaESS Open API) and then click on the variables tab fill in your AppID, AppSecret, systemSN and CheckCode into the initial and current value fields. 
3. Click on the getVerificationCode GET API call followed by the send button to send the request.  You should receive a verification code either in the response body or by email.


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
