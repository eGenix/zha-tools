"""Tests for the route query logic and its ZHA adapter helper.

The route logic works on the plain-Python route data returned by the adapter, so
it is tested with hand-built data. The adapter helper itself is tested against
fake zigpy / bellows objects, since neither is installed in the test
environment.
"""

from __future__ import annotations

import enum
import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zha_tools import zha_adapter
from custom_components.zha_tools.const import (
    STATUS_COMPLETE,
    STATUS_NO_ROUTE,
    ZHA_DOMAIN,
)
from custom_components.zha_tools.route import (
    MAX_HOPS,
    async_query_route,
    build_route_report,
)

COORD_IEEE = "00:00:00:00:00:00:00:01"
ROUTER_IEEE = "00:00:00:00:00:00:00:02"
TARGET_IEEE = "00:00:00:00:00:00:00:03"
COORD, ROUTER, TARGET = 0x0000, 0x1A2B, 0x3C4D

DEVICES = {
    COORD: {"ieee": COORD_IEEE, "device_type": "Coordinator"},
    ROUTER: {"ieee": ROUTER_IEEE, "device_type": "Router"},
    TARGET: {"ieee": TARGET_IEEE, "device_type": "EndDevice"},
}


def _register(hass, ieee, name):
    """Register a ZHA-style device (with a zigbee connection) by name."""
    entry = MockConfigEntry(domain=ZHA_DOMAIN)
    entry.add_to_hass(hass)
    return (
        dr.async_get(hass)
        .async_get_or_create(
            config_entry_id=entry.entry_id,
            connections={(dr.CONNECTION_ZIGBEE, ieee)},
            name=name,
        )
        .id
    )


@pytest.fixture
def names(hass):
    """Give the coordinator, router and target display names."""
    _register(hass, COORD_IEEE, "Coordinator")
    _register(hass, ROUTER_IEEE, "Hall plug")
    return _register(hass, TARGET_IEEE, "Bedroom sensor")


def _data(**overrides):
    """Return route data for coordinator -> router -> target (end device)."""
    data = {
        "coordinator": {"nwk": COORD, "ieee": COORD_IEEE},
        "target": {"nwk": TARGET, "ieee": TARGET_IEEE},
        "devices": DEVICES,
        "source_routing": True,
        "relays": [ROUTER],
        "topology_neighbors": {
            ROUTER_IEEE: [
                {
                    "nwk": TARGET,
                    "ieee": TARGET_IEEE,
                    "device_type": "EndDevice",
                    "relationship": "Child",
                    "rx_on_when_idle": "Off",
                    "depth": 2,
                    "lqi": 180,
                }
            ]
        },
        "topology_routes": {},
        "radio": {
            "ezsp_version": 13,
            "route_table": [
                {
                    "destination": TARGET,
                    "next_hop": ROUTER,
                    "status": "ACTIVE_AGE_0",
                    "age": 3,
                    "concentrator_type": "NOT_CONCENTRATOR",
                    "route_record_state": "NO_LONGER_NEEDED",
                }
            ],
            "neighbors": [
                {
                    "nwk": ROUTER,
                    "ieee": ROUTER_IEEE,
                    "average_lqi": 220,
                    "in_cost": 1,
                    "out_cost": 5,
                    "age": 2,
                }
            ],
            "children": [],
            "source_routes": [
                {"index": 0, "destination": ROUTER, "closer_index": 0xFF},
                {"index": 1, "destination": TARGET, "closer_index": 0},
            ],
        },
    }
    data.update(overrides)
    return data


def _active_route(destination, next_hop):
    """Return an active topology routing table entry."""
    return {"destination": destination, "status": "Active", "next_hop": next_hop}


def _names(section):
    """Return the hop names of a path section."""
    return [hop["device_name"] for hop in section["path"]]


async def test_full_report(hass, names):
    report = build_route_report(hass, _data())
    routes = report["routes"]

    assert report["status"] == STATUS_COMPLETE
    assert report["ezsp_version"] == 13
    assert report["coordinator"]["device_name"] == "Coordinator"

    record = routes["route_record"]
    assert record["direction"] == "device_to_coordinator"
    assert record["hops"] == 2
    assert _names(record) == ["Bedroom sensor", "Hall plug", "Coordinator"]
    assert record["path"][1] == {
        "nwk": "0x1A2B",
        "ieee": ROUTER_IEEE,
        "device_name": "Hall plug",
        "device_type": "Router",
    }

    source = routes["source_route"]
    assert source["complete"]
    assert _names(source) == ["Coordinator", "Hall plug", "Bedroom sensor"]

    table = routes["coordinator_route_table"]
    assert table["next_hop"]["device_name"] == "Hall plug"
    assert table["status"] == "ACTIVE_AGE_0"

    topology = routes["topology"]
    assert topology["reason"] == "reached"
    assert _names(topology) == ["Coordinator", "Hall plug", "Bedroom sensor"]
    # The first link comes from the radio's neighbor table, including costs.
    assert topology["path"][1]["lqi"] == 220
    assert topology["path"][1]["out_cost"] == 5
    assert topology["path"][2]["lqi"] == 180
    assert topology["path"][2]["relationship"] == "Child"

    assert [p["device_name"] for p in report["parents"]] == ["Hall plug"]
    assert report["parents"][0]["source"] == "topology"
    assert [h["device_name"] for h in report["heard_by"]] == ["Hall plug"]
    assert report["coordinator_neighbor"] is None
    assert report["coordinator_child"] is None


async def test_direct_child_of_coordinator(hass, names):
    radio = _data()["radio"]
    radio["children"] = [
        {"nwk": TARGET, "ieee": TARGET_IEEE, "node_type": "SLEEPY_END_DEVICE"}
    ]
    report = build_route_report(
        hass, _data(radio=radio, relays=None, topology_neighbors={})
    )

    assert report["coordinator_child"] == {"node_type": "SLEEPY_END_DEVICE"}
    assert report["parents"][0]["device_name"] == "Coordinator"
    assert report["parents"][0]["source"] == "radio_child_table"
    assert _names(report["routes"]["topology"]) == ["Coordinator", "Bedroom sensor"]
    assert report["routes"]["route_record"] is None


async def test_no_route_without_radio_or_tables(hass, names):
    report = build_route_report(
        hass, _data(radio=None, relays=None, topology_neighbors={})
    )

    assert report["status"] == STATUS_NO_ROUTE
    assert report["ezsp_version"] is None
    assert report["routes"]["source_route"] is None
    assert report["routes"]["coordinator_route_table"] is None
    topology = report["routes"]["topology"]
    assert topology == {
        "direction": "coordinator_to_device",
        "complete": False,
        "reason": "no_route",
        "hops": 0,
        "path": [topology["path"][0]],
    }


async def test_topology_walk_detects_loops(hass, names):
    """Two routers pointing at each other must not loop forever."""
    routes = {
        COORD_IEEE: [{"destination": TARGET, "status": "Active", "next_hop": ROUTER}],
        ROUTER_IEEE: [{"destination": TARGET, "status": "Active", "next_hop": COORD}],
    }
    report = build_route_report(
        hass,
        _data(radio=None, relays=None, topology_neighbors={}, topology_routes=routes),
    )

    topology = report["routes"]["topology"]
    assert topology["reason"] == "loop"
    assert not topology["complete"]
    assert len(topology["path"]) <= MAX_HOPS + 1


async def test_unknown_hops_are_reported_without_names(hass, names):
    report = build_route_report(hass, _data(relays=[0x7777]))

    hop = report["routes"]["route_record"]["path"][1]
    assert hop == {
        "nwk": "0x7777",
        "ieee": None,
        "device_name": None,
        "device_type": None,
    }


async def test_broken_source_route_chain_is_incomplete(hass, names):
    radio = _data()["radio"]
    radio["source_routes"] = [
        {"index": 1, "destination": TARGET, "closer_index": 5},
    ]
    report = build_route_report(hass, _data(radio=radio))

    assert not report["routes"]["source_route"]["complete"]


async def test_query_route_response_and_log(hass, names, caplog):
    device = SimpleNamespace(
        is_coordinator=False,
        device_type="EndDevice",
        available=True,
        last_seen=1_700_000_000.0,
        lqi=180,
        rssi=-60,
    )
    with (
        patch(
            "custom_components.zha_tools.route.async_get_zha_device",
            return_value=device,
        ),
        patch(
            "custom_components.zha_tools.route.async_get_route_data",
            new=AsyncMock(return_value=_data()),
        ),
        caplog.at_level(logging.INFO),
    ):
        result = await async_query_route(hass, names)

    assert result["device_id"] == names
    assert result["device_name"] == "Bedroom sensor"
    assert result["nwk"] == "0x3C4D"
    assert result["ieee"] == TARGET_IEEE
    assert result["last_seen"] == "2023-11-14T22:13:20+00:00"
    assert result["rssi"] == -60
    assert result["status"] == STATUS_COMPLETE
    assert result["complete_paths"] == ["route_record", "source_route", "topology"]
    message = next(
        r.getMessage() for r in caplog.records if r.getMessage().startswith("Route")
    )
    assert "Hall plug (0x1A2B)" in message


# --- adapter ---------------------------------------------------------------


class FakeStatus(enum.IntEnum):
    """Stand-in for bellows' ``EmberStatus`` / ``sl_Status``."""

    SUCCESS = 0
    ERR = 0x70


class FakeEnum(enum.Enum):
    """Stand-in for named zigpy / bellows enum members."""

    ACTIVE_AGE_0 = 0
    UNUSED = 3
    NOT_CONCENTRATOR = 10
    NO_LONGER_NEEDED = 11
    Router = 20
    Child = 21
    On = 22
    Active = 23
    EndDevice = 24
    END_DEVICE = 25


def _route_entry(destination, status):
    return SimpleNamespace(
        destination=destination,
        nextHop=ROUTER,
        status=status,
        age=1,
        concentratorType=FakeEnum.NOT_CONCENTRATOR,
        routeRecordState=FakeEnum.NO_LONGER_NEEDED,
    )


class FakeEzsp:
    """Minimal fake of bellows' EZSP connection."""

    def __init__(self, ezsp_version=13, neighbor_status=FakeStatus.SUCCESS):
        self.ezsp_version = ezsp_version
        self._neighbor_status = neighbor_status
        # EmberZNet answers unused slots with an error status, also in the
        # middle of the table.
        self._routes = [
            None,
            _route_entry(TARGET, FakeEnum.ACTIVE_AGE_0),
            _route_entry(0xFFFF, FakeEnum.UNUSED),
        ]
        self.route_reads = 0

    async def getConfigurationValue(self, config_id):
        assert config_id == "CONFIG_ROUTE_TABLE_SIZE"
        return FakeStatus.SUCCESS, len(self._routes)

    async def getRouteTableEntry(self, index):
        self.route_reads += 1
        entry = self._routes[index]
        if entry is None:
            return FakeStatus.ERR, None
        return FakeStatus.SUCCESS, entry

    async def neighborCount(self):
        return (2,)

    async def getNeighbor(self, index):
        if index == 1:
            # Aged out between neighborCount and the read.
            return FakeStatus.ERR, None
        return self._neighbor_status, SimpleNamespace(
            shortId=ROUTER,
            longId=ROUTER_IEEE,
            averageLqi=200,
            inCost=1,
            outCost=3,
            age=2,
        )

    async def read_child_data(self):
        yield TARGET, TARGET_IEEE, FakeEnum.END_DEVICE

    async def getSourceRouteTableTotalSize(self):
        return (2,)

    async def getSourceRouteTableEntry(self, index):
        if index == 0:
            return FakeStatus.ERR, 0, 0
        return FakeStatus.SUCCESS, TARGET, 0xFF


def _zha_device(ezsp):
    zigpy_target = SimpleNamespace(
        nwk=TARGET,
        ieee=TARGET_IEEE,
        relays=[ROUTER],
        node_desc=SimpleNamespace(logical_type=FakeEnum.EndDevice),
    )
    zigpy_router = SimpleNamespace(nwk=ROUTER, ieee=ROUTER_IEEE, node_desc=None)
    topology = SimpleNamespace(
        neighbors={
            ROUTER_IEEE: [
                SimpleNamespace(
                    nwk=TARGET,
                    ieee=TARGET_IEEE,
                    device_type=FakeEnum.EndDevice,
                    relationship=FakeEnum.Child,
                    rx_on_when_idle=FakeEnum.On,
                    depth=2,
                    lqi=150,
                )
            ]
        },
        routes={
            ROUTER_IEEE: [
                SimpleNamespace(
                    DstNWK=COORD,
                    RouteStatus=FakeEnum.Active,
                    NextHop=COORD,
                    ManyToOne=1,
                    RouteRecordRequired=0,
                    MemoryConstrained=0,
                )
            ]
        },
    )
    app = SimpleNamespace(
        state=SimpleNamespace(node_info=SimpleNamespace(nwk=COORD, ieee=COORD_IEEE)),
        devices={TARGET_IEEE: zigpy_target, ROUTER_IEEE: zigpy_router},
        topology=topology,
        config={"source_routing": True},
    )
    if ezsp is not None:
        app._ezsp = ezsp
    return SimpleNamespace(
        device=zigpy_target,
        gateway=SimpleNamespace(application_controller=app),
    )


@pytest.fixture(autouse=True)
def fake_bellows(monkeypatch):
    """Provide ``bellows.types`` and start every test with an empty cache."""
    types = ModuleType("bellows.types")
    types.EzspConfigId = SimpleNamespace(
        CONFIG_ROUTE_TABLE_SIZE="CONFIG_ROUTE_TABLE_SIZE"
    )
    bellows = ModuleType("bellows")
    bellows.types = types
    monkeypatch.setattr(zha_adapter, "_radio_tables_cache", None)
    with patch.dict(sys.modules, {"bellows": bellows, "bellows.types": types}):
        yield


async def test_adapter_collects_route_data():
    data = await zha_adapter.async_get_route_data(_zha_device(FakeEzsp()))

    assert data["coordinator"] == {"nwk": COORD, "ieee": COORD_IEEE}
    assert data["target"] == {"nwk": TARGET, "ieee": TARGET_IEEE}
    assert data["devices"][TARGET] == {"ieee": TARGET_IEEE, "device_type": "EndDevice"}
    assert data["devices"][ROUTER]["device_type"] is None
    assert data["source_routing"] is True
    assert data["relays"] == [ROUTER]
    assert data["topology_neighbors"][ROUTER_IEEE][0]["relationship"] == "Child"
    assert data["topology_routes"][ROUTER_IEEE][0]["many_to_one"] is True

    radio = data["radio"]
    # Unused slots (error status or UNUSED) are skipped, also before an
    # active entry.
    assert [e["destination"] for e in radio["route_table"]] == [TARGET]
    assert radio["route_table"][0]["status"] == "ACTIVE_AGE_0"
    assert len(radio["neighbors"]) == 1
    assert radio["neighbors"][0]["out_cost"] == 3
    assert radio["children"] == [
        {"nwk": TARGET, "ieee": TARGET_IEEE, "node_type": "END_DEVICE"}
    ]
    assert radio["source_routes"] == [
        {"index": 1, "destination": TARGET, "closer_index": 0xFF}
    ]


async def test_adapter_without_ezsp_radio():
    data = await zha_adapter.async_get_route_data(_zha_device(None))
    assert data["radio"] is None


async def test_adapter_skips_source_routes_on_old_firmware():
    data = await zha_adapter.async_get_route_data(_zha_device(FakeEzsp(5)))
    assert data["radio"]["source_routes"] is None


async def test_adapter_reuses_recent_radio_tables():
    ezsp = FakeEzsp()
    await zha_adapter.async_get_route_data(_zha_device(ezsp))
    await zha_adapter.async_get_route_data(_zha_device(ezsp))
    assert ezsp.route_reads == 3  # one read of the 3-slot table, not two

    other = FakeEzsp()
    await zha_adapter.async_get_route_data(_zha_device(other))
    assert other.route_reads == 3  # another radio is read afresh


async def test_coordinator_target_is_refused(hass):
    device = SimpleNamespace(is_coordinator=True)
    with (
        patch(
            "custom_components.zha_tools.route.async_get_zha_device",
            return_value=device,
        ),
        pytest.raises(ServiceValidationError, match="coordinator"),
    ):
        await async_query_route(hass, "coord")


async def test_former_child_entry_does_not_count_as_link(hass, names):
    """Only a Child entry links an end device to a router."""
    neighbors = _data()["topology_neighbors"]
    neighbors[ROUTER_IEEE][0]["relationship"] = "PreviousChild"
    report = build_route_report(
        hass,
        _data(
            relays=None,
            radio=None,
            topology_neighbors=neighbors,
            topology_routes={COORD_IEEE: [_active_route(TARGET, ROUTER)]},
        ),
    )

    topology = report["routes"]["topology"]
    assert topology["reason"] == "no_route"
    assert _names(topology) == ["Coordinator", "Hall plug"]
    assert report["parents"] == []
    assert report["status"] == STATUS_NO_ROUTE
    assert report["complete_paths"] == []


async def test_live_radio_tables_override_stale_coordinator_scan(hass, names):
    """With radio tables read, the coordinator's scanned tables are not used."""
    radio = _data()["radio"]
    radio["route_table"] = []
    stale = {COORD_IEEE: [_active_route(TARGET, ROUTER)]}
    report = build_route_report(
        hass, _data(relays=None, radio=radio, topology_routes=stale)
    )

    assert report["routes"]["topology"]["reason"] == "no_route"


async def test_source_route_cycle_stops_at_first_repeat(hass, names):
    radio = _data()["radio"]
    radio["source_routes"] = [
        {"index": 0, "destination": ROUTER, "closer_index": 1},
        {"index": 1, "destination": TARGET, "closer_index": 0},
    ]
    report = build_route_report(hass, _data(radio=radio))

    source = report["routes"]["source_route"]
    assert not source["complete"]
    assert source["hops"] == 2  # coordinator -> router -> target, no repeats


async def test_routers_have_no_parents(hass, names):
    devices = {**DEVICES, TARGET: {"ieee": TARGET_IEEE, "device_type": "Router"}}
    report = build_route_report(hass, _data(devices=devices))

    assert report["parents"] == []
    # A router target is linked through any neighbor entry.
    assert report["routes"]["topology"]["reason"] == "reached"


async def test_hop_prefers_reported_ieee_after_address_change(hass, names):
    """A NWK address now used by another device must not rename the hop."""
    neighbors = _data()["topology_neighbors"]
    neighbors[TARGET_IEEE] = [
        {
            "nwk": ROUTER,  # reused address, reported with another IEEE
            "ieee": "00:00:00:00:00:00:00:09",
            "device_type": "Router",
            "relationship": "Sibling",
            "rx_on_when_idle": "On",
            "depth": 1,
            "lqi": 90,
        }
    ]
    report = build_route_report(hass, _data(topology_neighbors=neighbors))

    hop = report["device_neighbors"][0]
    assert hop["ieee"] == "00:00:00:00:00:00:00:09"
    assert hop["device_name"] is None
