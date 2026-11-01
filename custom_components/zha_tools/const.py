"""Constants for the ZHA Tools integration."""

from __future__ import annotations

# Integration domain and service names.
DOMAIN = "zha_tools"
SERVICE_RECONFIGURE = "reconfigure"
SERVICE_REINTERVIEW = "reinterview"
SERVICE_REJOIN = "rejoin"
SERVICE_PING = "ping"
SERVICE_FRAME_COUNTER = "frame_counter"
SERVICE_QUERY_ROUTE = "query_route"
SERVICE_RETRY_ACTION = "retry_action"

# Service / options keys.
ATTR_DEVICE_ID = "device_id"
CONF_ALL_DEVICES = "all_devices"
CONF_TIMEOUT = "timeout"
CONF_MAX_RETRIES = "max_retries"
CONF_RETRY_DELAY = "retry_delay"
CONF_REJOIN_WAIT = "rejoin_wait"
CONF_CONFIRM = "confirm"
CONF_ACTION = "action"
CONF_DATA = "data"
CONF_TARGET = "target"
CONF_ATTEMPT_TIMEOUT = "attempt_timeout"
CONF_BACKOFF = "backoff"
CONF_MAX_DELAY = "max_delay"

# Default values (all user configurable via the options flow and overridable
# per service call).
DEFAULT_TIMEOUT = 20  # seconds to wait for a single attempt of a retried action
DEFAULT_MAX_RETRIES = 10  # additional attempts after the first one
DEFAULT_RETRY_DELAY = 5  # seconds to wait between attempts
DEFAULT_REJOIN_WAIT = 60  # seconds to wait for a device to return after a rejoin

# Defaults of the retry_action wrapper, which has its own timing parameters: it
# is meant to keep trying for a long time, slowing down as it goes.
DEFAULT_RETRY_ACTION_TIMEOUT = 600  # seconds for all attempts together
DEFAULT_RETRY_ACTION_ATTEMPT_TIMEOUT = 30  # seconds for a single attempt
DEFAULT_RETRY_ACTION_DELAY = 5  # seconds to wait after the first failed attempt
DEFAULT_RETRY_ACTION_BACKOFF = 1.5  # delay multiplier after each failed attempt
DEFAULT_RETRY_ACTION_MAX_DELAY = 120  # upper limit for the delay, in seconds

# Reported outcomes.
STATUS_COMPLETE = "complete"  # finished successfully (e.g. binding and reporting ok)
STATUS_INCOMPLETE = "incomplete"  # finished, but binding/reporting failed
STATUS_TIMEOUT = "timeout"  # attempt did not finish within the timeout
STATUS_UNAVAILABLE = "unavailable"  # device did not respond
STATUS_REQUESTED = "requested"  # rejoin request was sent; the outcome is unconfirmed
STATUS_NOT_NEIGHBOR = "not_neighbor"  # coordinator stores no frame counter for it
STATUS_NO_ROUTE = "no_route"  # no complete route to the device is known
STATUS_ERROR = "error"  # the action raised an error for this device (batch runs only)

# Keys used in the service response and the per-attempt result.
ATTR_STATUS = "status"
ATTR_ATTEMPTS = "attempts"

# Key under which ``hass.data[DOMAIN]`` records the batched action currently
# running, so that a second batched run can be refused.
DATA_BATCH_RUNNING = "batch_running"

# Key under which ``hass.data[DOMAIN]`` holds the lock that serializes this
# integration's reconfigures (ZHA's progress events carry no device id).
DATA_RECONFIGURE_LOCK = "reconfigure_lock"

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
