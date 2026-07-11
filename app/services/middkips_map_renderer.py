from collections import defaultdict
from html import escape as h


MAP_TITLES = {
    "mist-pods": "Mist Pod Map",
    "service-block": "Service Block Map",
    "legacy-datacenter": "Legacy Datacenter Map",
    "edge": "Edge Map",
}


def _badge(text, cls=""):
    cls_s = f" {cls}" if cls else ""
    return f'<span class="badge{cls_s}">{h(text)}</span>'


def _node_card(node, links_by_node):
    node_id = node.get("id") or ""
    label = node.get("label") or node_id
    role = node.get("role") or "unknown"
    source_system = node.get("source_system") or "unknown"
    status = node.get("status") or "unknown"
    detail_url = node.get("detail_url") or "#"
    management_url = node.get("management_url") or ""
    face_url = node.get("face_url") or ""

    links = links_by_node.get(node_id, [])
    redundant_links = [l for l in links if l.get("redundant")]

    status_cls = "good" if status == "up" else "bad" if status == "down" else "warn"

    link_preview = ""
    if links:
        rows = []
        for link in links[:6]:
            peer = link.get("target_label") or link.get("target") or ""
            label_text = link.get("label") or link.get("type") or "link"
            red = " redundant" if link.get("redundant") else ""
            rows.append(
                f"""
                <li>
                  <a href="{h(link.get('detail_url') or '#')}">{h(peer)}</a>
                  <span class="muted">{h(label_text)}</span>
                  { _badge("redundant", "good") if red else "" }
                </li>
                """
            )
        more = ""
        if len(links) > 6:
            more = f"<li class='muted'>... {len(links) - 6} more links</li>"
        link_preview = f"<ul class='map-link-list'>{''.join(rows)}{more}</ul>"
    else:
        link_preview = "<p class='muted'>No links found.</p>"

    face_html = ""
    if face_url:
        face_html = f'<div class="device-face-wrap"><img class="device-face-img" src="{h(face_url)}" alt="{h(label)} front panel" loading="lazy" /></div>'

    return f"""
    <section class="map-node-card">
      {face_html}
      <div class="map-card-head">
        <h3><a href="{h(detail_url)}">{h(label)}</a></h3>
        {_badge(status, status_cls)}
      </div>
      <div class="map-node-meta">
        {_badge(role)}
        {_badge(source_system)}
        {_badge(f"{len(links)} links")}
        {_badge(f"{len(redundant_links)} redundant", "good" if redundant_links else "")}
      </div>
      {link_preview}
      <div class="actions compact-actions">
        <a class="button" href="{h(detail_url)}">Drill Down</a>
        {f'<a class="button" href="{h(management_url)}">Device</a>' if management_url else ''}
      </div>
    </section>
    """


def render_map_page(map_data, q="", focus="", redundant_only=False):
    map_type = map_data.get("map_type") or "map"
    title = MAP_TITLES.get(map_type, map_type)

    nodes = map_data.get("nodes") or []
    links = map_data.get("links") or []
    groups = map_data.get("groups") or []

    node_by_id = {n.get("id"): n for n in nodes}

    enriched_links = []
    for link in links:
        source = link.get("source")
        target = link.get("target")
        source_label = (node_by_id.get(source) or {}).get("label") or source
        target_label = (node_by_id.get(target) or {}).get("label") or target

        l1 = dict(link)
        l1["target_label"] = target_label
        l1["peer_label"] = target_label
        enriched_links.append(l1)

        l2 = dict(link)
        l2["source"] = target
        l2["target"] = source
        l2["target_label"] = source_label
        l2["peer_label"] = source_label
        enriched_links.append(l2)

    links_by_node = defaultdict(list)
    for link in enriched_links:
        if redundant_only and not link.get("redundant"):
            continue
        links_by_node[link.get("source")].append(link)

    nodes_by_group = defaultdict(list)
    for node in nodes:
        if focus:
            text = " ".join(str(node.get(k) or "").lower() for k in ("label", "role", "group", "id"))
            if focus.lower() not in text:
                connected = any(
                    focus.lower() in str(link.get("target_label") or "").lower()
                    for link in links_by_node.get(node.get("id"), [])
                )
                if not connected:
                    continue

        if redundant_only and not links_by_node.get(node.get("id")):
            continue

        nodes_by_group[node.get("group") or "ungrouped"].append(node)

    group_meta = {g.get("id"): g for g in groups}

    sections = []
    for group_id, group_nodes in sorted(nodes_by_group.items(), key=lambda x: x[0]):
        meta = group_meta.get(group_id) or {}
        label = meta.get("label") or group_id
        color = meta.get("color") or "#38bdf8"

        cards = "".join(_node_card(node, links_by_node) for node in group_nodes)

        sections.append(f"""
        <section class="map-group-panel" style="--map-group-color:{h(color)}">
          <div class="map-group-header">
            <h2>{h(label)}</h2>
            <div>
              {_badge(f"{len(group_nodes)} nodes")}
            </div>
          </div>
          <div class="map-node-grid">
            {cards}
          </div>
        </section>
        """)

    if not sections:
        sections.append("<p class='muted'>No nodes matched this map/filter.</p>")

    checked = "checked" if redundant_only else ""

    return f"""
<h1>{h(title)}</h1>
<p class="muted">
  Real map view generated from normalized nodes and LLDP links. Click any device to drill down.
</p>

<form class="unused-controls" method="get">
  <label>
    Search/focus
    <input name="q" value="{h(q)}" placeholder="fabric-core, dfl, voter, dist-eastpod..." />
  </label>
  <label>
    <input type="checkbox" name="redundant_only" value="1" {checked} />
    Redundant links only
  </label>
  <button class="button" type="submit">Apply</button>
  <a class="button" href="/tools/maps/{h(map_type)}">Clear</a>
  <a class="button" href="/api/maps/{h(map_type)}?limit=300">JSON</a>
</form>

<div class="map-summary-row">
  {_badge(f"{len(nodes)} nodes")}
  {_badge(f"{len(links)} links")}
  {_badge(f"{len(groups)} groups")}
</div>

{''.join(sections)}
"""


def render_mist_pod_page(map_data, q="", focus="", redundant_only=False):
    """
    Purpose-built Mist fabric view:
    Core at top, then pods with Distribution and Access sections.
    Search remains optional. The default page should already be useful.
    """
    nodes = map_data.get("nodes") or []
    links = map_data.get("links") or []

    node_by_id = {n.get("id"): n for n in nodes}

    enriched_links = []
    for link in links:
        source = link.get("source")
        target = link.get("target")
        source_label = (node_by_id.get(source) or {}).get("label") or source
        target_label = (node_by_id.get(target) or {}).get("label") or target

        l1 = dict(link)
        l1["target_label"] = target_label
        enriched_links.append(l1)

        l2 = dict(link)
        l2["source"] = target
        l2["target"] = source
        l2["target_label"] = source_label
        enriched_links.append(l2)

    from collections import defaultdict
    links_by_node = defaultdict(list)
    for link in enriched_links:
        if redundant_only and not link.get("redundant"):
            continue
        links_by_node[link.get("source")].append(link)

    def pod_from_node(node):
        group = (node.get("group") or "").lower()
        label = (node.get("label") or "").lower()

        for pod in ("east", "north", "south", "west", "test"):
            if group.startswith(pod + "-") or pod + "pod" in label:
                return pod

        if "dfl" in group or "dfl" in label:
            return "dfl"
        if "voter" in group or "voter" in label or "vtr" in label:
            return "voter"

        return "unsorted"

    def keep_node(node):
        if not q:
            return True

        needle = q.lower()
        text = " ".join(str(node.get(k) or "").lower() for k in ("label", "role", "group", "source_system"))
        if needle in text:
            return True

        return any(needle in str(link.get("target_label") or "").lower() for link in links_by_node.get(node.get("id"), []))

    core_nodes = []
    pod_buckets = {
        "east": {"distribution": [], "access": [], "other": []},
        "north": {"distribution": [], "access": [], "other": []},
        "south": {"distribution": [], "access": [], "other": []},
        "west": {"distribution": [], "access": [], "other": []},
        "test": {"distribution": [], "access": [], "other": []},
        "dfl": {"distribution": [], "service": [], "access": [], "other": []},
        "voter": {"distribution": [], "service": [], "access": [], "other": []},
        "unsorted": {"distribution": [], "service": [], "access": [], "edge": [], "datacenter": [], "other": []},
    }

    for node in nodes:
        if not keep_node(node):
            continue

        role = node.get("role") or "unknown"

        if role in ("core", "fabric-core"):
            core_nodes.append(node)
            continue

        pod = pod_from_node(node)
        bucket = pod_buckets.setdefault(pod, {"distribution": [], "service": [], "access": [], "other": []})

        if role in bucket:
            bucket[role].append(node)
        elif role == "firewall":
            bucket.setdefault("edge", []).append(node)
        elif role == "datacenter":
            bucket.setdefault("datacenter", []).append(node)
        else:
            bucket.setdefault("other", []).append(node)

    def render_cards(items):
        if not items:
            return "<p class='muted'>None found.</p>"
        return "<div class='map-node-grid'>" + "".join(_node_card(n, links_by_node) for n in items) + "</div>"

    def count_bucket(bucket):
        return sum(len(v) for v in bucket.values())

    pod_titles = {
        "east": "East Pod",
        "north": "North Pod",
        "south": "South Pod",
        "west": "West Pod",
        "test": "Test Pod",
        "dfl": "DFL Local / Services",
        "voter": "Voter Local / Services",
        "unsorted": "Unsorted / Non-Mist Candidates",
    }

    sections = []

    sections.append(f"""
<section class="map-group-panel" style="--map-group-color:#39ff14">
  <div class="map-group-header">
    <h2>Mist Fabric Core</h2>
    <div>{_badge(str(len(core_nodes)) + " nodes")}</div>
  </div>
  {render_cards(core_nodes)}
</section>
""")

    for pod in ("east", "north", "south", "west", "test", "dfl", "voter", "unsorted"):
        bucket = pod_buckets.get(pod) or {}
        total = count_bucket(bucket)
        if total == 0:
            continue

        parts = []

        for role_title, key in (
            ("Distribution", "distribution"),
            ("Services", "service"),
            ("Access", "access"),
            ("Edge Boundary", "edge"),
            ("Datacenter / Legacy", "datacenter"),
            ("Other", "other"),
        ):
            items = bucket.get(key) or []
            if not items:
                continue

            parts.append(f"""
            <div class="mist-role-section">
              <h3>{h(role_title)} <span class="muted">({len(items)})</span></h3>
              {render_cards(items)}
            </div>
            """)

        sections.append(f"""
<section class="map-group-panel" style="--map-group-color:#38bdf8">
  <div class="map-group-header">
    <h2>{h(pod_titles.get(pod, pod))}</h2>
    <div>{_badge(str(total) + " nodes")}</div>
  </div>
  {''.join(parts)}
</section>
""")

    checked = "checked" if redundant_only else ""

    return f"""
<h1>Mist Pod Map</h1>
<p class="muted">
  Default view is grouped by pod. Search is optional and only narrows the map.
</p>

<form class="unused-controls" method="get">
  <label>
    Search/focus
    <input name="q" value="{h(q)}" placeholder="fabric-core, eastpod, dfl, voter, dist..." />
  </label>
  <label>
    <input type="checkbox" name="redundant_only" value="1" {checked} />
    Redundant links only
  </label>
  <button class="button" type="submit">Apply</button>
  <a class="button" href="/tools/maps/mist-pods">Clear</a>
  <a class="button" href="/api/maps/mist-pods?limit=300">JSON</a>
</form>

<div class="map-summary-row">
  {_badge(str(len(nodes)) + " nodes")}
  {_badge(str(len(links)) + " links")}
</div>

{''.join(sections)}
"""
