"""Constants for the ZHA Tools integration."""

from __future__ import annotations

# Integration domain and service names.
DOMAIN = "zha_tools"
SERVICE_RECONFIGURE = "reconfigure"
SERVICE_REINTERVIEW = "reinterview"
SERVICE_REJOIN = "rejoin"

# Service / options keys.
ATTR_DEVICE_ID = "device_id"
CONF_ALL_DEVICES = "all_devices"
CONF_TIMEOUT = "timeout"
CONF_MAX_RETRIES = "max_retries"
CONF_RETRY_DELAY = "retry_delay"
CONF_REJOIN_WAIT = "rejoin_wait"
CONF_CONFIRM = "confirm"

# Default values (all user configurable via the options flow and overridable
# per service call).
DEFAULT_TIMEOUT = 20  # seconds to wait for a single reconfigure/re-interview attempt
DEFAULT_MAX_RETRIES = 10  # additional attempts after the first one
DEFAULT_RETRY_DELAY = 5  # seconds to wait between attempts
DEFAULT_REJOIN_WAIT = 60  # seconds to wait for a device to return after a rejoin

# Reported outcomes.
STATUS_COMPLETE = "complete"  # finished, binding and reporting succeeded
STATUS_INCOMPLETE = "incomplete"  # finished, but binding/reporting failed
STATUS_TIMEOUT = "timeout"  # attempt did not finish within the timeout
STATUS_UNAVAILABLE = "unavailable"  # device did not respond
STATUS_REQUESTED = "requested"  # rejoin request was sent; the outcome is unconfirmed
STATUS_ERROR = "error"  # the action raised an error for this device (batch runs only)

# Keys used in the service response and the per-attempt result.
ATTR_STATUS = "status"
ATTR_ATTEMPTS = "attempts"

# Key under which ``hass.data[DOMAIN]`` records the batched action currently
# running, so that a second batched run can be refused.
DATA_BATCH_RUNNING = "batch_running"

# The following mirror constants from the bundled ``zha`` integration. They are
# duplicated here on purpose so importing this module does not pull in the heavy
# ZHA / zigpy dependency tree (the real values are forwarded on the Home
# Assistant dispatcher by ZHA's device proxy). Keep in sync with ZHA.
SIGNAL_DEVICE_RECONFIGURE_EVENT = "zha_device_reconfigure_event"
ZHA_EVENT_TYPE = "type"
ZHA_EVENT_BIND = "zha_channel_bind"
ZHA_EVENT_CFG_RPT = "zha_channel_configure_reporting"
ZHA_EVENT_CFG_DONE = "zha_channel_cfg_done"
ZHA_MSG_DATA = "zha_channel_msg_data"
ZHA_MSG_SUCCESS = "success"
ZHA_MSG_ATTRIBUTES = "attributes"
ZHA_MSG_ATTR_STATUS = "status"
ZHA_ATTR_STATUS_SUCCESS = "SUCCESS"
# Optional, descriptive fields used only for human-readable logging.
ZHA_MSG_CLUSTER_NAME = "cluster_name"
ZHA_MSG_CLUSTER_ID = "cluster_id"
