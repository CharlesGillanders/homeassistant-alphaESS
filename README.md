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



## Charge and discharge schedules

The periodic schedule API (`getTimeChargeBySn`/`setTimeChargeBySn`) — the
list-based schedule used by recent versions of the AlphaESS app — is the
**primary** schedule store. Whenever AlphaESS lets your developer account read
it, it is the only store the integration touches.

The older fixed-slot endpoints (`getChargeConfigInfo`/`updateChargeConfigInfo`,
`getDisChargeConfigInfo`/`updateDisChargeConfigInfo`) remain solely as a
**backup**: a definitive `6017 No operation permissions` on the periodic read
switches that system to the legacy two-slot surface, because until AlphaESS
grants the permission it is the only control path the OpenAPI offers. Ask
AlphaESS to enable the timed charge/discharge permission for your developer
account to move to the primary store.

A system is always in exactly one mode — the two stores are never read
together, merged, or mirrored. Issue #269 showed what mixing them does:
hidden values, phantom conflicts, and writes the server accepts without
acting on. Two backup-mode caveats, from live probing of the new server:
the inverter there may not act on the legacy *discharge* store even though it
accepts the write, and per-period **power** setpoints exist only in the
periodic store, so the Power entities stay unavailable in backup mode. A
system whose periodic *read* works but whose *write* is refused stays
fail-closed rather than falling back: the app-facing periodic store governs
that inverter, and editing a store it ignores would only mislead.

### Editing schedules with Home Assistant entities

Changes to charge/discharge times, cutoff SOC values, enable switches, and
per-slot power values are staged locally. They are not sent while you are still
editing related fields. Times are rounded to the nearest 15 minutes, as required
by AlphaESS.

AlphaESS has no "empty" time value — an unused slot is stored as start equal
to end (usually `00:00`–`00:00`). The time entities show such slots as
**unknown** and the period sensors as **Not set**, rather than pretending a
zero-length midnight window is configured. Setting both halves of a slot to
different times brings it to life; the write path already treats equal times
as "slot off". A half you have just staged keeps showing the staged time even
while it still matches the other half, so a half-finished edit does not read as
a lost one.

1. Edit all of the related time, SOC, switch, and power entities.
2. Select **Apply Charge/Discharge Schedule** to send the complete draft.
3. Select **Discard Schedule Changes** instead to abandon the draft and return to
   the latest remote values.

The Apply and Discard buttons are available only while a draft exists. Normal
polling keeps the draft visible instead of resetting an end time while the other
fields are being edited. A failed or conflicted Apply also keeps the draft so it
can be reviewed, retried, or discarded. Drafts are held only for the current
integration session, so apply or discard them before reloading Home Assistant or
the config entry. An entity's Home Assistant `Last changed` timestamp records a
local draft edit; it is not proof that AlphaESS accepted or activated a schedule.
Apply and Discard are temporarily unavailable while an Apply transaction is in
flight. If another entity edit arrives before that transaction finishes, the
accepted snapshot is reported and the newer edit remains pending for another
Apply.

The entity UI exposes the first two charge periods and first two discharge
periods. When a readable periodic schedule already exists, editing those periods
preserves its cycle type, weekday selections, per-period power, and any additional
periods that the UI does not expose. Changing a side's cutoff SOC applies that
limit only to the first two slots represented by Home Assistant; extra app-only
periods keep all of their writable fields unchanged.

Duration charge/discharge buttons and service calls are immediate actions;
they do not create a draft and do not require the Apply button. They still
fail safely when the existing periodic resource cannot form a complete valid
replacement. The charge/discharge services accept optional per-slot power
fields, which are required when the call creates a new periodic slot.

**Reset Charge/Discharge** exists only for backup mode, where it zeroes both
legacy two-slot stores. The periodic schedule cannot be cleared through the
API (empty period lists are rejected), so on periodic-governed systems the
button reports unavailable — clear the schedule in the AlphaESS app instead.

The duration buttons retarget only period/slot 1 of their side, carrying its
existing power and cutoff over, and leave every other period untouched — in
backup mode, slot 2 is likewise preserved from a fresh read. On a weekly
periodic schedule a duration button refuses to retarget a first period that
does not run today: AlphaESS would accept the write, nothing would happen
now, and the period's other weekdays would be silently rescheduled. A press
rejected by the 30-second cooldown fails with a visible error instead of
quietly doing nothing.

The one-sided duration buttons and `setbatterycharge`/`setbatterydischarge`
services cannot initialise a completely empty periodic resource: AlphaESS requires
both a charge list and a discharge list in the same replacement. They require the
opposite list and a valid power for the target slot to exist already. To initialise
both sides, stage the charge and discharge entities (including explicit power and
SOC values) and use Apply. Pending entity drafts are deliberately not consumed by
the immediate buttons or services.

### Driving schedules from an automation (Predbat and similar)

An automation has two surfaces here, and an inverter should use only one of
them:

- **Immediate** — `alphaess.setbatterycharge` / `alphaess.setbatterydischarge`.
  One call is one complete transaction. Every time field is required, so each
  call rewrites both slots of its side: a slot passed as `00:00`–`00:00` is
  removed from a periodic schedule, including one the AlphaESS app created.
- **Staged** — write the time, cutoff SOC, power and switch entities, then press
  **Apply Charge/Discharge Schedule** with `button.press`. This is the surface an
  optimiser should use: a whole plan change becomes a single API write, slots
  that are not named keep their values, and nothing has to be restated.

Do not mix them on one inverter. A service write replaces the remote schedule,
so a draft staged before it fails its next Apply as a conflict.

Default entity IDs are `<domain>.<serial in lower case>_<name>`. Installs that
predate the serial-prefixed rename keep their original IDs, which carry the
device name as well (`sensor.home_alpha_ess_energy_statistics_<serial>_...`) —
check the entity list rather than assuming the short form:

| Purpose | Entity |
| --- | --- |
| Charge window 1 | `time.<sn>_charge_start_time_1`, `time.<sn>_charge_end_time_1` |
| Discharge window 1 | `time.<sn>_discharge_start_time_1`, `time.<sn>_discharge_end_time_1` |
| Enable flags | `switch.<sn>_scheduled_charging`, `switch.<sn>_scheduled_discharging` |
| Cutoff SOC | `number.<sn>_charge_cut_off_soc`, `number.<sn>_discharge_to_soc` |
| Per-period power (periodic systems only) | `number.<sn>_charge_power_1`, `number.<sn>_discharge_power_1` |
| Commit or abandon a draft | `button.<sn>_apply_charge_discharge_schedule`, `button.<sn>_discard_charge_discharge_schedule` |
| Which store governs the system | `sensor.<sn>_periodic_schedule_read` |
| Timed control running | `binary_sensor.<sn>_time_based_control_active` |

#### Predbat

Predbat already drives inverters that stage settings and then press an apply
button (`time_button_press`), so it needs no custom service templates — only a
custom inverter definition in `apps.yaml`. Everything not listed is inherited
from Predbat's GivEnergy defaults, and slot 2 is left to the AlphaESS app.

```yaml
  inverter_type: ALPHA
  inverter:
    name: AlphaESS
    has_rest_api: False
    output_charge_control: "none"      # per-period power is not an inverter charge rate
    has_charge_enable_time: True
    has_discharge_enable_time: True
    has_target_soc: True
    has_reserve_soc: False             # Discharge To SOC is a cutoff, not a Predbat reserve
    has_timed_pause: False
    has_ge_inverter_mode: False
    charge_time_entity_is_option: False
    soc_units: "%"
    time_button_press: True            # press Apply once the draft is staged
    write_and_poll_sleep: 2
    support_charge_freeze: False
    support_discharge_freeze: False
    can_span_midnight: False

  # Control
  charge_start_time: time.<sn>_charge_start_time_1
  charge_end_time: time.<sn>_charge_end_time_1
  scheduled_charge_enable: switch.<sn>_scheduled_charging
  charge_limit: number.<sn>_charge_cut_off_soc
  discharge_start_time: time.<sn>_discharge_start_time_1
  discharge_end_time: time.<sn>_discharge_end_time_1
  scheduled_discharge_enable: switch.<sn>_scheduled_discharging
  charge_discharge_update_button: button.<sn>_apply_charge_discharge_schedule

  # Read
  soc_percent: sensor.<sn>_instantaneous_battery_soc
  soc_max: sensor.<sn>_installed_capacity
  inverter_limit: sensor.<sn>_inverter_nominal_power
  battery_rate_max: 5000               # W, from the battery spec; the inverter
                                       # nominal rating is not a battery rate
```

With more than one inverter, Predbat expects the `inverter:` block and each
argument as a list, one entry per inverter. The keys inside `inverter:` belong
to Predbat, not to this integration; check them against your Predbat version.

What this setup depends on:

- **Charge period 1 and discharge period 1 must already exist**, created in the
  AlphaESS app. An unused slot is stored as start equal to end and reads as
  `unknown`, which Predbat cannot parse as a window, and no one-sided write can
  create both sides at once.
- **Leave Scheduled Discharging on.** Predbat turns the charge enable switch off
  while it moves a window, and Home Assistant refuses to author the
  both-timers-off state because it is indistinguishable from a self-consumption
  mode.
- **Times are rounded to the nearest 15 minutes.** Predbat reads an entity back
  after writing it and warns when the value differs, so keep
  `inverter_clock_skew_start` and `inverter_clock_skew_end` at 0 and expect that
  warning for any window that does not land on a quarter hour. The schedule
  still applies, rounded.
- **Predbat cannot see whether an Apply succeeded.** Home Assistant timestamps a
  button press before the write is attempted, so every press looks successful to
  it. The real outcome — accepted, refused with the API's return code, or
  explicitly unknown after a timeout - is raised in Home Assistant and, where
  relevant, as a persistent notification. Look there when the plan and the
  inverter disagree.
- **Each Apply is one full transaction** (fresh read, then one write), and moving
  a window costs two because Predbat disables the charge flag first. Weigh that
  against the request limits described below.
- **Set both enable switches once before letting Predbat write.** The periodic
  API cannot report whether scheduled charging and discharging are on, so until
  Scheduled Charging and Scheduled Discharging have an answer every write is
  refused — see *The enable flags are write-only* below. Recording both as off
  additionally locks the time-based controls, since that is how the API is told
  to run self-consumption.

If you drive the services from Predbat instead, note two things about its
service templates: falsy values are dropped before the call, so `enabled: false`
is never sent and the service is rejected for a missing required field; and
`{charge_start_time}` resolves to the entity named by the `charge_start_time`
argument rather than to the plan, defaulting to `00:00:00` when that argument is
absent — which asks this integration to delete the period rather than to move
it.

### Safe writes and external-change conflicts

Before an Apply or immediate action, the integration reads a fresh copy of
the store the system runs on — the app-facing periodic resource, or in
backup mode the legacy two-slot resources — patches only the requested
fields, and serialises writes per inverter.

When a draft is opened, the integration records the remote schedule on which it
was based. If the remote schedule changes before Apply, the write is rejected
as a conflict instead of overwriting the newer schedule. Discard the draft,
allow fresh values to load if necessary, review them, and stage the change
again. Immediate actions and services have no long-lived draft, but still start
from a fresh read.

The binary sensor **Time Based Control Active** reports whether any timed
charge/discharge control is enabled in whichever store governs the system
(either enable flag set). It turns off when the inverter runs a non-timed
mode such as **Self Consumption (Plus)** — live probing showed those modes
flip both enable flags to `0` while keeping the configured windows stored —
and is unavailable while no schedule store is readable.

### The enable flags are write-only

`gridChargeCycle` and `ctrDisCycle` decide whether scheduled charging and
discharging run. On the periodic store they are **write-only**: probing on
issue #267 showed `getTimeChargeBySn` answers `0` for both however the inverter
is actually set, while `setTimeChargeBySn` acts on what it is sent.

| sent | effect on the inverter |
| --- | --- |
| `1 / 0` | schedule active, scheduled discharging off |
| `1 / 1` | both schedules enabled |
| `0 / 1` | scheduled discharging only |
| `0 / 0` | **switches the inverter to self-consumption** |

Two consequences follow, and the integration is built around them.

**The read is never echoed back.** A replacement built from the read would post
`0/0` on every write and quietly switch the inverter out of timed control.
Snapshots therefore drop both flags, and every write sends a value that came
from the user instead.

**The switches are the record.** *Scheduled Charging* and *Scheduled
Discharging* are the only place that answer can live, so their published state
is restored across restarts and handed back to the coordinator. Until both have
an answer they read **unknown**, and a schedule write is refused with a message
naming them rather than guessing which working mode you wanted. Editing and
staging still work; only the write needs the answer.

Once both are recorded, a request to set them to `0/0` is a real one — it is how
the API is told to run self-consumption — so it locks the time-based controls:
time entities, cutoff SOC and power numbers, duration buttons, Reset,
Apply/Discard, and any service call carrying a window, cutoff or power. The two
switches stay available so the inverter can be brought back to a timed mode from
Home Assistant, and turning one on there is sent immediately rather than staged,
since Apply is unavailable in that state. Home Assistant still refuses to author
`0/0` by turning off the *last* enabled timer from a service.

In legacy backup mode `gridCharge` and `ctrDis` are ordinary read-write fields
and none of the above applies: they are read from the store, reported as-is, and
a mode change made in the AlphaESS app is picked up on the next poll.

The diagnostic sensor `Periodic Schedule Read` reports:

- `readable` - the periodic resource can be read; the primary store governs;
- `unreadable` - AlphaESS definitively rejected the GET with error `6017`.
  The system runs on the legacy backup: schedule controls work through the
  two-slot endpoints, except the per-period Power entities, which exist only
  in the periodic store; or
- `unknown` - the integration has not yet obtained a definitive usable response.

An empty or malformed response, a transient error, or an `unknown` read state
fails closed: schedule entities remain unavailable and writes are refused
until a full periodic read succeeds. The integration never builds a
replacement schedule from anything but a fresh read, because doing so could
erase weekly settings, extra periods, or power values that it cannot see.

### AlphaESS API limitations

- The periodic API requires at least one complete charge period **and** one
  complete discharge period: an empty list answers `6001` and a missing one
  `10001`. The integration never inserts a fake `00:00-00:00` period. If either
  list would be empty, Apply fails rather than replacing an app-managed schedule
  with incomplete data. AlphaESS itself will happily *store* an empty list, so a
  system with no discharge periods cannot be written to from Home Assistant at
  all until one exists — add a period to that side in the app, or give it one and
  turn its switch off, which is how the API is told to run a single side.
- Both a start and end time must be set before adding a new period. Existing
  weekly periods can be edited while retaining their weekdays, but the entity UI
  cannot add a new weekly period because it has no weekday selector.
- Existing per-period power values are preserved. Adding a brand-new period
  requires an explicit positive power value for that slot; the inverter's nominal
  `poinv` rating is not a verified battery charge/discharge rate and is never used
  as a fallback.
- Periodic SOC limits must be from 10% through 100%, and every period must contain
  a valid positive power value. Invalid remote data is reported instead of being
  silently clamped or replaced.
- The periodic schedule cannot be cleared through the API (empty period lists
  are rejected), which is why the Reset button works only in backup mode.
  Clear a periodic schedule in the AlphaESS app.
- AlphaESS reports `6008` only as generic `Set failed`. Overlapping charge and
  discharge windows are one documented cause, but the code is not conclusive. The
  log records the periods sent and retains the original API explanation.

A network timeout after a POST is not proof of rejection: the server might
have stored the request before the response was lost. Home Assistant reports that
outcome as unknown, retains the duration-button cooldown, and asks you to check the
AlphaESS app before retrying. In periodic mode the schedule lives in a single
store, so there is no partial-write case: a change either landed, was refused
with the API's own return code, or has that explicitly-unknown outcome. Backup
mode writes two separate stores, so a two-sided change there can partially
land; that is reported as a partial-write error naming what already applied.

### EV charger controls

The integration exposes EV charger controls (start/stop and current setting) when
an EV charger is detected. `Can Start Charging` and `Can Stop Charging` are
advisory binary sensors based on the latest successful EV poll. Start and stop
buttons remain available, and every requested command is sent to AlphaESS even if
the cached status is old or already appears to match it. This lets the server make
the authoritative decision; an API rejection is surfaced to Home Assistant
instead of the command being skipped locally. Read-only charger status values
continue to refresh even when charger control is unavailable for the account.
The upstream charger-config endpoint can return multiple chargers, while the
current Home Assistant entity model exposes the first one only. `EV Charger
Count` and a one-time warning make that limitation explicit instead of silently
discarding the list shape.

### Currency and daily history sensors

- Monetary sensors now use ISO 4217 currency codes when provided by the API, and fall back to Home Assistant's configured currency when not available.
- Diagnostic currency sensors are available for troubleshooting:
  - `Currency Code`
- Daily history energy breakdown sensors are exposed:
  - `Daily PV Generation`
  - `Daily Grid Consumption`
  - `Daily Feed-in`
  - `Daily Grid Charge`
  - `Daily Battery Charge`
  - `Daily Battery Discharge`
  - `Daily EV Charging Energy`
  - `Daily Energy Date`

### AlphaESS request limits

The upstream API asks clients to leave at least 10 seconds between requests and
can return `6053` when calls are too frequent. The integration serialises and
spaces cloud reads and writes accordingly, including bind/unbind operations, and
clamps fast polling to 10 seconds or slower.

In practice the integration runs calls at 1-second spacing: live probing of
the current server showed reads accepted at sub-second spacing without
`6053`, and at the documented 10 seconds a two-inverter full poll took
minutes — blocking Home Assistant startup and delaying app-side changes
(such as a work-mode switch flipping `Time Based Control Active`) by up to
five minutes. The documented pace remains the fallback: if the server ever
answers `6053`, that call is retried once and the session drops back to
10-second spacing permanently.

Staged Apply avoids the old burst of one full replacement per entity edit, but
the server remains authoritative and may reject an Apply, duration button, or
service call made too soon. The 30-second guard on the duration buttons
prevents accidental double presses.

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

Schedule services are immediate from Home Assistant's point of view: they perform
one fresh read/modify/write operation and do not use the staged entity draft or
the Apply/Discard buttons. The two AlphaESS schedule APIs are still separate
server operations, so the periodic limitations and partial-mirror behavior
described above apply. In particular, a one-sided service cannot bootstrap an
empty periodic resource because AlphaESS requires both lists in the same request.

This project allows you to use the following services in Home Assistant:<br>

### Alpha ESS: Set Battery Charge<br>
 
  This service call allows you to set the grid charge settings for your system. <br>
  Times are rounded to the nearest quarter hour and zero-padded before being sent, since the API rejects anything else. <br>
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
  Times are rounded to the nearest quarter hour and zero-padded before being sent, since the API rejects anything else. <br>
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
