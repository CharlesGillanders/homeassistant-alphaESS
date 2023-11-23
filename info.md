[![GitHub Release][releases-shield]][releases]
![GitHub all releases][download-all]
![GitHub release (latest by SemVer)][download-latest]
[![GitHub Activity][commits-shield]][commits]

[![License][license-shield]][license]

[![hacs][hacsbadge]][hacs]
[![Project Maintenance][maintenance-shield]][user_profile]

Monitor your energy generation, storage, and usage data using the official Open API from Alpha ESS

## Setup integration

1. Enable Advanced Mode using Profile (click on your username at the bottom of the navigation column) -> Advanced Mode -> On
2. [![Add Integration][add-integration-badge]][add-integration] or in the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Alpha ESS"
3. You will be prompted for the AppID and AppSecret for your account on the Alpha ESS OpenAPI developer portal.

{% if not installed %}

## Installation

1. Click Download.
2. Reboot Home Assistant.
3. Enable Advanced Mode using Profile (click on your username at the bottom of the navigation column) -> Advanced Mode -> On
4. Hard refresh browser cache.
5. [![Add Integration][add-integration-badge]][add-integration] or in the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Alpha ESS".

{% endif %}


<!---->

---

[commits-shield]: https://img.shields.io/github/commit-activity/y/CharlesGillanders/homeassistant-alphaESS.svg?style=for-the-badge
[commits]: https://github.com/CharlesGillanders/homeassistant-alphaESS/commits/main
[hacs]: https://github.com/custom-components/hacs
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[discord]: https://discord.gg/Qa5fW2R
[discord-shield]: https://img.shields.io/discord/330944238910963714.svg?style=for-the-badge
[forum-shield]: https://img.shields.io/badge/community-forum-brightgreen.svg?style=for-the-badge
[forum]: https://community.home-assistant.io/
[license-shield]: https://img.shields.io/github/license/CharlesGillanders/homeassistant-alphaESS.svg?style=for-the-badge
[license]: LICENSE
[maintenance-shield]: https://img.shields.io/badge/maintainer-Charles%20Gillanders%20%40CharlesGillanders-blue.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/CharlesGillanders/homeassistant-alphaESS.svg?style=for-the-badge
[releases]: https://github.com/CharlesGillanders/homeassistant-alphaESS/releases
[user_profile]: https://github.com/CharlesGillanders
[download-all]: https://img.shields.io/github/downloads/CharlesGillanders/homeassistant-alphaESS/total?style=for-the-badge
[download-latest]: https://img.shields.io/github/downloads/CharlesGillanders/homeassistant-alphaESS/latest/total?style=for-the-badge
[add-integration]: https://my.home-assistant.io/redirect/config_flow_start?domain=alphaess
[add-integration-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
