from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from html import escape
from typing import Any

from app.core.db import fetch_all


SITE_SPECS: dict[str, dict[str, Any]] = {
    "datacenter": {
        "map_type": "site-datacenter",
        "position_module": "site-datacenter-layout-v1",
        "title": "Old Datacenter Flow",
        "subtitle": (
            "DFL datacenter, 700ES, and legacy non-Mist switching. "
            "Dynamic xDP links remain visible when missing."
        ),
        "patterns": [
            "dfl-dc",
            "vtr-racktop",
            "racktop",
            "700es",
            "aggregation-",
            "dc-fw",
            "dc-mgmt",
            "dfl-core",
            "vtr-core",
        ],
        "zones": ["DFL DC", "700ES", "VTR DC", "Other"],
        "zone_rules": [
            ("700ES", ["700es"]),
            ("VTR DC", ["vtr", "voter"]),
            ("DFL DC", ["dfl", "aggregation", "dc-fw", "cat-door"]),
        ],
        "default_zone": "Other",
        "role_order": [
            "core",
            "firewall",
            "aggregation",
            "storage",
            "access",
            "management",
            "unknown",
        ],
        "role_rules": [
            ("core", ["dfl-core", "vtr-core"]),
            ("firewall", ["dc-fw"]),
            ("aggregation", ["aggregation-", "racktop"]),
            ("storage", ["storage"]),
            ("access", ["700es", "dc-access"]),
            ("management", ["mgmt", "cat-door"]),
        ],
        "default_role": "unknown",
    },
    "hci": {
        "map_type": "site-hci",
        "position_module": "site-hci-layout-v1",
        "title": "HCI Cluster Flow",
        "subtitle": (
            "HCI, PVE, Ceph, storage, and associated switching. "
            "Core handoffs and parallel fabric links are shown separately."
        ),
        "patterns": [
            "pve-core",
            "hci",
            "ceph",
            "calamari",
            "bonefish",
            "browntrout",
            "laketrout",
            "storage",
            "700es-storage",
            "dfl-core",
            "vtr-core",
        ],
        "zones": ["DFL", "Cluster Fabric", "700ES", "VTR"],
        "zone_rules": [
            ("700ES", ["700es"]),
            ("VTR", ["vtr", "voter"]),
            ("DFL", ["dfl"]),
        ],
        "default_zone": "Cluster Fabric",
        "role_order": ["core", "fabric", "storage", "management", "unknown"],
        "role_rules": [
            ("core", ["dfl-core", "vtr-core"]),
            (
                "fabric",
                [
                    "pve-core",
                    "calamari",
                    "bonefish",
                    "browntrout",
                    "laketrout",
                    "hci",
                ],
            ),
            ("storage", ["storage", "ceph"]),
            ("management", ["mgmt", "management"]),
        ],
        "default_role": "unknown",
    },
    "washington": {
        "map_type": "site-washington",
        "position_module": "site-washington-layout-v1",
        "title": "Washington DC Flow",
        "subtitle": (
            "Initial Washington DC topology discovered from LibreNMS names "
            "and current xDP links."
        ),
        "patterns": [
            "washington",
            "washingtondc",
            "wash-",
            "washdc",
            "wash-dc",
            "wdc-",
            "1400 k st",
            "amc08",
            "dc-fw1",
        ],
        "zones": ["Washington DC"],
        "zone_rules": [],
        "default_zone": "Washington DC",
        "role_order": [
            "router",
            "firewall",
            "aggregation",
            "storage",
            "access",
            "management",
            "unknown",
        ],
        "role_rules": [
            ("router", ["router", "edge", "wan"]),
            ("firewall", ["fw", "firewall"]),
            ("aggregation", ["core", "aggregation", "dist"]),
            ("access", ["access", "switch", "-cx", "-j", "-h"]),
            ("management", ["mgmt", "management"]),
        ],
        "default_role": "unknown",
    },
    "monterey": {
        "map_type": "site-monterey",
        "position_module": "site-monterey-layout-v1",
        "title": "Monterey Flow",
        "subtitle": (
            "Initial retiring-site topology for Monterey. "
            "Kept separate so removal work does not pollute active modules."
        ),
        "patterns": ["monterey", "munras", "ca-787"],
        "zones": ["Monterey"],
        "zone_rules": [],
        "default_zone": "Monterey",
        "role_order": [
            "router",
            "firewall",
            "aggregation",
            "storage",
            "access",
            "management",
            "unknown",
        ],
        "role_rules": [
            ("router", ["core", "router", "wan"]),
            ("firewall", ["fw", "firewall"]),
            ("aggregation", ["aggregation", "dist"]),
            ("access", ["access", "switch", "-cx", "-j", "-h"]),
            ("management", ["mgmt", "management"]),
        ],
        "default_role": "unknown",
    },
}


def h(value: Any) -> str:
    return escape(str(value or ""))


def clean(value: Any) -> str:
    return str(value or "").strip()


def norm(value: Any) -> str:
    return clean(value).lower()


def slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", norm(value))
    return text.strip("-") or "node"



SUPPORT_DEVICE_PATTERNS = (
    "ups-",
    "apcups",
    "cat-door",
    "opengear",
    "acm700",
    "-vip",
    ".vip.",
)


def is_support_device_name(value: Any) -> bool:
    value = norm(value)
    return any(pattern in value for pattern in SUPPORT_DEVICE_PATTERNS)


def should_skip_site_device(module: str, row: dict[str, Any]) -> bool:
    text = site_search_text(row)
    name = norm(row.get("hostname") or row.get("sysName") or row.get("display") or "")

    if module in ("washington", "monterey") and is_support_device_name(text):
        return True

    if module == "datacenter" and (
        "cat-door" in text
        or "apcups" in text
        or "ups-" in text
    ):
        return True

    return False


def get_site_spec(module: str) -> dict[str, Any]:
    if module not in SITE_SPECS:
        raise KeyError(module)
    return SITE_SPECS[module]


def map_type_for_module(module: str) -> str:
    return str(get_site_spec(module)["map_type"])


def device_name(row: dict[str, Any]) -> str:
    for key in ("display", "sysName", "hostname"):
        value = clean(row.get(key))
        if value:
            return value
    return str(row.get("device_id") or "unknown")


def site_search_text(row: dict[str, Any]) -> str:
    return " ".join(
        norm(row.get(key))
        for key in (
            "display",
            "sysName",
            "hostname",
            "hardware",
            "os",
            "type",
            "purpose",
            "location",
            "location_name",
        )
    )


def contains_any(text: str, patterns: list[str]) -> bool:
    return any(norm(pattern) in text for pattern in patterns)


def classify_site_zone(module: str, value: Any) -> str:
    spec = get_site_spec(module)
    text = norm(value)

    for zone, patterns in spec.get("zone_rules", []):
        if contains_any(text, patterns):
            return str(zone)

    return str(spec["default_zone"])


def classify_site_role(module: str, value: Any) -> str:
    spec = get_site_spec(module)
    text = norm(value)

    for role, patterns in spec.get("role_rules", []):
        if contains_any(text, patterns):
            return str(role)

    return str(spec["default_role"])


def discover_site_devices(module: str) -> list[dict[str, Any]]:
    spec = get_site_spec(module)

    rows = fetch_all("""
    SELECT
      device_id,
      hostname,
      sysName,
      display,
      hardware,
      os,
      type,
      purpose,
      status,
      disabled,
      `ignore`
    FROM devices
    WHERE disabled = 0
      AND `ignore` = 0
    ORDER BY display, sysName, hostname
    """)

    out: list[dict[str, Any]] = []

    for row in rows:
        text = site_search_text(row)
        if not contains_any(text, spec["patterns"]):
            continue
        if should_skip_site_device(module, row):
            continue

        item = dict(row)
        item["_site_name"] = device_name(item)
        item["_site_role"] = classify_site_role(module, text)
        item["_site_zone"] = classify_site_zone(module, text)
        out.append(item)

    return out



def should_skip_site_endpoint(module: str, value: Any) -> bool:
    name = norm(value)

    if module in ("washington", "monterey") and is_support_device_name(name):
        return True

    if module == "datacenter" and (
        "cat-door" in name
        or "apcups" in name
        or "ups-" in name
    ):
        return True

    return False


def should_skip_site_link(module: str, link: dict[str, Any]) -> bool:
    local_device = link.get("local_device") or link.get("source") or ""
    remote_device = link.get("remote_device") or link.get("target") or ""
    return (
        should_skip_site_endpoint(module, local_device)
        or should_skip_site_endpoint(module, remote_device)
    )


def ensure_position_table() -> None:
    fetch_all("""
    CREATE TABLE IF NOT EXISTS middkips_network_flow_positions (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      module VARCHAR(64) NOT NULL,
      node_id VARCHAR(255) NOT NULL,
      x INT NOT NULL,
      y INT NOT NULL,
      w INT DEFAULT NULL,
      updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uniq_module_node (module, node_id),
      KEY idx_module (module)
    )
    """)


def load_site_positions(module: str) -> dict[str, dict[str, int]]:
    ensure_position_table()
    position_module = get_site_spec(module)["position_module"]

    rows = fetch_all("""
    SELECT node_id, x, y, w
    FROM middkips_network_flow_positions
    WHERE module = %s
    """, [position_module])

    return {
        str(row.get("node_id")): {
            "x": int(row.get("x") or 0),
            "y": int(row.get("y") or 0),
            "w": int(row.get("w") or 0),
        }
        for row in rows
    }


def _link_rows(module: str) -> list[dict[str, Any]]:
    return fetch_all("""
    SELECT
      link_hash,
      local_device,
      local_port,
      remote_device,
      remote_port,
      link_role,
      last_status,
      last_seen,
      source
    FROM middkips_expected_fabric_links
    WHERE map_type = %s
    ORDER BY local_device, local_port, remote_device, remote_port
    """, [map_type_for_module(module)])


def _make_missing_row(module: str, endpoint_name: str) -> dict[str, Any]:
    return {
        "device_id": None,
        "hostname": endpoint_name,
        "sysName": endpoint_name,
        "display": endpoint_name,
        "hardware": "Expected endpoint no longer found in LibreNMS",
        "os": "",
        "type": "",
        "purpose": "",
        "status": 0,
        "disabled": 0,
        "ignore": 0,
        "_site_name": endpoint_name,
        "_site_role": classify_site_role(module, endpoint_name),
        "_site_zone": classify_site_zone(module, endpoint_name),
        "_site_missing": True,
    }


def _auto_layout(
    module: str,
    nodes: list[dict[str, Any]],
) -> tuple[int, int, list[dict[str, Any]]]:
    spec = get_site_spec(module)
    zones = list(spec["zones"])
    role_order = list(spec["role_order"])

    canvas_width = 2400
    margin_x = 50
    zone_gap = 20
    zone_width = int(
        (canvas_width - (2 * margin_x) - zone_gap * (len(zones) - 1))
        / max(1, len(zones))
    )

    node_width = min(290, max(220, zone_width - 80))
    node_height = 68
    node_gap_x = 24
    node_gap_y = 24
    role_gap = 65
    top_y = 95

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        grouped[(node["zone"], node["role"])].append(node)

    for values in grouped.values():
        values.sort(key=lambda item: norm(item["label"]))

    role_rows: dict[str, int] = {}
    for role in role_order:
        max_rows = 1
        for zone in zones:
            count = len(grouped.get((zone, role), []))
            available = zone_width - 70
            columns = max(1, int((available + node_gap_x) / (node_width + node_gap_x)))
            rows = max(1, math.ceil(count / columns)) if count else 1
            max_rows = max(max_rows, rows)
        role_rows[role] = max_rows

    role_start_y: dict[str, int] = {}
    y_cursor = top_y
    for role in role_order:
        role_start_y[role] = y_cursor
        y_cursor += role_rows[role] * (node_height + node_gap_y) + role_gap

    for zone_index, zone in enumerate(zones):
        zone_start = margin_x + zone_index * (zone_width + zone_gap)

        for role in role_order:
            items = grouped.get((zone, role), [])
            if not items:
                continue

            available = zone_width - 70
            columns = max(1, int((available + node_gap_x) / (node_width + node_gap_x)))
            used_width = columns * node_width + (columns - 1) * node_gap_x
            left = zone_start + max(35, int((zone_width - used_width) / 2))

            for index, node in enumerate(items):
                column = index % columns
                row = index // columns
                node["x"] = left + column * (node_width + node_gap_x)
                node["y"] = role_start_y[role] + row * (node_height + node_gap_y)
                node["w"] = node_width
                node["h"] = node_height

    canvas_height = max(850, y_cursor + 100)
    zone_layout = []

    for zone_index, zone in enumerate(zones):
        zone_start = margin_x + zone_index * (zone_width + zone_gap)
        zone_layout.append({"label": zone, "x": zone_start, "width": zone_width})

    return canvas_width, canvas_height, zone_layout


def classify_site_link_role(
    module: str,
    source_role: str,
    target_role: str,
) -> str:
    roles = {source_role, target_role}

    if "core" in roles:
        if "aggregation" in roles or "fabric" in roles:
            return "core-fabric"
        return "core-link"
    if "firewall" in roles:
        return "firewall-link"
    if "aggregation" in roles and "access" in roles:
        return "aggregation-access"
    if "fabric" in roles and "storage" in roles:
        return "fabric-storage"
    if roles == {"fabric"}:
        return "fabric-interconnect"
    if roles == {"aggregation"}:
        return "aggregation-interconnect"
    if "management" in roles:
        return "management"
    return f"{module}-link"


def site_flow_data(module: str) -> dict[str, Any]:
    spec = get_site_spec(module)
    discovered = discover_site_devices(module)
    expected_rows = _link_rows(module)
    saved_positions = load_site_positions(module)

    by_name: dict[str, dict[str, Any]] = {
        norm(row["_site_name"]): row for row in discovered
    }

    for link in expected_rows:
        if should_skip_site_link(module, link):
            continue
        for endpoint in (
            clean(link.get("local_device")),
            clean(link.get("remote_device")),
        ):
            if endpoint and norm(endpoint) not in by_name:
                by_name[norm(endpoint)] = _make_missing_row(module, endpoint)

    nodes: list[dict[str, Any]] = []
    name_to_node_id: dict[str, str] = {}
    used_ids: set[str] = set()

    for row in by_name.values():
        label = clean(row.get("_site_name")) or device_name(row)
        base_id = slug(label)
        node_id = base_id

        if node_id in used_ids:
            node_id = f"{base_id}-{row.get('device_id') or len(used_ids)}"

        used_ids.add(node_id)
        name_to_node_id[norm(label)] = node_id

        if bool(row.get("_site_missing")):
            status = "missing"
        elif int(row.get("status") or 0) == 1:
            status = "up"
        else:
            status = "down"

        nodes.append({
            "id": node_id,
            "label": label,
            "subtitle": (
                clean(row.get("hardware"))
                or clean(row.get("os"))
                or clean(row.get("_site_role"))
            ),
            "hardware": clean(row.get("hardware")),
            "role": clean(row.get("_site_role")) or "unknown",
            "zone": clean(row.get("_site_zone")) or spec["default_zone"],
            "status": status,
            "device_id": row.get("device_id"),
            "links": [],
        })

    node_by_id = {node["id"]: node for node in nodes}
    links: list[dict[str, Any]] = []

    for row in expected_rows:
        if should_skip_site_link(module, row):
            continue
        source = name_to_node_id.get(norm(row.get("local_device")))
        target = name_to_node_id.get(norm(row.get("remote_device")))

        if not source or not target or source == target:
            continue

        source_role = node_by_id[source]["role"]
        target_role = node_by_id[target]["role"]

        links.append({
            "id": clean(row.get("link_hash")),
            "source": source,
            "target": target,
            "source_port": clean(row.get("local_port")),
            "target_port": clean(row.get("remote_port")),
            "role": (
                clean(row.get("link_role"))
                or classify_site_link_role(module, source_role, target_role)
            ),
            "status": clean(row.get("last_status")) or "unknown",
            "last_seen": str(row.get("last_seen") or ""),
        })

    parallel_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        parallel_groups[tuple(sorted([link["source"], link["target"]]))].append(link)

    for group in parallel_groups.values():
        group.sort(key=lambda item: (
            item["source_port"],
            item["target_port"],
            item["id"],
        ))
        for index, link in enumerate(group):
            link["parallel_index"] = index
            link["parallel_total"] = len(group)

    links_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        links_by_node[link["source"]].append(link)
        links_by_node[link["target"]].append(link)

    for node in nodes:
        attached = links_by_node.get(node["id"], [])

        if node["status"] == "up" and any(
            link["status"] in ("down", "missing") for link in attached
        ):
            node["status"] = "degraded"

        for link in attached:
            if link["source"] == node["id"]:
                peer_id = link["target"]
                local_port = link["source_port"]
                peer_port = link["target_port"]
            else:
                peer_id = link["source"]
                local_port = link["target_port"]
                peer_port = link["source_port"]

            peer = node_by_id.get(peer_id, {})
            node["links"].append({
                "id": link["id"],
                "peer": peer_id,
                "peer_label": peer.get("label", peer_id),
                "local_port": local_port,
                "peer_port": peer_port,
                "role": link["role"],
                "status": link["status"],
                "last_seen": link["last_seen"],
            })

        node["links"].sort(key=lambda item: (
            item["status"] == "up",
            item["peer_label"],
            item["local_port"],
        ))

    canvas_width, canvas_height, zones = _auto_layout(module, nodes)

    for node in nodes:
        saved = saved_positions.get(node["id"])
        if saved:
            node["x"] = saved["x"]
            node["y"] = saved["y"]
            if saved.get("w"):
                node["w"] = saved["w"]

    counts = defaultdict(int)
    for link in links:
        counts[link["status"]] += 1

    summary = {
        "total": len(links),
        "up": counts["up"],
        "down": counts["down"],
        "missing": counts["missing"],
        "unknown": counts["unknown"],
        "problems": counts["down"] + counts["missing"],
    }

    return {
        "module": module,
        "map_type": spec["map_type"],
        "position_module": spec["position_module"],
        "title": spec["title"],
        "subtitle": spec["subtitle"],
        "canvas": {"width": canvas_width, "height": canvas_height},
        "zones": zones,
        "nodes": nodes,
        "links": links,
        "summary": summary,
        "problems": [
            link for link in links if link["status"] in ("down", "missing")
        ],
    }


def site_flow_problems(module: str) -> dict[str, Any]:
    data = site_flow_data(module)
    return {
        "module": module,
        "summary": data["summary"],
        "problems": data["problems"],
    }


def render_site_flow(
    module: str,
    editable: bool = False,
    tv: bool = False,
) -> str:
    data = site_flow_data(module)
    spec = get_site_spec(module)
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")

    if editable:
        edit_controls = f'''
          <button class="button" id="ef-save">Save Layout</button>
          <button class="button" id="ef-reset">Reset Layout</button>
          <a class="button" href="/tools/network-flow/{h(module)}">View Mode</a>
        '''
    else:
        edit_controls = f'''
          <a class="button" href="/tools/network-flow/{h(module)}?edit=1">Edit Layout</a>
        '''

    zone_html = "".join(
        '<div class="ef-site-zone" '
        f'style="left:{int(zone["x"])}px;width:{int(zone["width"])}px">'
        f'<span>{h(zone["label"])}</span>'
        '</div>'
        for zone in data["zones"]
    )

    nav = '''
      <nav class="ef-module-nav">
        <a href="/tools/network-flow/mist">Mist Fabric</a>
        <a href="/tools/network-flow/edge">Edge</a>
        <a href="/tools/network-flow/datacenter">Old Datacenter</a>
        <a href="/tools/network-flow/hci">HCI Cluster</a>
        <a href="/tools/network-flow/washington">Washington DC</a>
        <a href="/tools/network-flow/monterey">Monterey</a>
      </nav>
    '''

    tv_class = " ef-tv-mode" if tv else ""
    controls = "" if tv else f'''
      <div class="ef-controls">
        <button class="button" id="ef-fit">Fit</button>
        <button class="button" id="ef-zoom-out">-</button>
        <button class="button" id="ef-zoom-in">+</button>
        <button class="button" id="ef-refresh">Refresh</button>
        {edit_controls}
      </div>
    '''

    header = "" if tv else f'''
      <header class="ef-header">
        <div>
          <h1>{h(spec["title"])}</h1>
          <p>{h(spec["subtitle"])}</p>
        </div>
        {controls}
      </header>
      {nav}
    '''

    return f'''
<link rel="stylesheet" href="/static/middkips.css?v=site-flow-1">
<link rel="stylesheet" href="/static/middkips_edge_flow.css?v=site-flow-1">

<div
  class="ef-page ef-site-page{tv_class}"
  data-editable="{str(editable).lower()}"
  data-position-module="{h(spec["position_module"])}"
  data-api-url="/api/network-flow/site/{h(module)}"
>
  {header}
  <section class="ef-summary" id="ef-summary"></section>
  <div class="ef-refresh-state">
    <span id="ef-refresh-state">Loading {h(spec["title"])}...</span>
  </div>
  <div class="ef-layout">
    <div class="ef-viewport" id="ef-viewport">
      <div class="ef-stage" id="ef-stage">
        {zone_html}
        <svg class="ef-svg" id="ef-svg"></svg>
        <div class="ef-nodes" id="ef-nodes"></div>
      </div>
    </div>
    <aside class="ef-details" id="ef-details">
      <h2>Module Details</h2>
      <p>Click a device or path to inspect interfaces and live status.</p>
    </aside>
  </div>
</div>

<script id="ef-data" type="application/json">{payload}</script>
<script src="/static/middkips_edge_flow.js?v=site-flow-1"></script>
'''


def render_site_flow_home() -> str:
    cards = []

    for module in ("datacenter", "hci", "washington", "monterey"):
        spec = get_site_spec(module)
        badge = '<span class="badge warn">Retiring</span>' if module == "monterey" else ""
        cards.append(f'''
        <a class="nf-module-card" href="/tools/network-flow/{h(module)}">
          <strong>{h(spec["title"])}</strong>
          {badge}
          <span>{h(spec["subtitle"])}</span>
        </a>
        ''')

    return f'''
    <div class="nf-page">
      <div class="nf-topbar">
        <div>
          <h1>Network Flow Modules</h1>
          <p>Focused topology modules for PiSignage and troubleshooting.</p>
        </div>
      </div>
      <div class="nf-module-grid">
        <a class="nf-module-card nf-module-mist" href="/tools/network-flow/mist">
          <strong>Mist Fabric</strong>
          <span>Core, distribution, and redundant Mist leaf paths.</span>
        </a>
        <a class="nf-module-card nf-module-edge" href="/tools/network-flow/edge">
          <strong>Network Edge</strong>
          <span>Providers, routers, handoffs, firewalls, and campus core.</span>
        </a>
        {''.join(cards)}
      </div>
    </div>
    '''
