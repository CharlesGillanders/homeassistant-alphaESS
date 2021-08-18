#MQTT Server Settings - Most likely the same for test and prod, but lets be consistent with the settings.
BROKER="test.mosquitto.org"
PORT=1883

#AlphaESS site settings
USER="username"
PASS="password"

#Topics to send to HA
PV="Alpha/PV"
SOC="Alpha/SOC"
BAT="Alpha/BAT"
LD="Alpha/Load"
GD="Alpha/GRID"