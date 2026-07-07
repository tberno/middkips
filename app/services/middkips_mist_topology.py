from html import escape
import json
import re
from urllib.parse import quote_plus

from app.core.db import fetch_all
from app.services.mist_tool import mist_context


def h(value):
    return escape("" if value is None else str(value), quote=True)


def clean_mac(value):
    return re.sub(r"[^0-9a-f]", "", str(value or "").lower())


def norm_id(value):
    value = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "", value) or "unknown"


def first(row, keys):
    if not isinstance(row, dict):
        return ""
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def row_matches(row, q):
    if not q:
        return False
    return q.lower() in json.dumps(row, default=str).lower()


def classify_node(name, row=None):
    row = row or {}
    text = " ".join([
        str(name or ""),
        str(row.get("role", "")),
        str(row.get("type", "")),
        str(row.get("model", "")),
        str(row.get("remote_platform", "")),
    ]).lower()

    if "core" in text or "spine" in text:
        return "Core"

    if "dist" in text or "distribution" in text or "agg" in text or "aggregation" in text:
        return "Distribution"

    if str(row.get("type", "")).upper() == "AP":
        return "AP"

    if re.search(r"\bap\d+", text) or "mist ap" in text:
        return "AP"

    if "client" in text or "user" in text:
        return "Client"

    if "access" in text or "leaf" in text or "ex4" in text or "ex3" in text or "switch" in text:
        return "Access / Leaf"

    if "qfx" in text:
        return "Core"

    return "Other"


def add_node(nodes, name, row=None, role=None):
    if not name:
        return ""

    nid = norm_id(name)
    row = row or {}

    existing = nodes.get(nid)
    if not existing:
        nodes[nid] = {
            "id": nid,
            "name": str(name),
            "role": role or classify_node(name, row),
            "site": first(row, ["site", "site_name"]),
            "model": first(row, ["model", "remote_platform"]),
            "status": first(row, ["status", "state", "active"]),
            "ip": first(row, ["ip", "ip_addr", "ip_address"]),
            "mac": first(row, ["mac", "mac_address"]),
            "seed": bool(row.get("_seed")),
        }
    else:
        if role and existing.get("role") == "Other":
            existing["role"] = role
        for key, fields in {
            "site": ["site", "site_name"],
            "model": ["model", "remote_platform"],
            "status": ["status", "state", "active"],
            "ip": ["ip", "ip_addr", "ip_address"],
            "mac": ["mac", "mac_address"],
        }.items():
            if not existing.get(key):
                existing[key] = first(row, fields)
        if row.get("_seed"):
            existing["seed"] = True

    return nid


def add_edge(edges, src, dst, label="", source=""):
    if not src or not dst or src == dst:
        return

    key = tuple(sorted([str(src), str(dst)])) + (str(label or ""), str(source or ""))

    if key not in edges:
        edges[key] = {
            "src": src,
            "dst": dst,
            "label": str(label or ""),
            "source": str(source or ""),
        }


def selected_mist_rows(ctx, q):
    selected = []

    # These are already search-focused in mist_context().
    for key in ["switch_matches", "device_matches", "client_matches"]:
        for row in ctx.get(key) or []:
            if isinstance(row, dict):
                r = dict(row)
                r["_source_list"] = key
                r["_seed"] = True
                selected.append(r)

    # switch_rows may be broad, so only include rows that actually match q.
    for key in ["switch_rows"]:
        for row in ctx.get(key) or []:
            if isinstance(row, dict) and row_matches(row, q):
                r = dict(row)
                r["_source_list"] = key
                r["_seed"] = True
                selected.append(r)

    return selected


def librenms_devices_for_query(q, limit=50):
    q = (q or "").strip()
    if not q:
        return []

    like = f"%{q}%"
    limit = max(1, min(int(limit or 50), 200))

    sql = f"""
        SELECT device_id, hostname, sysName, status
        FROM devices
        WHERE hostname LIKE %s
           OR sysName LIKE %s
        LIMIT {limit}
    """

    try:
        return fetch_all(sql, (like, like))
    except Exception:
        return []


def lldp_edges_for_device_ids(device_ids, limit=250):
    ids = [int(x) for x in device_ids if str(x).isdigit()]
    if not ids:
        return []

    ids = ids[:100]
    placeholders = ",".join(["%s"] * len(ids))
    limit = max(1, min(int(limit or 250), 1000))

    sql = f"""
        SELECT
            l.id,
            l.protocol,
            l.active,
            ld.device_id AS local_device_id,
            ld.hostname AS local_device,
            lp.ifName AS local_port,
            lp.ifAlias AS local_descr,
            l.remote_device_id,
            COALESCE(rd.hostname, l.remote_hostname) AS remote_device,
            rp.ifName AS remote_port_name,
            l.remote_port,
            l.remote_platform
        FROM links l
        LEFT JOIN devices ld ON ld.device_id = l.local_device_id
        LEFT JOIN ports lp ON lp.port_id = l.local_port_id
        LEFT JOIN devices rd ON rd.device_id = l.remote_device_id
        LEFT JOIN ports rp ON rp.port_id = l.remote_port_id
        WHERE l.active = 1
          AND (
            l.local_device_id IN ({placeholders})
            OR l.remote_device_id IN ({placeholders})
          )
        LIMIT {limit}
    """

    params = tuple(ids + ids)

    try:
        return fetch_all(sql, params)
    except Exception as exc:
        return [{"error": str(exc), "source": "LibreNMS LLDP"}]


def fdb_edges_for_ap_rows(ap_rows, limit=500):
    mac_to_ap = {}

    for row in ap_rows or []:
        mac = clean_mac(row.get("mac"))
        name = row.get("name")
        if mac and name:
            mac_to_ap[mac] = name

    if not mac_to_ap:
        return []

    macs = list(mac_to_ap.keys())[:200]
    placeholders = ",".join(["%s"] * len(macs))
    limit = max(1, min(int(limit or 500), 2000))

    sql = f"""
        SELECT
            d.hostname AS switch_name,
            p.ifName AS switch_port,
            p.ifAlias AS switch_descr,
            f.mac_address,
            f.vlan_id
        FROM ports_fdb f
        JOIN ports p ON p.port_id = f.port_id
        JOIN devices d ON d.device_id = p.device_id
        WHERE REPLACE(REPLACE(REPLACE(LOWER(f.mac_address), ':', ''), '-', ''), '.', '') IN ({placeholders})
        LIMIT {limit}
    """

    try:
        rows = fetch_all(sql, tuple(macs))
    except Exception as exc:
        return [{"error": str(exc), "source": "LibreNMS FDB"}]

    out = []

    for row in rows:
        mac = clean_mac(row.get("mac_address"))
        ap_name = mac_to_ap.get(mac)
        if not ap_name:
            continue
        r = dict(row)
        r["ap_name"] = ap_name
        out.append(r)

    return out


def build_topology(q="", limit=50):
    q = (q or "").strip()
    ctx = mist_context(q=q, limit=limit)

    nodes = {}
    edges = {}
    errors = ctx.setdefault("errors", [])

    mist_rows = selected_mist_rows(ctx, q)

    seed_device_ids = set()

    for row in mist_rows:
        name = first(row, ["name", "hostname", "device_name", "switch_name", "ap_name", "mac"])
        add_node(nodes, name, row)

    # Add LibreNMS devices matching the search as hard seeds.
    lib_devices = librenms_devices_for_query(q, limit=75)
    for dev in lib_devices:
        dev["_seed"] = True
        name = dev.get("hostname") or dev.get("sysName")
        add_node(nodes, name, dev)
        if dev.get("device_id") is not None:
            seed_device_ids.add(dev.get("device_id"))

    # Also seed any Mist switch names by looking them up exactly-ish in LibreNMS.
    mist_switch_names = [
        r.get("name")
        for r in mist_rows
        if r.get("name") and (r.get("_source_list") in ("switch_matches", "switch_rows") or r.get("role"))
    ]

    for name in mist_switch_names:
        rows = librenms_devices_for_query(name, limit=10)
        for dev in rows:
            dev["_seed"] = True
            add_node(nodes, dev.get("hostname") or dev.get("sysName") or name, dev)
            if dev.get("device_id") is not None:
                seed_device_ids.add(dev.get("device_id"))

    # One-hop LLDP from seed switches/devices.
    lldp_rows = lldp_edges_for_device_ids(seed_device_ids, limit=250)

    for row in lldp_rows:
        if row.get("error"):
            errors.append(row)
            continue

        local_name = row.get("local_device")
        remote_name = row.get("remote_device")

        local_id = add_node(nodes, local_name, row)
        remote_id = add_node(nodes, remote_name, row)

        label_parts = []
        if row.get("local_port"):
            label_parts.append(str(row.get("local_port")))
        if row.get("remote_port_name") or row.get("remote_port"):
            label_parts.append(str(row.get("remote_port_name") or row.get("remote_port")))

        add_edge(edges, local_id, remote_id, " / ".join(label_parts), "LibreNMS LLDP")

    # AP-to-switch from FDB, using only matching Mist APs.
    ap_rows = [
        r for r in mist_rows
        if str(r.get("type", "")).upper() == "AP" or str(r.get("model", "")).upper().startswith("AP")
    ]

    fdb_rows = fdb_edges_for_ap_rows(ap_rows, limit=500)

    for row in fdb_rows:
        if row.get("error"):
            errors.append(row)
            continue

        switch_id = add_node(nodes, row.get("switch_name"), row)
        ap_id = add_node(nodes, row.get("ap_name"), {"name": row.get("ap_name"), "type": "AP", "_seed": True})

        label = str(row.get("switch_port") or "")
        if row.get("vlan_id"):
            label = f"{label} vlan {row.get('vlan_id')}".strip()

        add_edge(edges, switch_id, ap_id, label, "LibreNMS FDB")

    role_order = ["Core", "Distribution", "Access / Leaf", "AP", "Client", "Other"]
    grouped = {role: [] for role in role_order}

    for node in nodes.values():
        grouped.setdefault(node.get("role") or "Other", []).append(node)

    for role in grouped:
        grouped[role] = sorted(
            grouped[role],
            key=lambda n: (not n.get("seed"), str(n.get("name") or "").lower())
        )

    return ctx, grouped, list(edges.values()), mist_rows, lldp_rows, fdb_rows


def render_svg(grouped, edges):
    role_order = ["Core", "Distribution", "Access / Leaf", "AP", "Client", "Other"]

    connected = set()
    for edge in edges:
        connected.add(edge.get("src"))
        connected.add(edge.get("dst"))

    display_grouped = {}
    for role in role_order:
        nodes = grouped.get(role) or []

        if role == "Other":
            nodes = [n for n in nodes if n.get("id") in connected or n.get("seed")]

        display_grouped[role] = nodes[:25]

    x_positions = {
        "Core": 80,
        "Distribution": 350,
        "Access / Leaf": 630,
        "AP": 910,
        "Client": 1190,
        "Other": 1470,
    }

    node_pos = {}
    svg_nodes = ""
    svg_edges = ""
    max_height = 250

    for role in role_order:
        nodes = display_grouped.get(role) or []
        x = x_positions[role]
        y = 72

        svg_nodes += f'<text x="{x}" y="32" fill="#cbd5e1" font-size="14" font-weight="700">{h(role)} ({len(grouped.get(role) or [])})</text>'

        for node in nodes:
            node_pos[node["id"]] = (x, y)

            name = str(node.get("name") or "")
            meta = " ".join(str(v) for v in [node.get("ip"), node.get("model")] if v)

            stroke = "#38bdf8" if node.get("seed") else "#64748b"

            svg_nodes += f'''
            <g>
              <a href="/lookup?q={h(name)}">
                <rect x="{x}" y="{y}" width="225" height="46" rx="8" fill="#111827" stroke="{stroke}" stroke-width="1.4"></rect>
                <text x="{x + 10}" y="{y + 18}" fill="#f8fafc" font-size="12" font-weight="700">{h(name[:32])}</text>
                <text x="{x + 10}" y="{y + 37}" fill="#cbd5e1" font-size="10">{h(meta[:38])}</text>
              </a>
            </g>
            '''
            y += 62
            max_height = max(max_height, y + 40)

        hidden = len(grouped.get(role) or []) - len(nodes)
        if hidden > 0:
            svg_nodes += f'<text x="{x}" y="{y}" fill="#cbd5e1" font-size="11">+{hidden} hidden</text>'

    drawn = 0
    show_labels = len(edges) <= 60

    for edge in edges[:180]:
        src = edge.get("src")
        dst = edge.get("dst")

        if src not in node_pos or dst not in node_pos:
            continue

        x1, y1 = node_pos[src]
        x2, y2 = node_pos[dst]

        x1 += 225
        y1 += 23
        y2 += 23

        svg_edges += f'''
        <path d="M{x1},{y1} C{x1 + 80},{y1} {x2 - 80},{y2} {x2},{y2}" fill="none" stroke="#94a3b8" stroke-width="1.2" opacity="0.75"></path>
        '''

        if show_labels and edge.get("label"):
            lx = int((x1 + x2) / 2)
            ly = int((y1 + y2) / 2) - 4
            svg_edges += f'<text x="{lx}" y="{ly}" fill="#cbd5e1" font-size="9">{h(str(edge.get("label"))[:30])}</text>'

        drawn += 1

    width = 1760
    height = max_height

    return f'''
    <section class="panel">
      <h2>Logical Topology <span class="muted">({drawn} drawn links)</span></h2>
      <p class="muted">Seed nodes are highlighted. Edges come from LibreNMS LLDP and AP MAC/FDB correlation.</p>
      <div style="overflow:auto; border:1px solid rgba(148,163,184,.25); border-radius:10px; background:rgba(15,23,42,.25);">
        <svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;">
          {svg_edges}
          {svg_nodes}
        </svg>
      </div>
    </section>
    '''


def render_table(title, rows, max_rows=100):
    rows = rows or []

    if not rows:
        return ""

    keys = []
    for row in rows[:max_rows]:
        if isinstance(row, dict):
            for key in row.keys():
                if key not in keys and not key.startswith("_"):
                    keys.append(key)

    head = "".join(f"<th>{h(k)}</th>" for k in keys)
    body = ""

    for row in rows[:max_rows]:
        body += "<tr>"
        for key in keys:
            value = row.get(key, "") if isinstance(row, dict) else ""
            if isinstance(value, (dict, list)):
                value = json.dumps(value, default=str)[:500]
            body += f"<td>{h(value)}</td>"
        body += "</tr>"

    more = ""
    if len(rows) > max_rows:
        more = f'<p class="muted">Showing {max_rows} of {len(rows)} rows.</p>'

    return f'''
    <section class="panel">
      <h2>{h(title)} <span class="muted">({h(len(rows))})</span></h2>
      {more}
      <div class="table-wrap">
        <table class="report">
          <thead><tr>{head}</tr></thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </section>
    '''


def render_mist_topology_page(q="", limit=50):
    q = (q or "").strip()

    try:
        limit = int(limit or 50)
    except Exception:
        limit = 50

    form = f'''
    <section class="panel">
      <h1>Mist Logical Topology</h1>
      <form class="unused-controls" method="get" action="/tools/mist/topology">
        <input name="q" value="{h(q)}" placeholder="Site, pod, switch, or building, for example Kohn or SouthPOD">
        <input name="limit" value="{h(limit)}" style="max-width:90px">
        <button class="button" type="submit">Build Map</button>
        <a class="button" href="/tools/mist/topology">Clear</a>
        <a class="button" href="/tools/mist?q={h(q)}">Mist Lookup</a>
      </form>
      <p class="muted">Live logical map using Mist inventory plus LibreNMS LLDP/FDB links.</p>
    </section>
    '''

    if not q:
        return form + '''
        <section class="panel">
          <h2>Enter a filter to build a logical map</h2>
          <p class="muted">Start with a building, pod, or switch name. Full-campus topology is intentionally not rendered.</p>
        </section>
        '''

    ctx, grouped, edges, mist_rows, lldp_rows, fdb_rows = build_topology(q=q, limit=limit)

    cards = ""
    for role in ["Core", "Distribution", "Access / Leaf", "AP", "Client", "Other"]:
        count = len(grouped.get(role) or [])
        if count:
            cards += f'<article class="card"><div class="card-title">{h(role)}</div><div class="card-value">{h(count)}</div></article>'

    cards += f'<article class="card"><div class="card-title">Links</div><div class="card-value">{h(len(edges))}</div></article>'
    cards += f'<article class="card"><div class="card-title">Errors</div><div class="card-value">{h(len(ctx.get("errors") or []))}</div></article>'

    body = form + f'<section class="cards">{cards}</section>'
    body += render_svg(grouped, edges)

    if not edges:
        body += '''
        <section class="panel">
          <h2>No logical links found</h2>
          <p class="muted">Mist inventory matched, but no LLDP or AP/FDB edges were found for those seed devices.</p>
        </section>
        '''

    body += render_table("Discovered Links", edges, max_rows=150)
    body += render_table("Mist Seed Rows", mist_rows, max_rows=50)
    body += render_table("LLDP Rows Used", lldp_rows, max_rows=75)
    body += render_table("AP / FDB Rows Used", fdb_rows, max_rows=75)

    return body
