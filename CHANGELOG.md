# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-21

Initial release of ZHA Tools, a Home Assistant custom integration that provides a growing collection of helper actions for ZHA (Zigbee Home Automation) devices, callable from automations and scripts.

### Added

- `zha_tools.reconfigure`, the first helper: runs a ZHA device reconfigure (the same operation as the "Reconfigure device" button) with automatic retries. The per-attempt timeout, maximum number of retries and delay between attempts are configurable as defaults and overridable per call. The action returns the outcome (`complete`, `incomplete`, `timeout` or `unavailable`) and the number of attempts made.
- UI-based setup and configuration through Home Assistant's config and options flows.
- Debug logging of each attempt and of the binding / configure-reporting results reported back by ZHA.
- A bundled brand logo with light and dark variants, served directly by the integration on Home Assistant 2026.3.0 and newer.

[0.1.0]: https://github.com/egenix/zha-tools/releases/tag/v0.1.0
