from html import escape
from urllib.parse import quote_plus
from collections import deque
import re

from app.core.db import fetch_all
from app.services.middkips_mist_topology import build_topology, render_table


def h(value):
    return escape(str(value if value is not None else ""))


def is_ip(value):
    return bool(re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", str(value or "").strip()))


def classify_node_split(name, node):
    text = " ".join([
        str(name or ""),
        str(node.get("role") or ""),
        str(node.get("model") or ""),
        str(node.get("os") or ""),
        str(node.get("hardware") or ""),
        str(node.get("type") or ""),
    ]).lower()

    n = str(name or "").lower()

    # Keep appliances/UPS/PDU gear out of the infrastructure topology columns.
    # These can include "core" in the hostname, so this must run before Core matching.
    if (
        n.startswith("ups-")
        or "-ups" in n
        or " ups" in text
        or "apc" in text
        or "eaton" in text
        or "symmetra" in text
        or "rackpdu" in text
        or " pdu" in text
        or "power distribution" in text
        or "battery" in text
    ):
        return "Appliance"

    if n.startswith("svcs-") or n.startswith("svc-") or n in ("svcs-dfl", "svcs-voter"):
        return "Services"

    if "core" in text or "qfx" in text or "spine" in text:
        return "Core"

    if "dist" in text or "distribution" in text or "agg" in text or "aggregation" in text:
        return "Distribution"

    if node.get("role") == "AP" or str(node.get("type") or "").upper() == "AP":
        return "AP"

    if (
        n.startswith("bl-")
        or n.endswith("-cx")
        or "-cx" in n
        or "access" in text
        or "leaf" in text
        or "ex4" in text
        or "ex3" in text
        or "arubaos-cx" in text
        or "procurve" in text
        or "hpe anw" in text
        or "2930" in text
        or "3810" in text
        or "6100" in text
        or "6200" in text
        or "6300" in text
        or "switch" in text
    ):
        return "Access / Leaf"

    if "ap3" in text or "ap4" in text or "ap6" in text or "mist ap" in text:
        return "AP"

    if "client" in text or "iphone" in text or "android" in text or "phone" in text:
        return "Client"

    return node.get("role") or "Other"


def enrich_ip_nodes(grouped):
    all_nodes = []

    for nodes in grouped.values():
        for node in nodes or []:
            all_nodes.append(node)

    ip_names = sorted({
        str(n.get("name") or "").strip()
        for n in all_nodes
        if is_ip(n.get("name"))
    })

    if not ip_names:
        return grouped

    placeholders = ",".join(["%s"] * len(ip_names))

    try:
        rows = fetch_all(f"""
            SELECT hostname, sysName, os, type, hardware, status
            FROM devices
            WHERE hostname IN ({placeholders})
        """, tuple(ip_names))
    except Exception:
        rows = []

    by_ip = {
        str(r.get("hostname")): r
        for r in rows
        if r.get("hostname")
    }

    rebuilt = {
        "Core": [],
        "Distribution": [],
        "Services": [],
        "Access / Leaf": [],
        "AP": [],
        "Appliance": [],
        "Client": [],
        "Other": [],
    }

    for node in all_nodes:
        name = str(node.get("name") or "").strip()
        row = by_ip.get(name)

        if row:
            sysname = str(row.get("sysName") or "").strip()
            if sysname:
                node["name"] = sysname

            if row.get("hardware"):
                node["model"] = row.get("hardware")

            if row.get("os"):
                node["os"] = row.get("os")

            if row.get("type"):
                node["type"] = row.get("type")

        role = classify_node_split(node.get("name"), node)
        node["role"] = role
        rebuilt.setdefault(role, []).append(node)

    for role in rebuilt:
        rebuilt[role] = sorted(
            rebuilt[role],
            key=lambda n: (not n.get("seed"), str(n.get("name") or "").lower())
        )

    return rebuilt


def build_focus(grouped, edges, focus):
    node_index = {}

    for nodes in grouped.values():
        for node in nodes or []:
            node_index[node.get("id")] = node

    adj = {}

    for idx, edge in enumerate(edges or []):
        src = edge.get("src")
        dst = edge.get("dst")

        if src not in node_index or dst not in node_index:
            continue

        adj.setdefault(src, []).append((dst, idx))
        adj.setdefault(dst, []).append((src, idx))

    focus = str(focus or "").strip()
    focus_id = ""

    if focus:
        fl = focus.lower()

        for nid, node in node_index.items():
            if fl == str(nid).lower() or fl == str(node.get("name") or "").lower():
                focus_id = nid
                break

        if not focus_id:
            for nid, node in node_index.items():
                if fl in str(node.get("name") or "").lower():
                    focus_id = nid
                    break

    if not focus_id:
        return "", set(), set()

    allowed_nodes = {focus_id}
    allowed_edges = set()

    for neighbor, edge_idx in adj.get(focus_id, []):
        allowed_nodes.add(neighbor)
        allowed_edges.add(edge_idx)

    q = deque([(focus_id, [focus_id], [])])
    seen = {focus_id}
    paths_found = 0

    while q and paths_found < 15:
        current, path_nodes, path_edges = q.popleft()
        role = (node_index.get(current) or {}).get("role") or ""

        if current != focus_id and role in {"Core", "Distribution", "Services", "Access / Leaf"}:
            allowed_nodes.update(path_nodes)
            allowed_edges.update(path_edges)
            paths_found += 1
            continue

        if len(path_nodes) >= 8:
            continue

        for neighbor, edge_idx in adj.get(current, []):
            if neighbor in seen:
                continue

            seen.add(neighbor)
            q.append((neighbor, path_nodes + [neighbor], path_edges + [edge_idx]))

    return focus_id, allowed_nodes, allowed_edges



def norm_identity_key(value):
    value = str(value or "").strip().lower()
    if not value:
        return ""

    value = value.replace(":", "").replace("-", "").replace(".", "") if re.search(r"[:.-]", value) and len(value) >= 12 else value
    return value


def mist_identity_set(mist_rows):
    out = set()

    for row in mist_rows or []:
        if not isinstance(row, dict):
            continue

        for key in [
            "name", "hostname", "device_name", "switch_name", "ap_name",
            "ip", "ip_addr", "ip_address", "mac", "mac_address", "serial"
        ]:
            value = row.get(key)
            nk = norm_identity_key(value)
            if nk:
                out.add(nk)

    return out


def node_identity_set(node):
    out = set()

    for key in [
        "name", "hostname", "sysName", "ip", "ip_addr", "ip_address",
        "mac", "mac_address", "serial"
    ]:
        value = node.get(key) if isinstance(node, dict) else None
        nk = norm_identity_key(value)
        if nk:
            out.add(nk)

    return out


def is_confirmed_mist(node, mist_ids):
    node_ids = node_identity_set(node)
    return bool(node_ids & mist_ids)


def force_legacy_name(name):
    n = str(name or "").strip().lower()

    if n in ("aggregation-a", "aggregation-b", "browntrout"):
        return True

    if n.startswith("aggregation-"):
        return True

    if "browntrout" in n:
        return True

    return False



def split_role_for_node(node, mist_names):
    role = node.get("role") or "Other"
    name = str(node.get("name") or "")
    n = name.lower()

    # These are legacy/non-Mist even though they may be QFX/core-like platforms.
    if force_legacy_name(name):
        return "Legacy / Non-Mist"

    if role == "Services":
        return "Services"

    if role == "AP":
        return "AP"

    if role in {"Core", "Distribution"}:
        return "Mist / Fabric Core-Dist"

    if role == "Access / Leaf":
        text = " ".join([
            str(node.get("name") or ""),
            str(node.get("model") or ""),
            str(node.get("hardware") or ""),
            str(node.get("os") or ""),
            str(node.get("type") or ""),
        ]).lower()

        # Hard legacy names that should never be placed on the Mist side.
        if force_legacy_name(name):
            return "Legacy / Non-Mist"

        # Source of truth: only put access switches on the Mist side when
        # the Mist API inventory actually confirms the device identity.
        if is_confirmed_mist(node, mist_names):
            return "Mist / Fabric Access"

        # Everything else access-like is non-Mist/legacy for this migration view.
        return "Legacy / Non-Mist"

    return "Collapsed"


def render_split_svg(grouped, edges, mist_rows, q="", limit=50, show_aps=False, focus=""):
    mist_names = mist_identity_set(mist_rows)

    focus_id, allowed_nodes, allowed_edge_indexes = build_focus(grouped, edges, focus)

    columns = [
        "Mist / Fabric Core-Dist",
        "Mist / Fabric Access",
        "Services",
        "Legacy / Non-Mist",
    ]

    if show_aps:
        columns.append("AP")

    split = {c: [] for c in columns}
    collapsed = 0

    for nodes in grouped.values():
        for node in nodes or []:
            if focus_id and node.get("id") not in allowed_nodes:
                collapsed += 1
                continue

            col = split_role_for_node(node, mist_names)

            if col in split:
                split[col].append(node)
            else:
                collapsed += 1

    for col in split:
        split[col] = sorted(
            split[col],
            key=lambda n: (not n.get("seed"), str(n.get("name") or "").lower())
        )[:45]

    # Hide infrastructure boxes that would render with no visible edge.
    # This cleans up random switches/appliances that matched the search but do not
    # actually participate in the visible services-centered path.
    prelim_visible_ids = set()
    for nodes in split.values():
        for node in nodes:
            if node.get("id"):
                prelim_visible_ids.add(node.get("id"))

    linked_visible_ids = set()
    for idx, edge in enumerate(edges or []):
        if focus_id and idx not in allowed_edge_indexes:
            continue

        src = edge.get("src")
        dst = edge.get("dst")

        if src in prelim_visible_ids and dst in prelim_visible_ids:
            linked_visible_ids.add(src)
            linked_visible_ids.add(dst)

    orphan_collapsed = 0
    if linked_visible_ids:
        for col in split:
            kept = []
            for node in split[col]:
                nid = node.get("id")
                if nid in linked_visible_ids or nid == focus_id:
                    kept.append(node)
                else:
                    orphan_collapsed += 1
            split[col] = kept

    collapsed += orphan_collapsed

    x_positions = {
        "Mist / Fabric Core-Dist": 70,
        "Mist / Fabric Access": 410,
        "Services": 770,
        "Legacy / Non-Mist": 1130,
        "AP": 1500,
    }

    node_pos = {}
    svg_nodes = ""
    svg_edges = ""
    max_height = 260

    q_enc = quote_plus(q or "")
    show_aps_int = "1" if show_aps else "0"

    for col in columns:
        nodes = split.get(col) or []
        x = x_positions[col]
        y = 72

        svg_nodes += f'<text x="{x}" y="32" fill="#cbd5e1" font-size="14" font-weight="700">{h(col)} ({len(nodes)})</text>'

        for node in nodes:
            nid = node.get("id")
            node_pos[nid] = (x, y)

            name = str(node.get("name") or "")
            meta = " ".join(str(v) for v in [node.get("role"), node.get("ip"), node.get("model")] if v)

            if nid == focus_id:
                stroke = "#f59e0b"
                stroke_width = "2.5"
            elif focus_id and nid in allowed_nodes:
                stroke = "#fbbf24"
                stroke_width = "1.8"
            elif node.get("seed"):
                stroke = "#38bdf8"
                stroke_width = "1.4"
            else:
                stroke = "#64748b"
                stroke_width = "1.3"

            focus_enc = quote_plus(str(nid or name))
            href = f"/tools/topology-v2-split?q={q_enc}&limit={int(limit)}&show_aps={show_aps_int}&focus={focus_enc}"

            svg_nodes += f'''
            <g>
              <a href="{h(href)}">
                <rect x="{x}" y="{y}" width="290" height="50" rx="8" fill="#111827" stroke="{stroke}" stroke-width="{stroke_width}"></rect>
                <text x="{x + 10}" y="{y + 19}" fill="#f8fafc" font-size="12" font-weight="700">{h(name[:41])}</text>
                <text x="{x + 10}" y="{y + 39}" fill="#cbd5e1" font-size="10">{h(meta[:50])}</text>
              </a>
            </g>
            '''

            y += 70
            max_height = max(max_height, y + 40)

    visible_ids = set(node_pos.keys())
    drawable_edges = []

    for idx, edge in enumerate(edges or []):
        if focus_id and idx not in allowed_edge_indexes:
            continue

        if edge.get("src") in visible_ids and edge.get("dst") in visible_ids:
            drawable_edges.append((idx, edge))

    def edge_score(item):
        _, edge = item
        source = str(edge.get("source") or "").lower()
        if "lldp" in source:
            return 0
        if "fdb" in source:
            return 1
        return 2

    drawable_edges = sorted(drawable_edges, key=edge_score)

    drawn = 0
    show_labels = len(drawable_edges) <= 90

    for idx, edge in drawable_edges[:400]:
        src = edge.get("src")
        dst = edge.get("dst")

        x1, y1 = node_pos[src]
        x2, y2 = node_pos[dst]

        x1 += 290
        y1 += 25
        y2 += 25

        if focus_id:
            color = "#f59e0b"
            width = "2.0"
            opacity = "0.95"
        else:
            color = "#94a3b8"
            width = "1.2"
            opacity = "0.75"

        svg_edges += f'''
        <path d="M{x1},{y1} C{x1 + 110},{y1} {x2 - 110},{y2} {x2},{y2}" fill="none" stroke="{color}" stroke-width="{width}" opacity="{opacity}"></path>
        '''

        if show_labels and edge.get("label"):
            lx = int((x1 + x2) / 2)
            ly = int((y1 + y2) / 2) - 4
            svg_edges += f'<text x="{lx}" y="{ly}" fill="#cbd5e1" font-size="9">{h(str(edge.get("label"))[:34])}</text>'

        drawn += 1

    width = 1850 if show_aps else 1500
    height = max_height

    focus_note = ""
    if focus_id:
        focused_name = focus_id

        for nodes in grouped.values():
            for node in nodes:
                if node.get("id") == focus_id:
                    focused_name = node.get("name") or focus_id
                    break

        clear_href = f"/tools/topology-v2-split?q={q_enc}&limit={int(limit)}&show_aps={show_aps_int}"
        focus_note = f'''
        <p class="muted">
          Focused path view for <strong>{h(focused_name)}</strong>.
          <a class="button" href="{h(clear_href)}">Clear Focus</a>
        </p>
        '''

    collapsed_note = ""
    if collapsed:
        collapsed_note = f"<p class='muted'>Collapsed {h(collapsed)} AP/client/endpoint/unknown/appliance/linkless nodes outside this split view.</p>"

    return f'''
    <section class="panel">
      <h2>Topology v2 Split <span class="muted">({drawn} drawn links)</span></h2>
      <p class="muted">
        Services-centered split: Mist/Fabric side -> Services block -> Legacy/non-Mist side.
        Click a node to highlight its path.
      </p>
      {focus_note}
      {collapsed_note}
      <div style="overflow:auto; border:1px solid rgba(148,163,184,.25); border-radius:10px; background:rgba(15,23,42,.25);">
        <svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;">
          {svg_edges}
          {svg_nodes}
        </svg>
      </div>
    </section>
    '''


def render_topology_v2_split_page(q="", limit=50, show_aps="0", focus=""):
    q = (q or "").strip()

    try:
        limit = int(limit or 50)
    except Exception:
        limit = 50

    show_aps_bool = str(show_aps or "0").strip().lower() in ("1", "true", "yes", "on")
    focus = str(focus or "").strip()

    ap_select = (
        f'<select name="show_aps" style="max-width:130px">'
        f'<option value="0" {"selected" if not show_aps_bool else ""}>APs Off</option>'
        f'<option value="1" {"selected" if show_aps_bool else ""}>APs On</option>'
        f'</select>'
    )

    focus_input = ""
    if focus:
        focus_input = f'<input type="hidden" name="focus" value="{h(focus)}">'

    form = f'''
    <section class="panel">
      <h1>Topology v2 Split</h1>
      <form class="unused-controls" method="get" action="/tools/topology-v2-split">
        <input name="q" value="{h(q)}" placeholder="core, bl, Kohn, SouthPOD, building, switch">
        <input name="limit" value="{h(limit)}" style="max-width:90px">
        {ap_select}
        {focus_input}
        <button class="button" type="submit">Build Split Map</button>
        <a class="button" href="/tools/topology-v2-split">Clear</a>
        <a class="button" href="/tools/topology-v2?q={h(q)}">Standard v2</a>
        <a class="button" href="/tools/mist/clients-v2?q={h(q)}">Client Search</a>
      </form>
      <p class="muted">Alternate topology view with Services block as the middle handoff point.</p>
    </section>
    '''

    if not q:
        return form + '''
        <section class="panel">
          <h2>Enter a filter</h2>
          <p class="muted">Start with core, a pod, a building, or a switch. Full-campus topology is intentionally not rendered.</p>
        </section>
        '''

    ctx, grouped, edges, mist_rows, lldp_rows, fdb_rows = build_topology(q=q, limit=limit)
    grouped = enrich_ip_nodes(grouped)

    cards = ""
    for role in ["Core", "Distribution", "Services", "Access / Leaf"]:
        count = len(grouped.get(role) or [])
        if count:
            cards += f'<article class="card"><div class="card-title">{h(role)}</div><div class="card-value">{h(count)}</div></article>'

    if show_aps_bool:
        cards += f'<article class="card"><div class="card-title">AP</div><div class="card-value">{h(len(grouped.get("AP") or []))}</div></article>'

    hidden = len(grouped.get("Client") or []) + len(grouped.get("Other") or [])
    if not show_aps_bool:
        hidden += len(grouped.get("AP") or [])

    if hidden:
        cards += f'<article class="card"><div class="card-title">Collapsed</div><div class="card-value">{h(hidden)}</div></article>'

    cards += f'<article class="card"><div class="card-title">Links</div><div class="card-value">{h(len(edges))}</div></article>'
    cards += f'<article class="card"><div class="card-title">Errors</div><div class="card-value">{h(len(ctx.get("errors") or []))}</div></article>'

    # For Mist membership, use the full Mist inventory. mist_rows is only the
    # query seed set, which is too narrow for confirming whether LLDP neighbors
    # are Mist-managed.
    mist_inventory_rows = []
    mist_inventory_rows.extend(ctx.get("switch_rows") or [])
    mist_inventory_rows.extend(ctx.get("switch_matches") or [])
    mist_inventory_rows.extend(mist_rows or [])

    body = form + f'<section class="cards">{cards}</section>'
    body += render_split_svg(
        grouped,
        edges,
        mist_inventory_rows,
        q=q,
        limit=limit,
        show_aps=show_aps_bool,
        focus=focus,
    )

    body += render_table("Discovered Links", edges, max_rows=150)
    body += render_table("Mist Seed Rows", mist_rows, max_rows=50)
    body += render_table("LLDP Rows Used", lldp_rows, max_rows=75)
    body += render_table("AP / FDB Rows Used", fdb_rows, max_rows=75)

    return body
