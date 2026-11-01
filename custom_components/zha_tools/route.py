"""Core route query logic for the ZHA Tools integration.

Reports everything the coordinator knows about how messages travel between it
and a ZHA device. Zigbee normally routes hop by hop: every router only knows the
next hop towards a destination, so no single table holds the full path. This
action therefore combines several sources, each reported in its own section:

* ``route_record`` -- the path the device's messages took to the coordinator,
  from the last route record the device sent. Devices only send route records
  when zigpy source routing is enabled (the coordinator then acts as a
  "concentrator"). zigpy keeps the last record across restarts, so this is the
  last recorded path, which may be outdated.
* ``source_route`` -- the path the radio stores for source routed messages
  from the coordinator to the device (Silicon Labs radios, source routing on).
* ``coordinator_route_table`` -- the radio's own route table entry for the
  device: the first hop of the regular, hop by hop route.
* ``topology`` -- the coordinator to device path rebuilt by following the next
  hops through the routing and neighbor tables of zigpy's last topology scan.
  These tables are snapshots taken every few hours, and some routers do not
  report them, so the path may be incomplete or outdated.

On top of that the response lists the device's parents (for end devices), the
routers that have the device in their neighbor tables, the device's own
neighbors and its route back to the coordinator.

Every hop is named by its NWK address, IEEE address and display name. Nothing is
sent over the air; the data comes from zigpy's memory and, for Silicon Labs
radios, from the radio firmware. Errors raised by the underlying calls are not
caught -- they propagate so the caller sees the real failure.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .const import STATUS_COMPLETE, STATUS_NO_ROUTE
from .helpers import device_label, device_name, device_name_by_ieee, iso_timestamp

# Imported as module-level names so tests can patch them on this module.
from .zha_adapter import async_get_route_data, async_get_zha_device

_LOGGER = logging.getLogger(__name__)

# Zigbee limits a route to 30 hops (twice the default maximum network depth),
# so a longer path can only come from inconsistent tables.
MAX_HOPS = 30

# ``closer_index`` value marking the end of a source route chain: the node is a
# direct neighbor of the coordinator.
_NO_CLOSER_INDEX = 0xFF

# Route statuses (zigpy topology names, EZSP route table name prefixes) of
# routes that are currently used to forward messages.
_TOPOLOGY_ROUTE_ACTIVE = ("Active", "Validation_Underway")
_RADIO_ROUTE_ACTIVE_PREFIXES = ("ACTIVE", "VALIDATING")

# Logical device type of end devices, which only talk through their parent.
_END_DEVICE = "EndDevice"

# Neighbor table relationship of a device to its parent.
_RELATIONSHIP_CHILD = "Child"


def format_nwk(nwk: int) -> str:
    """Return a NWK address in the usual ``0x1234`` notation."""
    return f"0x{nwk:04X}"


class _HopNamer:
    """Turn NWK and IEEE addresses into named hop descriptions."""

    def __init__(self, hass: HomeAssistant, data: dict) -> None:
        """Index the known devices for lookups by NWK and IEEE address.

        Args:
            hass: The Home Assistant instance, for the device registry.
            data: The route data returned by ``async_get_route_data``.
        """
        self._hass = hass
        self._devices: dict[int, dict] = data["devices"]
        self._nwk_by_ieee = {info["ieee"]: nwk for nwk, info in self._devices.items()}
        self._names: dict[str, str | None] = {}

    def ieee(self, nwk: int) -> str | None:
        """Return the IEEE address of a device, or ``None`` if unknown."""
        info = self._devices.get(nwk)
        return None if info is None else info["ieee"]

    def nwk(self, ieee: str) -> int | None:
        """Return the NWK address of a device, or ``None`` if unknown."""
        return self._nwk_by_ieee.get(ieee)

    def _name(self, ieee: str | None) -> str | None:
        """Return the display name for an IEEE address, cached."""
        if ieee is None:
            return None
        if ieee not in self._names:
            self._names[ieee] = device_name_by_ieee(self._hass, ieee)
        return self._names[ieee]

    def hop(self, nwk: int | None, ieee: str | None = None, **extra: object) -> dict:
        """Describe a hop by its NWK address (or, failing that, IEEE address).

        Args:
            nwk: The NWK address of the hop, if known.
            ieee: The IEEE address, used when ``nwk`` is unknown or to fill in
                devices zigpy does not know.
            **extra: Additional fields to add to the description.

        Returns:
            A mapping with ``nwk`` (formatted), ``ieee``, ``device_name`` and
            ``device_type`` (``None`` where unknown) plus ``extra``.
        """
        if nwk is None and ieee is not None:
            nwk = self.nwk(ieee)
        info = None if nwk is None else self._devices.get(nwk)
        if info is not None and ieee is not None and info["ieee"] != ieee:
            # The NWK address now belongs to another device (address change or
            # reuse); trust the IEEE address reported with the entry.
            info = None
        if info is not None:
            ieee = info["ieee"]
        return {
            "nwk": None if nwk is None else format_nwk(nwk),
            "ieee": ieee,
            "device_name": self._name(ieee),
            "device_type": None if info is None else info["device_type"],
            **extra,
        }


def _route_record(data: dict, namer: _HopNamer) -> dict | None:
    """Return the device to coordinator path from the last route record."""
    relays = data["relays"]
    if relays is None:
        return None
    path = [data["target"]["nwk"], *relays, data["coordinator"]["nwk"]]
    return {
        "direction": "device_to_coordinator",
        "complete": True,
        "hops": len(path) - 1,
        "path": [namer.hop(nwk) for nwk in path],
    }


def _source_route(data: dict, namer: _HopNamer) -> dict | None:
    """Return the coordinator to device path from the radio's source routes.

    Each source route table entry names a destination and the table index of
    the next node closer to the coordinator, so the path is found by following
    ``closer_index`` from the device until it marks a coordinator neighbor.
    """
    radio = data["radio"]
    if radio is None or radio["source_routes"] is None:
        return None
    target = data["target"]["nwk"]
    entries = {entry["index"]: entry for entry in radio["source_routes"]}
    entry = next(
        (entry for entry in entries.values() if entry["destination"] == target),
        None,
    )
    if entry is None:
        return None

    relays: list[int] = []
    complete = True
    visited = {entry["index"]}
    index = entry["closer_index"]
    while index != _NO_CLOSER_INDEX:
        closer = entries.get(index)
        if closer is None or index in visited or len(relays) >= MAX_HOPS:
            # A dangling index or a loop: report what was found so far.
            complete = False
            break
        visited.add(index)
        relays.append(closer["destination"])
        index = closer["closer_index"]

    path = [data["coordinator"]["nwk"], *reversed(relays), target]
    return {
        "direction": "coordinator_to_device",
        "complete": complete,
        "hops": len(path) - 1,
        "path": [namer.hop(nwk) for nwk in path],
    }


def _radio_route_entry(data: dict, nwk: int) -> dict | None:
    """Return the radio's route table entry for a destination, if any."""
    radio = data["radio"]
    if radio is None:
        return None
    return next(
        (entry for entry in radio["route_table"] if entry["destination"] == nwk),
        None,
    )


def _coordinator_route_table(data: dict, namer: _HopNamer) -> dict | None:
    """Return the radio's route table entry for the device, with named hop."""
    entry = _radio_route_entry(data, data["target"]["nwk"])
    if entry is None:
        return None
    return {
        "next_hop": namer.hop(entry["next_hop"]),
        "status": entry["status"],
        "age": entry["age"],
        "concentrator_type": entry["concentrator_type"],
        "route_record_state": entry["route_record_state"],
    }


def _matches(entry: dict, nwk: int, ieee: str | None) -> bool:
    """Return whether a neighbor table entry describes the given device."""
    return entry["nwk"] == nwk or (ieee is not None and entry["ieee"] == ieee)


def _radio_neighbor(data: dict, nwk: int, ieee: str | None) -> dict | None:
    """Return the radio's neighbor table entry for a device, if any."""
    radio = data["radio"]
    if radio is None:
        return None
    entry = next((e for e in radio["neighbors"] if _matches(e, nwk, ieee)), None)
    if entry is None:
        return None
    return {key: entry[key] for key in ("average_lqi", "in_cost", "out_cost", "age")}


def _radio_child(data: dict, nwk: int, ieee: str | None) -> dict | None:
    """Return the radio's child table entry for a device, if any."""
    radio = data["radio"]
    if radio is None:
        return None
    entry = next((e for e in radio["children"] if _matches(e, nwk, ieee)), None)
    return None if entry is None else {"node_type": entry["node_type"]}


def _topology_neighbor(
    data: dict, router_ieee: str | None, nwk: int, ieee: str | None
) -> dict | None:
    """Return a router's topology neighbor entry for a device, if any."""
    if router_ieee is None:
        return None
    table = data["topology_neighbors"].get(router_ieee, [])
    return next((entry for entry in table if _matches(entry, nwk, ieee)), None)


def _link(data: dict, namer: _HopNamer, from_nwk: int, to_nwk: int) -> dict | None:
    """Return what is known about the radio link between two devices.

    For the coordinator, the radio's own neighbor and child tables are used
    when they could be read, as they are current; otherwise the last topology
    scan is used. An end device is only reachable through its parent, so for
    end devices only a ``Child`` entry counts as a link: routers often keep
    entries for former children or devices they merely overhear.

    Returns:
        A mapping with the link details (empty if the devices are known to be
        neighbors but no details are available), or ``None`` if ``to_nwk`` is
        not known to be a neighbor of ``from_nwk``.
    """
    to_ieee = namer.ieee(to_nwk)
    if from_nwk == data["coordinator"]["nwk"] and data["radio"] is not None:
        neighbor = _radio_neighbor(data, to_nwk, to_ieee)
        if neighbor is not None:
            return {"lqi": neighbor["average_lqi"], **neighbor}
        if _radio_child(data, to_nwk, to_ieee) is not None:
            return {"relationship": _RELATIONSHIP_CHILD}
        return None
    entry = _topology_neighbor(data, namer.ieee(from_nwk), to_nwk, to_ieee)
    if entry is None:
        return None
    if (
        _device_type(data, to_nwk) == _END_DEVICE
        and entry["relationship"] != _RELATIONSHIP_CHILD
    ):
        return None
    return {"lqi": entry["lqi"], "relationship": entry["relationship"]}


def _device_type(data: dict, nwk: int) -> str | None:
    """Return the logical type of a device, or ``None`` if unknown."""
    info = data["devices"].get(nwk)
    return None if info is None else info["device_type"]


def _next_hop(data: dict, namer: _HopNamer, from_nwk: int, to_nwk: int) -> int | None:
    """Return the next hop from one device towards another, if known.

    For the coordinator, the radio's own route table is used when it could be
    read; otherwise the routing tables of the last topology scan.
    """
    if from_nwk == data["coordinator"]["nwk"] and data["radio"] is not None:
        entry = _radio_route_entry(data, to_nwk)
        if entry is not None and entry["status"].startswith(
            _RADIO_ROUTE_ACTIVE_PREFIXES
        ):
            return entry["next_hop"]
        return None
    from_ieee = namer.ieee(from_nwk)
    for route in data["topology_routes"].get(from_ieee or "", []):
        if route["destination"] == to_nwk and route["status"] in _TOPOLOGY_ROUTE_ACTIVE:
            return route["next_hop"]
    return None


def _topology_path(data: dict, namer: _HopNamer) -> dict:
    """Rebuild the coordinator to device path from the routing tables.

    Starting at the coordinator, each step delivers directly if the device is a
    neighbor of the current hop, and otherwise follows the current hop's route
    to the device. Each hop after the first carries the details of the link
    from the previous hop, where known.

    Returns:
        A mapping with ``complete``, the ``reason`` the walk ended
        (``reached``, ``no_route`` -- the last hop has no known route to the
        device, ``loop`` or ``too_long``), the number of ``hops`` and the
        ``path``.
    """
    target = data["target"]["nwk"]
    current = data["coordinator"]["nwk"]
    path = [namer.hop(current)]
    visited = {current}
    reason = "too_long"
    for _ in range(MAX_HOPS):
        link = _link(data, namer, current, target)
        if link is not None:
            path.append(namer.hop(target, **link))
            reason = "reached"
            break
        next_hop = _next_hop(data, namer, current, target)
        if next_hop is None:
            reason = "no_route"
            break
        link = _link(data, namer, current, next_hop) or {}
        path.append(namer.hop(next_hop, **link))
        if next_hop == target:
            reason = "reached"
            break
        if next_hop in visited:
            reason = "loop"
            break
        visited.add(next_hop)
        current = next_hop
    return {
        "direction": "coordinator_to_device",
        "complete": reason == "reached",
        "reason": reason,
        "hops": len(path) - 1,
        "path": path,
    }


def _heard_by(data: dict, namer: _HopNamer) -> list[dict]:
    """Return the routers listing the device in their topology neighbor tables.

    Sorted by link quality, best first.
    """
    nwk, ieee = data["target"]["nwk"], data["target"]["ieee"]
    heard = []
    for router_ieee in data["topology_neighbors"]:
        entry = _topology_neighbor(data, router_ieee, nwk, ieee)
        if entry is not None:
            heard.append(
                namer.hop(
                    None,
                    router_ieee,
                    lqi=entry["lqi"],
                    relationship=entry["relationship"],
                    depth=entry["depth"],
                )
            )
    heard.sort(key=lambda hop: -hop["lqi"])
    return heard


def _parents(data: dict, namer: _HopNamer, heard_by: list[dict]) -> list[dict]:
    """Return the devices which list the device as their child.

    An end device has exactly one parent. More than one means some router still
    holds an outdated child table entry; none means no parent is known. Routers
    have no parent once joined (some stacks still list them as children), so
    the list is empty for them.
    """
    coordinator = data["coordinator"]
    parents = []
    if _device_type(data, data["target"]["nwk"]) in ("Router", "Coordinator"):
        return parents
    if _radio_child(data, data["target"]["nwk"], data["target"]["ieee"]):
        parents.append(namer.hop(coordinator["nwk"], source="radio_child_table"))
    for hop in heard_by:
        if hop["relationship"] != _RELATIONSHIP_CHILD:
            continue
        if parents and hop["ieee"] == coordinator["ieee"]:
            # Already reported from the radio's own child table.
            continue
        parents.append({**hop, "source": "topology"})
    return parents


def _device_neighbors(data: dict, namer: _HopNamer) -> list[dict]:
    """Return the device's own neighbor table from the last topology scan."""
    table = data["topology_neighbors"].get(data["target"]["ieee"], [])
    return [
        namer.hop(
            entry["nwk"],
            entry["ieee"],
            lqi=entry["lqi"],
            relationship=entry["relationship"],
            depth=entry["depth"],
        )
        for entry in sorted(table, key=lambda entry: -entry["lqi"])
    ]


def _device_route_to_coordinator(data: dict, namer: _HopNamer) -> dict | None:
    """Return the device's routing table entry for the coordinator, if any."""
    coordinator = data["coordinator"]["nwk"]
    for route in data["topology_routes"].get(data["target"]["ieee"], []):
        if route["destination"] == coordinator:
            return {
                "next_hop": namer.hop(route["next_hop"]),
                "status": route["status"],
                "many_to_one": route["many_to_one"],
                "route_record_required": route["route_record_required"],
                "memory_constrained": route["memory_constrained"],
            }
    return None


def _path_text(section: dict | None) -> str:
    """Return a path as ``name (0x1234) -> ...`` for log messages."""
    if section is None:
        return "none"
    text = " -> ".join(
        f"{hop['device_name'] or hop['ieee'] or '?'} ({hop['nwk']})"
        for hop in section["path"]
    )
    if not section["complete"]:
        text += " -> ? (incomplete)"
    return text


def build_route_report(hass: HomeAssistant, data: dict) -> dict:
    """Turn the raw route data into the named route report.

    Args:
        hass: The Home Assistant instance, for looking up device names.
        data: The route data returned by ``async_get_route_data``.

    Returns:
        The route related part of the ``query_route`` response (see
        ``async_query_route``).
    """
    namer = _HopNamer(hass, data)
    target = data["target"]
    heard_by = _heard_by(data, namer)
    routes = {
        "route_record": _route_record(data, namer),
        "source_route": _source_route(data, namer),
        "coordinator_route_table": _coordinator_route_table(data, namer),
        "topology": _topology_path(data, namer),
    }
    complete_paths = [
        key
        for key in ("route_record", "source_route", "topology")
        if routes[key] is not None and routes[key]["complete"]
    ]
    radio = data["radio"]
    return {
        "status": STATUS_COMPLETE if complete_paths else STATUS_NO_ROUTE,
        "complete_paths": complete_paths,
        "source_routing": data["source_routing"],
        "ezsp_version": None if radio is None else radio["ezsp_version"],
        "coordinator": namer.hop(data["coordinator"]["nwk"]),
        "routes": routes,
        "parents": _parents(data, namer, heard_by),
        "coordinator_neighbor": _radio_neighbor(data, target["nwk"], target["ieee"]),
        "coordinator_child": _radio_child(data, target["nwk"], target["ieee"]),
        "heard_by": heard_by,
        "device_neighbors": _device_neighbors(data, namer),
        "device_route_to_coordinator": _device_route_to_coordinator(data, namer),
    }


async def async_query_route(hass: HomeAssistant, device_id: str) -> dict:
    """Report the known routes between the coordinator and a ZHA device.

    Args:
        hass: The Home Assistant instance.
        device_id: The device registry id of the ZHA device.

    Returns:
        A mapping with the device's ``device_id``, ``device_name``, ``ieee``,
        ``nwk``, ``device_type``, ``available``, ``last_seen`` (ISO 8601 UTC or
        ``None``), ``lqi`` and ``rssi`` (as last received by the coordinator),
        plus:

        * ``status`` -- ``complete`` if at least one full path is known,
          otherwise ``no_route``;
        * ``complete_paths`` -- the ``routes`` sections with a full path, so
          callers can tell a live radio source route from a recorded or
          scanned path, which may be outdated;
        * ``source_routing`` -- whether zigpy source routing is enabled;
        * ``ezsp_version`` -- of the radio, ``None`` for non-Silicon Labs
          radios (whose tables cannot be read);
        * ``coordinator`` -- the coordinator as a hop;
        * ``routes`` -- the ``route_record``, ``source_route``,
          ``coordinator_route_table`` and ``topology`` sections described in
          the module docs (``None`` where nothing is known);
        * ``parents``, ``coordinator_neighbor``, ``coordinator_child``,
          ``heard_by``, ``device_neighbors`` and
          ``device_route_to_coordinator``.

        Hops are mappings with ``nwk``, ``ieee``, ``device_name`` and
        ``device_type``, plus link details (``lqi``, ``relationship``, and for
        coordinator neighbors ``in_cost`` / ``out_cost``) where known.

    Raises:
        ServiceValidationError: If ``device_id`` is not a known ZHA device, or
            is the coordinator.
    """
    name = device_label(hass, device_id)
    device = async_get_zha_device(hass, device_id)
    if device.is_coordinator:
        raise ServiceValidationError(
            f"ZHA device {name} is the coordinator; there is no route to it"
        )
    data = await async_get_route_data(device)
    report = build_route_report(hass, data)

    routes = report["routes"]
    log = _LOGGER.info if report["status"] == STATUS_COMPLETE else _LOGGER.warning
    log(
        "Route to ZHA device %s (%s), status %s; route record: %s; source "
        "route: %s; topology: %s",
        name,
        format_nwk(data["target"]["nwk"]),
        report["status"],
        _path_text(routes["route_record"]),
        _path_text(routes["source_route"]),
        _path_text(routes["topology"]),
    )
    _LOGGER.debug("Route report for ZHA device %s: %s", name, report)

    return {
        "device_id": device_id,
        "device_name": device_name(hass, device_id),
        "ieee": data["target"]["ieee"],
        "nwk": format_nwk(data["target"]["nwk"]),
        "device_type": device.device_type,
        "available": device.available,
        "last_seen": iso_timestamp(device.last_seen),
        "lqi": device.lqi,
        "rssi": device.rssi,
        **report,
    }
