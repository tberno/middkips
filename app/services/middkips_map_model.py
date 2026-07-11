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
    source_system: str = "unknown"
    face_url: str = ""
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


def _source_system(row: Dict[str, Any]) -> str:
    text = _device_search_text(row)
    name = _device_name(row).lower()

    if any(x in name for x in ("edgefw-", "vpn-fw-", "enclave-fw-", "miis-fw", "fw1", "fw2")) or "panos" in text or "firewall" in text:
        return "edge"

    if any(x in name for x in ("700", "-dc-", "dc-", "storage", "racktop", "hpc", "mgmt")):
        return "legacy"

    if any(x in name for x in ("fabric-core-", "dist-", "svcs-")):
        return "mist"

    if any(x in text for x in ("arubaos-cx", "junos", "procurve")):
        return "librenms"

    if "opengear" in text:
        return "management"

    return "librenms"


def _role(row: Dict[str, Any]) -> str:
    text = _device_search_text(row)
    name = _device_name(row).lower()

    if any(x in name for x in ("edgefw-", "vpn-fw-", "enclave-fw-", "miis-fw", "fw1", "fw2")) or "panos" in text or "firewall" in text:
        return "firewall"

    if name in ("dfl-core.middlebury.edu", "vtr-core.middlebury.edu"):
        return "core"

    if name.startswith("fabric-core-"):
        return "fabric-core"

    if name.startswith("svcs-"):
        return "service"

    if name.startswith("dist-"):
        return "distribution"

    if any(x in name for x in ("700", "-dc-", "dc-", "storage", "racktop", "hpc", "mgmt")):
        return "datacenter"

    if "opengear" in text:
        return "management"

    if any(x in text for x in ("arubaos-cx", "junos", "procurve")):
        return "access"

    return "unknown"




def _name_matches_map(name: str, map_type: str) -> bool:
    n = (name or "").lower()

    if not re.search(r"[a-z]", n):
        return False

    # Global junk filters.
    if any(x in n for x in ("apc", "ups", "printer", "phone", "camera", "axis", "yealink", "polycom")):
        return False

    if map_type == "edge":
        return any(x in n for x in (
            "edgefw", "edge-fw",
            "vpn-fw", "vpnfw",
            "enclave-fw", "enclavefw",
            "miis-fw", "miisfw",
            "fw1", "fw2",
            "epl", "comcast", "wan",
            "libraryepl",
            "monterey-core", "ca-787munras",
            "dfl-core.middlebury.edu", "vtr-core.middlebury.edu"
        ))

    if map_type == "legacy-datacenter":
        return any(x in n for x in (
            "700", "dc-", "-dc-", "datacenter", "storage", "racktop",
            "hpc", "mgmt", "dc-fw", "cat-door-dc", "vtr-racktop", "dfl-dc"
        ))

    if map_type == "service-block":
        return any(x in n for x in (
            "fabric-core", "svcs-", "dist-", "dfl-core.middlebury.edu",
            "vtr-core.middlebury.edu", "edgefw", "vpn-fw", "enclave-fw",
            "700", "dc-", "-dc-", "storage", "racktop", "hpc", "mgmt"
        ))

    if map_type == "mist-pods":
        # Keep fabric/distribution/services/access, but exclude obvious edge/DC.
        if any(x in n for x in (
            "edgefw", "vpn-fw", "enclave-fw", "dc-fw", "700",
            "dc-", "-dc-", "storage", "racktop", "hpc", "mgmt", "cat-door"
        )):
            return False

        return any(x in n for x in (
            "fabric-core", "svcs-", "dist-", "pod", "-cx",
            "dfl-", "voter-", "oldchapel", "battell", "college", "adk",
            "bl-", "access"
        ))

    return True



def _map_membership(row: Dict[str, Any], map_type: str) -> bool:
    name = _device_name(row)
    return _name_matches_map(name, map_type)


def _include_for_map(row: Dict[str, Any], map_type: str) -> bool:
    return _map_membership(row, map_type)




def classify_role(hostname: str) -> str:
    hname = (hostname or "").lower()

    if any(x in hname for x in ("edgefw-", "vpn-fw-", "enclave-fw-", "miis-fw", "firewall", "fw-")):
        return "firewall"

    if hname in ("dfl-core.middlebury.edu", "vtr-core.middlebury.edu"):
        return "core"

    if hname.startswith("fabric-core-"):
        return "fabric-core"

    if hname.startswith("svcs-"):
        return "service"

    if hname.startswith("dist-"):
        return "distribution"

    if any(x in hname for x in ("700", "-dc-", "dc-", "storage", "racktop", "hpc", "mgmt")):
        return "datacenter"

    if any(x in hname for x in ("dfl-", "voter-", "cx", "-h", "-j")):
        return "access"

    return "unknown"


def _pod_from_name(name: str) -> str:
    n = (name or "").lower()

    if "eastpod" in n:
        return "east"
    if "northpod" in n:
        return "north"
    if "southpod" in n:
        return "south"
    if "westpod" in n:
        return "west"
    if "testpod" in n:
        return "test"

    # Core/service sites by name.
    if "dfl" in n:
        return "dfl"
    if "voter" in n or "vtr" in n:
        return "voter"

    return "unsorted"


def _mist_group_for_name(name: str) -> str:
    role = classify_role(name)
    pod = _pod_from_name(name)

    if role in ("core", "fabric-core"):
        return "mist-core"

    if role == "service":
        if pod in ("dfl", "voter"):
            return f"{pod}-services"
        return "mist-services"

    if role == "distribution":
        return f"{pod}-distribution"

    if role == "access":
        return f"{pod}-access"

    if role == "datacenter":
        return "non-mist-datacenter"

    if role == "firewall":
        return "edge-boundary"

    return "unsorted"


def classify_group(hostname: str, map_type: str) -> str:
    hname = (hostname or "").lower()
    role = classify_role(hostname)

    if map_type == "edge":
        if "dfl" in hname:
            return "dfl-edge"
        if "voter" in hname or "vtr" in hname:
            return "voter-edge"
        return "wan-edge"

    if map_type == "legacy-datacenter":
        if "700" in hname:
            return "700-datacenter"
        if "dfl" in hname:
            return "dfl-datacenter"
        if "vtr" in hname or "voter" in hname:
            return "voter-datacenter"
        return "legacy-datacenter"

    if map_type == "service-block":
        if role in ("core", "fabric-core"):
            return "core"
        if role == "service":
            return "services"
        if role == "distribution":
            return "distribution"
        if role == "firewall":
            return "edge-handoff"
        if role == "datacenter":
            return "legacy-handoff"
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
        if role in ("core", "fabric-core"):
            return "fabric-core"
        if role == "service":
            return "services"
        if "dfl" in hname:
            return "dfl-access"
        if "voter" in hname or "vtr" in hname:
            return "voter-access"
        return "campus-access"

    return "network"

def map_groups(map_type: str) -> List[MapGroup]:
    if map_type == "mist-pods":
        return [
            MapGroup("mist-core", "Mist Fabric Core", "core", "#39ff14"),
            MapGroup("dfl-services", "DFL Services", "service", "#ff9f1c"),
            MapGroup("voter-services", "Voter Services", "service", "#ff9f1c"),

            MapGroup("east-distribution", "East Pod Distribution", "distribution", "#00e5ff"),
            MapGroup("east-access", "East Pod Access", "access", "#38bdf8"),

            MapGroup("north-distribution", "North Pod Distribution", "distribution", "#00e5ff"),
            MapGroup("north-access", "North Pod Access", "access", "#38bdf8"),

            MapGroup("south-distribution", "South Pod Distribution", "distribution", "#00e5ff"),
            MapGroup("south-access", "South Pod Access", "access", "#38bdf8"),

            MapGroup("west-distribution", "West Pod Distribution", "distribution", "#00e5ff"),
            MapGroup("west-access", "West Pod Access", "access", "#38bdf8"),

            MapGroup("test-distribution", "Test Pod Distribution", "distribution", "#a855f7"),
            MapGroup("test-access", "Test Pod Access", "access", "#a855f7"),

            MapGroup("dfl-access", "DFL Access / Local", "access", "#22c55e"),
            MapGroup("voter-access", "Voter Access / Local", "access", "#22c55e"),

            MapGroup("unsorted", "Unsorted / Non-Mist Candidates", "unknown", "#94a3b8"),
            MapGroup("edge-boundary", "Edge Boundary", "edge", "#ff3131"),
            MapGroup("non-mist-datacenter", "Non-Mist Datacenter", "datacenter", "#a3a3a3"),
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
        "(display REGEXP '[A-Za-z]' OR sysName REGEXP '[A-Za-z]' OR hostname REGEXP '[A-Za-z]')",
    ]
    params = []

    if map_type == "edge":
        where.append("""(
            type = 'firewall'
            OR os = 'panos'
            OR display LIKE %s OR sysName LIKE %s OR hostname LIKE %s
            OR display LIKE %s OR sysName LIKE %s OR hostname LIKE %s
            OR display LIKE %s OR sysName LIKE %s OR hostname LIKE %s
            OR display LIKE %s OR sysName LIKE %s OR hostname LIKE %s
            OR display LIKE %s OR sysName LIKE %s OR hostname LIKE %s
            OR display LIKE %s OR sysName LIKE %s OR hostname LIKE %s
        )""")
        for term in ["edge", "fw", "vpn", "epl", "comcast", "wan"]:
            like = f"%{term}%"
            params.extend([like, like, like])

    else:
        map_keywords = {
            "legacy-datacenter": [
                "700", "dc-", "-dc-", "datacenter", "storage", "racktop",
                "hpc", "mgmt", "dc-fw", "cat-door-dc", "vtr-racktop", "dfl-dc"
            ],
            "service-block": [
                "fabric-core", "svcs-", "dist-", "dfl-core", "vtr-core",
                "edgefw", "vpn-fw", "enclave-fw", "700", "dc-", "-dc-",
                "storage", "racktop", "hpc", "mgmt"
            ],
            "mist-pods": [
                "fabric-core", "svcs-", "dist-", "pod", "-cx",
                "dfl-", "voter-", "oldchapel", "battell", "college", "adk",
                "bl-", "access"
            ],
        }

        keywords = map_keywords.get(map_type, [])
        if keywords:
            parts = []
            for kw in keywords:
                parts.append("(display LIKE %s OR sysName LIKE %s OR hostname LIKE %s)")
                like = f"%{kw}%"
                params.extend([like, like, like])
            where.append("(" + " OR ".join(parts) + ")")

    if q:
        where.append("(display LIKE %s OR sysName LIKE %s OR hostname LIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like])

    sql = """
        SELECT
          device_id, hostname, sysName, display, ip, status, type, os, hardware,
          sysDescr, location_id, bgpLocalAs, disabled, `ignore`
        FROM devices
        WHERE {where}
        ORDER BY display, sysName, hostname
        LIMIT %s
    """.format(where=" AND ".join(where))

    params.append(max(int(limit) * 10, 1000))

    rows = db_query(sql, params)
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



def _device_face_url(row: Dict[str, Any]) -> str:
    text = " ".join(
        str(row.get(k) or "").lower()
        for k in ("hardware", "sysDescr", "os", "icon", "display", "sysName")
    )

    if "pa-3440" in text or "pa-3400" in text:
        return "/static/device_faces/vendor/paloalto/pa-3440-front.webp"

    if "pa-3220" in text or "pa-3200" in text:
        return "/static/device_faces/vendor/paloalto/pa-3220-front.webp"

    return ""

def build_map(map_type: str, q: str = "", focus: str = "", limit: int = 300) -> Dict[str, Any]:
    devices = _device_rows(map_type=map_type, q=q, limit=limit)

    nodes: Dict[str, MapNode] = {}

    for row in devices:
        hostname = _device_name(row)
        role = _role(row)
        group = classify_group(hostname, map_type)
        source_system = _source_system(row)

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
            source_system=source_system,
            face_url=_device_face_url(row),
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
            if not _name_matches_map(str(remote_name), map_type):
                continue

            role = classify_role(remote_name)
            group = classify_group(remote_name, map_type)

            # Drop unclassifiable remote noise unless it is explicitly useful for the selected map.
            if role == "unknown" and map_type in ("mist-pods", "service-block"):
                continue

            nodes[remote_node_id] = MapNode(
                id=remote_node_id,
                label=remote_name,
                type="remote",
                role=role,
                group=group,
                source_system="remote",
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
