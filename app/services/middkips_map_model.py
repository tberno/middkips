from dataclasses import dataclass, asdict
from html import escape as h
import re
from typing import Any, Dict, List, Optional

from app.core.db import fetch_all as db_query


@dataclass
class MapNode:
    id: str
    label: str
    type: str
    role: str
    group: str
    status: str = "unknown"
    management_url: str = ""
    detail_url: str = ""


@dataclass
class MapLink:
    id: str
    source: str
    target: str
    type: str
    status: str = "unknown"
    label: str = ""
    detail_url: str = ""
    redundant: bool = False


@dataclass
class MapGroup:
    id: str
    label: str
    type: str
    color: str


def _slug(value: Any) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def _device_name(row: Dict[str, Any]) -> str:
    """
    LibreNMS hostname is often the IP. Prefer display/sysName for human map labels.
    """
    for key in ("display", "sysName", "hostname"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return str(row.get("device_id") or "unknown")


def _device_search_text(row: Dict[str, Any]) -> str:
    return " ".join(
        str(row.get(k) or "").lower()
        for k in ("display", "sysName", "hostname", "type", "os", "hardware")
    )


def _is_named_device(row: Dict[str, Any]) -> bool:
    name = _device_name(row)
    return bool(re.search(r"[a-zA-Z]", name))


def _include_for_map(row: Dict[str, Any], map_type: str) -> bool:
    text = _device_search_text(row)

    # Keep only useful named devices. Raw IP-only devices make the maps useless.
    if not _is_named_device(row):
        return False

    # Avoid obvious non-topology endpoints in the first pass.
    if any(x in text for x in ("apc", "ups", "printer", "phone", "camera", "axis", "yealink", "polycom")):
        return False

    if map_type == "edge":
        return any(x in text for x in ("edgefw", "vpn-fw", "firewall", "fw-", "panos", "comcast", "epl"))

    if map_type == "legacy-datacenter":
        return any(x in text for x in ("700", "dc-", "-dc-", "datacenter", "storage", "racktop", "mgmt"))

    if map_type == "service-block":
        return any(x in text for x in (
            "fabric-core", "svcs-", "dist-", "core", "dfl-core", "vtr-core",
            "edgefw", "vpn-fw", "700", "dc-", "-dc-"
        ))

    if map_type == "mist-pods":
        return any(x in text for x in (
            "fabric-core", "dist-", "svcs-", "dfl-", "voter-", "pod",
            "daisy-", "zuma-", "oldchapel", "battell", "access", "cx"
        ))

    return True




def classify_role(hostname: str) -> str:
    hname = (hostname or "").lower()

    if any(x in hname for x in ("edgefw", "vpn-fw", "firewall", "fw-", "panos", "comcast", "epl")):
        return "edge"
    if "fabric-core" in hname or hname in ("dfl-core.middlebury.edu", "vtr-core.middlebury.edu"):
        return "fabric-core"
    if hname.startswith("svcs-") or "-svcs" in hname:
        return "service"
    if hname.startswith("dist-") or "-dist-" in hname:
        return "distribution"
    if "700" in hname or "datacenter" in hname or "-dc-" in hname or hname.startswith("dc-"):
        return "datacenter"
    if any(x in hname for x in ("dfl-", "voter-", "oldchapel", "battell", "daisy-", "zuma-")):
        return "access"
    return "network"


    if "core" in hname and ("dfl" in hname or "voter" in hname or "dist" in hname):
        return "distribution-core"
    if "core" in hname:
        return "core"
    if "edge" in hname or "fw" in hname or "firewall" in hname or "pan" in hname:
        return "edge"
    if "700" in hname or "datacenter" in hname or "dc" in hname:
        return "datacenter"
    if "ap" in hname:
        return "ap"
    if "access" in hname or re.search(r"\b\d{2,3}-", hname):
        return "access"
    return "network"


def classify_group(hostname: str, map_type: str) -> str:
    hname = (hostname or "").lower()

    if map_type == "edge":
        if "dfl" in hname:
            return "dfl-edge"
        if "voter" in hname or "vtr" in hname:
            return "voter-edge"
        return "edge"

    if map_type == "legacy-datacenter":
        if "dfl" in hname:
            return "dfl-datacenter"
        if "700" in hname:
            return "700-datacenter"
        return "legacy-datacenter"

    if map_type == "service-block":
        if "fabric-core" in hname:
            return "fabric-core"
        if hname.startswith("svcs-"):
            return "services"
        if hname.startswith("dist-"):
            return "distribution"
        if "edgefw" in hname or "vpn-fw" in hname:
            return "edge"
        if "700" in hname or "-dc-" in hname:
            return "legacy-datacenter"
        return "service-block"

    if map_type == "mist-pods":
        if "eastpod" in hname:
            return "east-pod"
        if "northpod" in hname:
            return "north-pod"
        if "southpod" in hname:
            return "south-pod"
        if "testpod" in hname:
            return "test-pod"
        if "fabric-core" in hname:
            return "fabric-core"
        if hname.startswith("svcs-"):
            return "services"
        if "dfl" in hname:
            return "dfl-pod"
        if "voter" in hname or "vtr" in hname:
            return "voter-pod"
        return "mist-access"

    return "network"


    if map_type == "edge":
        return "edge"

    if "700" in hname or "datacenter" in hname or "dc" in hname:
        return "legacy-datacenter"

    if "dfl" in hname:
        return "dfl-pod"
    if "voter" in hname:
        return "voter-pod"

    if "core" in hname or "dist" in hname:
        return "service-block"

    if map_type == "mist-pods":
        return "mist-access"

    return "legacy"


def map_groups(map_type: str) -> List[MapGroup]:
    if map_type == "mist-pods":
        return [
            MapGroup("dfl-pod", "DFL Pod", "pod", "#39ff14"),
            MapGroup("voter-pod", "Voter Pod", "pod", "#00e5ff"),
            MapGroup("mist-access", "Mist Access", "access", "#a855f7"),
            MapGroup("service-block", "Service / Distribution", "service", "#ff9f1c"),
        ]

    if map_type == "service-block":
        return [
            MapGroup("service-block", "Service Block", "service", "#ff9f1c"),
            MapGroup("mist-access", "Mist Fabric", "mist", "#39ff14"),
            MapGroup("legacy", "Legacy Network", "legacy", "#38bdf8"),
            MapGroup("legacy-datacenter", "Legacy Datacenter", "datacenter", "#a855f7"),
        ]

    if map_type == "legacy-datacenter":
        return [
            MapGroup("legacy-datacenter", "Legacy Datacenter", "datacenter", "#a855f7"),
            MapGroup("service-block", "Service / Distribution", "service", "#ff9f1c"),
            MapGroup("legacy", "Legacy Network", "legacy", "#38bdf8"),
        ]

    if map_type == "edge":
        return [
            MapGroup("edge", "Edge / WAN / Firewall", "edge", "#ff3131"),
            MapGroup("service-block", "Service Block", "service", "#ff9f1c"),
            MapGroup("legacy", "Legacy Network", "legacy", "#38bdf8"),
        ]

    return [
        MapGroup("network", "Network", "network", "#38bdf8"),
    ]


def _device_rows(map_type: str, q: str = "", limit: int = 300) -> List[Dict[str, Any]]:
    where = [
        "disabled = 0",
        "`ignore` = 0",
        "status = 1",
        "(type IN ('network', 'firewall', 'management') OR os IN ('junos', 'arubaos-cx', 'procurve', 'panos', 'opengear'))",
        "(display REGEXP '[A-Za-z]' OR sysName REGEXP '[A-Za-z]')",
    ]
    params = []

    if q:
        where.append("(display LIKE %s OR sysName LIKE %s OR hostname LIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like])

    sql = """
        SELECT
          device_id, hostname, sysName, display, ip, status, type, os, hardware,
          location_id, bgpLocalAs, disabled, `ignore`
        FROM devices
        WHERE {where}
        ORDER BY display, sysName, hostname
        LIMIT %s
    """.format(where=" AND ".join(where))

    params.append(int(limit) * 4)

    rows = db_query(sql, params)

    # Python-side map filter, because these names are too human for pure SQL sanity.
    filtered = [r for r in rows if _include_for_map(r, map_type)]
    return filtered[: int(limit)]


def _link_rows(device_ids: List[int], limit: int = 1000) -> List[Dict[str, Any]]:
    if not device_ids:
        return []

    placeholders = ",".join(["%s"] * len(device_ids))

    sql = f"""
        SELECT
          l.*,
          d.hostname AS local_hostname,
          d.device_id AS local_device_id,
          p.ifName AS local_ifname,
          p.ifDescr AS local_ifdescr,
          p.ifAlias AS local_ifalias
        FROM links l
        LEFT JOIN ports p ON p.port_id = l.local_port_id
        LEFT JOIN devices d ON d.device_id = p.device_id
        WHERE d.device_id IN ({placeholders})
        ORDER BY d.hostname, p.ifName
        LIMIT %s
    """

    params = list(device_ids)
    params.append(int(limit))
    return db_query(sql, params)


def build_map(map_type: str, q: str = "", focus: str = "", limit: int = 300) -> Dict[str, Any]:
    devices = _device_rows(map_type=map_type, q=q, limit=limit)

    nodes: Dict[str, MapNode] = {}

    for row in devices:
        hostname = _device_name(row)
        role = classify_role(hostname)
        group = classify_group(hostname, map_type)

        # Map-specific filtering. Keep this gentle for pass one.
        if map_type == "edge" and role != "edge":
            continue
        if map_type == "legacy-datacenter" and group != "legacy-datacenter":
            continue
        if map_type == "mist-pods" and role == "edge":
            continue

        node_id = f"dev-{row.get('device_id')}"
        nodes[node_id] = MapNode(
            id=node_id,
            label=hostname,
            type="device",
            role=role,
            group=group,
            status="up" if str(row.get("status")) == "1" else "down",
            management_url=f"/dashboard?device_id={row.get('device_id')}",
            detail_url=f"/tools/maps/drilldown/device/{row.get('device_id')}",
        )

    device_ids = []
    for node in nodes.values():
        if node.id.startswith("dev-"):
            try:
                device_ids.append(int(node.id.replace("dev-", "")))
            except ValueError:
                pass

    links: List[MapLink] = []
    link_rows = _link_rows(device_ids)

    for row in link_rows:
        local_id = row.get("local_device_id")
        remote_name = row.get("remote_hostname") or row.get("remote_host") or row.get("remote_sysname")
        if not local_id or not remote_name:
            continue

        source = f"dev-{local_id}"
        remote_node_id = f"remote-{_slug(remote_name)}"

        if source not in nodes:
            continue

        if remote_node_id not in nodes:
            role = classify_role(remote_name)
            group = classify_group(remote_name, map_type)
            nodes[remote_node_id] = MapNode(
                id=remote_node_id,
                label=remote_name,
                type="remote",
                role=role,
                group=group,
                status="unknown",
                detail_url=f"/lookup?q={h(remote_name)}",
            )

        local_if = row.get("local_ifname") or row.get("local_ifdescr") or ""
        remote_if = row.get("remote_port") or row.get("remote_port_id") or row.get("remote_ifname") or ""
        label = " / ".join([str(x) for x in [local_if, remote_if] if x])

        link_id = f"link-{_slug(source)}-{_slug(remote_node_id)}-{len(links)}"
        redundant = nodes[source].role in ("access", "network") and nodes[remote_node_id].role in ("distribution-core", "core")

        links.append(
            MapLink(
                id=link_id,
                source=source,
                target=remote_node_id,
                type=str(row.get("protocol") or "lldp"),
                status="unknown",
                label=label,
                detail_url=f"/tools/maps/drilldown/link?source={h(source)}&target={h(remote_node_id)}",
                redundant=redundant,
            )
        )

    return {
        "map_type": map_type,
        "focus": focus,
        "groups": [asdict(g) for g in map_groups(map_type)],
        "nodes": [asdict(n) for n in nodes.values()],
        "links": [asdict(l) for l in links],
    }


def node_detail(device_id: int) -> Dict[str, Any]:
    rows = db_query(
        """
        SELECT device_id, hostname, sysName, ip, status, purpose, location_id, hardware, os, version
        FROM devices
        WHERE device_id = %s
        LIMIT 1
        """,
        [device_id],
    )

    device = rows[0] if rows else {}

    ports = db_query(
        """
        SELECT port_id, ifName, ifDescr, ifAlias, ifOperStatus, ifAdminStatus, ifSpeed
        FROM ports
        WHERE device_id = %s
        ORDER BY ifIndex
        LIMIT 250
        """,
        [device_id],
    )

    links = db_query(
        """
        SELECT
          l.*,
          p.ifName AS local_ifname,
          p.ifDescr AS local_ifdescr,
          p.ifAlias AS local_ifalias
        FROM links l
        LEFT JOIN ports p ON p.port_id = l.local_port_id
        WHERE p.device_id = %s
        ORDER BY p.ifName
        LIMIT 250
        """,
        [device_id],
    )

    return {
        "device": device,
        "ports": ports,
        "links": links,
    }
