"""Parent class for AlphaESSNames enum."""
from enum import Enum, unique


@unique
class AlphaESSNames(str, Enum):
    """Device names used by AlphaESS."""

    SolarProduction = "Solar Production"
    SolarToBattery = "Solar to Battery"
    SolarToGrid = "Solar to Grid"
    SolarToLoad = "Solar to Load"
    TotalLoad = "Total Load"
    GridToLoad = "Grid to Load"
    GridToBattery = "Grid to Battery"
    Charge = "Charge"
    Discharge = "Discharge"
    EVCharger = "EV Charger"
    Generation = "Instantaneous PV power"
    Load = "Instantaneous load power"
    BatterySOC = "Instantaneous battery soc"
    GridIO = "Instantaneous grid power"
    BatteryIO = "Instantaneous battery power"
    EVLoad = "Instantaneous EV power"
