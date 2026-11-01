"""Thin adapter around the ZHA integration internals.

All access to ZHA / zigpy lives here so that the rest of the integration stays
decoupled from ZHA's non-public API (which changes between Home Assistant
releases) and so the heavy ZHA imports are only performed when actually needed.
This module is the single place tests need to mock to exercise the integration
without a running Zigbee network.
"""

from __future__ import annotations

import logging
import time

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# A ping reads the ZCL version attribute of the Basic cluster. Both are
# mandatory in the Zigbee Cluster Library, so every Zigbee device supports them.
_BASIC_CLUSTER_ID = 0x0000
_ZCL_VERSION_ATTR_ID = 0x0000

# Endpoint 0 is the Zigbee Device Object (ZDO), which carries no ZCL clusters.
_ZDO_ENDPOINT_ID = 0

# The EZSP ``getNeighborFrameCounter`` command was added in EZSP version 8.
_MIN_EZSP_VERSION_FRAME_COUNTER = 8


class DeviceUnreachableError(Exception):
    """The radio could not deliver a request to the device.

    Raised in place of zigpy's ``DeliveryError``, so callers can tell an
    unreachable device apart from other failures without importing zigpy.
    """


def async_get_zha_device(hass: HomeAssistant, device_id: str) -> object:
    """Return the ZHA device object for a Home Assistant ``device_id``.

    Args:
        hass: The Home Assistant instance.
        device_id: A device registry id which must belong to a ZHA device.

    Returns:
        The underlying ZHA device object. It exposes at least an ``available``
        property and an awaitable ``async_configure()`` method.

    Raises:
        ServiceValidationError: If the id is unknown or not a ZHA device.
    """
    # Imported lazily: pulling in ZHA drags in zigpy and the whole Zigbee stack,
    # which we want to avoid unless a reconfigure is actually requested.
    from homeassistant.components.zha.helpers import async_get_zha_device_proxy

    try:
        proxy = async_get_zha_device_proxy(hass, device_id)
    except (KeyError, ValueError, AttributeError) as err:
        raise ServiceValidationError(
            f"Device {device_id!r} is not a known {DOMAIN} target ZHA device"
        ) from err
    _LOGGER.debug(
        "Resolved device %s to ZHA device %s (available=%s)",
        device_id,
        getattr(proxy.device, "name", proxy.device),
        getattr(proxy.device, "available", "unknown"),
    )
    return proxy.device


def selected_device_ids(device_proxies: list) -> list[str]:
    """Return the Home Assistant device ids of the given ZHA device proxies.

    The coordinator is left out: it is the radio itself, not a device that can
    be reconfigured or re-interviewed. The ids are returned sorted by device
    name so a batch run always works through the devices in the same,
    predictable order.

    Args:
        device_proxies: ZHA device proxies, each exposing a ``device_id`` and
            the underlying ZHA ``device``.

    Returns:
        The device registry ids of all non-coordinator devices.
    """
    proxies = [proxy for proxy in device_proxies if not proxy.device.is_coordinator]
    proxies.sort(key=lambda proxy: (str(proxy.device.name), str(proxy.device.ieee)))
    return [proxy.device_id for proxy in proxies]


def async_get_zha_device_ids(hass: HomeAssistant) -> list[str]:
    """Return the device ids of all ZHA devices except the coordinator.

    Args:
        hass: The Home Assistant instance.

    Returns:
        The device registry ids of every device managed by ZHA, minus the
        coordinator, sorted by device name.

    Raises:
        ServiceValidationError: If the ZHA integration is not set up.
    """
    # Imported lazily, for the same reason as in ``async_get_zha_device``.
    from homeassistant.components.zha.helpers import get_zha_gateway_proxy

    try:
        gateway_proxy = get_zha_gateway_proxy(hass)
    except ValueError as err:
        raise ServiceValidationError(
            "The ZHA integration is not set up, so its devices cannot be listed"
        ) from err

    device_ids = selected_device_ids(list(gateway_proxy.device_proxies.values()))
    _LOGGER.debug(
        "Found %d ZHA device(s) excluding the coordinator: %s",
        len(device_ids),
        ", ".join(device_ids),
    )
    return device_ids


async def async_trigger_reconfigure(device: object) -> None:
    """Trigger a reconfigure on a ZHA device.

    This runs the same binding and configure-reporting steps as the
    "Reconfigure device" button in the ZHA UI. Progress is reported by ZHA on
    the Home Assistant dispatcher (see ``SIGNAL_DEVICE_RECONFIGURE_EVENT``); the
    awaited call returns once configuration has finished. Per-cluster transport
    failures (e.g. an unresponsive device) are reported via those events rather
    than raised.

    Args:
        device: A ZHA device object as returned by ``async_get_zha_device``.
    """
    await device.async_configure()


async def async_trigger_reinterview(device: object) -> None:
    """Re-interview a ZHA device in place (rediscover endpoints and clusters).

    Goes through ZHA's gateway, which re-runs the zigpy device interview behind a
    shadow device and swaps the refreshed device in on success. The device is
    *not* removed from the network, so this cannot strand it.

    Args:
        device: A ZHA device object as returned by ``async_get_zha_device``.

    Raises:
        HomeAssistantError: If the installed ZHA has no in-place re-interview.
    """
    reinterview = getattr(device.gateway, "async_reinterview_device", None)
    if reinterview is None:
        raise HomeAssistantError(
            "The ZHA version of this Home Assistant installation cannot "
            "re-interview devices in place; please update Home Assistant"
        )
    await reinterview(device.ieee)


def async_get_current_device(device: object) -> object | None:
    """Return the ZHA device object ZHA currently holds for a device's IEEE.

    ZHA replaces its device object when a device leaves and rejoins (or is
    removed), so an object fetched earlier may be stale.

    Args:
        device: A ZHA device object as returned by ``async_get_zha_device``.

    Returns:
        The current ZHA device object, or ``None`` if ZHA does not know the
        device (any more).
    """
    return device.gateway.get_device(device.ieee)


async def async_trigger_rejoin(device: object) -> None:
    """Ask a ZHA device to leave the network and immediately rejoin it.

    Sends a ZDO leave request with the rejoin flag set, via the zigpy controller
    application directly -- ZHA's own ``async_remove_device`` removes a device
    without setting the rejoin flag, which would make it leave permanently. The
    call returns once the request has been sent; whether the device actually
    rejoins is up to its firmware.

    Args:
        device: A ZHA device object as returned by ``async_get_zha_device``.
    """
    await device.gateway.application_controller.remove(device.ieee, rejoin=True)


def _find_basic_cluster(zigpy_device: object) -> object | None:
    """Return the first Basic cluster of a zigpy device, or ``None``.

    Args:
        zigpy_device: The zigpy device to search.

    Returns:
        The Basic server cluster of the lowest numbered endpoint carrying one.
    """
    for endpoint_id, endpoint in sorted(zigpy_device.endpoints.items()):
        if endpoint_id == _ZDO_ENDPOINT_ID:
            continue
        cluster = endpoint.in_clusters.get(_BASIC_CLUSTER_ID)
        if cluster is not None:
            return cluster
    return None


async def async_trigger_ping(device: object, timeout: float) -> None:
    """Ping a ZHA device by reading an attribute from it, bypassing all caches.

    Reads the ZCL version attribute of the Basic cluster directly through
    zigpy, rather than through ZHA's cluster handler, which retries on its own
    and swallows the final failure. The request is sent whether or not ZHA
    currently considers the device available. A reply updates the device's
    ``last_seen``, from which ZHA's own availability checker marks the device
    available again.

    Args:
        device: A ZHA device object as returned by ``async_get_zha_device``.
        timeout: Seconds to wait for the reply. zigpy uses its own, longer
            reply timeout for end devices.

    Raises:
        TimeoutError: If the device did not reply in time.
        DeviceUnreachableError: If the radio could not deliver the request.
        HomeAssistantError: If the device has no Basic cluster to read from.
    """
    # Imported lazily, for the same reason as in ``async_get_zha_device``.
    from zigpy.exceptions import DeliveryError

    cluster = _find_basic_cluster(device.device)
    if cluster is None:
        raise HomeAssistantError(
            f"ZHA device {device.name} has no Basic cluster, so it cannot be pinged"
        )
    try:
        await cluster.read_attributes(
            [_ZCL_VERSION_ATTR_ID], allow_cache=False, timeout=timeout
        )
    except DeliveryError as err:
        raise DeviceUnreachableError(str(err)) from err


async def async_trigger_address_refresh(device: object) -> None:
    """Ask the network for the current network (NWK) address of a ZHA device.

    Broadcasts a ZDO ``NWK_addr_req`` for the device's IEEE address. When the
    device (or its parent) answers, zigpy updates the stored NWK address, so a
    device which rejoined under a new address without the coordinator noticing
    can be reached again. The call returns once the request has been sent; the
    answer is processed by zigpy in the background.

    Args:
        device: A ZHA device object as returned by ``async_get_zha_device``.

    Raises:
        DeviceUnreachableError: If the radio could not send the broadcast.
    """
    # Imported lazily, for the same reason as in ``async_get_zha_device``.
    import zigpy.zdo
    from zigpy.exceptions import DeliveryError
    from zigpy.zdo.types import AddrRequestType, ZDOCmd

    try:
        await zigpy.zdo.broadcast(
            device.gateway.application_controller,
            ZDOCmd.NWK_addr_req,
            None,  # no group id
            0,  # radius: use the network's maximum
            device.ieee,
            AddrRequestType.Single,
            0,  # start index
        )
    except DeliveryError as err:
        raise DeviceUnreachableError(str(err)) from err


async def async_get_neighbor_frame_counter(device: object) -> int | None:
    """Return the coordinator's incoming NWK frame counter for a ZHA device.

    Zigbee network (NWK) layer security is applied hop by hop, so the
    coordinator only tracks incoming frame counters for its direct neighbors.
    The counter is the highest one accepted from that neighbor so far; frames
    carrying a counter at or below it are dropped as replays and counted as
    ``NWK_FRAME_COUNTER_FAILURE``.

    This is read from the radio firmware with the EZSP
    ``getNeighborFrameCounter`` command, so it only works with Silicon Labs
    (bellows / EZSP version 8 or later) radios. bellows does not expose its EZSP
    connection publicly; it is reached through the controller's ``_ezsp``
    attribute.

    Args:
        device: A ZHA device object as returned by ``async_get_zha_device``.

    Returns:
        The frame counter, or ``None`` if the device is not a neighbor of the
        coordinator (the firmware stores no counter for it).

    Raises:
        HomeAssistantError: If the radio is not an EZSP radio, its EZSP version
            is too old, or the firmware reports an unexpected status.
    """
    ezsp = getattr(device.gateway.application_controller, "_ezsp", None)
    if ezsp is None:
        raise HomeAssistantError(
            "Reading frame counters needs a Silicon Labs (EZSP / bellows) radio"
        )
    if ezsp.ezsp_version < _MIN_EZSP_VERSION_FRAME_COUNTER:
        raise HomeAssistantError(
            f"Reading frame counters needs EZSP version "
            f"{_MIN_EZSP_VERSION_FRAME_COUNTER} or later; the radio speaks "
            f"version {ezsp.ezsp_version}"
        )

    status, frame_counter = await ezsp.getNeighborFrameCounter(eui64=device.ieee)
    # The status is an ``EmberStatus`` before EZSP version 14 and an
    # ``sl_Status`` from then on. Both use 0 for success and name the
    # "no such neighbor" case ``NOT_FOUND``.
    if status == 0:
        return int(frame_counter)
    if getattr(status, "name", None) == "NOT_FOUND":
        return None
    raise HomeAssistantError(
        f"Reading the frame counter of ZHA device {device.name} failed: {status!r}"
    )


# Unused route table entries have this destination address.
_UNUSED_NWK = 0xFFFF

# The EZSP source route table commands were added in EZSP version 6.
_MIN_EZSP_VERSION_SOURCE_ROUTES = 6

# How long, in seconds, radio tables read for a route query are reused. Reading
# them takes several hundred EZSP commands (the child table alone is probed at
# all 256 indexes), so an all-devices run reuses one read instead of repeating
# it for every device.
_RADIO_TABLES_MAX_AGE = 15.0

# Cache of the last radio table read: ``(ezsp object, read time, tables)``.
_radio_tables_cache: tuple[object, float, dict] | None = None


def _enum_name(value: object) -> str:
    """Return the member name of an enum value, or its string form."""
    return getattr(value, "name", str(value))


def _route_table_entry(entry: object) -> dict:
    """Convert an EZSP ``EmberRouteTableEntry`` to plain Python types."""
    return {
        "destination": int(entry.destination),
        "next_hop": int(entry.nextHop),
        "status": _enum_name(entry.status),
        "age": int(entry.age),
        "concentrator_type": _enum_name(entry.concentratorType),
        "route_record_state": _enum_name(entry.routeRecordState),
    }


def _neighbor_table_entry(entry: object) -> dict:
    """Convert an EZSP ``EmberNeighborTableEntry`` to plain Python types."""
    return {
        "nwk": int(entry.shortId),
        "ieee": str(entry.longId),
        "average_lqi": int(entry.averageLqi),
        "in_cost": int(entry.inCost),
        "out_cost": int(entry.outCost),
        "age": int(entry.age),
    }


async def _async_read_radio_tables(ezsp: object) -> dict:
    """Read the routing related tables of an EZSP (Silicon Labs) radio.

    EmberZNet reports unused table slots with an error status, so every table
    is read up to its configured size and slots with an error status are
    skipped. This also copes with entries aging out while the tables are read.

    Args:
        ezsp: The bellows EZSP connection of the coordinator.

    Returns:
        A mapping with the active ``route_table`` entries, the ``neighbors``
        (routers within radio range), the ``children`` (end devices using the
        coordinator as their parent), the ``source_routes`` table (``None`` if
        the firmware is too old to report it) and the ``ezsp_version``.
    """
    # Imported lazily: bellows is only installed (and only needed) for Silicon
    # Labs radios.
    from bellows.types import EzspConfigId

    route_table = []
    _status, size = await ezsp.getConfigurationValue(
        EzspConfigId.CONFIG_ROUTE_TABLE_SIZE
    )
    for index in range(size):
        status, entry = await ezsp.getRouteTableEntry(index=index)
        if (
            status != 0
            or int(entry.destination) == _UNUSED_NWK
            or _enum_name(entry.status) == "UNUSED"
        ):
            continue
        route_table.append(_route_table_entry(entry))

    neighbors = []
    (count,) = await ezsp.neighborCount()
    for index in range(count):
        status, entry = await ezsp.getNeighbor(index=index)
        if status == 0:
            neighbors.append(_neighbor_table_entry(entry))

    children = [
        {"nwk": int(nwk), "ieee": str(ieee), "node_type": _enum_name(node_type)}
        async for nwk, ieee, node_type in ezsp.read_child_data()
    ]

    source_routes = None
    if ezsp.ezsp_version >= _MIN_EZSP_VERSION_SOURCE_ROUTES:
        source_routes = []
        (total,) = await ezsp.getSourceRouteTableTotalSize()
        for index in range(total):
            status, destination, closer_index = await ezsp.getSourceRouteTableEntry(
                index=index
            )
            if status == 0:
                source_routes.append(
                    {
                        "index": index,
                        "destination": int(destination),
                        "closer_index": int(closer_index),
                    }
                )

    return {
        "ezsp_version": int(ezsp.ezsp_version),
        "route_table": route_table,
        "neighbors": neighbors,
        "children": children,
        "source_routes": source_routes,
    }


async def _async_get_radio_tables(ezsp: object) -> dict:
    """Return the radio tables, reusing a read from the last few seconds.

    Args:
        ezsp: The bellows EZSP connection of the coordinator.

    Returns:
        The tables as returned by ``_async_read_radio_tables``.
    """
    global _radio_tables_cache  # noqa: PLW0603 -- a module-wide cache
    now = time.monotonic()
    if _radio_tables_cache is not None:
        cached_ezsp, read_at, tables = _radio_tables_cache
        if cached_ezsp is ezsp and now - read_at < _RADIO_TABLES_MAX_AGE:
            return tables
    tables = await _async_read_radio_tables(ezsp)
    _radio_tables_cache = (ezsp, time.monotonic(), tables)
    return tables


def _topology_tables(topology: object) -> tuple[dict, dict]:
    """Convert zigpy's topology scan results to plain Python types.

    Args:
        topology: The zigpy ``Topology`` object of the controller.

    Returns:
        ``(neighbors, routes)``, both keyed by the IEEE address (string) of the
        router whose table was scanned.
    """
    neighbors = {
        str(ieee): [
            {
                "nwk": int(neighbor.nwk),
                "ieee": str(neighbor.ieee),
                "device_type": _enum_name(neighbor.device_type),
                "relationship": _enum_name(neighbor.relationship),
                "rx_on_when_idle": _enum_name(neighbor.rx_on_when_idle),
                "depth": int(neighbor.depth),
                "lqi": int(neighbor.lqi),
            }
            for neighbor in table
        ]
        for ieee, table in topology.neighbors.items()
    }
    routes = {
        str(ieee): [
            {
                "destination": int(route.DstNWK),
                "status": _enum_name(route.RouteStatus),
                "next_hop": int(route.NextHop),
                "many_to_one": bool(route.ManyToOne),
                "route_record_required": bool(route.RouteRecordRequired),
                "memory_constrained": bool(route.MemoryConstrained),
            }
            for route in table
        ]
        for ieee, table in topology.routes.items()
    }
    return neighbors, routes


def _logical_type(zigpy_device: object) -> str | None:
    """Return the logical type (Coordinator, Router, EndDevice) of a device."""
    node_desc = zigpy_device.node_desc
    if node_desc is None:
        return None
    return _enum_name(node_desc.logical_type)


async def async_get_route_data(device: object) -> dict:
    """Collect everything the coordinator knows about routes to a ZHA device.

    Nothing is sent over the air: the data comes from zigpy's in-memory state
    (route records, topology scans) and, for Silicon Labs radios, from tables
    read out of the radio firmware. The values are converted to plain Python
    types (NWK addresses as ``int``, IEEE addresses as ``str``) so the route
    logic does not need zigpy or bellows types.

    Args:
        device: A ZHA device object as returned by ``async_get_zha_device``.

    Returns:
        A mapping with:

        * ``coordinator`` / ``target`` -- ``nwk`` and ``ieee`` of both ends;
        * ``devices`` -- ``{nwk: {"ieee", "device_type"}}`` for every device
          zigpy knows, to name the hops;
        * ``source_routing`` -- whether zigpy source routing is enabled;
        * ``relays`` -- the relays of the device's last route record, in the
          order device to coordinator, or ``None`` if none was received;
        * ``topology_neighbors`` / ``topology_routes`` -- the neighbor and
          routing tables from the last topology scan, keyed by router IEEE;
        * ``radio`` -- the radio's own tables (see
          ``_async_read_radio_tables``), or ``None`` for non-EZSP radios.
          A read from the last ``_RADIO_TABLES_MAX_AGE`` seconds is reused.
    """
    app = device.gateway.application_controller
    zigpy_device = device.device
    node_info = app.state.node_info

    devices = {
        int(dev.nwk): {"ieee": str(dev.ieee), "device_type": _logical_type(dev)}
        for dev in app.devices.values()
    }
    relays = zigpy_device.relays
    topology_neighbors, topology_routes = _topology_tables(app.topology)

    ezsp = getattr(app, "_ezsp", None)
    radio = None if ezsp is None else await _async_get_radio_tables(ezsp)

    return {
        "coordinator": {"nwk": int(node_info.nwk), "ieee": str(node_info.ieee)},
        "target": {"nwk": int(zigpy_device.nwk), "ieee": str(zigpy_device.ieee)},
        "devices": devices,
        # The key of zigpy's ``CONF_SOURCE_ROUTING`` setting.
        "source_routing": bool(app.config.get("source_routing", False)),
        "relays": None if relays is None else [int(nwk) for nwk in relays],
        "topology_neighbors": topology_neighbors,
        "topology_routes": topology_routes,
        "radio": radio,
    }
