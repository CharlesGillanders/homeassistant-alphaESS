# homeassistant-alphaESS
![Project Stage](https://img.shields.io/badge/project%20stage-alpha%20testing-green.svg?style=plastic)
![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/CharlesGillanders/homeassistant-alphaESS)

Monitor your energy generation, storage, and usage data using an unofficial API from Alpha ESS

## Setup integration

1. Make a custom_components/alphaess folder in your Home Assistant file system.
2. Copy all of the files and folders from this repositary into that custom_components/alphaess folder
3. Restart Home Assistant
4. Enable Advanced Mode using Profile (click on your username at the bottom of the navigation column) -> Advanced Mode -> On
5. Setup this integration for your Alpha ESS energy storage system in Home Assistant via `Configuration -> Integrations -> Add -> Alpha ESS`
6. You will be prompted for the username and password for your account on the Alpha ESS website/app
