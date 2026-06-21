# ZHA Tools

<p align="center">
  <img src="icons/logo.svg" alt="ZHA Tools logo" width="180" height="180">
</p>

ZHA Tools is a [Home Assistant](https://www.home-assistant.io/) custom integration that lets your automations programmatically run a ZHA (Zigbee Home Automation) device *reconfigure* — the same operation as the "Reconfigure device" button in the ZHA UI — with automatic retries. This makes it possible to reconfigure flaky Zigbee devices on a schedule, or in response to an event, without anyone having to click the button manually.

## Features

- Registers a single action/service, `zha_tools.reconfigure`, that triggers a ZHA device reconfigure from automations and scripts.
- Automatically retries the reconfigure when it does not complete cleanly.
- Configurable per-attempt timeout, maximum number of retries, and delay between attempts — set sensible defaults once and override them per call.
- Returns structured response data (`device_id`, `status`, `attempts`) so automations can react to the outcome.
- Reports a clear status: `complete`, `incomplete`, `timeout`, or `unavailable`.
- UI-based setup and configuration via Home Assistant's config and options flows.

## Requirements

- Home Assistant **2025.8.0** or newer.
- The ZHA integration must already be set up, with the device(s) you want to reconfigure paired and managed by ZHA.

## Installation

### HACS (recommended)

1. In HACS, add this repository as a custom repository with the category **Integration**.
2. Install the **ZHA Tools** integration from HACS.
3. Restart Home Assistant.
4. Add the integration via **Settings -> Devices & Services -> Add Integration**, then search for **ZHA Tools**.

### Manual

1. Copy the `custom_components/zha_tools` directory into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Add the integration via **Settings -> Devices & Services -> Add Integration**, then search for **ZHA Tools**.

## Configuration

After the integration is added, you can configure the default behaviour of the action via **Settings -> Devices & Services -> ZHA Tools -> Configure**.

The following options are available:

- **timeout** (default `20`) — the per-attempt timeout, in seconds. A single reconfigure attempt that does not finish within this time is reported as a `timeout` and (if retries remain) retried.
- **max_retries** (default `10`) — the maximum number of retries *after* the first attempt. The total number of attempts is therefore up to `max_retries + 1`. A retry happens whenever the status is anything other than `complete`.
- **retry_delay** (default `5`) — the number of seconds to wait between attempts.

Each of these defaults can be overridden on a per-call basis by passing the corresponding field to the action.

## Usage

The integration provides one action: `zha_tools.reconfigure`.

### Fields

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `device_id` | yes | — | The ZHA device to reconfigure. The UI presents a device selector filtered to the `zha` integration. |
| `timeout` | no | configured default (`20`) | Per-attempt timeout, in seconds. |
| `max_retries` | no | configured default (`10`) | Maximum number of retries after the first attempt (total attempts up to `max_retries + 1`). |
| `retry_delay` | no | configured default (`5`) | Seconds to wait between attempts. |

### Response

The action uses `SupportsResponse.ONLY`, which means it **only** returns response data. Automations **must** capture the result with `response_variable`, and any blocking caller must request the response (for example, `return_response: true`).

The response is a mapping with the following keys:

- `device_id` (string) — the ID of the device that was reconfigured.
- `status` (string) — one of:
  - `complete` — the reconfigure finished and at least one binding and at least one reporting configuration step were reported back as successful (a flaky device where only some clusters succeed still counts).
  - `incomplete` — the reconfigure finished, but it did not get at least one successful binding and one successful reporting step.
  - `timeout` — an attempt did not finish within the timeout.
  - `unavailable` — the device did not respond / is offline.
- `attempts` (integer) — the number of attempts that were made.

### Example: automation

This automation reconfigures a device every night and sends a notification with the result.

```yaml
alias: Nightly reconfigure of the kitchen sensor
triggers:
  - trigger: time
    at: "03:00:00"
actions:
  - action: zha_tools.reconfigure
    data:
      device_id: 1234567890abcdef1234567890abcdef
      timeout: 15
      max_retries: 5
      retry_delay: 10
    response_variable: result
  - if:
      - condition: template
        value_template: "{{ result.status == 'complete' }}"
    then:
      - action: notify.mobile_app_phone
        data:
          title: ZHA Tools
          message: "Device reconfigured successfully after {{ result.attempts }} attempt(s)."
    else:
      - action: notify.mobile_app_phone
        data:
          title: ZHA Tools reconfigure failed
          message: "Reconfigure ended with status '{{ result.status }}' after {{ result.attempts }} attempt(s)."
```

### Example: script / Developer Tools

You can also call the action directly from a script, or from **Developer Tools -> Actions** (Perform action). When calling it programmatically, request the response so you can read the result.

```yaml
sequence:
  - action: zha_tools.reconfigure
    data:
      device_id: 1234567890abcdef1234567890abcdef
    response_variable: result
  - action: system_log.write
    data:
      level: info
      message: "Reconfigure status={{ result.status }} attempts={{ result.attempts }}"
```

In **Developer Tools -> Actions**, select `zha_tools.reconfigure`, pick the device, and enable "return response" to see the `device_id`, `status`, and `attempts` returned by the call.

## How it works

When the action is called, it triggers a ZHA device reconfigure for the selected device and watches ZHA's progress events for the device — in particular the binding and configure-reporting steps. Based on those events it decides the outcome: `complete` when at least one binding and at least one reporting step succeeded, `incomplete` when the run finished without that minimum success, `timeout` when an attempt did not finish in time, and `unavailable` when the device did not respond. If the result is anything other than `complete`, the action waits `retry_delay` seconds and tries again, up to `max_retries` additional attempts, then returns the final status and the number of attempts made.

## Development

This project uses [uv](https://docs.astral.sh/uv/) for dependency management and tooling, and targets Python 3.13.

- `make test` — run the test suite.
- `make dist` — build a distribution zip into the `dist/` directory.

You need `uv` installed; see the [uv documentation](https://docs.astral.sh/uv/) for installation instructions.

## Logo and branding

[`icons/logo.svg`](icons/logo.svg) is the editable source of the logo, and
[`icons/logo-dark.svg`](icons/logo-dark.svg) is the dark-theme variant (a deeper
gradient with a light edge ring). The PNGs they are rendered to — `icon.png`
256×256, `icon@2x.png` 512×512, `logo.png` / `logo@2x.png`, and the matching
`dark_*` variants — live in
[`custom_components/zha_tools/brand/`](custom_components/zha_tools/brand/) so they
ship with the integration. `make validate` checks the icon dimensions.

The assets are generated by [`tools/make_logo.py`](tools/make_logo.py); run
`make logo` to rebuild the dark variant and all brand PNGs from the SVG sources
(add `--wordmark` to also re-bake the wordmark from a font).

Since **Home Assistant 2026.3.0**, a custom integration serves its own brand
images directly from this `brand/` subdirectory — Home Assistant prefers them
over the brands CDN, with no `manifest.json` changes required (see the
[Brands Proxy API announcement](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api)).
So the logo appears in the UI as soon as the integration is installed; there is
no need to submit it to the [home-assistant/brands](https://github.com/home-assistant/brands)
repository. (On Home Assistant older than 2026.3.0 the local images are ignored
and a brands-repository submission would be required instead.)

## License

Copyright 2026 eGenix.com Software, Skills and Services GmbH.

Licensed under the Apache License, Version 2.0 (Apache-2.0). See [LICENSE.md](LICENSE.md) for the full license text.
