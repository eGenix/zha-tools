# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-09-26

### Added

- `zha_tools.ping` action: checks that a device answers by reading an attribute from it (bypassing all caches), with the same `device_id` / `all_devices`, `timeout`, `max_retries` and `retry_delay` fields and response as `reconfigure` and `reinterview`. Unlike those, it is also sent to devices ZHA marks unavailable, so an answer brings them back. After the first failed attempt, the device's network address is looked up once, to find devices that rejoined under a new address. Statuses: `complete`, `timeout`, `unavailable`.

## [0.3.0] - 2026-09-20

### Added

- `all_devices: true` option for `zha_tools.reconfigure` and `zha_tools.reinterview`: instead of a single `device_id`, the action works through every ZHA device except the coordinator, one at a time. The next device is started once the previous one has finished, whatever its outcome, and the response reports the per-device results together with `total`, `complete` and `failed` counts. Only one batched run can be in progress at a time; a second one is refused. The dangerous `rejoin` action deliberately has no such option.

### Changed

- Simplified the logo to the "ZHA TOOLS" wordmark only, removing the decorative hexagon graphic.

## [0.2.0] - 2026-06-21

### Added

- `zha_tools.reinterview`: re-interview a ZHA device in place (rediscover its endpoints and clusters) without removing it from the network, with the same retry behaviour as reconfigure. Safe — the device never leaves the network.
- `zha_tools.rejoin`: ask a ZHA device to leave the network and immediately rejoin it (a remote substitute for the pairing button), for installed devices whose button is hard to reach. **Dangerous**: if the device firmware ignores the rejoin flag it leaves and does not return, needing a manual re-pair; it is therefore never retried, requires an explicit `confirm: true` parameter, and is only sent to a reachable device.

## [0.1.0] - 2026-06-21

Initial release of ZHA Tools, a Home Assistant custom integration that provides a growing collection of helper actions for ZHA (Zigbee Home Automation) devices, callable from automations and scripts.

### Added

- `zha_tools.reconfigure`, the first helper: runs a ZHA device reconfigure (the same operation as the "Reconfigure device" button) with automatic retries. The per-attempt timeout, maximum number of retries and delay between attempts are configurable as defaults and overridable per call. The action returns the outcome (`complete`, `incomplete`, `timeout` or `unavailable`) and the number of attempts made.
- UI-based setup and configuration through Home Assistant's config and options flows.
- Debug logging of each attempt and of the binding / configure-reporting results reported back by ZHA.
- A bundled brand logo with light and dark variants, served directly by the integration on Home Assistant 2026.3.0 and newer.

[0.4.0]: https://github.com/egenix/zha-tools/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/egenix/zha-tools/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/egenix/zha-tools/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/egenix/zha-tools/releases/tag/v0.1.0
