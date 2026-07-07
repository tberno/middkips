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


def classify_node_v2(name, node):
    text = " ".join([
        str(name or ""),
        str(node.get("role") or ""),
        str(node.get("model") or ""),
        str(node.get("os") or ""),
        str(node.get("hardware") or ""),
        str(node.get("type") or ""),
    ]).lower()

    n = str(name or "").lower()

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

        role = classify_node_v2(node.get("name"), node)
        node["role"] = role
        rebuilt.setdefault(role, []).append(node)

    for role in rebuilt:
        rebuilt[role] = sorted(
            rebuilt[role],
            key=lambda n: (not n.get("seed"), str(n.get("name") or "").lower())
        )

    return rebuilt


def focus_filter(grouped, edges, focus=""):
    node_index = {}
    for nodes in grouped.values():
        for node in nodes or []:
            node_index[node.get("id")] = node

    edge_list = []
    adj = {}

    for idx, edge in enumerate(edges or []):
        src = edge.get("src")
        dst = edge.get("dst")

        if not src or not dst:
            continue

        if src not in node_index or dst not in node_index:
            continue

        edge_list.append((idx, edge))
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

    focus_role = (node_index.get(focus_id) or {}).get("role") or ""

    if focus_role == "AP":
        target_roles = {"Access / Leaf", "Services", "Distribution", "Core"}
    elif focus_role == "Access / Leaf":
        target_roles = {"Services", "Distribution", "Core"}
    elif focus_role == "Services":
        target_roles = {"Core", "Distribution", "Access / Leaf"}
    elif focus_role == "Distribution":
        target_roles = {"Core", "Services", "Access / Leaf"}
    elif focus_role == "Core":
        target_roles = {"Distribution", "Services", "Access / Leaf"}
    else:
        target_roles = {"Core", "Distribution", "Services", "Access / Leaf"}

    # Always include direct neighbors.
    for neighbor, edge_idx in adj.get(focus_id, []):
        allowed_nodes.add(neighbor)
        allowed_edges.add(edge_idx)

    # BFS shortest paths toward infrastructure roles.
    q = deque([(focus_id, [focus_id], [])])
    seen = {focus_id}
    paths_found = 0

    while q and paths_found < 12:
        current, path_nodes, path_edges = q.popleft()

        if current != focus_id:
            role = (node_index.get(current) or {}).get("role")
            if role in target_roles:
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


def render_svg_v2(grouped, edges, q="", limit=50, show_aps=False, focus=""):
    # Services intentionally sits between core/distribution and the access/legacy side.
    role_order = ["Core", "Distribution", "Services", "Access / Leaf"]

    if show_aps:
        role_order.append("AP")

    focus_id, allowed_nodes, allowed_edge_indexes = focus_filter(grouped, edges, focus=focus)

    display_grouped = {}
    collapsed = 0

    for role, nodes in grouped.items():
        nodes = nodes or []

        if role in role_order:
            if focus_id:
                nodes = [n for n in nodes if n.get("id") in allowed_nodes]

            display_grouped[role] = nodes[:40]
            collapsed += max(0, len(nodes) - len(display_grouped[role]))
        else:
            collapsed += len(nodes)

    # Wider spacing.
    x_positions = {
        "Core": 80,
        "Distribution": 430,
        "Services": 780,
        "Access / Leaf": 1130,
        "AP": 1500,
    }

    node_pos = {}
    svg_nodes = ""
    svg_edges = ""
    max_height = 260

    q_enc = quote_plus(q or "")
    show_aps_int = "1" if show_aps else "0"

    for role in role_order:
        nodes = display_grouped.get(role) or []
        x = x_positions[role]
        y = 72

        total = len(grouped.get(role) or [])
        shown = len(nodes)

        svg_nodes += f'<text x="{x}" y="32" fill="#cbd5e1" font-size="14" font-weight="700">{h(role)} ({shown}/{total})</text>'

        for node in nodes:
            nid = node.get("id")
            node_pos[nid] = (x, y)

            name = str(node.get("name") or "")
            meta = " ".join(str(v) for v in [node.get("ip"), node.get("model")] if v)

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
            href = f"/tools/topology-v2?q={q_enc}&limit={int(limit)}&show_aps={show_aps_int}&focus={focus_enc}"

            svg_nodes += f'''
            <g>
              <a href="{h(href)}">
                <rect x="{x}" y="{y}" width="280" height="50" rx="8" fill="#111827" stroke="{stroke}" stroke-width="{stroke_width}"></rect>
                <text x="{x + 10}" y="{y + 19}" fill="#f8fafc" font-size="12" font-weight="700">{h(name[:39])}</text>
                <text x="{x + 10}" y="{y + 39}" fill="#cbd5e1" font-size="10">{h(meta[:48])}</text>
              </a>
            </g>
            '''
            y += 70
            max_height = max(max_height, y + 40)

        hidden = total - shown
        if hidden > 0 and not focus_id:
            svg_nodes += f'<text x="{x}" y="{y}" fill="#cbd5e1" font-size="11">+{hidden} more</text>'

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
    show_labels = len(drawable_edges) <= 80

    for idx, edge in drawable_edges[:350]:
        src = edge.get("src")
        dst = edge.get("dst")

        if src not in node_pos or dst not in node_pos:
            continue

        x1, y1 = node_pos[src]
        x2, y2 = node_pos[dst]

        x1 += 280
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
        <path d="M{x1},{y1} C{x1 + 105},{y1} {x2 - 105},{y2} {x2},{y2}" fill="none" stroke="{color}" stroke-width="{width}" opacity="{opacity}"></path>
        '''

        if show_labels and edge.get("label"):
            lx = int((x1 + x2) / 2)
            ly = int((y1 + y2) / 2) - 4
            svg_edges += f'<text x="{lx}" y="{ly}" fill="#cbd5e1" font-size="9">{h(str(edge.get("label"))[:34])}</text>'

        drawn += 1

    width = 1850 if show_aps else 1500
    height = max_height

    flow = "Core → Distribution → Services → Access / Leaf"
    if show_aps:
        flow += " → AP"

    focus_note = ""
    if focus_id:
        focused_name = ""
        for nodes in grouped.values():
            for node in nodes:
                if node.get("id") == focus_id:
                    focused_name = node.get("name") or focus_id
                    break

        clear_href = f"/tools/topology-v2?q={q_enc}&limit={int(limit)}&show_aps={show_aps_int}"
        focus_note = f'''
        <p class="muted">
          Focused path view for <strong>{h(focused_name or focus_id)}</strong>.
          <a class="button" href="{h(clear_href)}">Clear Focus</a>
        </p>
        '''

    collapsed_note = ""
    if collapsed:
        collapsed_note = f"<p class='muted'>Collapsed {h(collapsed)} AP/client/endpoint/unknown nodes. APs can be shown with the toggle. Clients are handled by Client Search.</p>"

    return f'''
    <section class="panel">
      <h2>Logical Topology v2 <span class="muted">({drawn} drawn links)</span></h2>
      <p class="muted">Infrastructure view: {h(flow)}. Click a node to highlight its path.</p>
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


def render_topology_v2_page(q="", limit=50, show_aps="0", focus=""):
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
      <h1>Logical Topology v2</h1>
      <form class="unused-controls" method="get" action="/tools/topology-v2">
        <input name="q" value="{h(q)}" placeholder="Site, pod, switch, or building, for example core, bl, Kohn, SouthPOD">
        <input name="limit" value="{h(limit)}" style="max-width:90px">
        {ap_select}
        {focus_input}
        <button class="button" type="submit">Build Map</button>
        <a class="button" href="/tools/topology-v2">Clear</a>
        <a class="button" href="/tools/mist/clients-v2?q={h(q)}">Client Search</a>
      </form>
      <p class="muted">Safe v2 renderer. Existing topology page is untouched.</p>
    </section>
    '''

    if not q:
        return form + '''
        <section class="panel">
          <h2>Enter a filter</h2>
          <p class="muted">Start with a building, pod, or switch name. Full-campus topology is intentionally not rendered.</p>
        </section>
        '''

    ctx, grouped, edges, mist_rows, lldp_rows, fdb_rows = build_topology(q=q, limit=limit)
    grouped = enrich_ip_nodes(grouped)

    visible_roles = ["Core", "Distribution", "Services", "Access / Leaf"]
    if show_aps_bool:
        visible_roles.append("AP")

    cards = ""

    for role in visible_roles:
        count = len(grouped.get(role) or [])
        if count:
            cards += f'<article class="card"><div class="card-title">{h(role)}</div><div class="card-value">{h(count)}</div></article>'

    collapsed = 0
    for role, nodes in grouped.items():
        if role not in visible_roles:
            collapsed += len(nodes or [])

    if collapsed:
        cards += f'<article class="card"><div class="card-title">Collapsed</div><div class="card-value">{h(collapsed)}</div></article>'

    cards += f'<article class="card"><div class="card-title">Links</div><div class="card-value">{h(len(edges))}</div></article>'
    cards += f'<article class="card"><div class="card-title">Errors</div><div class="card-value">{h(len(ctx.get("errors") or []))}</div></article>'

    body = form + f'<section class="cards">{cards}</section>'
    body += render_svg_v2(grouped, edges, q=q, limit=limit, show_aps=show_aps_bool, focus=focus)

    body += render_table("Discovered Links", edges, max_rows=150)
    body += render_table("Mist Seed Rows", mist_rows, max_rows=50)
    body += render_table("LLDP Rows Used", lldp_rows, max_rows=75)
    body += render_table("AP / FDB Rows Used", fdb_rows, max_rows=75)

    return body
