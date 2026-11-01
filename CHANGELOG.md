# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.1] - 2026-11-01

### Changed

- `zha_tools.frame_counter` no longer writes anything to the log: no counter values, no notice that a read took place, also not in all-devices runs or at debug level. The warning / error log levels for counters above 100 / 1000 are gone. Results are only returned in the response; errors are still raised (single reads) or returned as the device's result (all-devices runs).

## [0.6.0] - 2026-11-01

### Added

- `zha_tools.query_route` action: reports everything the coordinator knows about the route between it and a device, naming each hop by its NWK address, IEEE address and display name. Combines the device's last route record (device → coordinator), the radio's source route and route table entry (coordinator → device), and a path rebuilt from the last topology scan. Also lists the device's parents, the routers that hear it (with LQI), the radio's neighbor entry for it (with in/out link costs), its own neighbors and its route back to the coordinator. Nothing is sent over the air. Radio tables are read on Silicon Labs (EZSP) radios only, and reused for 15 seconds so all-devices runs don't re-read them per device. `complete_paths` tells which sections hold a full path. Accepts `device_id` or `all_devices` (the coordinator itself is refused). Statuses: `complete`, `no_route`.
- `zha_tools.retry_action` action: calls any other action (e.g. `climate.set_temperature`, `cover.close_cover`) again and again until it goes through, which for ZHA entities means the device acknowledged the command. Waits between attempts grow with a slow exponential backoff. Configurable, bounded `timeout` (overall, default 600 s), `attempt_timeout` (30 s), `retry_delay` (initial wait, 5 s), `backoff` (1.5) and `max_delay` (120 s). While a targeted entity is unavailable the action is not called and the attempt counts as failed (Home Assistant would otherwise skip the entity and report success). Invalid calls and `zha_tools` actions are not retried; the wrapped action runs with the caller's permissions and its data is never logged. Only wrap actions that can safely run more than once. Statuses: `complete`, `timeout`.

### Changed

- All action responses now include `device_name`, the device's display name in Home Assistant. This includes the per-device entries of all-devices runs, also for devices that raised an error.
- `zha_tools.frame_counter` no longer writes the counter value to the log, also not in the service's debug output. The log level still shows whether it is above the warning (100) or error (1000) threshold, and the value is still returned in the response.
- All actions except `retry_action` are now admin-only. Automations and scripts are not affected; non-admin users calling them from the UI get an "unauthorized" error.
- The `timeout`, `max_retries`, `retry_delay` and `rejoin_wait` fields now enforce the same upper limits as the options flow and the UI (values like `inf` were accepted before and could retry or wait forever).
- Reconfigures started by this integration now run one at a time, as ZHA's progress events carry no device id and parallel runs mixed up their results.

### Fixed

- `zha_tools.rejoin` could report `complete` for a device that never came back: ZHA removes a device on leave, and the stale device object kept reading as available. The device is now looked up again after `rejoin_wait`, and only counts as back if ZHA heard from it after the leave. The docs now explain that a rejoin removes and re-adds the device.
- `zha_tools.ping` no longer aborts its retries when the network address lookup cannot be sent.
- `zha_tools.reinterview` now fails with a clear error on ZHA versions without in-place re-interviews, instead of an `AttributeError`.

## [0.5.0] - 2026-10-31

### Added

- `zha_tools.frame_counter` action: reads the incoming network (NWK) frame counter the coordinator stores for a device, plus its IEEE address and when it was last seen, to help track down the devices behind rising `NWK_FRAME_COUNTER_FAILURE` counts. Only direct neighbors of the coordinator have such a counter; other devices are reported as `not_neighbor`. Accepts `device_id` or `all_devices`. Needs a Silicon Labs (EZSP version 8 or later) radio. The counter is logged as a warning if it is higher than 100, and as an error if it is higher than 1000.

### Changed

- The response of all actions is now optional (`SupportsResponse.OPTIONAL`), so calls without a `response_variable` no longer fail. The documentation now uses `zha_response` as the standard response variable name.
- Log messages now name devices by their Home Assistant display name, followed by the device id, e.g. on timeouts and errors.

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

[0.6.1]: https://github.com/egenix/zha-tools/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/egenix/zha-tools/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/egenix/zha-tools/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/egenix/zha-tools/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/egenix/zha-tools/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/egenix/zha-tools/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/egenix/zha-tools/releases/tag/v0.1.0
