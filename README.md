# homeassistant-alphaESS
![Project Stage](https://img.shields.io/badge/project%20stage-in%20production-green.svg?style=for-the-badge)
![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)


Monitor your energy generation, storage, usage data and electric vehicle using the official Open API from Alpha ESS

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



## Alpha ESS: GUI based Set Battery Charge/Discharge Times information

These settings will only use slot 1 for charging and discharging, while you are not able to modify slot 2 from this integration, you are able to view its current settings

Alpha has recently, and unannounced removed most of the restrictions around the POST API calls that can be made. The current restrictions are:
- Charging can only be set once every 30s 
- Discharging can only be set once every 30s 
- The reset button calls both set Charging and set Discharging

Some values are set by default, this includes 
- updateChargeConfigInfo: gridcharge = 1 
- updateDisChargeConfigInfo: ctrDis = 0

Setting the bathighcap and batusecap only save it to hass data (making it persistent across restarts)
and will only be applied when the respective button (or the reset button) is pressed

An error will be placed in the logs 

The current charge config, discharge config and charging range will only update once the API is re-called (can be up to 1 min)

If you want to adjust the restrictions yourself, you are able to by modifying the `ALPHA_POST_REQUEST_RESTRICTION` variable in const.py to the amount of seconds allowed per call

### Time Window Calculation

The time window calculation has been updated to ensure that charge/discharge periods start immediately when activated. The system now:
- Rounds the current time to the next 15-minute interval
- Sets the start time to 15 minutes BEFORE the rounded time (alphaess requires a 15-minute interval to be set)
- Calculates the end time based on the selected duration

This ensures the current time always falls within the configured window, allowing immediate effect.

#### Examples:

**Example 1: 30-minute charge at 10:23**
- Current time: 10:23
- Rounded to: 10:30
- Start time: 10:15 (10:30 - 15 minutes)
- End time: 10:45 (10:15 + 30 minutes)
- Result: Charging window 10:15 - 10:45

**Example 2: 60-minute discharge at 14:46**
- Current time: 14:46
- Rounded to: 15:00
- Start time: 14:45 (15:00 - 15 minutes)
- End time: 15:45 (14:45 + 60 minutes)
- Result: Discharging window 14:45 - 15:45

**Example 3: 15-minute charge at 09:02**
- Current time: 09:02
- Rounded to: 09:15
- Start time: 09:00 (09:15 - 15 minutes)
- End time: 09:15 (09:00 + 15 minutes)
- Result: Charging window 09:00 - 09:15

This approach maintains the 15-minute interval alignment while ensuring the battery immediately begins charging or discharging when the button is pressed.
## Local Inverter Support

To use the local inverter support, you will need to have a local inverter that is able to reach your HA instance (preferably on the same subnet). 

To add a local inverter to an existing AlphaESS integration, you will need to select the "Configure" option from the AlphaESS integration in Home Assistant, and then input your inverter's IP address, you can also do this if you need to reconfigure your inverter's IP address (due to DHCP changes, etc).

To remove/reset the local inverter integration, you will need to go back to the configuration settings, and set it to 0. (this will "remove" all the sensors linked, and will need to be manually deleted)

For now, if you have more than one inverter linked to your OpenAPI Account, the local inverter settings will only work on the first inverter that is linked to your account. support for setting it to be a custom one is coming.

![](https://i.imgur.com/rHWI2gh.png)


## Issues with registering systems to the AlphaESS OpenAPI

There has been a few issues regarding registering systems to the AlphaESS OpenAPI.  The following are some of the issues that have been reported and how to resolve them.

### Issue: Unable to register system to AlphaESS OpenAPI (not receiving verification code) 

If you are unable to register your system to the AlphaESS OpenAPI because you are not receiving the verification code, you can try the following steps to resolve the issue:
1. Access the current postman collection library for the AlphaESS OpenAPI [here](https://www.postman.com/poshy163/alphaess/collection/tsy43t1/alphaess-open-api?action=share&creator=11219653) (you will need to fork the collection)
2. Clicking on the root of the list of API calls (should be called AlphaESS Open API) and then click on the variables tab and fill in your AppID, AppSecret, systemSN and CheckCode into the initial and current value fields. 
3. Click on the getVerificationCode GET API call followed by the send button to send the request.  You should receive a verification code either in the response body or by email.

### Issue: Entities becoming unavailable/not working and/or defaulting to 0

Newer AlphaESS Inverters have a firmware that can introduce incompatibilities with the current iteration of the integration. This new inverters has caused issues with the API calls that are currently supported and the data currently sent.

If you would like to help improve it for your specie inverter, please open an issue with the following information:

Use the postman collection found [here](https://github.com/CharlesGillanders/alphaess-openAPI/blob/main/AlphaESS%20Open%20API.postman_collection.json) you will need an account here: https://www.postman.com/

In the variables tab you need to edit the Initial Value and Current Value fields for AppID, AppSecret, and SysSN. (found within the openAPI developer portal) and the AppID and AppSecret from https://open.alphaess.com/

The SysSN is the serial number of your invertor and the AppID and AppSecret are your AppID and AppSecret from https://open.alphaess.com/, once you have set all three variables in both current and initial value hit the save button.

Then you should run each of the API calls in turn - one after the other and paste the results here, removing your SysSN for confidentiality.

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
