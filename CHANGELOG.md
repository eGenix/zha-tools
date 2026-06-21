# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A colorful project logo (`icons/logo.svg`, with rendered PNG variants),
  shown at the top of the README.
- Brand icons in `custom_components/zha_tools/brand/` so the integration serves
  its own logo directly (Home Assistant 2026.3.0+ Brands Proxy API; no brands
  repository submission needed), including dark-theme (`dark_*`) variants.
  `make validate` checks the icon dimensions.
- Logo tooling under `tools/`, runnable via `make logo`, that builds the logo
  SVGs and renders the brand PNGs.
- Detailed logging of each reconfigure: the action and its parameters, every
  binding and configure-reporting result reported back by ZHA (at debug level),
  a per-attempt summary, and the final outcome.

### Changed

- Renamed the integration from "ZHA Reconfigure" to "ZHA Tools" (domain
  `zha_reconfigure` -> `zha_tools`). The action is now `zha_tools.reconfigure`.
- The default per-attempt timeout is now 20 seconds (was 10 seconds).
- A reconfigure is now reported as `complete` when at least one binding and at
  least one configure-reporting step are reported back as successful, instead of
  requiring every step to succeed. This better matches flaky devices where only
  some clusters succeed.

## [0.1.0] - 2026-06-20

### Added

- Initial release of the ZHA Tools Home Assistant custom integration.
- New `zha_tools.reconfigure` action that programmatically runs a ZHA
  device reconfigure.
- Configurable per-attempt timeout (default: 10s), maximum number of retries
  (default: 10) and delay between retries (default: 5s).
- The action returns a status of `complete`, `incomplete`, `timeout` or
  `unavailable` describing the outcome of the reconfigure operation.
- Config flow for setting up the integration.
- Options flow for adjusting the integration settings after setup.
