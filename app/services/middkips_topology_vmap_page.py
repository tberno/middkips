from __future__ import annotations

import html
import math
import re
from urllib.parse import quote, urlencode
from collections import defaultdict

from app.core.db import fetch_all


def h(v):
    return html.escape(str(v or ""), quote=True)


def clean_name(v):
    return str(v or "").strip()


def norm(v):
    return str(v or "").strip().lower()


def bool_param(value, default=False):
    if value is None:
        return bool(default)
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")



def display_role(role):
    """
    Collapse detailed infrastructure roles into the layer shown on the V-map.
    This keeps the page readable without losing the ability to refine roles later.
    """
    if role in (
        "Core Infrastructure",
        "Legacy Core",
        "Fabric Core",
        "Edge / WAN",
        "Core",
    ):
        return "Core Infrastructure"
    return role


def keep_role(role, show_core=True, show_services=True, show_distribution=True, show_access=True, show_aps=False):
    if role in ("Core Infrastructure", "Core", "Legacy Core", "Fabric Core", "Edge / WAN"):
        return show_core
    if role == "Services":
        return show_services
    if role == "Distribution":
        return show_distribution
    if role == "Access / Leaf":
        return show_access
    if role == "AP":
        return show_aps
    return False


def layer_url(site, show_ports, show_core, show_services, show_distribution, show_access, show_aps):
    return (
        "/tools/topology-vmap"
        f"?site={h(site)}"
        f"&show_ports={h(show_ports)}"
        f"&show_core={1 if show_core else 0}"
        f"&show_services={1 if show_services else 0}"
        f"&show_distribution={1 if show_distribution else 0}"
        f"&show_access={1 if show_access else 0}"
        f"&show_aps={1 if show_aps else 0}"
    )


def is_monterey_device(d):
    text = " ".join(
        str(d.get(k) or "")
        for k in ("hostname", "sysName", "hardware", "os", "type")
    ).lower()

    if any(x in text for x in [
        "monterey",
        "miis",
        "munras",
        "787munras",
        "middlebury institute",
        "middlebury-institute",
    ]):
        return True

    # Monterey device names we have seen in LibreNMS.
    hn = norm(d.get("hostname"))
    sn = norm(d.get("sysName"))
    if hn.startswith("ca-") or sn.startswith("ca-"):
        return True

    if "192.168.194." in text:
        return True

    return False


def is_obvious_endpoint_name(name):
    n = norm(name)
    endpoint_terms = [
        "ap", "printer", "camera", "phone", "ups", "pdu", "apc",
        "cohesity", "phlash", "flashblade", "flash blade",
        "pure", "storage", "idrac", "ilo", "bmc"
    ]
    return any(t in n for t in endpoint_terms)


def is_power_device(d):
    """
    Hide UPS/PDU/power devices from the infrastructure V-map.
    These may be valid LibreNMS devices, but they are not part of the
    core/distribution/access switch topology view.
    """
    text = " ".join(
        str(d.get(k) or "")
        for k in ("hostname", "sysName", "display_name", "hardware", "os", "type")
    ).lower()

    power_terms = [
        "ups",
        "pdu",
        "apc",
        "eaton",
        "symmetra",
        "tripp lite",
        "tripplite",
        "cyberpower",
        "rackpdu",
        "powernet",
        "power distribution",
        "battery backup",
        "battery-backup",
    ]

    if any(t in text for t in power_terms):
        return True

    # Catch clean names like lib-ups-1, srv_pdu_2, etc.
    if re.search(r'(^|[-_.\s])(ups|pdu)([-_.\s]|$)', text):
        return True

    return False



def is_ap_device(d):
    """
    Detect managed APs so the V-map can toggle them on/off.
    Default view should be switch-only.
    """
    text = " ".join(
        str(d.get(k) or "")
        for k in ("hostname", "sysName", "display_name", "hardware", "os", "type")
    ).lower()

    ap_terms = [
        "wireless access point",
        "access point",
        "mist ap",
        "juniper ap",
        "ap12",
        "ap32",
        "ap33",
        "ap43",
        "ap45",
        "ap47",
    ]

    if any(t in text for t in ap_terms):
        return True

    # Catch names like AP-123, DFL-AP-101, 107ShannonSt-202 AP-ish devices,
    # without catching normal words like "application".
    if re.search(r'(^|[-_.\s])(ap|wap)([-_.\s0-9]|$)', text):
        return True

    return False


def device_display_name(d):
    return clean_name(d.get("sysName")) or clean_name(d.get("hostname")) or str(d.get("device_id"))



def role_override(d):
    """
    Hard role overrides for infrastructure devices that should not fall into
    generic Access / Leaf. This separates fabric core, legacy core, edge/WAN,
    services, and distribution so the map reads correctly.
    """
    text = " ".join(
        str(d.get(k) or "")
        for k in ("hostname", "sysName", "display_name", "hardware", "os", "type")
    ).lower()

    name = str(d.get("display_name") or d.get("hostname") or d.get("sysName") or "").lower()

    # WAN / edge routers
    if any(t in name for t in ("turbo-", "zuma-", "edge-fw", "vpn-fw")):
        return "Edge / WAN"

    if any(t in text for t in ("mx204", "jnp204", "pa-3220", "palo alto")):
        return "Edge / WAN"

    # New Mist/fabric core
    if any(t in name for t in ("fabric-core-dfl", "fabric-core-voter")):
        return "Fabric Core"

    if "qfx5120" in text and "fabric-core" in name:
        return "Fabric Core"

    # Legacy campus/core backbone
    if any(t in name for t in (
        "dfl-core",
        "vtr-core",
        "bonefish",
        "calamari",
        "monterey-core",
        "vtr-racktop",
        "racktop",
        "core-qfx",
        "core-4200",
    )):
        return "Legacy Core"

    if any(t in text for t in ("qfx10002", "qfx5100")):
        return "Legacy Core"

    # Distribution / aggregation
    if any(t in name for t in (
        "dist-",
        "aggregation-a",
        "aggregation-b",
        "browntrout",
    )):
        return "Distribution"

    # Services aggregation. Keep narrow so services-b-sfs-cx stays access.
    if any(t in name for t in (
        "svc-dfl",
        "svc-voter",
        "svcs-dfl",
        "svcs-voter",
    )):
        return "Services"

    return None


def classify_role(d):
    if is_ap_device(d):
        return "AP"

    override = role_override(d)
    if override:
        return override

    name = norm(device_display_name(d))
    hw = norm(d.get("hardware"))
    text = f"{name} {hw} {norm(d.get('type'))}"

    if "fabric-core" in name:
        return "Core"

    if "monterey-core" in name:
        return "Core"

    if name in ("dfl-core", "vtr-core") or name.endswith("-core") or "-core-" in name:
        return "Core"

    if name.startswith("svcs-") or name in ("svcs-dfl", "svcs-voter"):
        return "Services"

    if name.startswith("dist-") or name.startswith("dist"):
        return "Distribution"

    if "aggregation-a" in name or "aggregation-b" in name:
        return "Distribution"

    if name.startswith("aggregation-"):
        return "Distribution"

    if "agg-" in name or "-agg" in name or "aggregation" in name:
        return "Distribution"

    if "qfx5100" in text and any(x in name for x in ["browntrout", "aggregation"]):
        return "Distribution"

    return "Access / Leaf"


def site_scope(d):
    return "monterey" if is_monterey_device(d) else "vt"




def is_network_infra_device(d):
    """
    Keep the V-map limited to actual network infrastructure:
    switches, firewalls, and edge/core routers.

    This intentionally excludes servers, VMs, Avaya phone systems,
    generic boxes, SolidServer appliances, TLRS/eduroam hosts, etc.
    """
    text = " ".join(
        str(d.get(k) or "")
        for k in ("hostname", "sysName", "display_name", "hardware", "os", "type")
    ).lower()

    # Obvious non-network-infra devices.
    reject_terms = [
        "standard pc",
        "virtual machine",
        "vmware",
        "hyper-v",
        "i440fx",
        "qemu",
        "solidserver",
        "solid server",
        "zeus-eip",
        "hera-eip",
        "juno-eip",
        "jupiter-eip",
        "avaya ip office",
        "ip500",
        "tlrs",
        "eduroam.us",
        "no idea",
        "generic",
        "printer",
        "camera",
        "ups",
        "pdu",
        "apc",
        "server",
        "windows",
        "linux",
    ]

    if any(t in text for t in reject_terms):
        return False

    # Network infra we want.
    keep_terms = [
        # Juniper switching / routing
        "juniper",
        "ex2200",
        "ex2300",
        "ex3300",
        "ex3400",
        "ex4100",
        "ex4300",
        "ex4400",
        "qfx",
        "qfx5100",
        "qfx5120",
        "qfx10002",
        "mx204",
        "jnp204",

        # Aruba / HPE switching
        "hpe anw",
        "aruba",
        "procurve",
        "2930",
        "2920",
        "5400",
        "6100",
        "6200",
        "6300",
        "6400",
        "8320",
        "8360",
        "vsf stack",

        # Palo firewalls
        "pa-",
        "pa-820",
        "pa-1420",
        "pa-3220",
        "pa-3440",
        "palo alto",
        "pan-os",
        "panos",

        # Naming patterns that are network infra even if hardware is vague
        "core",
        "fabric-core",
        "dist-",
        "aggregation-",
        "racktop",
        "edgefw",
        "edge-fw",
        "enclave-fw",
        "vpn-fw",
        "dc-fw",
        "miis-fw",
        "firewall",
    ]

    return any(t in text for t in keep_terms)


def final_vmap_role(d):
    """
    Final display role for the V-map.

    This is applied inside load_devices after the older classifier.
    It prevents core, edge, firewall, and backbone infrastructure from
    falling through into Access / Leaf.
    """
    text = " ".join(
        str(d.get(k) or "")
        for k in ("hostname", "sysName", "display_name", "hardware", "os", "type", "role")
    ).lower()

    name = str(d.get("display_name") or d.get("hostname") or d.get("sysName") or "").lower()

    detailed_core_roles = [
        "legacy core",
        "fabric core",
        "edge / wan",
        "core infrastructure",
        "core",
    ]

    core_name_terms = [
        "bonefish",
        "browntrout",
        "calamari",
        "dfl-core",
        "vtr-core",
        "fabric-core",
        "turbo-vtr",
        "zuma-dfl",
        "vpn-fw",
        "edgefw",
        "edge-fw",
        "enclave-fw",
        "dc-fw",
        "miis-fw",
        "monterey-core",
        "core-qfx",
        "core-4200",
    ]

    core_hw_terms = [
        "qfx10002",
        "qfx5100",
        "qfx5120",
        "mx204",
        "jnp204",
        "pa-",
        "pa-820",
        "pa-1420",
        "pa-3220",
        "pa-3440",
        "palo alto",
        "pan-os",
        "panos",
    ]

    if any(str(t) in text for t in detailed_core_roles):
        return "Core Infrastructure"

    if any(str(t) in name for t in core_name_terms):
        return "Core Infrastructure"

    if any(str(t) in text for t in core_hw_terms):
        return "Core Infrastructure"

    if any(t in name for t in ("svc-dfl", "svc-voter", "svcs-dfl", "svcs-voter")):
        return "Services"

    if any(t in name for t in ("dist-", "aggregation-a", "aggregation-b")):
        return "Distribution"

    return d.get("role") or "Access / Leaf"


def load_devices(site="vt", show_aps=False):
    rows = fetch_all(
        """
        SELECT
            device_id,
            hostname,
            sysName,
            hardware,
            os,
            type,
            status,
            disabled
        FROM devices
        WHERE disabled = 0
          AND status = 1
        ORDER BY sysName, hostname
        """
    )

    devices = {}
    for d in rows:
        d = dict(d)
        d["display_name"] = device_display_name(d)
        d["role"] = classify_role(d)
        d["raw_role"] = d.get("role")
        d["role"] = final_vmap_role(d)
        d["site_scope"] = site_scope(d)

        if site == "vt" and d["site_scope"] != "vt":
            continue
        if site == "monterey" and d["site_scope"] != "monterey":
            continue

        # Keep managed switches/routers. Drop power devices from the V-map.
        if is_power_device(d):
            continue

        # Default V-map is switch-only. APs can be toggled on.
        if is_ap_device(d) and not show_aps:
            continue

        # Drop obvious endpoints if they ever got discovered as managed nodes.
        # Do not drop APs here when show_aps is enabled.
        if d["role"] != "AP" and is_obvious_endpoint_name(d["display_name"]) and d["role"] == "Access / Leaf":
            continue

        # Keep the V-map strict: switching, firewalls, and core/edge routing only.
        # No VMs, servers, Avaya systems, SolidServer appliances, TLRS boxes, etc.
        if not is_network_infra_device(d):
            continue

        devices[int(d["device_id"])] = d

    return devices


def load_lldp_edges(devices):
    if not devices:
        return []

    ids = sorted(devices.keys())
    placeholders = ",".join(["%s"] * len(ids))

    rows = fetch_all(
        f"""
        SELECT
            l.id,
            l.local_device_id,
            l.remote_device_id,
            l.remote_hostname,
            l.remote_port,
            lp.ifName AS local_port,
            lp.ifDescr AS local_ifdescr,
            lp.ifAlias AS local_descr,
            rp.ifName AS remote_port_name,
            rp.ifDescr AS remote_ifdescr,
            rp.ifAlias AS remote_descr
        FROM links l
        JOIN ports lp ON lp.port_id = l.local_port_id
        LEFT JOIN ports rp ON rp.port_id = l.remote_port_id
        WHERE l.protocol = 'lldp'
          AND l.local_device_id IN ({placeholders})
          AND l.remote_device_id IN ({placeholders})
          AND l.remote_device_id > 0
        ORDER BY l.local_device_id, l.remote_device_id, lp.ifName, l.remote_port
        """,
        ids + ids,
    )

    # Deduplicate the two LLDP directions, but keep distinct physical members.
    seen = set()
    out = []

    for r in rows:
        r = dict(r)
        a = int(r.get("local_device_id") or 0)
        b = int(r.get("remote_device_id") or 0)
        if not a or not b or a == b:
            continue

        local_port = clean_name(r.get("local_port"))
        remote_port = clean_name(r.get("remote_port_name")) or clean_name(r.get("remote_port"))

        # device+port pair key means two links in an AE/LAG remain two separate edges.
        p1 = (a, local_port)
        p2 = (b, remote_port)
        key = tuple(sorted([p1, p2]))

        if key in seen:
            continue
        seen.add(key)

        r["a"] = a
        r["b"] = b
        r["a_port"] = local_port
        r["b_port"] = remote_port
        r["a_name"] = device_display_name(devices[a])
        r["b_name"] = device_display_name(devices[b])
        out.append(r)

    return out





def positioned_nodes(devices, tv_mode=False):
    # Deterministic V-map layout. Every node is bucketed by final_vmap_role(d).
    layer_order = [
        "Core Infrastructure",
        "Services",
        "Distribution",
        "Access / Leaf",
        "AP",
    ]

    if tv_mode:
        # TV / wallboard mode: spread horizontally. Humans invented 4K,
        # then immediately used only 2/3 of it. We are correcting that.
        role_cols = {
            "Core Infrastructure": 8,
            "Services": 6,
            "Distribution": 8,
            "Access / Leaf": 10,
            "AP": 12,
        }
    else:
        role_cols = {
            "Core Infrastructure": 6,
            "Services": 4,
            "Distribution": 6,
            "Access / Leaf": 8,
            "AP": 10,
        }

    bucket = defaultdict(list)

    for d in devices.values():
        role = final_vmap_role(d)
        d["role"] = role
        d["vmap_role"] = role

        if role not in layer_order:
            role = "Access / Leaf"
            d["role"] = role
            d["vmap_role"] = role

        bucket[role].append(d)

    node_w = 230
    node_h = 58

    if tv_mode:
        gap_x = 110
        gap_y = 42
        x0 = 110
        y = 115
        layer_gap = 175
        max_width = 3000
    else:
        gap_x = 72
        gap_y = 34
        x0 = 80
        y = 150
        layer_gap = 150
        max_width = 1400

    pos = {}
    layers = []

    for role in layer_order:
        nodes = sorted(bucket.get(role, []), key=lambda d: str(d.get("display_name") or "").lower())

        if not nodes:
            continue

        cols_for_role = role_cols.get(role, 8)

        layer_label_y = y
        node_start_y = y + 44

        cols = min(cols_for_role, max(1, len(nodes)))
        rows = math.ceil(len(nodes) / cols_for_role)

        layer_width = x0 * 2 + cols * node_w + (cols - 1) * gap_x
        max_width = max(max_width, layer_width)

        for idx, d in enumerate(nodes):
            row = idx // cols_for_role
            col = idx % cols_for_role

            # Center short rows so Services/Core do not bunch on the far left.
            this_row_count = min(cols_for_role, len(nodes) - row * cols_for_role)
            row_width = this_row_count * node_w + max(0, this_row_count - 1) * gap_x
            full_width = cols_for_role * node_w + max(0, cols_for_role - 1) * gap_x
            row_offset = max(0, (full_width - row_width) / 2)

            x = x0 + row_offset + col * (node_w + gap_x)
            ny = node_start_y + row * (node_h + gap_y)
            pos[int(d["device_id"])] = (x, ny)

        layers.append({
            "role": role,
            "count": len(nodes),
            "label_y": layer_label_y,
            "line_y": layer_label_y + 22,
            "nodes": nodes,
        })

        y = node_start_y + rows * (node_h + gap_y) + layer_gap

    height = max(700, y + 80)
    return pos, max_width, height, layers


def edge_color(a_role, b_role):
    pair = {a_role, b_role}
    if "Core Infrastructure" in pair:
        return "#38bdf8"
    if "Services" in pair:
        return "#a78bfa"
    if "Distribution" in pair and "Access / Leaf" in pair:
        return "#22c55e"
    if "Distribution" in pair:
        return "#22c55e"
    if "AP" in pair:
        return "#eab308"
    return "#94a3b8"


def role_rank(role):
    return {
        "Core Infrastructure": 10,
        "Services": 20,
        "Distribution": 30,
        "Access / Leaf": 40,
        "AP": 50,
    }.get(role, 99)


def directed_edge_order(a, b, devices):
    ar = role_rank(final_vmap_role(devices[a]))
    br = role_rank(final_vmap_role(devices[b]))
    if ar <= br:
        return a, b
    return b, a




def is_stack_device(d):
    """
    Detect logical switch stacks / virtual chassis so the SVG can draw them
    as stacked cards.
    """
    text = " ".join(
        str(d.get(k) or "")
        for k in ("hostname", "sysName", "display_name", "hardware", "os", "type")
    ).lower()

    stack_terms = [
        "virtual chassis",
        "vsf stack",
        "stack",
        "chassis switch",
    ]

    return any(t in text for t in stack_terms)


def node_drilldown_url(d):
    """
    V-map node click target.

    Use MiddKiPS lookup because it works across device name/IP/MAC and avoids
    depending on a specific device-detail route.
    """
    q = (
        d.get("display_name")
        or d.get("hostname")
        or d.get("sysName")
        or d.get("ip")
        or d.get("device_id")
        or ""
    )
    return "/tools/lookup?q=" + quote(str(q))



def focus_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def find_focus_device_id(devices, focus):
    focus = str(focus or "").strip()
    if not focus:
        return None

    # Device ID focus is preferred.
    try:
        did = int(focus)
        if did in devices:
            return did
    except Exception:
        pass

    wanted = focus_key(focus)
    if not wanted:
        return None

    for did, d in devices.items():
        for k in ("device_id", "display_name", "hostname", "sysName", "ip"):
            if focus_key(d.get(k)) == wanted:
                return int(did)

    # Loose contains match, useful for hostname fragments.
    for did, d in devices.items():
        blob = " ".join(str(d.get(k) or "") for k in ("display_name", "hostname", "sysName", "ip")).lower()
        if focus.lower() in blob:
            return int(did)

    return None


def apply_focus_filter(devices, edges, focus_id):
    focus_id = int(focus_id)
    keep_ids = {focus_id}
    focus_edges = []

    for e in edges:
        a = int(e["a"])
        b = int(e["b"])

        if a == focus_id or b == focus_id:
            keep_ids.add(a)
            keep_ids.add(b)
            focus_edges.append(e)

    focused_devices = {
        did: d
        for did, d in devices.items()
        if int(did) in keep_ids
    }

    return focused_devices, focus_edges



def upstream_domain(d):
    """
    Best-effort geographic / redundancy domain from known distribution names.
    Used only for the focused access-switch audit.
    """
    name = str(d.get("display_name") or d.get("hostname") or d.get("sysName") or "").lower()

    if name.startswith("dist-"):
        parts = name.split("-")
        if len(parts) >= 3:
            # dist-eastpod-dfl -> dfl
            # dist-westpod-stewart -> stewart
            return parts[-1].upper()

    domain_terms = [
        ("dfl", "DFL"),
        ("voter", "Voter"),
        ("twilight", "Twilight"),
        ("stewart", "Stewart"),
        ("carr", "Carr"),
        ("mbh", "MBH"),
        ("emmaw", "Emma Willard"),
        ("mac", "MAC"),
        ("aggregation-a", "Aggregation A"),
        ("aggregation-b", "Aggregation B"),
        ("fabric-core-dfl", "Fabric Core DFL"),
        ("fabric-core-voter", "Fabric Core Voter"),
        ("vtr-core", "VTR Core"),
        ("dfl-core", "DFL Core"),
    ]

    for needle, label in domain_terms:
        if needle in name:
            return label

    return str(d.get("display_name") or d.get("hostname") or d.get("sysName") or "Unknown")


def is_access_device_for_focus(d):
    return final_vmap_role(d) == "Access / Leaf"


def is_upstream_for_access(d):
    return final_vmap_role(d) in ("Distribution", "Services", "Core Infrastructure")


def edge_other_id(e, focus_id):
    a = int(e["a"])
    b = int(e["b"])

    if a == int(focus_id):
        return b
    if b == int(focus_id):
        return a
    return None


def edge_ports_for_focus(e, focus_id):
    a = int(e["a"])
    b = int(e["b"])

    if a == int(focus_id):
        return str(e.get("a_port") or ""), str(e.get("b_port") or "")
    if b == int(focus_id):
        return str(e.get("b_port") or ""), str(e.get("a_port") or "")

    return "", ""


def build_access_focus_summary(all_devices, focus_id, focus_edges):
    focus = all_devices.get(int(focus_id))
    if not focus:
        return ""

    focus_name = h(focus.get("display_name") or focus.get("hostname") or focus_id)
    focus_role = final_vmap_role(focus)

    upstream_rows = []
    all_direct_rows = []

    for e in focus_edges:
        other_id = edge_other_id(e, focus_id)
        if other_id is None or other_id not in all_devices:
            continue

        other = all_devices[other_id]
        other_role = final_vmap_role(other)
        local_port, remote_port = edge_ports_for_focus(e, focus_id)

        row = {
            "neighbor": other,
            "neighbor_name": str(other.get("display_name") or other.get("hostname") or other_id),
            "neighbor_role": other_role,
            "domain": upstream_domain(other),
            "local_port": local_port,
            "remote_port": remote_port,
        }

        all_direct_rows.append(row)

        if is_upstream_for_access(other):
            upstream_rows.append(row)

    rows_for_status = upstream_rows if focus_role == "Access / Leaf" else all_direct_rows

    neighbor_names = sorted(set(r["neighbor_name"] for r in rows_for_status))
    domains = sorted(set(r["domain"] for r in rows_for_status))

    link_count = len(rows_for_status)
    neighbor_count = len(neighbor_names)
    domain_count = len(domains)

    if focus_role == "Access / Leaf":
        if neighbor_count >= 2 and domain_count >= 2:
            status = "PASS"
            status_class = "ok"
            status_text = "Redundant upstream distribution points detected."
        elif link_count >= 2 and neighbor_count >= 2:
            status = "WARN"
            status_class = "warn"
            status_text = "Multiple upstream devices found, but geographic domains look the same or unclear."
        elif link_count >= 1:
            status = "FAIL"
            status_class = "bad"
            status_text = "Only one upstream distribution path detected."
        else:
            status = "FAIL"
            status_class = "bad"
            status_text = "No upstream distribution links detected."
    else:
        status = "INFO"
        status_class = "info"
        status_text = "Focused device is not Access / Leaf, showing direct LLDP neighbors."

    grouped = {}
    for r in rows_for_status:
        key = (r["neighbor_name"], r["neighbor_role"], r["domain"])
        grouped.setdefault(key, []).append(r)

    table_rows = []
    for (neighbor_name, neighbor_role, domain), links in sorted(grouped.items(), key=lambda x: (x[0][2], x[0][0])):
        port_pairs = ", ".join(
            h(f'{r["local_port"]} -> {r["remote_port"]}')
            for r in links
        )

        table_rows.append(
            "<tr>"
            f"<td>{h(neighbor_name)}</td>"
            f"<td>{h(neighbor_role)}</td>"
            f"<td>{h(domain)}</td>"
            f"<td>{len(links)}</td>"
            f"<td>{port_pairs}</td>"
            "</tr>"
        )

    if not table_rows:
        table_rows.append(
            '<tr><td colspan="5">No direct upstream links found for this focused device.</td></tr>'
        )

    return (
        '<div class="focus-audit">'
        f'<div class="focus-status {status_class}">{h(status)}</div>'
        '<div class="focus-audit-body">'
        f'<div class="focus-title">Access uplink audit for <b>{focus_name}</b></div>'
        f'<div class="focus-subtitle">{h(status_text)}</div>'
        '<div class="focus-metrics">'
        f'<span>Upstream links: <b>{link_count}</b></span>'
        f'<span>Upstream devices: <b>{neighbor_count}</b></span>'
        f'<span>Geo domains: <b>{domain_count}</b></span>'
        f'<span>Domains: <b>{h(", ".join(domains) if domains else "None")}</b></span>'
        '</div>'
        '<table class="focus-table">'
        '<thead><tr><th>Neighbor</th><th>Role</th><th>Geo domain</th><th>Links</th><th>Ports</th></tr></thead>'
        '<tbody>'
        + "".join(table_rows) +
        '</tbody></table>'
        '</div></div>'
    )


def apply_access_uplink_focus_filter(all_devices, all_edges, focus_id):
    """
    Focus behavior:
    - Access switch: show only direct upstream links to Distribution/Services/Core.
    - Other devices: show direct LLDP neighbors.
    """
    focus_id = int(focus_id)
    focus = all_devices.get(focus_id)

    direct_edges = [
        e for e in all_edges
        if int(e["a"]) == focus_id or int(e["b"]) == focus_id
    ]

    if focus and is_access_device_for_focus(focus):
        upstream_edges = []
        for e in direct_edges:
            other_id = edge_other_id(e, focus_id)
            if other_id is None or other_id not in all_devices:
                continue

            if is_upstream_for_access(all_devices[other_id]):
                upstream_edges.append(e)

        focus_edges = upstream_edges if upstream_edges else direct_edges
    else:
        focus_edges = direct_edges

    keep_ids = {focus_id}
    for e in focus_edges:
        keep_ids.add(int(e["a"]))
        keep_ids.add(int(e["b"]))

    focused_devices = {
        did: d
        for did, d in all_devices.items()
        if int(did) in keep_ids
    }

    summary = build_access_focus_summary(all_devices, focus_id, direct_edges)
    return focused_devices, focus_edges, summary


def node_vmap_focus_url(d, node_url_params=None):
    if not node_url_params:
        return node_drilldown_url(d)

    params = dict(node_url_params)
    params["focus"] = str(d.get("device_id") or "")
    return "/tools/topology-vmap?" + urlencode(params)



def vmap_drag_script():
    return r"""
<style>
  .vmap-drag-controls {
    display:flex;
    gap:8px;
    align-items:center;
    flex-wrap:wrap;
    margin: 8px 0 10px 0;
    padding: 8px;
    border: 1px solid #334155;
    border-radius: 10px;
    background: #020617;
  }

  .vmap-drag-controls button {
    border: 1px solid #334155;
    border-radius: 8px;
    background: #0f172a;
    color: #e5e7eb;
    padding: 7px 10px;
    font-weight: 800;
    cursor: pointer;
  }

  .vmap-drag-controls button:hover {
    border-color: #38bdf8;
  }

  .vmap-drag-controls.active button.move-toggle {
    background: #1d4ed8;
    border-color: #60a5fa;
  }

  .vmap-drag-help {
    color:#cbd5e1;
    font-size:12px;
  }

  svg.vmap .draggable-node {
    cursor: pointer;
  }

  svg.vmap.vmap-drag-enabled .draggable-node {
    cursor: grab;
  }

  svg.vmap.vmap-drag-enabled .draggable-node.dragging {
    cursor: grabbing;
  }

      .layer-switches {
        display:flex;
        align-items:center;
        gap:8px;
        flex-wrap:wrap;
        margin: 0 0 14px 0;
        padding: 9px;
        border:1px solid #334155;
        border-radius:12px;
        background:#0f172a;
      }

      .layer-switch-title {
        color:#93c5fd;
        font-weight:900;
        margin-right:6px;
      }

      .layer-switch {
        display:inline-flex;
        align-items:center;
        gap:7px;
        padding:7px 9px;
        border:1px solid #334155;
        border-radius:999px;
        background:#0f172a;
        color:#e5e7eb;
        text-decoration:none;
        font-size:12px;
        font-weight:800;
      }

      .layer-switch:hover {
        border-color:#38bdf8;
        background:#111c33;
      }

      .layer-switch .switch-pill {
        width:30px;
        height:16px;
        border-radius:999px;
        background:#475569;
        position:relative;
        flex:0 0 auto;
      }

      .layer-switch .switch-dot {
        width:12px;
        height:12px;
        border-radius:999px;
        background:#e5e7eb;
        position:absolute;
        top:2px;
        left:2px;
        transition:left 120ms ease;
      }

      .layer-switch.on {
        border-color:#38bdf8;
      }

      .layer-switch.on .switch-pill {
        background:#2563eb;
      }

      .layer-switch.on .switch-dot {
        left:16px;
        background:#f8fafc;
      }

      .layer-switch.off {
        opacity:.7;
      }

      .switch-state {
        color:#94a3b8;
        font-size:11px;
      }

    
      .layer-reset-view {
        display:inline-flex;
        align-items:center;
        padding:7px 10px;
        border:1px solid #475569;
        border-radius:999px;
        background:#0f172a;
        color:#f8fafc;
        text-decoration:none;
        font-size:12px;
        font-weight:900;
      }

      .layer-reset-view:hover {
        border-color:#f97316;
        background:#1f2937;
      }

    
  .mist-color-legend {
    display:flex;
    flex-wrap:wrap;
    gap:10px;
    align-items:center;
    margin:0 0 12px 0;
    padding:8px 10px;
    border:1px solid #334155;
    border-radius:10px;
    background:#0f172a;
    color:#cbd5e1;
    font-size:12px;
  }

  .mist-color-legend b {
    color:#f8fafc;
  }

  .mist-color-legend span {
    display:inline-flex;
    align-items:center;
    gap:6px;
  }

  .mist-color-chip {
    width:24px;
    height:4px;
    border-radius:999px;
    display:inline-block;
  }


  .nonmist-toggle-bar {
    display:flex;
    gap:10px;
    align-items:center;
    flex-wrap:wrap;
    border:1px solid #334155;
    border-radius:10px;
    background:#0f172a;
    color:#cbd5e1;
    padding:8px 10px;
    margin:0 0 12px 0;
    font-size:12px;
  }

  .nonmist-toggle-bar b {
    color:#f8fafc;
  }

  .nonmist-toggle-bar a {
    color:#f8fafc;
    background:#1e3a8a;
    border:1px solid #60a5fa;
    border-radius:999px;
    padding:6px 10px;
    text-decoration:none;
    font-weight:900;
  }

  .nonmist-toggle-bar a:hover {
    background:#1d4ed8;
  }


  .mist-highlight-bar {
    border:1px solid #334155;
    border-radius:12px;
    padding:8px 10px;
    margin:0 0 10px 0;
    background:#020617;
    color:#cbd5e1;
    display:flex;
    gap:10px;
    align-items:center;
    flex-wrap:wrap;
  }

  .mist-highlight-bar b {
    color:#f8fafc;
  }

  .highlight-pill {
    display:inline-flex;
    align-items:center;
    gap:6px;
    padding:5px 10px;
    border-radius:999px;
    font-weight:900;
    font-size:12px;
    text-decoration:none;
    border:1px solid #334155;
    background:#0f172a;
    color:#cbd5e1;
  }

  .highlight-pill.edge.on {
    color:#fff7fb;
    border-color:#ff2bd6;
    box-shadow:0 0 12px rgba(255,43,214,.7);
  }

  .highlight-pill.mist.on {
    color:#f6fff4;
    border-color:#39ff14;
    box-shadow:0 0 12px rgba(57,255,20,.7);
  }

  svg.vmap-mist-pods .node.neon-edge rect {
    stroke:#ff2bd6 !important;
    stroke-width:4 !important;
    filter:drop-shadow(0 0 10px #ff2bd6) drop-shadow(0 0 18px rgba(255,43,214,.65));
  }

  svg.vmap-mist-pods .node.neon-edge text {
    fill:#fff7fb !important;
  }

  svg.vmap-mist-pods .node.neon-mist rect {
    stroke:#39ff14 !important;
    stroke-width:4 !important;
    filter:drop-shadow(0 0 10px #39ff14) drop-shadow(0 0 18px rgba(57,255,20,.65));
  }

  svg.vmap-mist-pods .node.neon-mist text {
    fill:#f6fff4 !important;
  }

  svg.vmap-mist-pods .old-wan-link.neon-edge-link {
    stroke:#ff2bd6 !important;
    stroke-width:5 !important;
    opacity:1 !important;
    filter:drop-shadow(0 0 8px #ff2bd6) drop-shadow(0 0 18px rgba(255,43,214,.75));
  }

  svg.vmap-mist-pods .edge.neon-mist-edge path,
  svg.vmap-mist-pods .edge.neon-mist-edge line {
    stroke:#39ff14 !important;
    stroke-width:4.5 !important;
    opacity:1 !important;
    filter:drop-shadow(0 0 8px #39ff14) drop-shadow(0 0 18px rgba(57,255,20,.75));
  }

  svg.vmap-mist-pods .edge.neon-edge-edge path,
  svg.vmap-mist-pods .edge.neon-edge-edge line {
    stroke:#ff2bd6 !important;
    stroke-width:4.5 !important;
    opacity:1 !important;
    filter:drop-shadow(0 0 8px #ff2bd6) drop-shadow(0 0 18px rgba(255,43,214,.75));
  }

</style>

<script>
(function () {
  if (window.__middkipsVmapDragInstalled) {
    return;
  }
  window.__middkipsVmapDragInstalled = true;

  function storageKey() {
    const url = new URL(window.location.href);
    url.searchParams.delete("t");
    url.searchParams.delete("selected");
    url.searchParams.delete("focus");
    url.searchParams.delete("selected");
    url.searchParams.delete("focus");
    url.searchParams.delete("selected");
    url.searchParams.delete("focus");
    url.searchParams.delete("selected");
    url.searchParams.delete("focus");
    return "middkips:vmap:node-positions:" + url.pathname + "?" + url.searchParams.toString();
  }

  function boot() {
    document.querySelectorAll("svg.vmap:not([data-drag-init='1'])").forEach(initSvg);
  }

  function initSvg(svg) {
    svg.dataset.dragInit = "1";

    const key = storageKey();
    const nodes = {};
    const edgeGroups = Array.from(svg.querySelectorAll("g.edge[data-a][data-b]"));

    svg.querySelectorAll("g.node[data-node-id]").forEach(function (node) {
      const id = String(node.dataset.nodeId || "");
      if (!id) return;

      nodes[id] = node;
      node.dataset.dx = node.dataset.dx || "0";
      node.dataset.dy = node.dataset.dy || "0";
    });

    function svgPoint(evt) {
      const pt = svg.createSVGPoint();
      pt.x = evt.clientX;
      pt.y = evt.clientY;

      const ctm = svg.getScreenCTM();
      if (!ctm) {
        return { x: evt.clientX, y: evt.clientY };
      }

      const p = pt.matrixTransform(ctm.inverse());
      return { x: p.x, y: p.y };
    }

    function setNodeTransform(node, dx, dy) {
      node.dataset.dx = String(dx);
      node.dataset.dy = String(dy);
      node.setAttribute("transform", "translate(" + dx + " " + dy + ")");
    }

    function nodeCenter(node) {
      const box = node.getBBox();
      const dx = parseFloat(node.dataset.dx || "0") || 0;
      const dy = parseFloat(node.dataset.dy || "0") || 0;

      return {
        x: box.x + box.width / 2 + dx,
        y: box.y + box.height / 2 + dy
      };
    }

    function updateEdges() {
      edgeGroups.forEach(function (edge) {
        const a = nodes[String(edge.dataset.a || "")];
        const b = nodes[String(edge.dataset.b || "")];
        const path = edge.querySelector("path.edge-path");

        if (!a || !b || !path) return;

        const ca = nodeCenter(a);
        const cb = nodeCenter(b);

        let d;
        if (Math.abs(ca.y - cb.y) < 80) {
          const lift = 70 + Math.min(160, Math.abs(ca.x - cb.x) / 10);
          const arcY = Math.min(ca.y, cb.y) - lift;
          d = "M" + ca.x.toFixed(1) + "," + ca.y.toFixed(1) +
              " C" + ca.x.toFixed(1) + "," + arcY.toFixed(1) +
              " " + cb.x.toFixed(1) + "," + arcY.toFixed(1) +
              " " + cb.x.toFixed(1) + "," + cb.y.toFixed(1);
        } else {
          const midY = ((ca.y + cb.y) / 2).toFixed(1);
          d = "M" + ca.x.toFixed(1) + "," + ca.y.toFixed(1) +
              " C" + ca.x.toFixed(1) + "," + midY +
              " " + cb.x.toFixed(1) + "," + midY +
              " " + cb.x.toFixed(1) + "," + cb.y.toFixed(1);
        }

        path.setAttribute("d", d);
      });
    }

    function savePositions() {
      const saved = {};

      Object.keys(nodes).forEach(function (id) {
        const node = nodes[id];
        const dx = parseFloat(node.dataset.dx || "0") || 0;
        const dy = parseFloat(node.dataset.dy || "0") || 0;

        if (Math.abs(dx) > 0.1 || Math.abs(dy) > 0.1) {
          saved[id] = { dx: dx, dy: dy };
        }
      });

      localStorage.setItem(key, JSON.stringify(saved));
    }

    function loadPositions() {
      let saved = {};
      try {
        saved = JSON.parse(localStorage.getItem(key) || "{}");
      } catch (e) {
        saved = {};
      }

      Object.keys(saved).forEach(function (id) {
        if (!nodes[id]) return;

        const pos = saved[id] || {};
        setNodeTransform(
          nodes[id],
          parseFloat(pos.dx || "0") || 0,
          parseFloat(pos.dy || "0") || 0
        );
      });

      updateEdges();
    }

    const controls = document.createElement("div");
    controls.className = "vmap-drag-controls";
    controls.innerHTML =
      '<button type="button" class="move-toggle">Move Nodes: Off</button>' +
      '<button type="button" class="save-layout">Save Layout</button>' +
      '<button type="button" class="reset-layout">Reset Layout</button>' +
      '<span class="vmap-drag-help">Turn on Move Nodes, drag switches/firewalls, then click Save Layout. Reset Layout clears only your browser-saved positions.</span>';

    svg.parentNode.insertBefore(controls, svg);

    const moveButton = controls.querySelector(".move-toggle");
    const saveButton = controls.querySelector(".save-layout");
    const resetButton = controls.querySelector(".reset-layout");

    let enabled = false;
    let active = null;

    moveButton.addEventListener("click", function () {
      enabled = !enabled;
      controls.classList.toggle("active", enabled);
      svg.classList.toggle("vmap-drag-enabled", enabled);
      moveButton.textContent = enabled ? "Move Nodes: On" : "Move Nodes: Off";
    });

    saveButton.addEventListener("click", function () {
      savePositions();
      saveButton.textContent = "Saved";
      setTimeout(function () {
        saveButton.textContent = "Save Layout";
      }, 1200);
    });

    resetButton.addEventListener("click", function () {
      localStorage.removeItem(key);

      Object.keys(nodes).forEach(function (id) {
        setNodeTransform(nodes[id], 0, 0);
      });

      updateEdges();

      resetButton.textContent = "Reset Done";
      setTimeout(function () {
        resetButton.textContent = "Reset Layout";
      }, 1200);
    });

    Object.keys(nodes).forEach(function (id) {
      const node = nodes[id];

      node.addEventListener("pointerdown", function (evt) {
        if (!enabled) return;

        evt.preventDefault();
        evt.stopPropagation();

        const p = svgPoint(evt);

        active = {
          node: node,
          startX: p.x,
          startY: p.y,
          baseDx: parseFloat(node.dataset.dx || "0") || 0,
          baseDy: parseFloat(node.dataset.dy || "0") || 0,
          moved: false
        };

        node.classList.add("dragging");

        try {
          node.setPointerCapture(evt.pointerId);
        } catch (e) {}
      });

      node.addEventListener("pointermove", function (evt) {
        if (!enabled || !active || active.node !== node) return;

        evt.preventDefault();
        evt.stopPropagation();

        const p = svgPoint(evt);
        const dx = active.baseDx + (p.x - active.startX);
        const dy = active.baseDy + (p.y - active.startY);

        if (Math.abs(p.x - active.startX) > 2 || Math.abs(p.y - active.startY) > 2) {
          active.moved = true;
        }

        setNodeTransform(node, dx, dy);
        updateEdges();
      });

      node.addEventListener("pointerup", function (evt) {
        if (!active || active.node !== node) return;

        if (active.moved) {
          node.dataset.justDragged = "1";
        }

        node.classList.remove("dragging");
        active = null;
        savePositions();

        try {
          node.releasePointerCapture(evt.pointerId);
        } catch (e) {}
      });

      node.addEventListener("click", function (evt) {
        if (node.dataset.justDragged === "1") {
          evt.preventDefault();
          evt.stopPropagation();
          node.dataset.justDragged = "0";
        }
      }, true);
    });

    loadPositions();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
</script>
"""

def render_svg(devices, edges, show_ports=True, flow_mode=False, redundant_only=False, tv_mode=False, node_url_params=None):
    for d in devices.values():
        role = final_vmap_role(d)
        d["role"] = role
        d["vmap_role"] = role

    pos, width, height, layers = positioned_nodes(devices, tv_mode=tv_mode)

    parts = []
    parts.append(f'<svg class="vmap" viewBox="0 0 {int(width)} {int(height)}" preserveAspectRatio="xMidYMin meet" role="img">')
    parts.append(
        '<defs>'
        '<marker id="flowArrow" markerWidth="10" markerHeight="10" refX="9" refY="3" '
        'orient="auto" markerUnits="strokeWidth">'
        '<path d="M0,0 L0,6 L9,3 z" fill="#e2e8f0" opacity="0.9"/>'
        '</marker>'
        '</defs>'
    )

    for layer in layers:
        role = h(layer["role"])
        count = layer["count"]
        y = layer["label_y"]
        line_y = layer["line_y"]
        parts.append(
            f'<text x="34" y="{y}" fill="#e2e8f0" font-size="18" font-weight="900">'
            f'{role} ({count})</text>'
        )
        parts.append(
            f'<line x1="34" y1="{line_y}" x2="{width - 34}" y2="{line_y}" '
            f'stroke="#334155" stroke-width="1"/>'
        )

    pair_counts = defaultdict(int)
    for e in edges:
        a = int(e["a"])
        b = int(e["b"])

        if a not in pos or b not in pos:
            continue

        a_role = final_vmap_role(devices[a])
        b_role = final_vmap_role(devices[b])

        # Hide same-layer links except core infrastructure backbone links.
        if a_role == b_role and a_role != "Core Infrastructure":
            continue

        pair_counts[tuple(sorted([a, b]))] += 1

    pair_index = defaultdict(int)
    same_layer_seen = defaultdict(int)
    src_fan_seen = defaultdict(int)
    dst_fan_seen = defaultdict(int)

    for e in edges:
        a = int(e["a"])
        b = int(e["b"])

        if a not in pos or b not in pos:
            continue

        a_role = final_vmap_role(devices[a])
        b_role = final_vmap_role(devices[b])

        if a_role == b_role and a_role != "Core Infrastructure":
            continue

        draw_a, draw_b = directed_edge_order(a, b, devices) if flow_mode else (a, b)

        ax, ay = pos[draw_a]
        bx, by = pos[draw_b]

        key = tuple(sorted([a, b]))

        # Operational redundant view:
        # Do NOT hide single-member pairs. If one member of a redundant pair is down,
        # LLDP may only show the remaining member. Hiding singles would hide the problem.
        # Instead, dim singles and make redundant pairs more obvious.
        is_redundant_pair = pair_counts.get(key, 0) >= 2

        idx = pair_index[key]
        pair_index[key] += 1
        total = max(1, pair_counts[key])
        offset = (idx - (total - 1) / 2) * 14

        node_w = 230
        node_h = 58
        same_layer = a_role == b_role

        if same_layer and a_role == "Core Infrastructure":
            # Backbone/core links: draw as high horizontal arcs with separate lanes.
            if ax <= bx:
                x1 = ax + node_w
                x2 = bx
            else:
                x1 = ax
                x2 = bx + node_w

            y1 = ay + node_h / 2
            y2 = by + node_h / 2

            pair_key = tuple(sorted((draw_a, draw_b)))
            band = same_layer_seen[pair_key]
            same_layer_seen[pair_key] += 1

            span = abs(x2 - x1)
            arc_lift = 42 + (band % 10) * 18 + min(90, span / 12)
            arc_y = max(28, min(y1, y2) - arc_lift)

            path_d = f"M{x1:.1f},{y1:.1f} C{x1:.1f},{arc_y:.1f} {x2:.1f},{arc_y:.1f} {x2:.1f},{y2:.1f}"
            label_x = (x1 + x2) / 2
            label_y = arc_y - 5
        else:
            # Cross-layer links: fan out source/destination anchors to reduce overlap.
            src_idx = src_fan_seen[draw_a]
            src_fan_seen[draw_a] += 1
            dst_idx = dst_fan_seen[draw_b]
            dst_fan_seen[draw_b] += 1

            src_fan = ((src_idx % 9) - 4) * 9
            dst_fan = ((dst_idx % 9) - 4) * 9

            x1 = ax + node_w / 2 + src_fan + offset
            y1 = ay + node_h
            x2 = bx + node_w / 2 + dst_fan - offset
            y2 = by

            mid_y = (y1 + y2) / 2
            path_d = f"M{x1:.1f},{y1:.1f} C{x1:.1f},{mid_y:.1f} {x2:.1f},{mid_y:.1f} {x2:.1f},{y2:.1f}"
            label_x = (x1 + x2) / 2
            label_y = mid_y - 5

        color = edge_color(a_role, b_role)
        marker_end = 'marker-end="url(#flowArrow)"' if flow_mode else ""

        stroke_width = 2.6 if is_redundant_pair else 1.2
        opacity = 0.86 if is_redundant_pair else 0.34

        if not redundant_only:
            stroke_width = 1.8
            opacity = 0.72

        raw_label = f'{e.get("a_port") or ""} ↔ {e.get("b_port") or ""}'
        flow_label = f'{devices[draw_a]["display_name"]} → {devices[draw_b]["display_name"]}'
        title = f'{flow_label} | {raw_label}' if flow_mode else f'{devices[a]["display_name"]} {raw_label} {devices[b]["display_name"]}'

        parts.append(f'<g class="edge draggable-edge" data-a="{locals().get("draw_a", e.get("a"))}" data-b="{locals().get("draw_b", e.get("b"))}">')
        parts.append(f'<title>{h(title)}</title>')
        parts.append(
            f'<path class="edge-path" d="{path_d}" '
            f'stroke="{color}" stroke-width="{stroke_width}" fill="none" opacity="{opacity}" {marker_end}/>'
        )

        # In big maps, port labels create most of the clutter. Keep labels for
        # smaller views and core backbone links; always keep full details in hover title.
        show_text_label = show_ports and (len(edges) <= 40 or same_layer)
        if show_text_label:
            parts.append(
                f'<text x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="middle" '
                f'fill="#cbd5e1" font-size="9">{h(raw_label[:42])}</text>'
            )

        parts.append('</g>')

    node_color = {
        "Core Infrastructure": "#38bdf8",
        "Services": "#a78bfa",
        "Distribution": "#22c55e",
        "Access / Leaf": "#fb923c",
        "AP": "#eab308",
    }

    for d in sorted(devices.values(), key=lambda x: (role_rank(final_vmap_role(x)), str(x.get("display_name") or "").lower())):
        did = int(d["device_id"])
        if did not in pos:
            continue

        x, y = pos[did]
        role = final_vmap_role(d)
        stroke = node_color.get(role, "#64748b")

        name = h(d.get("display_name") or "")
        subtitle = h(str(d.get("hardware") or d.get("os") or d.get("hostname") or "")[:42])
        title = h(f'{d.get("display_name") or ""} | {d.get("hostname") or ""} | {d.get("hardware") or ""}')

        url = h(node_vmap_focus_url(d, node_url_params))

        stacked = is_stack_device(d)

        parts.append(f'<g class="node draggable-node" data-node-id="{d.get("device_id") or ""}">')
        parts.append(f'<a href="{url}" target="_self">')
        parts.append(f'<title>{title}</title>')

        if stacked:
            # Draw offset backing cards so Virtual Chassis / VSF stacks read as stacks.
            parts.append(
                f'<rect class="stack-card-back" x="{x + 10}" y="{y + 10}" width="230" height="58" rx="10" '
                f'fill="#0f172a" stroke="{stroke}" stroke-width="1.4"/>'
            )
            parts.append(
                f'<rect class="stack-card-back" x="{x + 5}" y="{y + 5}" width="230" height="58" rx="10" '
                f'fill="#0f172a" stroke="{stroke}" stroke-width="1.6"/>'
            )

        parts.append(
            f'<rect x="{x}" y="{y}" width="230" height="58" rx="10" '
            f'fill="#0f172a" stroke="{stroke}" stroke-width="2"/>'
        )

        stack_badge = " VC/Stack" if stacked else ""
        parts.append(
            f'<text x="{x + 12}" y="{y + 22}" fill="#f8fafc" font-size="13" font-weight="800">{name[:28]}{stack_badge}</text>'
        )
        parts.append(
            f'<text x="{x + 12}" y="{y + 43}" fill="#cbd5e1" font-size="10">{subtitle}</text>'
        )
        parts.append('</a>')
        parts.append('</g>')

    parts.append('</svg>')
    parts.append(vmap_drag_script())
    return "".join(parts)



def render_focus_popout_page(all_devices, focus_id, focus_device, focus_edges, site, show_ports_bool, flow_bool, redundant_only_bool, tv_bool):
    """
    Clean focused topology view.
    This intentionally does not use the giant full-map grid.
    It answers: does this access switch have redundant/geographic upstream paths?
    """
    focus_id = int(focus_id)
    focus_name = str(focus_device.get("display_name") or focus_device.get("hostname") or focus_id)

    upstream_edges = []
    upstream_ids = set()

    for e in focus_edges:
        other_id = edge_other_id(e, focus_id) if "edge_other_id" in globals() else None
        if other_id is None or other_id not in all_devices:
            continue

        other = all_devices[other_id]
        if final_vmap_role(other) in ("Distribution", "Services", "Core Infrastructure"):
            upstream_edges.append(e)
            upstream_ids.add(other_id)

    # If the focused node is not access, fall back to direct links.
    if not upstream_edges:
        upstream_edges = focus_edges
        for e in upstream_edges:
            other_id = edge_other_id(e, focus_id) if "edge_other_id" in globals() else None
            if other_id is not None:
                upstream_ids.add(other_id)

    focused_devices = {focus_id: focus_device}
    for did in upstream_ids:
        if did in all_devices:
            focused_devices[did] = all_devices[did]

    domains = []
    neighbor_names = []
    rows = []

    for e in upstream_edges:
        other_id = edge_other_id(e, focus_id)
        if other_id is None or other_id not in all_devices:
            continue

        other = all_devices[other_id]
        domain = upstream_domain(other) if "upstream_domain" in globals() else final_vmap_role(other)
        local_port, remote_port = edge_ports_for_focus(e, focus_id) if "edge_ports_for_focus" in globals() else (str(e.get("a_port") or ""), str(e.get("b_port") or ""))

        domains.append(domain)
        neighbor_names.append(str(other.get("display_name") or other.get("hostname") or other_id))

        rows.append(
            "<tr>"
            f"<td>{h(other.get('display_name') or other.get('hostname') or other_id)}</td>"
            f"<td>{h(final_vmap_role(other))}</td>"
            f"<td>{h(domain)}</td>"
            f"<td>{h(local_port)}</td>"
            f"<td>{h(remote_port)}</td>"
            "</tr>"
        )

    unique_neighbors = sorted(set(neighbor_names))
    unique_domains = sorted(set(domains))

    if len(unique_neighbors) >= 2 and len(unique_domains) >= 2:
        status = "PASS"
        status_class = "ok"
        status_text = "Redundant upstream paths to separate distribution domains detected."
    elif len(unique_neighbors) >= 2:
        status = "WARN"
        status_class = "warn"
        status_text = "Multiple upstream devices detected, but geographic separation is unclear."
    elif len(unique_neighbors) == 1:
        status = "FAIL"
        status_class = "bad"
        status_text = "Only one upstream path detected."
    else:
        status = "FAIL"
        status_class = "bad"
        status_text = "No upstream path detected."

    if not rows:
        rows.append('<tr><td colspan="5">No direct upstream links found.</td></tr>')

    clear_params = {
        "site": site,
        "layout": layout,
        "show_ports": "1" if show_ports_bool else "0",
        "show_core": "1",
        "show_services": "1",
        "show_distribution": "1",
        "show_access": "1",
        "show_aps": "0",
        "flow": "1" if flow_bool else "0",
        "redundant_only": "1" if redundant_only_bool else "0",
        "tv": "1" if tv_bool else "0",
    }

    clear_url = "/tools/topology-vmap?" + urlencode(clear_params)
    lookup_url = "/tools/lookup?q=" + quote(focus_name)

    css = """
    <style>
      .focus-popout {
        border:1px solid #334155;
        border-radius:14px;
        background:#0f172a;
        padding:16px;
        margin:12px 0 16px 0;
      }

      .focus-head {
        display:flex;
        align-items:center;
        gap:14px;
        flex-wrap:wrap;
        margin-bottom:12px;
      }

      .focus-status {
        min-width:82px;
        text-align:center;
        border-radius:10px;
        padding:10px 14px;
        font-weight:900;
        color:#020617;
      }

      .focus-status.ok { background:#22c55e; }
      .focus-status.warn { background:#fb923c; }
      .focus-status.bad { background:#ef4444; color:#fff; }

      .focus-title {
        font-size:18px;
        font-weight:900;
        color:#f8fafc;
      }

      .focus-subtitle {
        color:#cbd5e1;
      }

      .focus-metrics {
        display:flex;
        gap:10px;
        flex-wrap:wrap;
        margin:10px 0 14px 0;
      }

      .focus-metrics span {
        background:#0f172a;
        border:1px solid #334155;
        border-radius:8px;
        padding:7px 10px;
        color:#e5e7eb;
      }

      .focus-table {
        width:100%;
        border-collapse:collapse;
        margin-top:10px;
      }

      .focus-table th,
      .focus-table td {
        border-bottom:1px solid #334155;
        padding:7px 9px;
        text-align:left;
        color:#e5e7eb;
      }

      .focus-table th {
        color:#93c5fd;
        font-weight:900;
      }

      .focus-mini {
        margin-top:14px;
      }
    
      .preset-dropdown {
        position: relative;
        display: inline-block;
        margin: 0 0 14px 0;
        z-index: 20;
      }

      .preset-dropdown summary {
        list-style: none;
        cursor: pointer;
        display: inline-block;
        padding: 9px 14px;
        border: 1px solid #334155;
        border-radius: 10px;
        background: #0f172a;
        color: #e2e8f0;
        font-weight: 900;
      }

      .preset-dropdown summary::-webkit-details-marker {
        display: none;
      }

      .preset-dropdown summary::after {
        content: " ▾";
        color: #93c5fd;
      }

      .preset-menu {
        position: absolute;
        top: 44px;
        left: 0;
        width: 430px;
        max-height: 70vh;
        overflow: auto;
        border: 1px solid #334155;
        border-radius: 12px;
        background: #020617;
        box-shadow: 0 18px 40px rgba(0,0,0,.45);
        padding: 8px;
      }

      .preset-option {
        display: block;
        text-decoration: none;
        border-radius: 10px;
        padding: 10px 11px;
        color: #e5e7eb;
        border: 1px solid transparent;
      }

      .preset-option:hover {
        background: #0f172a;
        border-color: #38bdf8;
      }

      .preset-label {
        display: block;
        font-weight: 900;
        color: #f8fafc;
        margin-bottom: 3px;
      }

      .preset-desc {
        display: block;
        font-size: 12px;
        line-height: 1.3;
        color: #cbd5e1;
      }

    </style>
    """

    body = css
    body += '<div class="focus-popout">'
    body += '<div class="focus-head">'
    body += f'<div class="focus-status {status_class}">{h(status)}</div>'
    body += '<div>'
    body += f'<div class="focus-title">{h(focus_name)}</div>'
    body += f'<div class="focus-subtitle">{h(status_text)}</div>'
    body += '</div>'
    body += f'<a class="button" href="{h(clear_url)}">Clear Focus</a>'
    body += f'<a class="button" href="{h(lookup_url)}">Open Lookup</a>'
    body += '</div>'

    body += '<div class="focus-metrics">'
    body += f'<span>Upstream links: <b>{len(upstream_edges)}</b></span>'
    body += f'<span>Upstream devices: <b>{len(unique_neighbors)}</b></span>'
    body += f'<span>Geo domains: <b>{len(unique_domains)}</b></span>'
    body += f'<span>Domains: <b>{h(", ".join(unique_domains) if unique_domains else "None")}</b></span>'
    body += '</div>'

    body += '<table class="focus-table">'
    body += '<thead><tr><th>Neighbor</th><th>Role</th><th>Geo domain</th><th>Local port</th><th>Remote port</th></tr></thead>'
    body += '<tbody>' + "".join(rows) + '</tbody></table>'

    body += '<div class="focus-mini">'
    body += render_svg(
        focused_devices,
        upstream_edges,
        show_ports=True,
        flow_mode=flow_bool,
        redundant_only=False,
        tv_mode=False,
        node_url_params={
            "site": site,
        "layout": layout,
            "show_ports": "1",
            "show_core": "1",
            "show_services": "1",
            "show_distribution": "1",
            "show_access": "1",
            "show_aps": "0",
            "flow": "1" if flow_bool else "0",
            "redundant_only": "0",
            "tv": "0",
        },
    )
    body += '</div>'
    body += '</div>'

    return body



def vmap_options_dropdown():
    """
    Dropdown of common topology map views.
    Each row has a visible description and a hover title.
    """
    presets = [
        {
            "label": "Mist POD View",
            "url": "/tools/topology-vmap?site=vt&layout=mist_pods&show_core=1&show_services=0&show_distribution=1&show_access=1&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0",
            "desc": "Mist-only view grouped by POD. Shows fabric core, distribution, access, and optional APs.",
        },
        {
            "label": "Mist -> Services -> Non-Mist",
            "url": "/tools/topology-vmap?site=vt&layout=transition&show_core=1&show_services=1&show_distribution=1&show_access=0&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0",
            "desc": "Left-to-right transition map with Mist on the left, service switches in the middle, and non-Mist infrastructure on the right.",
        },
        {
            "label": "Old Network vs Mist Network",
            "url": "/tools/topology-vmap?site=vt&layout=old_mist&show_core=1&show_services=1&show_distribution=1&show_access=0&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0",
            "desc": "Horizontal transition view: old/legacy network on the left, svcs switches in the middle, Mist fabric/distribution on the right.",
        },
        {
            "label": "Wallboard: Core / Services / Distribution",
            "url": "/tools/topology-vmap?site=vt&show_core=1&show_services=1&show_distribution=1&show_access=0&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0",
            "desc": "Clean top-down infrastructure view. Best default for TV and status review. Hides access switch noise.",
        },
        {
            "label": "Full Switch Map",
            "url": "/tools/topology-vmap?site=vt&show_core=1&show_services=1&show_distribution=1&show_access=1&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0",
            "desc": "Shows core, services, distribution, and access switches. Good for broad campus review, noisy on small screens.",
        },
        {
            "label": "Full Switch Map + Ports",
            "url": "/tools/topology-vmap?site=vt&show_core=1&show_services=1&show_distribution=1&show_access=1&show_aps=0&show_ports=1&flow=1&redundant_only=1&tv=0",
            "desc": "Same as full switch map, but shows port labels. Useful for troubleshooting, messy for wallboards.",
        },
        {
            "label": "Access Layer Only",
            "url": "/tools/topology-vmap?site=vt&show_core=0&show_services=0&show_distribution=0&show_access=1&show_aps=0&show_ports=0&flow=0&redundant_only=0&tv=0",
            "desc": "Shows only access switches. Useful for inventory sanity checks, not useful for uplink validation by itself.",
        },
        {
            "label": "Distribution Only",
            "url": "/tools/topology-vmap?site=vt&show_core=0&show_services=0&show_distribution=1&show_access=0&show_aps=0&show_ports=0&flow=0&redundant_only=0&tv=0",
            "desc": "Shows only distribution switches. Useful for validating the distribution layer layout.",
        },
        {
            "label": "Core Infrastructure Only",
            "url": "/tools/topology-vmap?site=vt&show_core=1&show_services=0&show_distribution=0&show_access=0&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0",
            "desc": "Shows core infrastructure, firewalls, edge routers, and backbone switches. Best for core-link review.",
        },
        {
            "label": "Dist + Access",
            "url": "/tools/topology-vmap?site=vt&show_core=0&show_services=0&show_distribution=1&show_access=1&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0",
            "desc": "Shows distribution and access. Best for checking whether access switches land on redundant distribution points.",
        },
        {
            "label": "APs Only",
            "url": "/tools/topology-vmap?site=vt&show_core=0&show_services=0&show_distribution=0&show_access=0&show_aps=1&show_ports=0&flow=0&redundant_only=0&tv=0",
            "desc": "Shows managed APs only if present in the topology source. Mostly for wireless sanity checks.",
        },
        {
            "label": "All + APs",
            "url": "/tools/topology-vmap?site=vt&show_core=1&show_services=1&show_distribution=1&show_access=1&show_aps=1&show_ports=0&flow=1&redundant_only=1&tv=0",
            "desc": "Everything. Also known as the 'make the browser regret its choices' view.",
        },
        {
            "label": "TV Wallboard",
            "url": "/tv/topology?zoom=1.35",
            "desc": "Dedicated full-screen TV view. Auto-refreshes and hides the regular app chrome.",
        },
        {
            "label": "Legacy Split View",
            "url": "/tools/topology-v2-split?q=core&limit=250&show_aps=0",
            "desc": "Older split topology page. Still useful for the pop-out/focus behavior while V-map focus gets cleaned up.",
        },
        {
            "label": "Mist Logical Topology",
            "url": "/tools/mist/topology?q=core&limit=80",
            "desc": "Mist-derived logical topology. Useful when validating Mist/fabric inventory and roles.",
        },
    ]

    rows = []
    for preset in presets:
        rows.append(
            f'<a class="preset-option" href="{h(preset["url"])}" title="{h(preset["desc"])}">'
            f'<span class="preset-label">{h(preset["label"])}</span>'
            f'<span class="preset-desc">{h(preset["desc"])}</span>'
            f'</a>'
        )

    return (
        '<details class="preset-dropdown">'
        '<summary>Map Options</summary>'
        '<div class="preset-menu">'
        + "".join(rows) +
        '</div>'
        '</details>'
    )



def vmap_layer_switches(current):
    """
    Clickable layer switches for the V-map.
    These are link-based toggles so they work without JS and survive refreshes.
    """
    def enabled(key):
        return str(current.get(key, "0")) == "1"

    def toggle_url(key):
        params = dict(current)
        params[key] = "0" if enabled(key) else "1"
        params.pop("focus", None)
        params.pop("t", None)
        return "/tools/topology-vmap?" + urlencode(params)

    switches = [
        ("show_core", "Core", "Core infrastructure, firewalls, edge routers, and backbone switches."),
        ("show_services", "Services", "Service switching layer, including service aggregation switches."),
        ("show_distribution", "Distribution", "Distribution layer switches and pod uplinks."),
        ("show_access", "Access", "Access and leaf switches. Useful, but noisy. Humanity's burden."),
        ("show_aps", "APs", "Wireless APs if present in topology data."),
        ("show_ports", "Ports", "Show port labels on links. Helpful for troubleshooting, ugly for wallboards."),
        ("flow", "Flow", "Draw directional arrows from upper layers toward lower layers."),
        ("redundant_only", "Highlight Redundant", "Highlight redundant physical links and dim single links."),
    ]

    default_url = (
        "/tools/topology-vmap?"
        "site=vt&show_core=1&show_services=1&show_distribution=1"
        "&show_access=0&show_aps=0&show_ports=0"
        "&flow=1&redundant_only=1&tv=0"
    )

    html = ['<div class="layer-switches" aria-label="Topology layer switches">']
    html.append('<span class="layer-switch-title">Layer Switches</span>')
    html.append(
        f'<a class="layer-reset-view" href="{h(default_url)}" '
        f'title="Reset to the clean default wallboard view: Core, Services, and Distribution only.">'
        f'Reset View</a>'
    )

    for key, label, desc in switches:
        state = enabled(key)
        cls = "on" if state else "off"
        state_text = "On" if state else "Off"

        html.append(
            f'<a class="layer-switch {cls}" href="{h(toggle_url(key))}" title="{h(desc)}">'
            f'<span class="switch-pill"><span class="switch-dot"></span></span>'
            f'<span class="switch-label">{h(label)}</span>'
            f'<span class="switch-state">{h(state_text)}</span>'
            f'</a>'
        )

    html.append("</div>")
    return "".join(html)



def vmap_horizontal_group(d, show_access_bool=False):
    """
    Horizontal transition map grouping:
    left = old / legacy network
    middle = service switches
    right = Mist fabric/distribution
    optional far-right = access/leaf
    """
    name = str(d.get("display_name") or d.get("hostname") or d.get("sysName") or "").lower()
    role = final_vmap_role(d)

    if role == "Access / Leaf":
        return "Access / Leaf" if show_access_bool else None

    if role == "Services" or any(t in name for t in ("svcs-dfl", "svcs-voter", "svc-dfl", "svc-voter")):
        return "Services"

    if any(t in name for t in ("fabric-core", "dist-")):
        return "Mist Network"

    if role == "Distribution":
        if any(t in name for t in ("aggregation-a", "aggregation-b", "aggregation")):
            return "Old Network"
        return "Mist Network"

    if role == "Core Infrastructure":
        return "Old Network"

    return None


def vmap_horizontal_sort_key(d):
    name = str(d.get("display_name") or d.get("hostname") or d.get("sysName") or "").lower()
    role = final_vmap_role(d)

    priority = 50

    if "dfl-core" in name:
        priority = 1
    elif "vtr-core" in name:
        priority = 2
    elif "fabric-core-dfl" in name:
        priority = 3
    elif "fabric-core-voter" in name:
        priority = 4
    elif "aggregation-a" in name:
        priority = 5
    elif "aggregation-b" in name:
        priority = 6
    elif "svcs-dfl" in name:
        priority = 1
    elif "svcs-voter" in name:
        priority = 2
    elif name.startswith("dist-"):
        priority = 10
    elif "fw" in name or "firewall" in name:
        priority = 30
    elif role == "Access / Leaf":
        priority = 100

    return (priority, name)


def render_old_mist_horizontal_layout(devices, edges, show_ports=False, flow_mode=True, redundant_only=True, tv_mode=False, node_url_params=None):
    group_order = ["Old Network", "Services", "Mist Network"]
    if show_ports:
        # no-op; keeps the parameter intentionally used
        pass

    show_access_bool = False
    if node_url_params and str(node_url_params.get("show_access", "0")) == "1":
        show_access_bool = True

    if show_access_bool:
        group_order.append("Access / Leaf")

    groups = {g: [] for g in group_order}

    for did, d in devices.items():
        group = vmap_horizontal_group(d, show_access_bool=show_access_bool)
        if group in groups:
            groups[group].append((int(did), d))

    for group in groups:
        groups[group].sort(key=lambda item: vmap_horizontal_sort_key(item[1]))

    node_w = 250
    node_h = 58
    gap_y = 26
    col_gap = 150
    x0 = 90
    y0 = 130

    if tv_mode:
        node_w = 270
        gap_y = 34
        col_gap = 190
        x0 = 120
        y0 = 150

    col_w = node_w + col_gap

    pos = {}
    max_rows = 1

    for col_idx, group in enumerate(group_order):
        rows = groups[group]
        max_rows = max(max_rows, len(rows))

        x = x0 + col_idx * col_w

        for row_idx, (did, d) in enumerate(rows):
            y = y0 + row_idx * (node_h + gap_y)
            pos[int(did)] = {
                "x": x,
                "y": y,
                "group": group,
                "device": d,
            }

    width = x0 * 2 + len(group_order) * col_w + node_w
    height = y0 + max_rows * (node_h + gap_y) + 120

    pair_counts = defaultdict(int)
    for e in edges:
        a = int(e.get("a"))
        b = int(e.get("b"))
        if a in pos and b in pos:
            pair_counts[tuple(sorted((a, b)))] += 1

    parts = []
    parts.append("""
<style>
  .horizontal-map-note {
    border:1px solid #334155;
    border-radius:12px;
    padding:10px 12px;
    margin: 0 0 12px 0;
    background:#0f172a;
    color:#cbd5e1;
  }

  .horizontal-map-note b {
    color:#f8fafc;
  }

  svg.vmap-horizontal .column-label {
    font-size:18px;
    font-weight:900;
    fill:#e5e7eb;
  }

  svg.vmap-horizontal .column-desc {
    font-size:11px;
    fill:#94a3b8;
  }
</style>
""")

    parts.append(
        '<div class="horizontal-map-note">'
        '<b>Horizontal transition view:</b> Old / legacy network on the left, '
        'the two service switches in the middle, and Mist fabric/distribution on the right. '
        'Use the Access layer switch to add access switches as a far-right column.'
        '</div>'
    )

    parts.append('<div class="vmap-wrap horizontal">')
    parts.append(
        f'<svg class="vmap vmap-horizontal" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
    )

    parts.append("""
<defs>
  <marker id="flowArrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L0,6 L9,3 z" fill="#38bdf8"/>
  </marker>
</defs>
""")

    group_descriptions = {
        "Old Network": "Legacy core, old aggregation, edge/firewall/racktop infrastructure",
        "Services": "svcs-dfl and svcs-voter bridge between old and Mist",
        "Mist Network": "Fabric core and distribution pods",
        "Access / Leaf": "Access switches, shown only when Access is enabled",
    }

    for col_idx, group in enumerate(group_order):
        x = x0 + col_idx * col_w
        parts.append(f'<text class="column-label" x="{x}" y="42">{h(group)}</text>')
        parts.append(f'<text class="column-desc" x="{x}" y="62">{h(group_descriptions.get(group, ""))}</text>')

    # Edges first, nodes on top.
    edge_seen = defaultdict(int)

    for e in edges:
        a = int(e.get("a"))
        b = int(e.get("b"))

        if a not in pos or b not in pos:
            continue

        key = tuple(sorted((a, b)))
        band = edge_seen[key]
        edge_seen[key] += 1

        pa = pos[a]
        pb = pos[b]

        ax = pa["x"] + node_w / 2
        ay = pa["y"] + node_h / 2
        bx = pb["x"] + node_w / 2
        by = pb["y"] + node_h / 2

        redundant = pair_counts.get(key, 0) >= 2

        stroke_width = 2.8 if redundant else 1.2
        opacity = 0.88 if redundant else 0.34

        if not redundant_only:
            stroke_width = 1.8
            opacity = 0.72

        color = "#38bdf8" if redundant else "#64748b"

        offset = (band % 6 - 2.5) * 8
        mid_x = (ax + bx) / 2
        bend = 80 + abs(ax - bx) / 8

        if abs(ax - bx) < 80:
            mid_y = min(ay, by) - 70 - band * 16
            path_d = (
                f"M{ax:.1f},{ay:.1f} "
                f"C{ax + offset:.1f},{mid_y:.1f} {bx + offset:.1f},{mid_y:.1f} {bx:.1f},{by:.1f}"
            )
            label_x = (ax + bx) / 2
            label_y = mid_y - 4
        else:
            path_d = (
                f"M{ax:.1f},{ay:.1f} "
                f"C{mid_x:.1f},{ay + offset:.1f} {mid_x:.1f},{by + offset:.1f} {bx:.1f},{by:.1f}"
            )
            label_x = mid_x
            label_y = (ay + by) / 2 - 6

        marker_end = 'marker-end="url(#flowArrow)"' if flow_mode else ""

        a_name = devices[a].get("display_name") or devices[a].get("hostname") or a
        b_name = devices[b].get("display_name") or devices[b].get("hostname") or b
        a_port = e.get("a_port") or ""
        b_port = e.get("b_port") or ""

        title = h(f"{a_name} -> {b_name} | {a_port} <-> {b_port}")

        parts.append(
            f'<g class="edge draggable-edge" data-a="{a}" data-b="{b}">'
            f'<title>{title}</title>'
            f'<path class="edge-path" d="{path_d}" stroke="{color}" '
            f'stroke-width="{stroke_width}" fill="none" opacity="{opacity}" {marker_end}/>'
        )

        if show_ports:
            label = h(f"{a_port} <-> {b_port}")
            parts.append(
                f'<text x="{label_x:.1f}" y="{label_y:.1f}" fill="#cbd5e1" '
                f'font-size="10" text-anchor="middle">{label}</text>'
            )

        parts.append('</g>')

    # Nodes.
    for did, item in pos.items():
        d = item["device"]
        x = item["x"]
        y = item["y"]
        group = item["group"]
        role = final_vmap_role(d)

        if group == "Services":
            stroke = "#a78bfa"
        elif group == "Mist Network":
            stroke = "#22c55e"
        elif group == "Access / Leaf":
            stroke = "#fb923c"
        else:
            stroke = "#38bdf8"

        name = str(d.get("display_name") or d.get("hostname") or d.get("sysName") or did)
        hw = str(d.get("hardware") or d.get("os") or role or "")

        params = dict(node_url_params or {})
        params["focus"] = str(did)
        params["layout"] = "old_mist"
        url = "/tools/topology-vmap?" + urlencode(params)

        parts.append(f'<g class="node draggable-node" data-node-id="{did}">')
        parts.append(f'<a href="{h(url)}" target="_self">')
        parts.append(f'<title>{h(name)} | {h(hw)}</title>')

        if is_stack_device(d):
            parts.append(
                f'<rect class="stack-card-back" x="{x + 10}" y="{y + 10}" width="{node_w}" height="{node_h}" '
                f'rx="10" fill="#0f172a" stroke="{stroke}" stroke-width="1.4"/>'
            )
            parts.append(
                f'<rect class="stack-card-back" x="{x + 5}" y="{y + 5}" width="{node_w}" height="{node_h}" '
                f'rx="10" fill="#0f172a" stroke="{stroke}" stroke-width="1.6"/>'
            )

        parts.append(
            f'<rect x="{x}" y="{y}" width="{node_w}" height="{node_h}" rx="10" '
            f'fill="#0f172a" stroke="{stroke}" stroke-width="2"/>'
        )

        label = name
        if is_stack_device(d) and "VC/Stack" not in label:
            label = label + " VC/Stack"

        if len(label) > 31:
            label = label[:28] + "..."

        hw_label = hw
        if len(hw_label) > 37:
            hw_label = hw_label[:34] + "..."

        parts.append(
            f'<text x="{x + 12}" y="{y + 22}" fill="#f8fafc" '
            f'font-size="13" font-weight="800">{h(label)}</text>'
        )
        parts.append(
            f'<text x="{x + 12}" y="{y + 43}" fill="#cbd5e1" '
            f'font-size="10">{h(hw_label)}</text>'
        )

        parts.append('</a>')
        parts.append('</g>')

    parts.append('</svg>')
    parts.append('</div>')

    if "vmap_drag_script" in globals():
        parts.append(vmap_drag_script())

    parts.append("""
<script>
(function () {
  const svg = document.querySelector("svg.vmap-mist-pods");
  if (!svg) return;

  const params = new URLSearchParams(window.location.search);
  const edgeOn = params.get("highlight_edge") === "1";
  const mistOn = params.get("highlight_mist") === "1";

  const edgeTerms = [
    "zuma-dfl",
    "turbo-vtr",
    "daisy-dfl",
    "chester-vtr",
    "edgefw",
    "edge-fw",
    "vpnfw",
    "vpn-fw",
    "dfl-core",
    "vtr-core"
  ];

  const mistTerms = [
    "fabric-core",
    "dist-eastpod",
    "dist-northpod",
    "dist-southpod",
    "dist-westpod",
    "dist-testpod",
    "eastpod",
    "northpod",
    "southpod",
    "westpod",
    "testpod"
  ];

  function nodeText(g) {
    return (g.textContent || "").toLowerCase();
  }

  function hasAny(text, terms) {
    return terms.some(function (term) {
      return text.indexOf(term) !== -1;
    });
  }

  if (edgeOn || mistOn) {
    svg.querySelectorAll("g.node").forEach(function (g) {
      const txt = nodeText(g);

      if (edgeOn && hasAny(txt, edgeTerms)) {
        g.classList.add("neon-edge");
      }

      if (mistOn && hasAny(txt, mistTerms)) {
        g.classList.add("neon-mist");
      }
    });
  }

  if (edgeOn) {
    svg.querySelectorAll(".old-wan-link").forEach(function (el) {
      el.classList.add("neon-edge-link");
    });

    svg.querySelectorAll("g.edge").forEach(function (edge) {
      const txt = (edge.textContent || "").toLowerCase();
      if (hasAny(txt, edgeTerms)) {
        edge.classList.add("neon-edge-edge");
      }
    });
  }

  if (mistOn) {
    svg.querySelectorAll("g.edge").forEach(function (edge) {
      const txt = (edge.textContent || "").toLowerCase();
      let looksMist = hasAny(txt, mistTerms);

      edge.querySelectorAll("path,line").forEach(function (line) {
        const stroke = (
          line.getAttribute("stroke") ||
          line.style.stroke ||
          ""
        ).toLowerCase();

        if (
          stroke.indexOf("#22c55e") !== -1 ||
          stroke.indexOf("34, 197, 94") !== -1 ||
          stroke.indexOf("34,197,94") !== -1
        ) {
          looksMist = true;
        }
      });

      if (looksMist) {
        edge.classList.add("neon-mist-edge");
      }
    });
  }
})();
</script>
""")

    return "\n".join(parts)



def transition_zone(d, show_access_bool=False):
    """
    Left-to-right transition map zones:
    Mist side on the left, services bridge in the middle, non-Mist infra on the right.
    """
    name = str(d.get("display_name") or d.get("hostname") or d.get("sysName") or "").lower()
    role = final_vmap_role(d)

    if role == "Access / Leaf":
        return "Mist Access" if show_access_bool else None

    if role == "Services" or any(t in name for t in ("svcs-dfl", "svcs-voter", "svc-dfl", "svc-voter")):
        return "Services"

    if any(t in name for t in ("fabric-core",)):
        return "Mist Fabric"

    if name.startswith("dist-") or "dist-" in name:
        return "Mist Distribution"

    # Legacy aggregation belongs to the non-Mist side.
    if any(t in name for t in ("aggregation-a", "aggregation-b", "aggregation")):
        return "Non-Mist Infra"

    if role in ("Core Infrastructure", "Distribution"):
        return "Non-Mist Infra"

    return None


def transition_sort_key(d):
    name = str(d.get("display_name") or d.get("hostname") or d.get("sysName") or "").lower()
    role = final_vmap_role(d)

    priority = 50

    if "fabric-core-dfl" in name:
        priority = 1
    elif "fabric-core-voter" in name:
        priority = 2
    elif name.startswith("dist-east"):
        priority = 10
    elif name.startswith("dist-north"):
        priority = 11
    elif name.startswith("dist-south"):
        priority = 12
    elif name.startswith("dist-west"):
        priority = 13
    elif "svcs-dfl" in name:
        priority = 1
    elif "svcs-voter" in name:
        priority = 2
    elif "dfl-core" in name:
        priority = 1
    elif "vtr-core" in name:
        priority = 2
    elif "aggregation-a" in name:
        priority = 3
    elif "aggregation-b" in name:
        priority = 4
    elif "edgefw" in name:
        priority = 20
    elif "vpn-fw" in name:
        priority = 21
    elif "enclave-fw" in name:
        priority = 22
    elif "dc-fw" in name:
        priority = 23
    elif "racktop" in name:
        priority = 30
    elif role == "Access / Leaf":
        priority = 100

    return (priority, name)


def transition_url_params(base_params, **updates):
    params = dict(base_params or {})
    params.update({k: str(v) for k, v in updates.items() if v is not None})
    params.pop("t", None)
    return "/tools/topology-vmap?" + urlencode(params)


def edge_other_id(e, did):
    a = int(e.get("a"))
    b = int(e.get("b"))
    did = int(did)
    if a == did:
        return b
    if b == did:
        return a
    return None


def edge_ports_for_device(e, did):
    a = int(e.get("a"))
    b = int(e.get("b"))
    did = int(did)
    if a == did:
        return str(e.get("a_port") or ""), str(e.get("b_port") or "")
    if b == did:
        return str(e.get("b_port") or ""), str(e.get("a_port") or "")
    return "", ""


def transition_drilldown_panel(devices, edges, selected_id, node_url_params):
    if selected_id is None:
        return (
            '<aside class="transition-panel empty">'
            '<h2>Drilldown</h2>'
            '<p>Click a switch, firewall, or service block to inspect neighbors, ports, and redundancy.</p>'
            '<p class="muted">The map stays left-to-right; the detail lives here so the SVG does not become interpretive dance.</p>'
            '</aside>'
        )

    try:
        selected_id = int(selected_id)
    except Exception:
        return (
            '<aside class="transition-panel empty">'
            '<h2>Drilldown</h2>'
            '<p>Invalid selected device.</p>'
            '</aside>'
        )

    d = devices.get(selected_id)
    if not d:
        return (
            '<aside class="transition-panel empty">'
            '<h2>Drilldown</h2>'
            '<p>Selected device was not found in this filtered map.</p>'
            '</aside>'
        )

    name = str(d.get("display_name") or d.get("hostname") or d.get("sysName") or selected_id)
    role = final_vmap_role(d)
    zone = transition_zone(d, show_access_bool=True) or role
    hw = str(d.get("hardware") or d.get("os") or "")

    direct_edges = [
        e for e in edges
        if int(e.get("a")) == selected_id or int(e.get("b")) == selected_id
    ]

    rows = []
    domains = set()
    neighbors = set()
    collapsed_access_count = 0

    mist_count = 0
    service_count = 0
    non_mist_count = 0
    access_count = 0

    for e in direct_edges:
        other_id = edge_other_id(e, selected_id)
        if other_id is None or other_id not in devices:
            continue

        other = devices[other_id]
        other_name = str(other.get("display_name") or other.get("hostname") or other.get("sysName") or other_id)
        other_role = final_vmap_role(other)
        other_zone = transition_zone(other, show_access_bool=True) or other_role
        local_port, remote_port = edge_ports_for_device(e, selected_id)

        neighbors.add(other_name)
        domains.add(other_zone)

        # collapse noisy downstream access rows when selecting fabric/distribution/service devices
        if zone in ("Mist Fabric", "Mist Distribution", "Services") and other_zone == "Mist Access":
            collapsed_access_count += 1
            continue

        if other_zone.startswith("Mist"):
            mist_count += 1
        elif other_zone == "Services":
            service_count += 1
        elif other_zone == "Non-Mist Infra":
            non_mist_count += 1
        elif other_zone == "Mist Access":
            access_count += 1

        rows.append(
            "<tr>"
            f"<td>{h(other_name)}</td>"
            f"<td>{h(other_zone)}</td>"
            f"<td>{h(other_role)}</td>"
            f"<td>{h(local_port)}</td>"
            f"<td>{h(remote_port)}</td>"
            "</tr>"
        )

    if collapsed_access_count:
        rows.append(
            f'<tr><td colspan="5"><b>{collapsed_access_count}</b> downstream Mist Access neighbors collapsed. '
            'Select an access switch to inspect its exact upstream path.</td></tr>'
        )

    if not rows:
        rows.append('<tr><td colspan="5">No direct links visible in this map mode.</td></tr>')

    status = "INFO"
    status_class = "info"
    status_text = "Showing direct visible neighbors."

    if role == "Access / Leaf":
        upstream_zones = [
            transition_zone(devices[edge_other_id(e, selected_id)], show_access_bool=True)
            for e in direct_edges
            if edge_other_id(e, selected_id) in devices
        ]
        upstream_zones = [z for z in upstream_zones if z and z != "Mist Access"]

        upstream_neighbors = set()
        for e in direct_edges:
            other_id = edge_other_id(e, selected_id)
            if other_id in devices:
                oz = transition_zone(devices[other_id], show_access_bool=True)
                if oz and oz != "Mist Access":
                    upstream_neighbors.add(other_id)

        if len(upstream_neighbors) >= 2 and len(set(upstream_zones)) >= 1:
            status = "PASS"
            status_class = "ok"
            status_text = "Access switch has multiple visible upstream links."
        elif len(upstream_neighbors) == 1:
            status = "FAIL"
            status_class = "bad"
            status_text = "Only one visible upstream neighbor."
        else:
            status = "FAIL"
            status_class = "bad"
            status_text = "No visible upstream neighbors."

    elif zone == "Services":
        if mist_count > 0 and non_mist_count > 0:
            status = "BRIDGE"
            status_class = "ok"
            status_text = "Service switch connects Mist-side and non-Mist-side infrastructure."
        else:
            status = "WARN"
            status_class = "warn"
            status_text = "Service switch does not currently show both sides in this filtered map."

    clear_params = dict(node_url_params or {})
    clear_params.pop("selected", None)
    clear_params.pop("focus", None)
    clear_url = "/tools/topology-vmap?" + urlencode(clear_params)

    lookup_url = "/tools/lookup?q=" + quote(name)

    return (
        '<aside class="transition-panel">'
        f'<div class="panel-status {status_class}">{h(status)}</div>'
        f'<h2>{h(name)}</h2>'
        f'<p class="panel-sub">{h(zone)} · {h(role)}</p>'
        f'<p class="panel-hw">{h(hw)}</p>'
        f'<p>{h(status_text)}</p>'
        '<div class="panel-metrics">'
        f'<span>Links <b>{len(direct_edges)}</b></span>'
        f'<span>Neighbors <b>{len(neighbors)}</b></span>'
        f'<span>Zones <b>{len(domains)}</b></span>'
        '</div>'
        '<div class="panel-actions">'
        f'<a class="button" href="{h(clear_url)}">Clear Selection</a>'
        f'<a class="button" href="{h(lookup_url)}">Open Lookup</a>'
        '</div>'
        '<table class="panel-table">'
        '<thead><tr><th>Neighbor</th><th>Zone</th><th>Role</th><th>Local port</th><th>Remote port</th></tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody>'
        '</table>'
        '</aside>'
    )


def render_transition_map_page(devices, edges, show_ports=False, flow_mode=True, redundant_only=True, tv_mode=False, selected="", node_url_params=None):
    show_access_bool = str((node_url_params or {}).get("show_access", "0")) == "1"

    group_order = ["Mist Fabric", "Mist Distribution"]
    if show_access_bool:
        group_order.append("Mist Access")
    group_order += ["Services", "Non-Mist Infra"]

    groups = {g: [] for g in group_order}

    for did, d in devices.items():
        group = transition_zone(d, show_access_bool=show_access_bool)
        if group in groups:
            groups[group].append((int(did), d))

    for group in groups:
        groups[group].sort(key=lambda item: transition_sort_key(item[1]))

    try:
        selected_id = int(selected) if str(selected or "").strip() else None
    except Exception:
        selected_id = None

    selected_neighbors = set()
    if selected_id is not None:
        selected_device_for_neighbors = devices.get(selected_id)
        selected_zone_for_neighbors = (
            transition_zone(selected_device_for_neighbors, show_access_bool=True)
            if selected_device_for_neighbors else None
        )

        for e in edges:
            other = edge_other_id(e, selected_id)
            if other is None or other not in devices:
                continue

            other_zone = transition_zone(devices[other], show_access_bool=True)

            # When selecting distribution/fabric/service, don't visually promote all downstream access.
            if selected_zone_for_neighbors in ("Mist Fabric", "Mist Distribution", "Services") and other_zone == "Mist Access":
                continue

            selected_neighbors.add(other)

    node_w = 248
    node_h = 58
    gap_x = 34
    gap_y = 24
    block_gap = 118
    x0 = 70
    y0 = 140

    if tv_mode:
        node_w = 270
        gap_x = 42
        gap_y = 30
        block_gap = 150
        x0 = 95
        y0 = 150

    cols_by_group = {
        "Mist Fabric": 1,
        "Mist Distribution": 2,
        "Mist Access": 4,
        "Services": 1,
        "Non-Mist Infra": 3,
    }

    x_by_group = {}
    block_widths = {}
    cursor_x = x0

    for group in group_order:
        cols = cols_by_group.get(group, 2)
        width = cols * node_w + max(0, cols - 1) * gap_x
        x_by_group[group] = cursor_x
        block_widths[group] = width
        cursor_x += width + block_gap

    pos = {}

    services = []
    for _did, _d in devices.items():
        _did = int(_did)
        _role = final_vmap_role(_d)
        _name = device_display_name(_d).lower()
        if _role == "Services" or "svc-dfl" in _name or "svc-voter" in _name or "svcs-dfl" in _name or "svcs-voter" in _name:
            services.append((_did, _d))

    services.sort(key=lambda item: device_display_name(item[1]).lower())

    max_bottom = y0 + node_h

    for group in group_order:
        rows = groups[group]
        cols = cols_by_group.get(group, 2)
        base_x = x_by_group[group]
        base_y = y0

        if group == "Services":
            base_y = y0 + 95

        for idx, (did, d) in enumerate(rows):
            col = idx % cols
            row = idx // cols

            x = base_x + col * (node_w + gap_x)
            y = base_y + row * (node_h + gap_y)

            pos[int(did)] = {"x": x, "y": y, "group": group, "device": d}
            max_bottom = max(max_bottom, y + node_h)

    visible_edges = []
    pair_counts = defaultdict(int)

    for e in edges:
        a = int(e.get("a"))
        b = int(e.get("b"))
        if a in pos and b in pos:
            visible_edges.append(e)
            pair_counts[tuple(sorted((a, b)))] += 1

    width = cursor_x + x0
    height = max(max_bottom + 130, 760)

    css = """
<style>
  .transition-shell {
    display:grid;
    grid-template-columns: minmax(0, 1fr) 430px;
    gap:14px;
    align-items:start;
  }

  .transition-map-pane {
    min-width:0;
    max-width:100%;
    overflow:auto;
  }

  .vmap-wrap.transition {
    max-width:100%;
    overflow:auto !important;
  }

  .transition-panel {
    position:sticky;
    top:12px;
    border:1px solid #334155;
    border-radius:14px;
    background:#0f172a;
    padding:14px;
    color:#e5e7eb;
    max-height: calc(100vh - 24px);
    overflow:auto;
  }

  .transition-panel.empty {
    color:#cbd5e1;
  }

  .transition-panel h2 {
    margin:8px 0 2px 0;
    font-size:18px;
  }

  .panel-sub,
  .panel-hw,
  .muted {
    color:#94a3b8;
    margin:4px 0;
  }

  .panel-status {
    display:inline-block;
    padding:6px 10px;
    border-radius:999px;
    font-size:12px;
    font-weight:900;
    color:#020617;
  }

  .panel-status.ok { background:#22c55e; }
  .panel-status.warn { background:#fb923c; }
  .panel-status.bad { background:#ef4444; color:#fff; }
  .panel-status.info { background:#38bdf8; }

  .panel-metrics {
    display:flex;
    flex-wrap:wrap;
    gap:8px;
    margin:10px 0;
  }

  .panel-metrics span {
    border:1px solid #334155;
    border-radius:8px;
    background:#0f172a;
    padding:6px 8px;
    color:#cbd5e1;
  }

  .panel-actions {
    display:flex;
    gap:8px;
    flex-wrap:wrap;
    margin:10px 0;
  }

  .panel-table {
    width:100%;
    border-collapse:collapse;
    font-size:12px;
    margin-top:10px;
  }

  .panel-table th,
  .panel-table td {
    border-bottom:1px solid #334155;
    padding:6px 5px;
    text-align:left;
    vertical-align:top;
  }

  .panel-table th {
    color:#93c5fd;
  }

  .transition-note {
    border:1px solid #334155;
    border-radius:12px;
    padding:10px 12px;
    margin:0 0 12px 0;
    background:#0f172a;
    color:#cbd5e1;
  }

  .transition-note b {
    color:#f8fafc;
  }

  svg.vmap-transition .zone-label {
    font-size:18px;
    font-weight:900;
    fill:#e5e7eb;
  }

  svg.vmap-transition .zone-desc {
    font-size:11px;
    fill:#94a3b8;
  }

  svg.vmap-transition .transition-link-label {
    font-size:12px;
    fill:#93c5fd;
    font-weight:900;
  }

  svg.vmap-transition .node.dim {
    opacity:.24;
  }

  svg.vmap-transition .node.selected rect {
    stroke:#fb7185 !important;
    stroke-width:4 !important;
    filter: drop-shadow(0 0 10px #fb7185);
  }

  svg.vmap-transition .node.related rect {
    stroke-width:3 !important;
  }

  svg.vmap-transition .edge.dim {
    opacity:.12;
  }

  svg.vmap-transition .edge.selected-edge path {
    stroke:#fb7185 !important;
    stroke-width:4.5 !important;
    opacity:1 !important;
    filter: drop-shadow(0 0 8px #fb7185);
  }

  svg.vmap-transition .edge.selected-downstream path {
    stroke:#fb923c !important;
    stroke-width:2 !important;
    opacity:.38 !important;
    filter:none !important;
  }

  @media (max-width: 1200px) {
    .transition-shell {
      grid-template-columns: 1fr;
    }

    .transition-panel {
      position:relative;
      top:auto;
      max-height:none;
    }
  }
</style>
"""

    params = dict(node_url_params or {})
    params["layout"] = "transition"

    panel = transition_drilldown_panel(devices, visible_edges, selected_id, params)

    parts = [css]

    parts.append('<div class="transition-note"><b>Transition view:</b> Mist is on the left, Services are in the middle, and non-Mist infrastructure is on the right. Click a device for drilldown. Turn on Access when you want the noisy stuff, because apparently switches reproduce in closets.</div>')

    parts.append('<div class="transition-shell">')
    parts.append('<div class="transition-map-pane">')
    parts.append('<div class="vmap-wrap transition">')
    parts.append(
        f'<svg class="vmap vmap-transition" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
    )

    parts.append("""
<defs>
  <marker id="flowArrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L0,6 L9,3 z" fill="#38bdf8"/>
  </marker>
</defs>
""")

    descs = {
        "Mist Fabric": "Fabric core / Mist-side backbone",
        "Mist Distribution": "Mist distribution pods",
        "Mist Access": "Edge / access switches, optional",
        "Services": "svcs-dfl / svcs-voter bridge",
        "Non-Mist Infra": "Legacy core, firewalls, edge, aggregation",
    }

    for group in group_order:
        x = x_by_group[group]
        parts.append(f'<text class="zone-label" x="{x}" y="42">{h(group)}</text>')
        parts.append(f'<text class="zone-desc" x="{x}" y="62">{h(descs.get(group, ""))}</text>')

    for idx in range(len(group_order) - 1):
        left = group_order[idx]
        right = group_order[idx + 1]
        x1 = x_by_group[left] + block_widths[left] + 22
        x2 = x_by_group[right] - 22
        y = 88
        if x2 > x1:
            parts.append(
                f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" '
                f'stroke="#38bdf8" stroke-width="3" opacity=".6" marker-end="url(#flowArrow)"/>'
            )

    if services:
        parts.append(f'<text class="pod-title" x="{services_x}" y="42">Services Block</text>')
        parts.append(f'<rect class="pod-bg" x="{services_x - 18}" y="82" width="{services_w}" height="{height - 150}" rx="14"/>')
        parts.append(f'<text class="section-title" x="{services_x}" y="112">SERVICES</text>')

    if non_mist:
        parts.append(f'<text class="pod-title" x="{non_mist_x}" y="42">Non-Mist Infra</text>')
        parts.append(f'<rect class="pod-bg" x="{non_mist_x - 18}" y="82" width="{non_mist_w}" height="{height - 150}" rx="14"/>')
        parts.append(f'<text class="section-title" x="{non_mist_x}" y="112">WAN / CORE / FIREWALLS / SWITCHING</text>')

    def anchor(did, toward_x):
        item = pos[int(did)]
        x = item["x"]
        y = item["y"]
        cx = x + node_w / 2
        cy = y + node_h / 2
        if toward_x >= cx:
            return x + node_w, cy
        return x, cy

    edge_seen = defaultdict(int)

    for e in visible_edges:
        a = int(e.get("a"))
        b = int(e.get("b"))

        key = tuple(sorted((a, b)))
        band = edge_seen[key]
        edge_seen[key] += 1

        a_cx = pos[a]["x"] + node_w / 2
        b_cx = pos[b]["x"] + node_w / 2
        ax, ay = anchor(a, b_cx)
        bx, by = anchor(b, a_cx)

        redundant = pair_counts.get(key, 0) >= 2

        stroke_width = 2.8 if redundant else 1.2
        opacity = 0.88 if redundant else 0.34

        if not redundant_only:
            stroke_width = 1.8
            opacity = 0.72

        selected_edge = False
        downstream_edge = False

        if selected_id is not None and (a == selected_id or b == selected_id):
            other_id = b if a == selected_id else a
            selected_device = devices.get(selected_id)
            other_device = devices.get(other_id)

            selected_zone = transition_zone(selected_device, show_access_bool=True) if selected_device else None
            other_zone = transition_zone(other_device, show_access_bool=True) if other_device else None

            if selected_zone in ("Mist Fabric", "Mist Distribution", "Services") and other_zone == "Mist Access":
                downstream_edge = True
            else:
                selected_edge = True

        edge_class = "edge draggable-edge"
        if selected_edge:
            edge_class += " selected-edge"
        elif downstream_edge:
            edge_class += " selected-downstream"
        elif selected_id is not None:
            edge_class += " dim"

        base_color = mist_edge_color_for_map(a, b, devices, role_by_device)
        color = base_color if redundant else "#64748b"
        marker_end = 'marker-end="url(#flowArrow)"' if flow_mode else ""

        offset = (band % 7 - 3) * 9

        if abs(ax - bx) < 90:
            arc_y = min(ay, by) - 70 - band * 18
            path_d = (
                f"M{ax:.1f},{ay:.1f} "
                f"C{ax + offset:.1f},{arc_y:.1f} {bx + offset:.1f},{arc_y:.1f} {bx:.1f},{by:.1f}"
            )
            label_x = (ax + bx) / 2
            label_y = arc_y - 4
        else:
            mid_x = (ax + bx) / 2
            path_d = (
                f"M{ax:.1f},{ay:.1f} "
                f"C{mid_x:.1f},{ay + offset:.1f} {mid_x:.1f},{by + offset:.1f} {bx:.1f},{by:.1f}"
            )
            label_x = mid_x
            label_y = (ay + by) / 2 - 6

        a_name = devices[a].get("display_name") or devices[a].get("hostname") or a
        b_name = devices[b].get("display_name") or devices[b].get("hostname") or b
        a_port = e.get("a_port") or ""
        b_port = e.get("b_port") or ""
        title = h(f"{a_name} -> {b_name} | {a_port} <-> {b_port}")

        parts.append(
            f'<g class="{edge_class}" data-a="{a}" data-b="{b}">'
            f'<title>{title}</title>'
            f'<path class="edge-path" d="{path_d}" stroke="{color}" '
            f'stroke-width="{stroke_width}" fill="none" opacity="{opacity}" {marker_end}/>'
        )

        if show_ports:
            label = h(f"{a_port} <-> {b_port}")
            parts.append(
                f'<text x="{label_x:.1f}" y="{label_y:.1f}" fill="#cbd5e1" '
                f'font-size="10" text-anchor="middle">{label}</text>'
            )

        parts.append('</g>')

    for did, item in pos.items():
        d = item["device"]
        x = item["x"]
        y = item["y"]
        group = item["group"]

        if group == "Services":
            stroke = "#a78bfa"
        elif group.startswith("Mist"):
            stroke = "#22c55e"
        else:
            stroke = "#38bdf8"

        node_class = "node draggable-node"
        if selected_id == did:
            node_class += " selected"
        elif selected_id is not None and did in path_nodes:
            node_class += " path-node"
        elif selected_id is not None and did in selected_neighbors:
            node_class += " related"
        elif selected_id is not None:
            node_class += " dim"

        name = str(d.get("display_name") or d.get("hostname") or d.get("sysName") or did)
        hw = str(d.get("hardware") or d.get("os") or final_vmap_role(d) or "")

        link_params = dict(params)
        link_params["selected"] = str(did)
        url = "/tools/topology-vmap?" + urlencode(link_params)

        label = name
        if is_stack_device(d) and "VC/Stack" not in label:
            label += " VC/Stack"
        if len(label) > 31:
            label = label[:28] + "..."

        hw_label = hw
        if len(hw_label) > 37:
            hw_label = hw_label[:34] + "..."

        parts.append(f'<g class="{node_class}" data-node-id="{did}">')
        parts.append(f'<a href="{h(url)}" target="_self">')
        parts.append(f'<title>{h(name)} | {h(hw)}</title>')

        if is_stack_device(d):
            parts.append(
                f'<rect class="stack-card-back" x="{x + 10}" y="{y + 10}" width="{node_w}" height="{node_h}" '
                f'rx="10" fill="#0f172a" stroke="{stroke}" stroke-width="1.4"/>'
            )
            parts.append(
                f'<rect class="stack-card-back" x="{x + 5}" y="{y + 5}" width="{node_w}" height="{node_h}" '
                f'rx="10" fill="#0f172a" stroke="{stroke}" stroke-width="1.6"/>'
            )

        parts.append(
            f'<rect x="{x}" y="{y}" width="{node_w}" height="{node_h}" rx="10" '
            f'fill="#0f172a" stroke="{stroke}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{x + 12}" y="{y + 22}" fill="#f8fafc" '
            f'font-size="13" font-weight="800">{h(label)}</text>'
        )
        parts.append(
            f'<text x="{x + 12}" y="{y + 43}" fill="#cbd5e1" '
            f'font-size="10">{h(hw_label)}</text>'
        )

        parts.append('</a></g>')

    parts.append('</svg>')
    parts.append('</div>')

    if "vmap_drag_script" in globals():
        parts.append(vmap_drag_script())

    parts.append('</div>')
    parts.append(panel)
    parts.append('</div>')

    return "\n".join(parts)


def mist_pod_from_name(value):
    name = str(value or "").lower()

    pod_terms = [
        ("eastpod", "East POD"),
        ("east-pod", "East POD"),
        ("northpod", "North POD"),
        ("north-pod", "North POD"),
        ("southpod", "South POD"),
        ("south-pod", "South POD"),
        ("westpod", "West POD"),
        ("west-pod", "West POD"),
        ("testpod", "Test POD"),
        ("test-pod", "Test POD"),
    ]

    for needle, label in pod_terms:
        if needle in name:
            return label

    return None


def mist_device_name(d):
    return str(d.get("display_name") or d.get("hostname") or d.get("sysName") or d.get("device_id") or "")


def mist_edge_other_id(e, did):
    a = int(e.get("a"))
    b = int(e.get("b"))
    did = int(did)

    if a == did:
        return b
    if b == did:
        return a

    return None


def mist_edge_ports_for_device(e, did):
    a = int(e.get("a"))
    b = int(e.get("b"))
    did = int(did)

    if a == did:
        return str(e.get("a_port") or ""), str(e.get("b_port") or "")
    if b == did:
        return str(e.get("b_port") or ""), str(e.get("a_port") or "")

    return "", ""


def mist_role_for_pod_map(d):
    name = mist_device_name(d).lower()
    role = final_vmap_role(d)

    if "fabric-core" in name:
        return "Fabric Core"

    if name.startswith("dist-") or "dist-" in name:
        return "Distribution"

    if is_ap_device(d) or role == "AP":
        return "AP"

    if role == "Access / Leaf":
        return "Access"

    return None


def build_mist_pod_groups(devices, edges, show_access_bool=False, show_aps_bool=False):
    """
    Build Mist-only grouping:
    - Fabric Core separate
    - Distribution grouped by POD from dist-* name
    - Access inherits POD from directly connected dist switch
    - AP inherits POD from connected access switch
    """
    pod_order = ["East POD", "North POD", "South POD", "West POD", "Test POD", "Other POD"]
    pod_by_device = {}
    role_by_device = {}

    fabric_ids = set()
    dist_ids = set()
    access_ids = set()
    ap_ids = set()

    for did, d in devices.items():
        did = int(did)
        name = mist_device_name(d)
        mrole = mist_role_for_pod_map(d)
        role_by_device[did] = mrole

        if mrole == "Fabric Core":
            fabric_ids.add(did)
        elif mrole == "Distribution":
            dist_ids.add(did)
            pod_by_device[did] = mist_pod_from_name(name) or "Other POD"
        elif mrole == "Access":
            access_ids.add(did)
        elif mrole == "AP":
            ap_ids.add(did)

    # Access inherits pod from directly connected distribution.
    changed = True
    while changed:
        changed = False

        for e in edges:
            a = int(e.get("a"))
            b = int(e.get("b"))

            if a not in devices or b not in devices:
                continue

            a_role = role_by_device.get(a)
            b_role = role_by_device.get(b)

            if a_role == "Distribution" and b_role == "Access" and a in pod_by_device and b not in pod_by_device:
                pod_by_device[b] = pod_by_device[a]
                changed = True

            if b_role == "Distribution" and a_role == "Access" and b in pod_by_device and a not in pod_by_device:
                pod_by_device[a] = pod_by_device[b]
                changed = True

            # AP inherits from access.
            if show_aps_bool:
                if a_role == "Access" and b_role == "AP" and a in pod_by_device and b not in pod_by_device:
                    pod_by_device[b] = pod_by_device[a]
                    changed = True

                if b_role == "Access" and a_role == "AP" and b in pod_by_device and a not in pod_by_device:
                    pod_by_device[a] = pod_by_device[b]
                    changed = True

    pods = {
        pod: {
            "Distribution": [],
            "Access": [],
            "AP": [],
        }
        for pod in pod_order
    }

    fabric = []

    for did in sorted(fabric_ids, key=lambda x: mist_device_name(devices[x]).lower()):
        fabric.append((did, devices[did]))

    for did in sorted(dist_ids, key=lambda x: mist_device_name(devices[x]).lower()):
        pod = pod_by_device.get(did) or "Other POD"
        pods.setdefault(pod, {"Distribution": [], "Access": [], "AP": []})
        pods[pod]["Distribution"].append((did, devices[did]))

    if show_access_bool:
        for did in sorted(access_ids, key=lambda x: mist_device_name(devices[x]).lower()):
            pod = pod_by_device.get(did)
            if not pod:
                continue

            pods.setdefault(pod, {"Distribution": [], "Access": [], "AP": []})
            pods[pod]["Access"].append((did, devices[did]))

    if show_aps_bool:
        for did in sorted(ap_ids, key=lambda x: mist_device_name(devices[x]).lower()):
            pod = pod_by_device.get(did)
            if not pod:
                continue

            pods.setdefault(pod, {"Distribution": [], "Access": [], "AP": []})
            pods[pod]["AP"].append((did, devices[did]))

    # Remove empty pods.
    pods = {
        pod: data
        for pod, data in pods.items()
        if data["Distribution"] or data["Access"] or data["AP"]
    }

    return fabric, pods, pod_by_device, role_by_device


def mist_pod_drilldown_card(devices, edges, selected_id, pod_by_device, role_by_device, node_url_params):
    if not selected_id:
        return (
            '<div class="mist-detail-card empty">'
            '<b>Mist drilldown:</b> Click a fabric, distribution, access switch, or AP to inspect visible neighbors and ports.'
            '</div>'
        )

    try:
        selected_id = int(selected_id)
    except Exception:
        return '<div class="mist-detail-card empty">Invalid selected device.</div>'

    d = devices.get(selected_id)
    if not d:
        return '<div class="mist-detail-card empty">Selected device is not visible in this Mist POD view.</div>'

    name = mist_device_name(d)
    role = role_by_device.get(selected_id) or final_vmap_role(d)
    pod = pod_by_device.get(selected_id) or ("Fabric Core" if role == "Fabric Core" else "Unknown POD")
    hw = str(d.get("hardware") or d.get("os") or "")

    rows = []
    neighbor_count = 0
    link_count = 0
    collapsed_downstream_count = 0

    for e in edges:
        other_id = mist_edge_other_id(e, selected_id)
        if other_id is None or other_id not in devices:
            continue

        other_role = role_by_device.get(other_id)
        if not other_role:
            continue

        selected_role = role_by_device.get(selected_id) or final_vmap_role(d)

        # If selecting fabric/distribution/services, don't list every downstream edge/AP row.
        # The path card carries the useful chain; this card should not become a phone book.
        if selected_role in ("Fabric Core", "Distribution", "Services", "Non-Mist Infra") and other_role in ("Access", "AP"):
            collapsed_downstream_count += 1
            continue

        other = devices[other_id]
        other_name = mist_device_name(other)
        other_pod = pod_by_device.get(other_id) or ("Fabric Core" if other_role == "Fabric Core" else "Unknown")
        local_port, remote_port = mist_edge_ports_for_device(e, selected_id)

        neighbor_count += 1
        link_count += 1

        rows.append(
            "<tr>"
            f"<td>{h(other_name)}</td>"
            f"<td>{h(other_pod)}</td>"
            f"<td>{h(other_role)}</td>"
            f"<td>{h(local_port)}</td>"
            f"<td>{h(remote_port)}</td>"
            "</tr>"
        )

    if collapsed_downstream_count:
        rows.append(
            f'<tr><td colspan="5"><b>{collapsed_downstream_count}</b> downstream edge/access or AP neighbors collapsed. '
            'Select an edge/access switch to inspect its exact upstream path.</td></tr>'
        )

    if not rows:
        rows.append('<tr><td colspan="5">No visible Mist neighbors in this filtered view.</td></tr>')

    clear_params = dict(node_url_params or {})
    clear_params.pop("selected", None)
    clear_params.pop("focus", None)
    clear_url = "/tools/topology-vmap?" + urlencode(clear_params)

    lookup_url = "/tools/lookup?q=" + quote(name)

    return (
        '<div class="mist-detail-card">'
        '<div class="mist-detail-head">'
        '<div>'
        f'<h2>{h(name)}</h2>'
        f'<p>{h(pod)} · {h(role)} · {h(hw)}</p>'
        '</div>'
        '<div class="mist-detail-actions">'
        f'<a class="button" href="{h(clear_url)}">Clear Selection</a>'
        f'<a class="button" href="{h(lookup_url)}">Open Lookup</a>'
        '</div>'
        '</div>'
        '<div class="mist-detail-metrics">'
        f'<span>Visible links <b>{link_count}</b></span>'
        f'<span>Visible neighbors <b>{neighbor_count}</b></span>'
        '</div>'
        '<table class="mist-detail-table">'
        '<thead><tr><th>Neighbor</th><th>POD</th><th>Role</th><th>Local port</th><th>Remote port</th></tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody>'
        '</table>'
        '</div>'
    )



def build_mist_upstream_path(selected_id, visible_edges, devices, role_by_device):
    """
    Build an upstream Mist path for selected nodes.

    AP -> Access -> Distribution -> Fabric Core
    Access -> Distribution -> Fabric Core
    Distribution -> Fabric Core

    Returns:
      path_nodes: set[int]
      path_edges: set[tuple[int, int]]
      path_rows: list[dict]
    """
    path_nodes = set()
    path_edges = set()
    path_rows = []

    try:
        selected_id = int(selected_id)
    except Exception:
        return path_nodes, path_edges, path_rows

    if selected_id not in devices:
        return path_nodes, path_edges, path_rows

    def edge_key(a, b):
        return tuple(sorted((int(a), int(b))))

    adjacency = defaultdict(list)
    for e in visible_edges:
        a = int(e.get("a"))
        b = int(e.get("b"))

        if a not in devices or b not in devices:
            continue

        adjacency[a].append((b, e))
        adjacency[b].append((a, e))

    def add_step(src, dst, e, label):
        src = int(src)
        dst = int(dst)

        path_nodes.add(src)
        path_nodes.add(dst)
        path_edges.add(edge_key(src, dst))

        local_port, remote_port = mist_edge_ports_for_device(e, src)

        path_rows.append({
            "step": label,
            "from": mist_device_name(devices[src]),
            "from_role": role_by_device.get(src) or final_vmap_role(devices[src]),
            "to": mist_device_name(devices[dst]),
            "to_role": role_by_device.get(dst) or final_vmap_role(devices[dst]),
            "local_port": local_port,
            "remote_port": remote_port,
        })

    selected_role = role_by_device.get(selected_id)
    path_nodes.add(selected_id)

    # AP starts by walking to access.
    access_ids = set()
    if selected_role == "AP":
        for nbr, e in adjacency.get(selected_id, []):
            if role_by_device.get(nbr) == "Access":
                add_step(selected_id, nbr, e, "AP to access")
                access_ids.add(nbr)

    elif selected_role == "Access":
        access_ids.add(selected_id)

    # Access walks to distribution.
    dist_ids = set()
    for access_id in access_ids:
        for nbr, e in adjacency.get(access_id, []):
            if role_by_device.get(nbr) == "Distribution":
                add_step(access_id, nbr, e, "Access to distribution")
                dist_ids.add(nbr)

    if selected_role == "Distribution":
        dist_ids.add(selected_id)

    # Distribution walks to fabric core.
    fabric_ids = set()
    for dist_id in dist_ids:
        for nbr, e in adjacency.get(dist_id, []):
            if role_by_device.get(nbr) == "Fabric Core":
                add_step(dist_id, nbr, e, "Distribution to fabric")
                fabric_ids.add(nbr)

    if selected_role == "Fabric Core":
        fabric_ids.add(selected_id)

    service_ids = set()

    # Fabric walks to Services so the selected path shows the bridge point.
    for fabric_id in list(fabric_ids):
        for nbr, e in adjacency.get(fabric_id, []):
            if role_by_device.get(nbr) == "Services":
                add_step(fabric_id, nbr, e, "Fabric to services")
                service_ids.add(nbr)

    # Services walk to Non-Mist Infra.
    for svc_id in list(service_ids):
        for nbr, e in adjacency.get(svc_id, []):
            if role_by_device.get(nbr) == "Non-Mist Infra":
                add_step(svc_id, nbr, e, "Services to non-Mist infra")

    # If a service switch is selected, walk backward to fabric and forward to non-Mist.
    if selected_role == "Services":
        service_ids.add(selected_id)
        for nbr, e in adjacency.get(selected_id, []):
            if role_by_device.get(nbr) == "Fabric Core":
                add_step(selected_id, nbr, e, "Services to fabric")
            elif role_by_device.get(nbr) == "Non-Mist Infra":
                add_step(selected_id, nbr, e, "Services to non-Mist infra")

    # If a non-Mist device is selected, walk back toward Services.
    if selected_role == "Non-Mist Infra":
        for nbr, e in adjacency.get(selected_id, []):
            if role_by_device.get(nbr) == "Services":
                add_step(selected_id, nbr, e, "Non-Mist infra to services")

    # For fabric selection, show directly connected distribution switches,
    # but do not explode into every access switch. We are making a map,
    # not recreating every closet in Vermont.
    if selected_role == "Fabric Core":
        for nbr, e in adjacency.get(selected_id, []):
            if role_by_device.get(nbr) == "Distribution":
                add_step(selected_id, nbr, e, "Fabric to distribution")

    return path_nodes, path_edges, path_rows


def mist_upstream_path_table(path_rows):
    if not path_rows:
        return (
            '<div class="mist-path-card">'
            '<h3>Upstream path</h3>'
            '<p>No upstream Mist path found in the visible map.</p>'
            '</div>'
        )

    rows = []
    for r in path_rows:
        rows.append(
            "<tr>"
            f"<td>{h(r.get('step'))}</td>"
            f"<td>{h(r.get('from'))}<br><span>{h(r.get('from_role'))}</span></td>"
            f"<td>{h(r.get('local_port'))}</td>"
            f"<td>{h(r.get('to'))}<br><span>{h(r.get('to_role'))}</span></td>"
            f"<td>{h(r.get('remote_port'))}</td>"
            "</tr>"
        )

    return (
        '<div class="mist-path-card">'
        '<h3>Upstream path</h3>'
        '<table class="mist-path-table">'
        '<thead><tr><th>Step</th><th>From</th><th>Local port</th><th>To</th><th>Remote port</th></tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody>'
        '</table>'
        '</div>'
    )


def is_non_mist_infra_for_pod_map(d):
    """
    Right side of the Mist POD view:
    legacy/non-Mist core, aggregation, firewalls, edge routers, racktop infra.
    """
    name = mist_device_name(d).lower()
    role = final_vmap_role(d)

    if role == "Services":
        return False

    if "fabric-core" in name:
        return False

    if name.startswith("dist-") or "dist-" in name:
        return False

    if role == "Access / Leaf" or role == "AP":
        return False

    non_mist_terms = [
        "dfl-core",
        "vtr-core",
        "aggregation-a",
        "aggregation-b",
        "aggregation",
        "edgefw",
        "edge-fw",
        "vpn-fw",
        "enclave-fw",
        "dc-fw",
        "miis-fw",
        "racktop",
        "turbo-vtr",
        "zuma-dfl",
        "bonefish",
        "browntrout",
        "calamari",
        "firewall",
    ]

    if any(t in name for t in non_mist_terms):
        return True

    if role in ("Core Infrastructure", "Distribution"):
        return True

    return False


def mist_edge_color_for_map(a, b, devices, role_by_device):
    """
    Consistent link colors for the Mist POD map.

    Mist side = green
    Service bridge = purple
    Non-Mist side = cyan/blue
    Unknown/background = slate
    """
    a = int(a)
    b = int(b)

    a_role = role_by_device.get(a) or final_vmap_role(devices.get(a, {}))
    b_role = role_by_device.get(b) or final_vmap_role(devices.get(b, {}))

    roles = {a_role, b_role}

    if "Services" in roles:
        return "#a78bfa"   # purple bridge

    if "Non-Mist Infra" in roles:
        return "#38bdf8"   # cyan/blue non-Mist side

    if roles & {"Fabric Core", "Distribution", "Access", "AP"}:
        return "#22c55e"   # green Mist side

    return "#64748b"



def is_leftover_non_mist_switch_for_pod_map(d, did, pod_by_device, role_by_device):
    """
    Include real network switches that are not already assigned to a Mist POD.

    This catches legacy/non-Mist access, closet, aggregation, racktop, and
    campus switches that are still real infrastructure but are not part of
    the Middlebury Mist POD grouping.
    """
    did = int(did)
    role = role_by_device.get(did) or final_vmap_role(d)
    name = mist_device_name(d).lower()
    text = " ".join(
        str(d.get(k) or "")
        for k in ("hostname", "sysName", "display_name", "hardware", "os", "type", "role")
    ).lower()

    if role in ("Fabric Core", "Distribution", "Services", "AP"):
        return False

    # If it already inherited a Mist POD, it belongs on the Mist side.
    if pod_by_device.get(did) in ("East POD", "North POD", "South POD", "West POD", "Test POD", "Other POD"):
        return False

    reject_terms = [
        "solidserver",
        "solid server",
        "virtual machine",
        "vmware",
        "windows",
        "linux",
        "server",
        "avaya",
        "ip office",
        "tlrs",
        "eduroam",
        "printer",
        "camera",
        "ups",
        "pdu",
        "generic",
        "no idea",
    ]

    if any(t in text for t in reject_terms):
        return False

    switch_terms = [
        "juniper",
        "ex2200",
        "ex2300",
        "ex3300",
        "ex3400",
        "ex4100",
        "ex4200",
        "ex4300",
        "ex4400",
        "qfx",
        "virtual chassis",
        "hpe anw",
        "aruba",
        "procurve",
        "2930",
        "2920",
        "5400",
        "6100",
        "6200",
        "6300",
        "6400",
        "vsf stack",
        "switch",
    ]

    if any(t in text for t in switch_terms):
        return True

    # Name hints for real switching infra that sometimes has bland hardware strings.
    name_terms = [
        "racktop",
        "aggregation",
        "access",
        "core",
        "dist",
        "sw-",
        "-sw",
        "-cx",
    ]

    return any(t in name for t in name_terms)



def topo_identity_keys_for_dedupe(d):
    """
    Stable identity keys for de-duping Mist and LibreNMS views of the same device.
    Uses names/hostnames/shortnames because LibreNMS device_id and Mist IDs are not the same universe.
    """
    keys = set()

    for k in ("device_id", "id"):
        v = d.get(k)
        if v is not None and str(v).strip():
            keys.add("id:" + str(v).strip().lower())

    for k in ("display_name", "hostname", "sysName", "name", "device_name"):
        v = d.get(k)
        if not v:
            continue

        raw = str(v).strip().lower()
        if not raw:
            continue

        keys.add("name:" + raw)

        short = raw.split(".")[0]
        if short:
            keys.add("short:" + short)

        compact = re.sub(r"[^a-z0-9]+", "", raw)
        if compact:
            keys.add("compact:" + compact)

    return keys




def build_mist_identity_set_for_dedupe(devices, pod_by_device, role_by_device):
    """
    Build identity keys for devices that truly belong to the Mist side.

    Important:
    All Mist devices may also exist in LibreNMS, but not every LibreNMS
    switch is Mist. So do NOT treat every Access / Leaf device as Mist.

    Mist identity means:
    - fabric-core devices
    - dist-* Mist distribution devices
    - access/AP devices that inherited a Mist POD
    """
    mist_keys = set()
    mist_ids = set()

    mist_pods = {
        "East POD",
        "North POD",
        "South POD",
        "West POD",
        "Test POD",
        "Other POD",
    }

    for did, d in devices.items():
        did = int(did)
        role = role_by_device.get(did)
        pod = pod_by_device.get(did)
        name = mist_device_name(d).lower()

        is_mist_fabric = role == "Fabric Core" and "fabric-core" in name
        is_mist_distribution = role == "Distribution" and (
            name.startswith("dist-") or "dist-" in name or pod in mist_pods
        )

        # Access/AP only count as Mist if they were actually assigned to a Mist POD.
        is_mist_edge = role in ("Access", "AP") and pod in mist_pods

        if is_mist_fabric or is_mist_distribution or is_mist_edge:
            mist_ids.add(did)
            mist_keys.update(topo_identity_keys_for_dedupe(d))

    return mist_ids, mist_keys



def device_matches_mist_identity_for_dedupe(d, did, mist_ids, mist_keys):
    did = int(did)

    if did in mist_ids:
        return True

    keys = topo_identity_keys_for_dedupe(d)
    return bool(keys & mist_keys)


def is_mist_side_device_for_pod_map(d, did, pod_by_device, role_by_device):
    """
    True only for devices that belong on the Mist side of this map.

    Important:
    A device being in LibreNMS does NOT make it non-Mist.
    A device being Access / Leaf does NOT automatically make it Mist.
    It must be assigned to a Mist POD or be one of the Mist fabric/dist devices.
    """
    did = int(did)
    name = mist_device_name(d).lower()
    role = role_by_device.get(did)
    pod = pod_by_device.get(did)

    mist_pods = {
        "East POD",
        "North POD",
        "South POD",
        "West POD",
        "Test POD",
        "Other POD",
    }

    if role == "Fabric Core" and "fabric-core" in name:
        return True

    if role == "Distribution" and (name.startswith("dist-") or "dist-" in name):
        return True

    if role in ("Access", "AP") and pod in mist_pods:
        return True

    return False


def should_include_non_mist_for_pod_map(d, did, pod_by_device, role_by_device):
    """
    Include real network infrastructure on the non-Mist side if it is not already
    assigned to the Mist side.

    This catches LibreNMS-only campus/core/firewall/legacy switches without
    duplicating Mist devices that LibreNMS also knows about.
    """
    did = int(did)

    if is_mist_side_device_for_pod_map(d, did, pod_by_device, role_by_device):
        return False

    name = mist_device_name(d).lower()
    role = final_vmap_role(d)

    text = " ".join(
        str(d.get(k) or "")
        for k in ("hostname", "sysName", "display_name", "hardware", "os", "type", "role")
    ).lower()

    reject_terms = [
        "solidserver",
        "solid server",
        "virtual machine",
        "vmware",
        "hyper-v",
        "windows",
        "linux",
        "server",
        "avaya",
        "ip office",
        "tlrs",
        "eduroam",
        "printer",
        "camera",
        "ups",
        "pdu",
        "generic",
        "no idea",
    ]

    if any(t in text for t in reject_terms):
        return False

    explicit_non_mist_terms = [
        "dfl-core",
        "vtr-core",
        "turbo-vtr",
        "zuma-dfl",
        "aggregation-a",
        "aggregation-b",
        "bonefish",
        "browntrout",
        "calamari",
        "racktop",
        "edgefw",
        "edge-fw",
        "vpn-fw",
        "enclave-fw",
        "dc-fw",
        "miis-fw",
        "monterey",
        "dcoffice",
        "dcoffice-h",
    ]

    if any(t in name for t in explicit_non_mist_terms):
        return True

    switch_terms = [
        "juniper",
        "ex2200",
        "ex2300",
        "ex3300",
        "ex3400",
        "ex4100",
        "ex4200",
        "ex4300",
        "ex4400",
        "qfx",
        "virtual chassis",
        "hpe anw",
        "aruba",
        "procurve",
        "2930",
        "2920",
        "5400",
        "6100",
        "6200",
        "6300",
        "6400",
        "vsf stack",
        "switch",
    ]

    if any(t in text for t in switch_terms):
        return True

    if role in ("Core Infrastructure", "Distribution", "Access / Leaf"):
        return True

    return False




def firewall_subsection_for_pod_map(d):
    name = mist_device_name(d).lower()

    if "edgefw" in name or "edge-fw" in name:
        return "Edge Firewalls"

    if "vpn-fw" in name:
        return "VPN Firewalls"

    if "enclave-fw" in name:
        return "Enclave Firewalls"

    if "dc-fw" in name or "miis-fw" in name:
        return "DC / MIIS Firewalls"

    if "firewall" in name or "-fw" in name or "fw-" in name:
        return "Other Firewalls"

    return None


def non_mist_section_for_pod_map(d):
    name = mist_device_name(d).lower()
    role = final_vmap_role(d)

    fw_section = firewall_subsection_for_pod_map(d)
    if fw_section:
        return fw_section

    if any(t in name for t in (
        "zuma-dfl",
        "turbo-vtr",
        "daisy-dfl",
        "chester-vtr",
        "mx204",
        "jnp204",
    )):
        return "WAN / Internet Edge"

    if any(t in name for t in (
        "dfl-core",
        "vtr-core",
    )):
        return "Campus Core"

    if any(t in name for t in (
        "bonefish",
        "browntrout",
        "calamari",
        "dfl-dc-racktop",
        "vtr-racktop",
        "racktop",
        "dfl-dc-mgmt",
    )):
        return "Legacy DC / Racktop"

    if any(t in name for t in (
        "aggregation-a",
        "aggregation-b",
        "aggregation",
    )):
        return "Legacy Aggregation"

    if name.startswith("bl-") or "breadloaf" in name or "bread-loaf" in name:
        return "Bread Loaf / Remote Campus"

    if any(t in name for t in (
        "snowbowl",
        "sb-",
    )):
        return "Snowbowl"

    if any(t in name for t in (
        "monterey",
        "miis",
        "munras",
        "ca-787",
        "ca-",
    )):
        return "Monterey / CA"

    if any(t in name for t in (
        "dcoffice",
        "dc-office",
        "washington",
    )):
        return "DC Office"

    if any(t in name for t in (
        "lib-av",
        "-av-",
        "av-",
        "theater",
        "theatre",
        "pbx",
    )):
        return "AV / Specialty Switches"

    if any(t in name for t in (
        "700es",
        "adk",
        "battell",
        "brackett",
        "carr",
        "chellis",
        "college",
        "courtst",
        "farrell",
        "gifford",
        "hadley",
        "hepburn",
        "hillcrest",
        "johnson",
        "mccullough",
        "mead",
        "munroe",
        "painter",
        "palmer",
        "perkins",
        "prescott",
        "proctor",
        "raj",
        "recycling",
        "ridgeline",
        "service-building",
        "services-",
        "wright",
        "longwell",
        "bowker",
        "brooker",
        "homer",
        "laketrout",
        "library",
    )):
        return "Legacy Access / Building Switches"

    if role in ("Core Infrastructure", "Distribution"):
        return "Other Non-Mist Switches"

    return "Other Non-Mist Switches"


def non_mist_sort_key(d):
    name = mist_device_name(d).lower()
    section = non_mist_section_for_pod_map(d)

    section_order = {
        "WAN / Internet Edge": 1,
        "Campus Core": 2,
        "Legacy DC / Racktop": 3,
        "Legacy Aggregation": 4,
        "Edge Firewalls": 5,
        "VPN Firewalls": 6,
        "Enclave Firewalls": 7,
        "DC / MIIS Firewalls": 8,
        "Other Firewalls": 9,
        "Legacy Access / Building Switches": 10,
        "Bread Loaf / Remote Campus": 11,
        "Snowbowl": 12,
        "Monterey / CA": 13,
        "DC Office": 14,
        "AV / Specialty Switches": 15,
        "Other Non-Mist Switches": 16,
    }

    priority = 50

    priority_terms = [
        ("zuma-dfl", 1),
        ("turbo-vtr", 2),
        ("daisy-dfl", 3),
        ("chester-vtr", 4),

        ("dfl-core", 1),
        ("vtr-core", 2),

        ("dfl-dc-racktop", 1),
        ("vtr-racktop", 2),
        ("bonefish", 3),
        ("browntrout", 4),
        ("calamari", 5),

        ("aggregation-a", 1),
        ("aggregation-b", 2),

        ("edgefw-dfl", 1),
        ("edgefw-voter", 2),
        ("vpn-fw-dfl", 1),
        ("vpn-fw-vtr", 2),
        ("enclave-fw-dfl", 1),
        ("enclave-fw-voter", 2),
        ("dc-fw1", 1),
        ("dc-fw2", 2),
        ("miis-fw1", 3),
        ("miis-fw2", 4),
    ]

    for term, value in priority_terms:
        if term in name:
            priority = value
            break

    return (section_order.get(section, 99), priority, name)

def render_mist_pods_map_page(devices, edges, show_ports=False, show_access=True, show_aps=False, flow_mode=True, redundant_only=True, tv_mode=False, selected="", node_url_params=None):
    """
    Mist-only POD layout.
    Fabric core on the left, then PODs left-to-right.
    Non-Mist is intentionally excluded for now.
    """
    show_non_mist_access_bool = str((node_url_params or {}).get("show_nonmist_access", "0")) == "1"

    fabric, pods, pod_by_device, role_by_device = build_mist_pod_groups(
        devices,
        edges,
        show_access_bool=show_access,
        show_aps_bool=show_aps,
    )

    services = []
    for did, d in devices.items():
        did = int(did)
        name = mist_device_name(d).lower()
        if final_vmap_role(d) == "Services" or any(t in name for t in ("svcs-dfl", "svcs-voter", "svc-dfl", "svc-voter")):
            services.append((did, d))
            role_by_device[did] = "Services"
            pod_by_device[did] = "Services"

    services.sort(key=lambda item: mist_device_name(item[1]).lower())

    mist_ids_for_dedupe, mist_keys_for_dedupe = build_mist_identity_set_for_dedupe(
        devices,
        pod_by_device,
        role_by_device,
    )

    non_mist = []
    non_mist_seen = set()
    skipped_mist_dupes = []
    skipped_nonmist_access_count = 0

    dense_nonmist_sections = {
        "Legacy Access / Building Switches",
        "Bread Loaf / Remote Campus",
        "Snowbowl",
        "Monterey / CA",
        "DC Office",
        "AV / Specialty Switches",
        "Other Non-Mist Switches",
    }

    for did, d in devices.items():
        did = int(did)

        if is_mist_side_device_for_pod_map(d, did, pod_by_device, role_by_device):
            skipped_mist_dupes.append(mist_device_name(d))
            continue

        if not should_include_non_mist_for_pod_map(d, did, pod_by_device, role_by_device):
            continue

        section = non_mist_section_for_pod_map(d)

        if section in dense_nonmist_sections and not show_non_mist_access_bool:
            skipped_nonmist_access_count += 1
            continue

        if did in non_mist_seen:
            continue

        non_mist_seen.add(did)
        non_mist.append((did, d))
        role_by_device[did] = "Non-Mist Infra"
        pod_by_device[did] = section

    non_mist.sort(key=lambda item: non_mist_sort_key(item[1]))

    try:
        selected_id = int(selected) if str(selected or "").strip() else None
    except Exception:
        selected_id = None

    node_w = 218
    node_h = 50
    ap_w = 150
    ap_h = 26

    x0 = 70
    y0 = 145
    fabric_w = 245
    pod_w = 505
    pod_gap = 34
    section_gap = 18
    card_gap = 12

    if tv_mode:
        node_w = 265
        node_h = 60
        pod_w = 660
        pod_gap = 60
        x0 = 95
        y0 = 160

    pos = {}
    visible_ids = set()

    # Fabric column.
    fabric_x = x0
    fabric_y = y0 + 38

    for idx, (did, d) in enumerate(fabric):
        x = fabric_x
        y = fabric_y + idx * (node_h + card_gap)
        pos[did] = {"x": x, "y": y, "w": node_w, "h": node_h, "role": "Fabric Core", "pod": "Fabric Core", "device": d}
        visible_ids.add(did)

    # POD columns.
    pod_x_start = x0 + fabric_w + pod_gap
    pod_bottoms = [fabric_y + max(1, len(fabric)) * (node_h + card_gap)]

    ordered_pods = []
    for pod in ["East POD", "North POD", "South POD", "West POD", "Test POD", "Other POD"]:
        if pod in pods:
            ordered_pods.append(pod)

    for pod_idx, pod in enumerate(ordered_pods):
        data = pods[pod]
        px = pod_x_start + pod_idx * (pod_w + pod_gap)

        y = y0 + 38

        # Distribution section.
        for idx, (did, d) in enumerate(data["Distribution"]):
            col = idx % 2
            row = idx // 2
            x = px + col * (node_w + card_gap)
            yy = y + row * (node_h + card_gap)
            pos[did] = {"x": x, "y": yy, "w": node_w, "h": node_h, "role": "Distribution", "pod": pod, "device": d}
            visible_ids.add(did)

        dist_rows = max(1, (len(data["Distribution"]) + 1) // 2)
        y += dist_rows * (node_h + card_gap) + section_gap

        # Access section.
        if show_access:
            for idx, (did, d) in enumerate(data["Access"]):
                col = idx % 2
                row = idx // 2
                x = px + col * (node_w + card_gap)
                yy = y + row * (node_h + card_gap)
                pos[did] = {"x": x, "y": yy, "w": node_w, "h": node_h, "role": "Access", "pod": pod, "device": d}
                visible_ids.add(did)

            access_rows = max(0, (len(data["Access"]) + 1) // 2)
            y += access_rows * (node_h + card_gap) + section_gap

        # AP section, compact cards.
        if show_aps:
            for idx, (did, d) in enumerate(data["AP"]):
                col = idx % 3
                row = idx // 3
                x = px + col * (ap_w + 12)
                yy = y + row * (ap_h + 10)
                pos[did] = {"x": x, "y": yy, "w": ap_w, "h": ap_h, "role": "AP", "pod": pod, "device": d}
                visible_ids.add(did)

            ap_rows = max(0, (len(data["AP"]) + 2) // 3)
            y += ap_rows * (ap_h + 10) + section_gap

        pod_bottoms.append(y)

    # Services block sits to the right of the Mist PODs.
    services_x = pod_x_start + max(1, len(ordered_pods)) * (pod_w + pod_gap)
    services_w = 260
    services_y = y0 + 78

    for idx, (did, d) in enumerate(services):
        x = services_x
        y = services_y + idx * (node_h + card_gap)
        pos[did] = {"x": x, "y": y, "w": node_w, "h": node_h, "role": "Services", "pod": "Services", "device": d}
        visible_ids.add(did)
        pod_bottoms.append(y + node_h)

    # Non-Mist / old WAN design sits to the right of the Services block.
    # Layout mirrors the uploaded "New WAN with IPs" drawing:
    # DFL on the left, Voter on the right, WAN/clouds up top, routers, Daisy/Chester,
    # firewalls, then legacy cores at the bottom. Access/site inventory remains optional.
    non_mist_x = services_x + services_w + pod_gap
    non_mist_y = y0 + 36

    non_mist_by_section = defaultdict(list)
    non_mist_used = set()

    for did, d in non_mist:
        section = non_mist_section_for_pod_map(d)
        non_mist_by_section[section].append((did, d))

    for section in non_mist_by_section:
        non_mist_by_section[section].sort(key=lambda item: non_mist_sort_key(item[1]))

    non_mist_panels = []
    non_mist_annotations = []
    non_mist_links = []

    def _norm_device_name(d):
        return mist_device_name(d).lower()

    def take_non_mist_device(*terms):
        terms_l = [str(t).lower() for t in terms if str(t).strip()]

        for did, d in non_mist:
            if did in non_mist_used:
                continue

            name = _norm_device_name(d)
            if any(t in name for t in terms_l):
                non_mist_used.add(did)
                return did, d

        return None

    def add_panel(title, x, y, w, h, cls="nonmist-panel nonmist-core-panel"):
        non_mist_panels.append({
            "section": title,
            "class": cls,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
        })

    def add_note(text, x, y, cls="wan-note"):
        non_mist_annotations.append({
            "kind": "text",
            "text": text,
            "x": x,
            "y": y,
            "class": cls,
        })

    def add_cloud(text, x, y, w=150, h=54):
        non_mist_annotations.append({
            "kind": "cloud",
            "text": text,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "class": "wan-cloud",
        })

    def add_old_link(a_key, b_key, label="", cls="old-wan-link"):
        non_mist_links.append({
            "a": a_key,
            "b": b_key,
            "label": label,
            "class": cls,
        })

    old_pos_keys = {}

    def place_named(key, terms, x, y, role="Old WAN", pod="Old WAN"):
        hit = take_non_mist_device(*terms)
        if not hit:
            return None

        did, d = hit
        pos[did] = {
            "x": x,
            "y": y,
            "w": node_w,
            "h": node_h,
            "role": role,
            "pod": pod,
            "device": d,
        }
        visible_ids.add(did)
        pod_bottoms.append(y + node_h)
        old_pos_keys[key] = did
        return did

    old_panel_x = non_mist_x - 18
    old_panel_y = non_mist_y - 28
    old_panel_w = 1285
    old_panel_h = 620

    dfl_x = non_mist_x + 30
    voter_x = non_mist_x + 630
    top_y = non_mist_y + 96
    mid_y = non_mist_y + 245
    fw_y = non_mist_y + 390
    core_y = non_mist_y + 535

    # No outer Old WAN container panel here.
    # The old-side nodes should float organically beside Services; grouped remainder panels stay on the far right.

    # Region labels and split line.
    add_note("DFL", dfl_x + 180, non_mist_y - 2, "wan-region-title")
    add_note("Voter", voter_x + 190, non_mist_y - 2, "wan-region-title")

    non_mist_annotations.append({
        "kind": "vline",
        "x": non_mist_x + 590,
        "y1": non_mist_y + 8,
        "y2": non_mist_y + 575,
        "class": "wan-split",
    })

    # Clouds and WAN notes from the PDF.
    add_cloud("FirstLight\n216.238.164.73", dfl_x + 8, non_mist_y + 22)
    add_cloud("UVM/I2\n132.198.255.17\n132.298.255.213", voter_x + 4, non_mist_y + 22, 190, 66)
    add_cloud("Lumen\n4.16.160.29", voter_x + 370, non_mist_y + 22)

    # Core WAN path devices.
    place_named("zuma", ("zuma-dfl",), dfl_x + 4, top_y, role="WAN Edge", pod="WAN / Internet Edge")
    place_named("turbo", ("turbo-vtr",), voter_x + 245, top_y, role="WAN Edge", pod="WAN / Internet Edge")

    place_named("daisy", ("daisy-dfl",), dfl_x + 4, mid_y, role="WAN Distribution", pod="WAN / Internet Edge")
    place_named("chester", ("chester-vtr",), voter_x + 245, mid_y, role="WAN Distribution", pod="WAN / Internet Edge")

    place_named("vpn_dfl", ("vpnfw dfl", "vpn-fw-dfl", "vpnfw-dfl"), dfl_x - 80, fw_y, role="Firewall", pod="VPN Firewalls")
    place_named("edge_dfl", ("edgefw dfl", "edgefw-dfl", "edge-fw-dfl"), dfl_x + 205, fw_y, role="Firewall", pod="Edge Firewalls")
    place_named("edge_vtr", ("edgefw voter", "edgefw-voter", "edgefw vtr", "edge-fw-vtr"), voter_x + 165, fw_y, role="Firewall", pod="Edge Firewalls")
    place_named("vpn_vtr", ("vpnfw vtr", "vpn-fw-vtr", "vpnfw-vtr"), voter_x + 450, fw_y, role="Firewall", pod="VPN Firewalls")

    place_named("core_dfl", ("dfl-core",), dfl_x + 205, core_y, role="Legacy Core", pod="Campus Core")
    place_named("core_vtr", ("vtr-core",), voter_x + 165, core_y, role="Legacy Core", pod="Campus Core")

    # In-diagram IP/VRRP labels.
    add_note("lo0 140.233.5.225/32", dfl_x - 160, top_y + 38, "wan-ip magenta")
    add_note("lo0 140.233.5.226/32", voter_x + 520, top_y + 38, "wan-ip magenta")

    add_note("140.233.5.18/28", dfl_x - 58, top_y + 86, "wan-ip blue")
    add_note("140.233.5.2/28", dfl_x + 175, top_y + 86, "wan-ip green")
    add_note("140.233.5.3/28", voter_x + 225, top_y + 86, "wan-ip green")
    add_note("140.233.5.19/28", voter_x + 440, top_y + 86, "wan-ip blue")

    add_note("VRRP 140.233.5.1/28", non_mist_x + 390, top_y + 115, "wan-ip green")
    add_note("VRRP 140.233.5.20/28", non_mist_x + 390, top_y + 145, "wan-ip blue")

    add_note("140.233.5.21/28", dfl_x - 116, fw_y - 14, "wan-ip blue")
    add_note("140.233.5.4/28", dfl_x + 225, fw_y - 14, "wan-ip green")
    add_note("140.233.102.30/29", dfl_x + 220, fw_y + 70, "wan-ip magenta")
    add_note("140.233.9.130", dfl_x + 225, fw_y + 92, "wan-ip purple")

    add_note("140.233.9.3/24", dfl_x + 250, core_y + 52, "wan-ip purple")
    add_note("140.233.9.2/24", voter_x + 210, core_y + 52, "wan-ip purple")
    add_note("VRRP 140.233.9.9/24", non_mist_x + 390, core_y + 102, "wan-ip purple")
    add_note("VRRP Not configured", non_mist_x + 390, core_y + 128, "wan-ip magenta")
    add_note("VRRP Not configured", non_mist_x + 390, core_y + 154, "wan-ip orange")

    # Manual visual links to make the old side read like the uploaded WAN drawing.
    add_old_link("zuma", "turbo", "iBGP 40g", "old-wan-link green")
    add_old_link("zuma", "daisy", "Trunk 40G", "old-wan-link blue")
    add_old_link("turbo", "chester", "Trunk 40G", "old-wan-link green")
    add_old_link("daisy", "chester", "100g Trunk", "old-wan-link green thick")
    add_old_link("daisy", "vpn_dfl", "10g", "old-wan-link blue")
    add_old_link("chester", "vpn_vtr", "10g vlan512", "old-wan-link blue")
    add_old_link("daisy", "edge_dfl", "10g", "old-wan-link green")
    add_old_link("chester", "edge_vtr", "10g", "old-wan-link green")
    add_old_link("edge_dfl", "edge_vtr", "HA", "old-wan-link")
    add_old_link("edge_dfl", "core_dfl", "40g", "old-wan-link purple")
    add_old_link("edge_vtr", "core_vtr", "40g", "old-wan-link purple")
    add_old_link("vpn_dfl", "core_dfl", "10g", "old-wan-link magenta")
    add_old_link("vpn_vtr", "core_vtr", "10g", "old-wan-link orange")
    add_old_link("edge_dfl", "core_vtr", "10g", "old-wan-link magenta")
    add_old_link("vpn_vtr", "core_dfl", "10g", "old-wan-link orange")
    add_old_link("core_dfl", "core_vtr", "", "old-wan-link")

    # Any remaining design-core devices that did not have a manual slot.
    remainder_sections = [
        "Legacy DC / Racktop",
        "Legacy Aggregation",
        "Enclave Firewalls",
        "DC / MIIS Firewalls",
        "Other Firewalls",
    ]

    remainder_x = non_mist_x + old_panel_w + 55
    remainder_y = non_mist_y + 20
    remainder_cols = 2

    def place_compact_section(section, x, y, cols=2):
        rows = [
            (did, d)
            for did, d in non_mist_by_section.get(section, [])
            if did not in non_mist_used
        ]

        if not rows:
            return y

        cols = max(1, int(cols))
        row_count = max(1, (len(rows) + cols - 1) // cols)

        panel_x = x - 14
        panel_y = y - 28
        panel_w = cols * node_w + (cols - 1) * card_gap + 28
        panel_h = 36 + row_count * (node_h + card_gap) + 10

        non_mist_panels.append({
            "section": section,
            "class": "nonmist-panel nonmist-core-panel",
            "x": panel_x,
            "y": panel_y,
            "w": panel_w,
            "h": panel_h,
        })

        card_y = y + 4

        for idx, (did, d) in enumerate(rows):
            non_mist_used.add(did)
            col = idx % cols
            row = idx // cols
            xx = x + col * (node_w + card_gap)
            yy = card_y + row * (node_h + card_gap)

            pos[did] = {
                "x": xx,
                "y": yy,
                "w": node_w,
                "h": node_h,
                "role": "Non-Mist Infra",
                "pod": section,
                "device": d,
            }
            visible_ids.add(did)
            pod_bottoms.append(yy + node_h)

        return panel_y + panel_h + 18

    for section in remainder_sections:
        before_y = remainder_y
        remainder_y = place_compact_section(section, remainder_x, remainder_y, cols=remainder_cols)
        if remainder_y == before_y:
            continue

    # Optional access/site/specialty inventory. This stays separate so the old WAN design
    # does not get buried under every access switch we have ever loved and regretted.
    inventory_sections = [
        "Legacy Access / Building Switches",
        "Bread Loaf / Remote Campus",
        "Snowbowl",
        "Monterey / CA",
        "DC Office",
        "AV / Specialty Switches",
        "Other Non-Mist Switches",
    ]

    inventory_x = remainder_x + 2 * node_w + card_gap + 120
    inventory_y = non_mist_y + 20
    inventory_cols = 3

    max_inventory_bottom = inventory_y
    if show_non_mist_access_bool:
        for section in inventory_sections:
            before_y = inventory_y
            inventory_y = place_compact_section(section, inventory_x, inventory_y, cols=inventory_cols)
            if inventory_y != before_y:
                max_inventory_bottom = max(max_inventory_bottom, inventory_y)

    non_mist_w = old_panel_w + 55 + (2 * node_w + card_gap + 28)

    if show_non_mist_access_bool:
        non_mist_w += 120 + (inventory_cols * node_w + (inventory_cols - 1) * card_gap + 28)

    width = non_mist_x + non_mist_w + x0
    height = max(760, max(pod_bottoms + [old_panel_y + old_panel_h, remainder_y, max_inventory_bottom]) + 110)

    visible_edges = []
    pair_counts = defaultdict(int)

    for e in edges:
        a = int(e.get("a"))
        b = int(e.get("b"))

        if a in visible_ids and b in visible_ids:
            visible_edges.append(e)
            pair_counts[tuple(sorted((a, b)))] += 1

    selected_neighbors = set()
    path_nodes = set()
    path_edges = set()
    path_rows = []

    if selected_id in visible_ids:
        for e in visible_edges:
            other = mist_edge_other_id(e, selected_id)
            if other is not None:
                selected_neighbors.add(other)

        path_nodes, path_edges, path_rows = build_mist_upstream_path(
            selected_id,
            visible_edges,
            devices,
            role_by_device,
        )

        # Path nodes should be treated as related even if they are not direct neighbors.
        selected_neighbors.update(path_nodes)

    params = dict(node_url_params or {})
    params["layout"] = "mist_pods"

    css = """
<style>
  .mist-pods-note {
    border:1px solid #334155;
    border-radius:12px;
    padding:10px 12px;
    margin:0 0 12px 0;
    background:#0f172a;
    color:#cbd5e1;
  }

  .mist-pods-note b {
    color:#f8fafc;
  }

  .mist-detail-card {
    border:1px solid #334155;
    border-radius:14px;
    background:#0f172a;
    padding:14px;
    margin:0 0 12px 0;
    color:#e5e7eb;
  }

  .mist-detail-card.empty {
    color:#cbd5e1;
  }

  .mist-detail-head {
    display:flex;
    justify-content:space-between;
    gap:12px;
    flex-wrap:wrap;
    align-items:flex-start;
  }

  .mist-detail-head h2 {
    margin:0 0 4px 0;
    font-size:18px;
  }

  .mist-detail-head p {
    margin:0;
    color:#94a3b8;
  }

  .mist-detail-actions {
    display:flex;
    gap:8px;
    flex-wrap:wrap;
  }

  .mist-detail-metrics {
    display:flex;
    gap:8px;
    flex-wrap:wrap;
    margin:10px 0;
  }

  .mist-detail-metrics span {
    border:1px solid #334155;
    border-radius:8px;
    background:#0f172a;
    padding:6px 8px;
    color:#cbd5e1;
  }

  .mist-detail-table {
    width:100%;
    border-collapse:collapse;
    font-size:12px;
  }

  .mist-detail-table th,
  .mist-detail-table td {
    border-bottom:1px solid #334155;
    padding:6px 8px;
    text-align:left;
  }

  .mist-detail-table th {
    color:#93c5fd;
  }

  .mist-pods-scroll {
    width:100%;
    max-width:100%;
    overflow:auto;
    border:1px solid #334155;
    border-radius:14px;
    background:#0f172a;
  }

  svg.vmap-mist-pods {
    display:block;
    width:100% !important;
    height:auto !important;
    min-width:1900px;
    max-width:none !important;
  }

  svg.vmap-mist-pods .pod-bg {
    fill:#0f172a;
    stroke:#334155;
    stroke-width:1.4;
    opacity:.95;
  }


  svg.vmap-mist-pods .nonmist-panel {
    fill:#0b1220;
    stroke:#2563eb;
    stroke-width:1.2;
    opacity:.9;
  }


  svg.vmap-mist-pods .nonmist-core-panel {
    stroke:#38bdf8;
  }

  svg.vmap-mist-pods .nonmist-firewall-panel {
    stroke:#818cf8;
  }

  svg.vmap-mist-pods .pod-title {
    fill:#e5e7eb;
    font-weight:900;
    font-size:18px;
  }

  svg.vmap-mist-pods .section-title {
    fill:#93c5fd;
    font-size:11px;
    font-weight:900;
    letter-spacing:.04em;
  }

  svg.vmap-mist-pods .node.dim {
    opacity:.2;
  }

  svg.vmap-mist-pods .node.selected rect {
    stroke:#fb7185 !important;
    stroke-width:4 !important;
    filter: drop-shadow(0 0 10px #fb7185);
  }

  svg.vmap-mist-pods .node.related rect {
    stroke-width:3 !important;
    filter: drop-shadow(0 0 6px #38bdf8);
  }

  svg.vmap-mist-pods .edge.dim {
    opacity:.025;
  }

  svg.vmap-mist-pods .edge.selected-edge path {
    stroke:#fb7185 !important;
    stroke-width:4.2 !important;
    opacity:1 !important;
    filter: drop-shadow(0 0 8px #fb7185);
  }

  svg.vmap-mist-pods .edge.path-edge path {
    stroke:#fb7185 !important;
    stroke-width:4.6 !important;
    opacity:1 !important;
    filter: drop-shadow(0 0 9px #fb7185);
  }

  svg.vmap-mist-pods .node.path-node rect {
    stroke:#fb7185 !important;
    stroke-width:3.5 !important;
    filter: drop-shadow(0 0 8px #fb7185);
  }

  .mist-path-card {
    border:1px solid #334155;
    border-radius:14px;
    background:#0f172a;
    padding:14px;
    margin:0 0 12px 0;
    color:#e5e7eb;
  }

  .mist-path-card h3 {
    margin:0 0 10px 0;
    font-size:16px;
    color:#f8fafc;
  }

  .mist-path-card p {
    color:#cbd5e1;
    margin:0;
  }

  .mist-path-table {
    width:100%;
    border-collapse:collapse;
    font-size:12px;
  }

  .mist-path-table th,
  .mist-path-table td {
    border-bottom:1px solid #334155;
    padding:7px 8px;
    text-align:left;
    vertical-align:top;
  }

  .mist-path-table th {
    color:#93c5fd;
  }

  .mist-path-table span {
    color:#94a3b8;
    font-size:11px;
  }
</style>
"""

    parts = [css]
    parts.append(
        '<div class="mist-pods-note">'
        '<b>Mist POD view:</b> Fabric core is on the left. Fabric core, distribution, edge/access, and APs are grouped by POD. '
        'The Services block bridges Mist to the Non-Mist Infra block on the far right. '
        f'<span class="muted">Skipped Mist/LibreNMS duplicate devices: {h(len(skipped_mist_dupes))}</span>'
        '</div>'
    )

    nonmist_toggle_params = dict(params)
    nonmist_toggle_params["show_nonmist_access"] = "0" if show_non_mist_access_bool else "1"
    nonmist_toggle_url = "/tools/topology-vmap?" + urlencode(nonmist_toggle_params)
    nonmist_toggle_label = "Hide non-Mist access/site switches" if show_non_mist_access_bool else "Show non-Mist access/site switches"

    parts.append(
        '<div class="nonmist-toggle-bar">'
        '<b>Non-Mist display:</b> '
        f'{h(skipped_nonmist_access_count)} access/site/specialty non-Mist switches hidden. '
        f'<a href="{h(nonmist_toggle_url)}">{h(nonmist_toggle_label)}</a>'
        '</div>'
    )

    def highlight_toggle_url(key):
        hp = dict(params)
        hp[key] = "0" if str(hp.get(key, "0")) == "1" else "1"
        hp.pop("focus", None)
        hp.pop("t", None)
        return "/tools/topology-vmap?" + urlencode(hp)

    highlight_edge_on = str(params.get("highlight_edge", "0")) == "1"
    highlight_mist_on = str(params.get("highlight_mist", "0")) == "1"

    parts.append(
        '<div class="mist-highlight-bar">'
        '<b>Highlight:</b> '
        f'<a class="highlight-pill edge {"on" if highlight_edge_on else "off"}" '
        f'href="{h(highlight_toggle_url("highlight_edge"))}">'
        f'Neon Edge: {"On" if highlight_edge_on else "Off"}</a>'
        f'<a class="highlight-pill mist {"on" if highlight_mist_on else "off"}" '
        f'href="{h(highlight_toggle_url("highlight_mist"))}">'
        f'Neon Mist: {"On" if highlight_mist_on else "Off"}</a>'
        '</div>'
    )

    parts.append(mist_pod_drilldown_card(devices, visible_edges, selected_id, pod_by_device, role_by_device, params))

    parts.append(
        '<div class="mist-color-legend">'
        '<b>Link colors:</b> '
        '<span><i class="mist-color-chip" style="background:#22c55e"></i>Mist</span>'
        '<span><i class="mist-color-chip" style="background:#a78bfa"></i>Services bridge</span>'
        '<span><i class="mist-color-chip" style="background:#38bdf8"></i>Non-Mist</span>'
        '<span><i class="mist-color-chip" style="background:#fb7185"></i>Selected path</span>'
        '</div>'
    )

    if selected_id in visible_ids:
        parts.append(mist_upstream_path_table(path_rows))

    parts.append('<div class="mist-pods-scroll">')
    parts.append(
        f'<svg class="vmap vmap-mist-pods" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
    )

    if show_non_mist_access_bool:
        parts.append(f'<text class="pod-title" x="{inventory_x}" y="42">Legacy / Site Inventory</text>')

    for panel in non_mist_panels:
        parts.append(
            f'<rect class="pod-bg {panel.get("class", "nonmist-panel")}" '
            f'x="{panel["x"]}" y="{panel["y"]}" '
            f'width="{panel["w"]}" height="{panel["h"]}" rx="14"/>'
        )
        parts.append(
            f'<text class="section-title" '
            f'x="{panel["x"] + 12}" y="{panel["y"] + 22}">'
            f'{h(str(panel["section"]).upper())}</text>'
        )


    parts.append("""
<defs>
  <marker id="flowArrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L0,6 L9,3 z" fill="#38bdf8"/>
  </marker>
</defs>
""")

    # Background groups.
    parts.append(f'<text class="pod-title" x="{fabric_x}" y="42">Mist Fabric Core</text>')
    parts.append(f'<rect class="pod-bg" x="{fabric_x - 18}" y="82" width="{fabric_w}" height="{height - 150}" rx="14"/>')
    parts.append(f'<text class="section-title" x="{fabric_x}" y="112">FABRIC</text>')

    for pod_idx, pod in enumerate(ordered_pods):
        px = pod_x_start + pod_idx * (pod_w + pod_gap)
        parts.append(f'<text class="pod-title" x="{px}" y="42">{h(pod)}</text>')
        parts.append(f'<rect class="pod-bg" x="{px - 18}" y="82" width="{pod_w + 18}" height="{height - 150}" rx="14"/>')
        parts.append(f'<text class="section-title" x="{px}" y="112">DISTRIBUTION</text>')

        data = pods[pod]
        dist_rows = max(1, (len(data["Distribution"]) + 1) // 2)
        access_label_y = y0 + 38 + dist_rows * (node_h + card_gap) + section_gap - 8

        if show_access:
            parts.append(f'<text class="section-title" x="{px}" y="{access_label_y}">EDGE / ACCESS</text>')

        if show_aps:
            # AP label placement is approximate; good enough for now, unlike most meetings.
            ap_count = len(data["AP"])
            if ap_count:
                parts.append(f'<text class="section-title" x="{px}" y="{height - 90}">APS</text>')

    def anchor(did, toward_x):
        item = pos[int(did)]
        x = item["x"]
        y = item["y"]
        w = item["w"]
        hgt = item["h"]
        cx = x + w / 2
        cy = y + hgt / 2

        if toward_x >= cx:
            return x + w, cy
        return x, cy

    edge_seen = defaultdict(int)

    # Edges first.
    for e in visible_edges:
        a = int(e.get("a"))
        b = int(e.get("b"))

        if a not in pos or b not in pos:
            continue

        key = tuple(sorted((a, b)))
        band = edge_seen[key]
        edge_seen[key] += 1

        a_cx = pos[a]["x"] + pos[a]["w"] / 2
        b_cx = pos[b]["x"] + pos[b]["w"] / 2

        ax, ay = anchor(a, b_cx)
        bx, by = anchor(b, a_cx)

        redundant = pair_counts.get(key, 0) >= 2

        stroke_width = 2.2 if redundant else .9
        opacity = .50 if redundant else .16

        if not redundant_only:
            stroke_width = 1.7
            opacity = .62

        edge_key = tuple(sorted((a, b)))
        selected_edge = selected_id is not None and (a == selected_id or b == selected_id)
        path_edge = edge_key in path_edges
        downstream_edge = False

        if selected_edge and selected_id is not None:
            other_id = b if a == selected_id else a
            selected_role_for_edge = role_by_device.get(selected_id)
            other_role_for_edge = role_by_device.get(other_id)

            if selected_role_for_edge in ("Fabric Core", "Distribution", "Services") and other_role_for_edge in ("Access", "AP"):
                downstream_edge = True

        edge_class = "edge draggable-edge"
        if path_edge:
            edge_class += " path-edge"
        elif downstream_edge:
            edge_class += " selected-downstream"
        elif selected_edge:
            edge_class += " selected-edge"
        elif selected_id is not None:
            edge_class += " dim"

        base_color = mist_edge_color_for_map(a, b, devices, role_by_device)
        color = base_color if redundant else "#64748b"
        marker_end = 'marker-end="url(#flowArrow)"' if flow_mode else ""

        offset = (band % 7 - 3) * 9

        if abs(ax - bx) < 70:
            arc_y = min(ay, by) - 55 - band * 14
            path_d = (
                f"M{ax:.1f},{ay:.1f} "
                f"C{ax + offset:.1f},{arc_y:.1f} {bx + offset:.1f},{arc_y:.1f} {bx:.1f},{by:.1f}"
            )
            label_x = (ax + bx) / 2
            label_y = arc_y - 4
        else:
            mid_x = (ax + bx) / 2
            path_d = (
                f"M{ax:.1f},{ay:.1f} "
                f"C{mid_x:.1f},{ay + offset:.1f} {mid_x:.1f},{by + offset:.1f} {bx:.1f},{by:.1f}"
            )
            label_x = mid_x
            label_y = (ay + by) / 2 - 6

        a_name = mist_device_name(devices[a])
        b_name = mist_device_name(devices[b])
        a_port = e.get("a_port") or ""
        b_port = e.get("b_port") or ""

        parts.append(
            f'<g class="{edge_class}" data-a="{a}" data-b="{b}">'
            f'<title>{h(a_name)} -> {h(b_name)} | {h(a_port)} <-> {h(b_port)}</title>'
            f'<path class="edge-path" d="{path_d}" stroke="{color}" '
            f'stroke-width="{stroke_width}" fill="none" opacity="{opacity}" {marker_end}/>'
        )

        if show_ports:
            parts.append(
                f'<text x="{label_x:.1f}" y="{label_y:.1f}" fill="#cbd5e1" '
                f'font-size="10" text-anchor="middle">{h(a_port)} ↔ {h(b_port)}</text>'
            )

        parts.append('</g>')

    # Nodes.
    for did, item in pos.items():
        d = item["device"]
        x = item["x"]
        y = item["y"]
        w = item["w"]
        hgt = item["h"]
        role = item["role"]

        stroke = "#22c55e"
        if role == "Fabric Core":
            stroke = "#38bdf8"
        elif role == "Distribution":
            stroke = "#22c55e"
        elif role == "Access":
            stroke = "#fb923c"
        elif role == "Services":
            stroke = "#a78bfa"
        elif role == "Non-Mist Infra":
            section = item.get("pod") or ""
            if section == "WAN / Internet Edge":
                stroke = "#0ea5e9"
            elif section == "Campus Core":
                stroke = "#38bdf8"
            elif section == "Legacy DC / Racktop":
                stroke = "#22d3ee"
            elif section == "Legacy Aggregation":
                stroke = "#60a5fa"
            elif section in ("Edge Firewalls", "VPN Firewalls", "Enclave Firewalls", "DC / MIIS Firewalls"):
                stroke = "#818cf8"
            elif section == "Other Non-Mist Switches":
                stroke = "#67e8f9"
            else:
                stroke = "#38bdf8"
        elif role == "AP":
            stroke = "#a78bfa"

        node_class = "node draggable-node"
        if selected_id == did:
            node_class += " selected"
        elif selected_id is not None and did in selected_neighbors:
            node_class += " related"
        elif selected_id is not None:
            node_class += " dim"

        name = mist_device_name(d)
        hw = str(d.get("hardware") or d.get("os") or role or "")

        link_params = dict(params)
        link_params["selected"] = str(did)
        url = "/tools/topology-vmap?" + urlencode(link_params)

        label = name
        if role != "AP" and is_stack_device(d) and "VC/Stack" not in label:
            label += " VC/Stack"

        max_label = 30 if role != "AP" else 22
        if len(label) > max_label:
            label = label[:max_label - 3] + "..."

        hw_label = hw
        if len(hw_label) > 36:
            hw_label = hw_label[:33] + "..."

        parts.append(f'<g class="{node_class}" data-node-id="{did}">')
        parts.append(f'<a href="{h(url)}" target="_self">')
        parts.append(f'<title>{h(name)} | {h(hw)}</title>')

        if role != "AP" and is_stack_device(d):
            parts.append(
                f'<rect class="stack-card-back" x="{x + 10}" y="{y + 10}" width="{w}" height="{hgt}" '
                f'rx="10" fill="#0f172a" stroke="{stroke}" stroke-width="1.4"/>'
            )
            parts.append(
                f'<rect class="stack-card-back" x="{x + 5}" y="{y + 5}" width="{w}" height="{hgt}" '
                f'rx="10" fill="#0f172a" stroke="{stroke}" stroke-width="1.6"/>'
            )

        parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{hgt}" rx="10" '
            f'fill="#0f172a" stroke="{stroke}" stroke-width="2"/>'
        )

        if role == "AP":
            parts.append(
                f'<text x="{x + 9}" y="{y + 18}" fill="#f8fafc" '
                f'font-size="10" font-weight="800">{h(label)}</text>'
            )
        else:
            parts.append(
                f'<text x="{x + 12}" y="{y + 22}" fill="#f8fafc" '
                f'font-size="13" font-weight="800">{h(label)}</text>'
            )
            parts.append(
                f'<text x="{x + 12}" y="{y + 42}" fill="#cbd5e1" '
                f'font-size="10">{h(hw_label)}</text>'
            )

        parts.append('</a></g>')

    parts.append('</svg>')
    parts.append('</div>')

    if "vmap_drag_script" in globals():
        parts.append(vmap_drag_script())

    return "\n".join(parts)

def render_topology_vmap_page(
    site="vt",
    show_ports="1",
    show_aps="0",
    show_core="1",
    show_services="1",
    show_distribution="1",
    show_access="1",
    flow="0",
    redundant_only="0",
    tv="0",
    layout="topdown",
    focus="",
    selected="",
    show_nonmist_access="0"):
    site = norm(site) or "vt"
    if site not in ("vt", "monterey", "all"):
        site = "vt"

    show_ports_bool = bool_param(show_ports, True)
    show_aps_bool = bool_param(show_aps, False)
    show_core_bool = bool_param(show_core, True)
    show_services_bool = bool_param(show_services, True)
    show_distribution_bool = bool_param(show_distribution, True)
    show_access_bool = bool_param(show_access, True)
    show_nonmist_access_bool = bool_param(show_nonmist_access, False)
    flow_bool = bool_param(flow, False)
    redundant_only_bool = bool_param(redundant_only, False)
    tv_bool = bool_param(tv, False)

    devices_all = load_devices(site=site, show_aps=show_aps_bool)

    devices = {
        device_id: d
        for device_id, d in devices_all.items()
        if keep_role(
            final_vmap_role(d),
            show_core=show_core_bool,
            show_services=show_services_bool,
            show_distribution=show_distribution_bool,
            show_access=show_access_bool,
            show_aps=show_aps_bool,
        )
    }

    edges = load_lldp_edges(devices)

    # MIST_PODS_LAYOUT_EARLY_RETURN
    if str(layout or "").lower() in ("mist_pods", "pods"):
        mist_params = {
            "site": site,
            "layout": "mist_pods",
            "show_ports": "1" if show_ports_bool else "0",
            "show_core": "1" if show_core_bool else "0",
            "show_services": "1" if show_services_bool else "0",
            "show_distribution": "1" if show_distribution_bool else "0",
            "show_access": "1" if show_access_bool else "0",
            "show_nonmist_access": "1" if show_nonmist_access_bool else "0",
            "show_aps": "1" if show_aps_bool else "0",
            "flow": "1" if flow_bool else "0",
            "redundant_only": "1" if redundant_only_bool else "0",
            "tv": "1" if tv_bool else "0",
        }

        body = ""
        if "vmap_options_dropdown" in globals():
            body += vmap_options_dropdown()

        if "vmap_layer_switches" in globals():
            body += vmap_layer_switches(mist_params)

        selected_value = selected or focus or ""
        body += render_mist_pods_map_page(
            devices,
            edges,
            show_ports=show_ports_bool,
            show_access=show_access_bool,
            show_aps=show_aps_bool,
            flow_mode=flow_bool,
            redundant_only=redundant_only_bool,
            tv_mode=tv_bool,
            selected=selected_value,
            node_url_params=mist_params,
        )
        return body


    # TRANSITION_LAYOUT_EARLY_RETURN
    if str(layout or "").lower() in ("transition", "mist_transition", "mist"):
        transition_params = {
            "site": site,
            "layout": "transition",
            "show_ports": "1" if show_ports_bool else "0",
            "show_core": "1" if show_core_bool else "0",
            "show_services": "1" if show_services_bool else "0",
            "show_distribution": "1" if show_distribution_bool else "0",
            "show_access": "1" if show_access_bool else "0",
            "show_aps": "1" if show_aps_bool else "0",
            "flow": "1" if flow_bool else "0",
            "redundant_only": "1" if redundant_only_bool else "0",
            "tv": "1" if tv_bool else "0",
        }

        body = ""
        if "vmap_options_dropdown" in globals():
            body += vmap_options_dropdown()

        if "vmap_layer_switches" in globals():
            body += vmap_layer_switches(transition_params)

        selected_value = selected or focus or ""
        body += render_transition_map_page(
            devices,
            edges,
            show_ports=show_ports_bool,
            flow_mode=flow_bool,
            redundant_only=redundant_only_bool,
            tv_mode=tv_bool,
            selected=selected_value,
            node_url_params=transition_params,
        )
        return body


    focus_id = find_focus_device_id(devices, focus)
    focus_device = devices.get(focus_id) if focus_id is not None else None

    focus_summary = ""
    if focus_id is not None:
        devices, edges, focus_summary = apply_access_uplink_focus_filter(devices, edges, focus_id)

    edge_pairs = defaultdict(int)
    for e in edges:
        edge_pairs[tuple(sorted([e["a"], e["b"]]))] += 1

    multi = sum(1 for _, c in edge_pairs.items() if c >= 2)

    full_switch_url = layer_url(site, show_ports, True, True, True, True, False)
    full_with_aps_url = layer_url(site, show_ports, True, True, True, True, True)
    transport_url = layer_url(site, show_ports, True, True, True, False, False)
    dist_access_url = layer_url(site, show_ports, False, False, True, True, False)
    current_layers_url = layer_url(site, show_ports, show_core_bool, show_services_bool, show_distribution_bool, show_access_bool, show_aps_bool)
    flow_on_url = current_layers_url + "&flow=1"
    flow_off_url = current_layers_url + "&flow=0"
    redundant_on_url = current_layers_url + f"&flow={1 if flow_bool else 0}&redundant_only=1"
    redundant_off_url = current_layers_url + f"&flow={1 if flow_bool else 0}&redundant_only=0"
    tv_mode_url = "/tools/topology-vmap?site=vt&show_core=1&show_services=1&show_distribution=1&show_access=1&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=1"
    normal_mode_url = current_layers_url + f"&flow={1 if flow_bool else 0}&redundant_only={1 if redundant_only_bool else 0}&tv=0"
    core_only_url = layer_url(site, show_ports, True, False, False, False, False)
    services_only_url = layer_url(site, show_ports, False, True, False, False, False)
    dist_only_url = layer_url(site, show_ports, False, False, True, False, False)
    access_only_url = layer_url(site, show_ports, False, False, False, True, False)
    ap_only_url = layer_url(site, show_ports, False, False, False, False, True)

    buttons = f"""
    <div class="toolbar">
      <a class="button" href="{full_switch_url}">All Switch Layers</a>
      <a class="button" href="{full_with_aps_url}">All + APs</a>
      <a class="button" href="{transport_url}">Core/Svcs/Dist</a>
      <a class="button" href="{dist_access_url}">Dist + Access</a>\n      <a class="button" href="{flow_on_url}">Show Data Flow</a>\n      <a class="button" href="{flow_off_url}">Hide Data Flow</a>\n      <a class="button" href="{redundant_on_url}">Highlight Redundant</a>\n      <a class="button" href="{redundant_off_url}">Normal Links</a>\n      <a class="button" href="{tv_mode_url}">TV Mode</a>\n      <a class="button" href="{normal_mode_url}">Normal Size</a>
      <a class="button" href="{core_only_url}">Core Only</a>
      <a class="button" href="{services_only_url}">Services Only</a>
      <a class="button" href="{dist_only_url}">Distribution Only</a>
      <a class="button" href="{access_only_url}">Access/Leaf Only</a>
      <a class="button" href="{ap_only_url}">APs Only</a>
      <a class="button" href="/tools/topology-vmap?site=monterey&show_ports=1&show_core=1&show_services=1&show_distribution=1&show_access=1&show_aps=0">Monterey Switches</a>
      <a class="button" href="/tools/topology-v2-split?q=core&limit=250&show_aps=0">Current Split View</a>
    </div>
    """

    css = """
    <style>
      .toolbar { display:flex; gap:10px; flex-wrap:wrap; margin: 0 0 16px 0; }
      .button {
        display:inline-block; padding:8px 12px; border:1px solid #334155; border-radius:8px;
        background:#0f172a; color:#e2e8f0; text-decoration:none; font-weight:700;
      }
      .button:hover { border-color:#38bdf8; color:#fff; }
      .note { color:#cbd5e1; margin: 0 0 14px 0; }
      .vmap-wrap {
        overflow:auto; border:1px solid #334155; border-radius:14px; background:#1e293b;
        padding:14px; max-height: calc(100vh - 230px);
      }
      svg.vmap {
        width: 100%;
        min-width: 0;
        height: auto;
        display: block;
      }
      .edge:hover path { stroke-width:9 !important; opacity:1 !important; filter: drop-shadow(0 0 10px #38bdf8); }
      .edge-path { transition: stroke-width 120ms ease, opacity 120ms ease, filter 120ms ease; }
      .node a { cursor:pointer; }
      .edge:hover path { stroke-width:9 !important; opacity:1 !important; filter: drop-shadow(0 0 10px #38bdf8); }
      .edge-path { transition: stroke-width 120ms ease, opacity 120ms ease, filter 120ms ease; }
      .node a { cursor:pointer; }
      .stack-card-back { opacity:0.55; }
      .node:hover rect { filter: brightness(1.25); }

      /* Normal mode: fit the current screen. No forced horizontal scroll. */
      .vmap-wrap:not(.tv) {
        width: 100%;
        max-width: 100%;
        overflow: auto;
      }

      .vmap-wrap:not(.tv) svg.vmap {
        width: 100%;
        min-width: 0 !important;
        height: auto;
        display: block;
      }

      /* TV mode: only get aggressive on genuinely large displays. */
      .vmap-wrap.tv {
        width: 100%;
        max-width: 100%;
        height: calc(100vh - 185px);
        max-height: none;
        overflow: auto;
        padding: 10px;
        border-radius: 10px;
      }

      .vmap-wrap.tv svg.vmap {
        width: 100%;
        min-width: 0;
        height: auto;
        display: block;
      }

      @media (min-width: 2200px) {
        .vmap-wrap.tv {
          width: calc(100vw - 24px);
          margin-left: calc(50% - 50vw + 12px);
          height: calc(100vh - 165px);
        }

        .vmap-wrap.tv svg.vmap {
          min-width: 2600px;
        }
      }

      @media (min-width: 3000px) {
        .vmap-wrap.tv svg.vmap {
          min-width: 3400px;
        }
      }

      @media (min-width: 3800px) {
        .vmap-wrap.tv svg.vmap {
          min-width: 3900px;
        }
      }

    
      .focus-audit {
        display:flex;
        gap:14px;
        align-items:stretch;
        margin: 12px 0 16px 0;
        border:1px solid #334155;
        border-radius:12px;
        background:#0f172a;
        overflow:hidden;
      }

      .focus-status {
        min-width:92px;
        display:flex;
        align-items:center;
        justify-content:center;
        font-weight:900;
        font-size:20px;
        letter-spacing:.04em;
        color:#020617;
      }

      .focus-status.ok { background:#22c55e; }
      .focus-status.warn { background:#fb923c; }
      .focus-status.bad { background:#ef4444; color:#fff; }
      .focus-status.info { background:#38bdf8; }

      .focus-audit-body {
        padding:12px 14px;
        flex:1;
      }

      .focus-title {
        color:#e5e7eb;
        font-size:16px;
        margin-bottom:4px;
      }

      .focus-subtitle {
        color:#cbd5e1;
        margin-bottom:10px;
      }

      .focus-metrics {
        display:flex;
        gap:12px;
        flex-wrap:wrap;
        margin-bottom:10px;
        color:#cbd5e1;
      }

      .focus-metrics span {
        border:1px solid #334155;
        border-radius:8px;
        padding:5px 8px;
        background:#0f172a;
      }

      .focus-table {
        width:100%;
        border-collapse:collapse;
        font-size:13px;
      }

      .focus-table th,
      .focus-table td {
        border-bottom:1px solid #334155;
        padding:6px 8px;
        text-align:left;
        color:#e5e7eb;
      }

      .focus-table th {
        color:#93c5fd;
        font-weight:800;
      }

    </style>
    """

    title = {
        "vt": "Middlebury VT Infrastructure V Map",
        "monterey": "Monterey Infrastructure V Map",
        "all": "All Managed Infrastructure V Map",
    }[site]

    selected_layers = []
    if show_core_bool:
        selected_layers.append("Core Infrastructure")
    if show_services_bool:
        selected_layers.append("Services")
    if show_distribution_bool:
        selected_layers.append("Distribution")
    if show_access_bool:
        selected_layers.append("Access / Leaf")
    if show_aps_bool:
        selected_layers.append("AP")

    selected_layers_text = ", ".join(selected_layers) if selected_layers else "None"

    body = css
    body += f"<h1>{h(title)}</h1>"
    body += vmap_options_dropdown()

    layer_params = {
        "site": site,
        "show_ports": "1" if show_ports_bool else "0",
        "show_core": "1" if show_core_bool else "0",
        "show_services": "1" if show_services_bool else "0",
        "show_distribution": "1" if show_distribution_bool else "0",
        "show_access": "1" if show_access_bool else "0",
        "show_aps": "1" if show_aps_bool else "0",
        "flow": "1" if flow_bool else "0",
        "redundant_only": "1" if redundant_only_bool else "0",
        "tv": "1" if tv_bool else "0",
    }
    body += vmap_layer_switches(layer_params)
    body += f'<p class="note">Selected layers: <b>{h(selected_layers_text)}</b></p>'
    body += (
        f'<p class="note">Managed switch-to-switch LLDP only. '
        f'Nodes: <b>{len(devices)}</b>. Physical links: <b>{len(edges)}</b>. '
        f'Device pairs with 2+ physical members: <b>{multi}</b>. '
        f'Clients, storage, UPS/PDU, and unmanaged LLDP endpoints are intentionally hidden. '
        f'APs are shown only when the AP layer is enabled.</p>'
    )
    wrap_class = "vmap-wrap tv" if tv_bool else "vmap-wrap"
    node_url_params = {
        "site": site,
        "show_ports": "1" if show_ports_bool else "0",
        "show_core": "1" if show_core_bool else "0",
        "show_services": "1" if show_services_bool else "0",
        "show_distribution": "1" if show_distribution_bool else "0",
        "show_access": "1" if show_access_bool else "0",
        "show_aps": "1" if show_aps_bool else "0",
        "flow": "1" if flow_bool else "0",
        "redundant_only": "1" if redundant_only_bool else "0",
        "tv": "1" if tv_bool else "0",
    }

    focus_controls = ""
    if focus_device:
        clear_params = dict(node_url_params)
        clear_params.pop("focus", None)
        clear_url = "/tools/topology-vmap?" + urlencode(clear_params)

        lookup_q = (
            focus_device.get("display_name")
            or focus_device.get("hostname")
            or focus_device.get("sysName")
            or focus_device.get("ip")
            or focus_device.get("device_id")
            or ""
        )
        lookup_url = "/tools/lookup?q=" + quote(str(lookup_q))

        focus_controls = (
            f'<p class="note">Focused device: <b>{h(focus_device.get("display_name") or focus_device.get("hostname") or focus_id)}</b>. '
            f'Direct neighbors shown: <b>{max(0, len(devices) - 1)}</b>. '
            f'<a class="button" href="{h(clear_url)}">Clear Focus</a> '
            f'<a class="button" href="{h(lookup_url)}">Open Lookup</a></p>'
        )

    body += focus_controls
    body += focus_summary
    body += f'<div class="{wrap_class}">'
    if str(layout or "").lower() in ("old_mist", "horizontal", "transition"):
        body += render_old_mist_horizontal_layout(
            devices,
            edges,
            show_ports=show_ports_bool,
            flow_mode=flow_bool,
            redundant_only=redundant_only_bool,
            tv_mode=tv_bool,
            node_url_params=node_url_params,
        )
    else:
        body += render_svg(
            devices,
            edges,
            show_ports=show_ports_bool,
            flow_mode=flow_bool,
            redundant_only=redundant_only_bool,
            tv_mode=tv_bool,
            node_url_params=node_url_params,
        )
    body += '</div>'

    return body

