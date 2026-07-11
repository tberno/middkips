from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from html import escape
from typing import Any

from app.core.db import fetch_all


EDGE_NODE_SPECS: dict[str, dict[str, Any]] = {
    # External providers.
    "provider-firstlight": {
        "label": "FirstLight",
        "role": "provider",
        "virtual": True,
        "subtitle": "216.238.164.73",
        "x": 120,
        "y": 40,
        "w": 330,
        "h": 110,
    },
    "provider-uvm-i2": {
        "label": "UVM/I2",
        "role": "provider",
        "virtual": True,
        "subtitle": "132.198.255.17 / 132.198.255.213",
        "x": 1240,
        "y": 40,
        "w": 340,
        "h": 110,
    },
    "provider-lumen": {
        "label": "Lumen",
        "role": "provider",
        "virtual": True,
        "subtitle": "4.16.160.29",
        "x": 1880,
        "y": 40,
        "w": 330,
        "h": 110,
    },

    # Edge routers.
    "zuma-dfl": {
        "label": "Zuma-DFL",
        "role": "router",
        "patterns": ["zuma-dfl.middlebury.edu", "zuma-dfl"],
        "x": 220,
        "y": 250,
        "w": 360,
        "h": 82,
    },
    "turbo-vtr": {
        "label": "Turbo-VTR",
        "role": "router",
        "patterns": ["turbo-vtr.middlebury.edu", "turbo-vtr"],
        "x": 1570,
        "y": 250,
        "w": 360,
        "h": 82,
    },

    # Edge handoff switches.
    "daisy-dfl": {
        "label": "Daisy-DFL",
        "role": "handoff",
        "patterns": ["daisy-dfl"],
        "x": 220,
        "y": 470,
        "w": 360,
        "h": 82,
    },
    "chester-vtr": {
        "label": "Chester-VTR",
        "role": "handoff",
        "patterns": ["chester-vtr"],
        "x": 1570,
        "y": 470,
        "w": 360,
        "h": 82,
    },

    # Firewalls.
    "vpn-fw-dfl": {
        "label": "VPNFW DFL",
        "role": "firewall",
        "patterns": ["vpn-fw-dfl"],
        "x": 60,
        "y": 740,
        "w": 330,
        "h": 86,
    },
    "edgefw-dfl": {
        "label": "EdgeFW DFL",
        "role": "firewall",
        "patterns": ["edgefw-dfl"],
        "x": 500,
        "y": 740,
        "w": 330,
        "h": 86,
    },
    "edgefw-voter": {
        "label": "EdgeFW VTR",
        "role": "firewall",
        "patterns": ["edgefw-voter"],
        "x": 1300,
        "y": 740,
        "w": 330,
        "h": 86,
    },
    "vpn-fw-vtr": {
        "label": "VPNFW VTR",
        "role": "firewall",
        "patterns": ["vpn-fw-vtr"],
        "x": 1740,
        "y": 740,
        "w": 330,
        "h": 86,
    },

    # Core handoff.
    "dfl-core": {
        "label": "Core DFL",
        "role": "core",
        "patterns": ["dfl-core.middlebury.edu", "dfl-core"],
        "x": 420,
        "y": 1060,
        "w": 380,
        "h": 88,
    },
    "vtr-core": {
        "label": "Core VTR",
        "role": "core",
        "patterns": ["vtr-core.middlebury.edu", "vtr-core"],
        "x": 1500,
        "y": 1060,
        "w": 380,
        "h": 88,
    },
}


# Provider links are configured because the provider clouds are not necessarily
# LibreNMS xDP devices. Status comes from the router-facing interface.
EDGE_PROVIDER_LINKS: list[dict[str, str]] = [
    {
        "provider": "provider-firstlight",
        "router": "zuma-dfl",
        "router_port": "xe-0/1/7",
        "peer_ip": "216.238.164.73",
        "local_ip": "216.238.164.74",
        "speed": "10G",
    },
    {
        "provider": "provider-uvm-i2",
        "router": "turbo-vtr",
        "router_port": "xe-0/1/6",
        "peer_ip": "132.198.255.17",
        "local_ip": "132.198.255.18",
        "speed": "10G",
    },
    {
        "provider": "provider-uvm-i2",
        "router": "turbo-vtr",
        "router_port": "xe-0/1/6",
        "peer_ip": "132.198.255.213",
        "local_ip": "132.198.255.214",
        "speed": "10G",
    },
    {
        "provider": "provider-lumen",
        "router": "turbo-vtr",
        "router_port": "xe-0/1/7",
        "peer_ip": "4.16.160.29",
        "local_ip": "4.16.160.30",
        "speed": "10G",
    },
]


def h(value: Any) -> str:
    return escape(str(value or ""))


def clean(value: Any) -> str:
    return str(value or "").strip()


def norm(value: Any) -> str:
    return clean(value).lower()


def canonical_link_hash(
    map_type: str,
    device_a: str,
    port_a: str,
    device_b: str,
    port_b: str,
) -> str:
    left, right = sorted([
        (norm(device_a), norm(port_a)),
        (norm(device_b), norm(port_b)),
    ])
    raw = "|".join([map_type, left[0], left[1], right[0], right[1]])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def classify_link_role(device_a: str, device_b: str) -> str:
    role_a = EDGE_NODE_SPECS.get(device_a, {}).get("role", "")
    role_b = EDGE_NODE_SPECS.get(device_b, {}).get("role", "")
    roles = {role_a, role_b}

    if "provider" in roles:
        return "provider"

    if roles == {"router"}:
        return "router-interconnect"

    if roles == {"router", "handoff"}:
        return "router-handoff"

    if roles == {"handoff"}:
        return "handoff-trunk"

    if roles == {"handoff", "firewall"}:
        if device_a.startswith("vpn-fw") or device_b.startswith("vpn-fw"):
            return "vpn-uplink"
        return "edge-uplink"

    if roles == {"firewall", "core"}:
        if device_a.startswith("vpn-fw") or device_b.startswith("vpn-fw"):
            return "vpn-core"
        return "edgefw-core"

    if roles == {"firewall"}:
        return "firewall-ha"

    if roles == {"core"}:
        return "core-interconnect"

    return ""


def _row_names(row: dict[str, Any]) -> list[str]:
    return [
        clean(row.get("display")),
        clean(row.get("sysName")),
        clean(row.get("hostname")),
    ]


def resolve_edge_devices() -> dict[str, dict[str, Any]]:
    rows = fetch_all("""
    SELECT
      device_id,
      hostname,
      sysName,
      display,
      hardware,
      os,
      type,
      status,
      disabled,
      `ignore`
    FROM devices
    WHERE disabled = 0
      AND `ignore` = 0
    """)

    resolved: dict[str, dict[str, Any]] = {}

    for key, spec in EDGE_NODE_SPECS.items():
        if spec.get("virtual"):
            continue

        patterns = [norm(p) for p in spec.get("patterns", [])]
        candidates: list[tuple[int, int, dict[str, Any], str]] = []

        for row in rows:
            for name in _row_names(row):
                low = norm(name)
                if not low:
                    continue

                for pattern in patterns:
                    if low == pattern:
                        score = 0
                    elif low.startswith(pattern):
                        score = 1
                    elif pattern in low:
                        score = 2
                    else:
                        continue

                    candidates.append((score, len(low), row, name))

        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]))
            row = dict(candidates[0][2])
            row["_matched_name"] = candidates[0][3]
            resolved[key] = row

    return resolved


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


def load_edge_positions() -> dict[str, dict[str, int]]:
    ensure_position_table()

    rows = fetch_all("""
    SELECT node_id, x, y, w
    FROM middkips_network_flow_positions
    WHERE module = 'edge'
    """)

    out: dict[str, dict[str, int]] = {}

    for row in rows:
        out[str(row.get("node_id"))] = {
            "x": int(row.get("x") or 0),
            "y": int(row.get("y") or 0),
            "w": int(row.get("w") or 0),
        }

    return out


def _status_rank(status: str) -> int:
    return {
        "missing": 5,
        "down": 4,
        "unknown": 3,
        "device-down": 2,
        "up": 1,
    }.get(status, 3)


def _worst_status(statuses: list[str]) -> str:
    if not statuses:
        return "unknown"
    return max(statuses, key=_status_rank)


def edge_flow_data() -> dict[str, Any]:
    resolved = resolve_edge_devices()
    positions = load_edge_positions()

    rows = fetch_all("""
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
    WHERE map_type = 'edge'
    ORDER BY local_device, local_port, remote_device, remote_port
    """)

    links: list[dict[str, Any]] = []

    for row in rows:
        source = clean(row.get("local_device"))
        target = clean(row.get("remote_device"))

        if source not in EDGE_NODE_SPECS or target not in EDGE_NODE_SPECS:
            continue

        links.append({
            "id": clean(row.get("link_hash")),
            "source": source,
            "target": target,
            "source_port": clean(row.get("local_port")),
            "target_port": clean(row.get("remote_port")),
            "role": clean(row.get("link_role")) or "edge-link",
            "status": clean(row.get("last_status")) or "unknown",
            "last_seen": str(row.get("last_seen") or ""),
            "source_type": clean(row.get("source")),
        })

    parallel_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for link in links:
        pair = tuple(sorted([link["source"], link["target"]]))
        parallel_groups[pair].append(link)

    for group in parallel_groups.values():
        group.sort(key=lambda item: (
            item["source_port"],
            item["target_port"],
            item["id"],
        ))

        total = len(group)
        for index, link in enumerate(group):
            link["parallel_index"] = index
            link["parallel_total"] = total

    links_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for link in links:
        links_by_node[link["source"]].append(link)
        links_by_node[link["target"]].append(link)

    nodes: list[dict[str, Any]] = []

    for key, spec in EDGE_NODE_SPECS.items():
        row = resolved.get(key)
        attached = links_by_node.get(key, [])
        attached_statuses = [link["status"] for link in attached]

        if spec.get("virtual"):
            node_status = _worst_status(attached_statuses)
            hardware = "External provider"
            device_id = None
            matched_name = spec["label"]
        elif row is None:
            node_status = "missing"
            hardware = "Not found in LibreNMS devices"
            device_id = None
            matched_name = key
        else:
            device_up = int(row.get("status") or 0) == 1

            if not device_up:
                node_status = "down"
            elif any(status in ("down", "missing") for status in attached_statuses):
                node_status = "degraded"
            else:
                node_status = "up"

            hardware = clean(row.get("hardware")) or clean(row.get("os"))
            device_id = row.get("device_id")
            matched_name = clean(row.get("_matched_name")) or key

        node = {
            "id": key,
            "label": spec["label"],
            "subtitle": spec.get("subtitle") or hardware or spec["role"],
            "hardware": hardware,
            "role": spec["role"],
            "virtual": bool(spec.get("virtual")),
            "status": node_status,
            "x": int(spec["x"]),
            "y": int(spec["y"]),
            "w": int(spec["w"]),
            "h": int(spec["h"]),
            "device_id": device_id,
            "matched_name": matched_name,
            "links": [],
        }

        saved = positions.get(key)
        if saved:
            node["x"] = saved["x"]
            node["y"] = saved["y"]
            if saved.get("w"):
                node["w"] = saved["w"]

        for link in attached:
            peer = link["target"] if link["source"] == key else link["source"]
            local_port = (
                link["source_port"]
                if link["source"] == key
                else link["target_port"]
            )
            peer_port = (
                link["target_port"]
                if link["source"] == key
                else link["source_port"]
            )

            node["links"].append({
                "id": link["id"],
                "peer": peer,
                "peer_label": EDGE_NODE_SPECS.get(peer, {}).get("label", peer),
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

        nodes.append(node)

    counts = {
        "up": 0,
        "down": 0,
        "missing": 0,
        "unknown": 0,
        "device-down": 0,
    }

    for link in links:
        status = link["status"]
        counts[status] = counts.get(status, 0) + 1

    summary = {
        "total": len(links),
        "up": counts.get("up", 0),
        "down": counts.get("down", 0),
        "missing": counts.get("missing", 0),
        "unknown": counts.get("unknown", 0),
        "device_down": counts.get("device-down", 0),
        "problems": counts.get("down", 0) + counts.get("missing", 0),
    }

    problems = [
        link for link in links
        if link["status"] in ("down", "missing")
    ]

    return {
        "module": "edge",
        "canvas": {
            "width": 2400,
            "height": 1320,
            "divider_x": 1200,
        },
        "nodes": nodes,
        "links": links,
        "summary": summary,
        "problems": problems,
    }


def edge_flow_problems() -> dict[str, Any]:
    data = edge_flow_data()
    return {
        "summary": data["summary"],
        "problems": data["problems"],
    }


def render_edge_flow(editable: bool = False) -> str:
    data = edge_flow_data()
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")

    edit_controls = """
      <button class="button" id="ef-save">Save Layout</button>
      <button class="button" id="ef-reset">Reset Layout</button>
      <a class="button" href="/tools/network-flow/edge">View Mode</a>
    """ if editable else """
      <a class="button" href="/tools/network-flow/edge?edit=1">Edit Layout</a>
    """

    return f"""
<link rel="stylesheet" href="/static/middkips_edge_flow.css?v=20260711-2">

<div class="ef-page" data-editable="{str(editable).lower()}">
  <header class="ef-header">
    <div>
      <h1>Dynamic Edge Flow</h1>
      <p>
        Live provider, router, handoff, firewall, and core paths.
        Parallel physical and logical links remain separate.
      </p>
    </div>

    <div class="ef-controls">
      <button class="button" id="ef-fit">Fit</button>
      <button class="button" id="ef-zoom-out">-</button>
      <button class="button" id="ef-zoom-in">+</button>
      <button class="button" id="ef-refresh">Refresh</button>
      {edit_controls}
    </div>
  </header>

  <nav class="ef-module-nav">
    <a href="/tools/network-flow/mist">Mist Fabric</a>
    <a class="active" href="/tools/network-flow/edge">Edge</a>
    <a href="/tools/network-flow/datacenter">Old Datacenter</a>
    <a href="/tools/network-flow/hci">HCI Cluster</a>
    <a href="/tv/network-flow">TV Dashboard</a>
  </nav>

  <section class="ef-summary" id="ef-summary"></section>

  <div class="ef-refresh-state">
    <span id="ef-refresh-state">Loading edge state...</span>
  </div>

  <div class="ef-layout">
    <div class="ef-viewport" id="ef-viewport">
      <div class="ef-stage" id="ef-stage">
        <div class="ef-zone-label ef-zone-dfl">DFL</div>
        <div class="ef-zone-label ef-zone-vtr">Voter / VTR</div>
        <div class="ef-divider"></div>

        <svg class="ef-svg" id="ef-svg"></svg>
        <div class="ef-nodes" id="ef-nodes"></div>
      </div>
    </div>

    <aside class="ef-details" id="ef-details">
      <h2>Edge Details</h2>
      <p>Click a node or path to inspect interfaces and live status.</p>
    </aside>
  </div>
</div>

<script id="ef-data" type="application/json">{payload}</script>
<script src="/static/middkips_edge_flow.js?v=20260711-2"></script>
"""
