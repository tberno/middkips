from html import escape as h


def _card(title, desc, href, badge="Planned"):
    return f"""
    <a class="topology-card map-card" href="{h(href)}">
      <div class="map-card-head">
        <h3>{h(title)}</h3>
        <span class="badge warn">{h(badge)}</span>
      </div>
      <p>{h(desc)}</p>
    </a>
    """


def render_maps_home():
    return """
<h1>MiddKiPS Maps</h1>
<p class="muted">
  The old all-in-one topology views have been archived. This is the new clean map workspace.
</p>

<div class="legacy-note">
  Old maps were mixing Mist inventory, legacy topology, clients, APs, edge, datacenter, and service gear
  into one giant graph. That made the view noisy and hard to trust. The replacement maps are split by purpose.
</div>

<h2 class="section-title">New Map Workspace</h2>
<div class="topology-grid">
  """ + _card(
        "Mist Pod Map",
        "Mist-first campus/fabric map with neon colors per pod. This should become the primary operational Mist view.",
        "/tools/maps/mist-pods",
        "New",
    ) + _card(
        "Service Block Map",
        "Bridge map showing how Mist connects to shared services and the legacy network handoff points.",
        "/tools/maps/service-block",
        "New",
    ) + _card(
        "Legacy Datacenter Map",
        "Legacy datacenter network map only. Kept separate so it does not pollute the Mist fabric view.",
        "/tools/maps/legacy-datacenter",
        "New",
    ) + _card(
        "Edge Map",
        "WAN, edge, firewall, external handoff, and tunnel-oriented view.",
        "/tools/maps/edge",
        "New",
    ) + _card(
        "TV Map Dashboard",
        "Wallboard view that brings the smaller maps together into one operations dashboard.",
        "/tv/maps",
        "New",
    ) + """
</div>

<h2 class="section-title">Archived Maps</h2>
<p class="muted">
  Old topology routes are still present as archived placeholders so bookmarks do not break, but they are no longer advertised as active tools.
</p>
<div class="actions">
  <a class="button" href="/tools/maps/archive">Archived Map Routes</a>
</div>
"""


def render_archived_maps_index():
    old_routes = [
        ("/tools/topology-vmap", "Old V-map renderer"),
        ("/tools/topology-v2", "Old topology v2"),
        ("/tools/topology-v2-split", "Old split topology"),
        ("/tools/mist/topology", "Old Mist logical topology"),
        ("/tv/topology", "Old TV topology"),
        ("/tv/topology-wallboard", "Old wallboard topology"),
    ]

    rows = "\n".join(
        f"<tr><td><code>{h(route)}</code></td><td>{h(desc)}</td><td><span class='badge warn'>Archived</span></td></tr>"
        for route, desc in old_routes
    )

    return f"""
<h1>Archived Map Routes</h1>
<p class="muted">
  These old topology routes were intentionally archived during the map reset.
</p>

<div class="table-wrap">
<table>
  <thead>
    <tr>
      <th>Route</th>
      <th>Previous Purpose</th>
      <th>Status</th>
    </tr>
  </thead>
  <tbody>
    {rows}
  </tbody>
</table>
</div>

<div class="actions" style="margin-top:16px">
  <a class="button" href="/tools/maps">Back to New Maps</a>
</div>
"""


def render_archived_map_page(name, replacement="/tools/maps"):
    return f"""
<h1>Archived Map</h1>
<div class="legacy-note">
  <strong>{h(name)}</strong> has been archived. The old topology/map views were becoming too noisy and are being rebuilt as separate, purpose-specific maps.
</div>

<h2 class="section-title">Use the new map workspace</h2>
<p class="muted">
  Start from the new maps home and choose the Mist Pod, Service Block, Legacy Datacenter, or Edge map.
</p>

<div class="actions">
  <a class="button" href="{h(replacement)}">Open New Maps</a>
  <a class="button" href="/tools/maps/archive">View Archived Routes</a>
</div>
"""


def _placeholder_map(title, desc, legend_rows, next_steps):
    legend = "\n".join(
        f"""
        <div class="map-legend-item">
          <span class="map-color-chip" style="background:{h(color)}"></span>
          <span>{h(label)}</span>
        </div>
        """
        for color, label in legend_rows
    )

    steps = "\n".join(f"<li>{h(step)}</li>" for step in next_steps)

    return f"""
<h1>{h(title)}</h1>
<p class="muted">{h(desc)}</p>

<div class="legacy-note">
  This is the new clean scaffold. Next pass will wire it to normalized map data instead of reusing the old all-in-one topology graph.
</div>

<h2 class="section-title">Color Plan</h2>
<div class="panel">
  <div class="map-legend">
    {legend}
  </div>
</div>

<h2 class="section-title">Build Plan</h2>
<div class="panel">
  <ol>
    {steps}
  </ol>
</div>

<div class="actions" style="margin-top:16px">
  <a class="button" href="/tools/maps">Back to Maps</a>
</div>
"""


def render_mist_pod_map():
    return _placeholder_map(
        "Mist Pod Map",
        "Mist-first fabric map with neon pod colors and a clean pod/service/access hierarchy.",
        [
            ("#39ff14", "East Pod"),
            ("#00e5ff", "West Pod"),
            ("#a855f7", "Datacenter Pod"),
            ("#ff9f1c", "Transition / Legacy Handoff"),
            ("#ff3131", "Problem / Down / Attention"),
        ],
        [
            "Normalize Mist sites into pod groups.",
            "Show core, service, distribution, and access layers only.",
            "Hide clients by default.",
            "Show APs only as an optional layer.",
            "Use neon pod colors consistently across cards, links, and legends.",
        ],
    )


def render_service_block_map():
    return _placeholder_map(
        "Service Block Map",
        "Bridge map showing how Mist fabric links into shared services and the legacy network.",
        [
            ("#39ff14", "Mist Fabric"),
            ("#ff9f1c", "Service Block / Handoff"),
            ("#38bdf8", "Legacy Network"),
            ("#a855f7", "Shared Services"),
        ],
        [
            "Identify service handoff nodes between Mist and legacy.",
            "Draw only service, core, and distribution relationships.",
            "Keep access switches and clients out of this view.",
            "Highlight transition paths clearly.",
        ],
    )


def render_legacy_datacenter_map():
    return _placeholder_map(
        "Legacy Datacenter Map",
        "Legacy datacenter network map only, separated from Mist so each view stays clean.",
        [
            ("#38bdf8", "Legacy Core / Distribution"),
            ("#a855f7", "Datacenter Services"),
            ("#ff9f1c", "Migration / Handoff"),
            ("#ff3131", "Problem / Down / Attention"),
        ],
        [
            "Inventory legacy datacenter nodes separately.",
            "Group datacenter core, service, and access/leaf devices.",
            "Do not mix Mist AP/client inventory into this map.",
            "Expose links to device detail and config views.",
        ],
    )


def render_edge_map():
    return _placeholder_map(
        "Edge Map",
        "Edge and perimeter network map: WAN, firewalls, tunnels, external handoffs, and border/service-adjacent components.",
        [
            ("#ff3131", "Internet / WAN / Critical Edge"),
            ("#ff9f1c", "Firewall / Security Boundary"),
            ("#38bdf8", "External / ISP / Tunnel"),
            ("#a855f7", "Shared Edge Services"),
            ("#39ff14", "Healthy / Active Path"),
        ],
        [
            "Inventory WAN, firewall, tunnel, and edge handoff devices separately.",
            "Keep campus access, Mist APs, and clients out of this view.",
            "Show active/standby or primary/secondary paths clearly.",
            "Expose links to firewall, tunnel, DNS, and route health detail views.",
            "Use this as the edge tile in the TV map dashboard.",
        ],
    )


def render_tv_maps_dashboard():
    return """
<h1>TV Map Dashboard</h1>
<p class="muted">
  Wallboard dashboard for the new separated map views. Each map stays focused, because one monster map was how we got into this mess.
</p>

<div class="map-dashboard-grid">
  <section class="panel map-dashboard-tile">
    <div class="map-card-head">
      <h2>Mist Pod Map</h2>
      <span class="badge good">Primary</span>
    </div>
    <p class="muted">Mist pod/fabric view with neon pod coloring.</p>
    <a class="button" href="/tools/maps/mist-pods">Open</a>
  </section>

  <section class="panel map-dashboard-tile">
    <div class="map-card-head">
      <h2>Service Block Map</h2>
      <span class="badge warn">Bridge</span>
    </div>
    <p class="muted">Mist-to-legacy handoff and shared service blocks.</p>
    <a class="button" href="/tools/maps/service-block">Open</a>
  </section>

  <section class="panel map-dashboard-tile">
    <div class="map-card-head">
      <h2>Legacy Datacenter Map</h2>
      <span class="badge">Legacy</span>
    </div>
    <p class="muted">Datacenter-only legacy topology.</p>
    <a class="button" href="/tools/maps/legacy-datacenter">Open</a>
  </section>

  <section class="panel map-dashboard-tile">
    <div class="map-card-head">
      <h2>Edge Map</h2>
      <span class="badge bad">Edge</span>
    </div>
    <p class="muted">WAN, firewall, tunnel, and edge handoff topology.</p>
    <a class="button" href="/tools/maps/edge">Open</a>
  </section>
</div>

<h2 class="section-title">Dashboard Build Notes</h2>
<div class="legacy-note">
  Next pass should replace these tiles with live compact map panels. For now this gives us the route structure and dashboard layout without dragging the old topology monster back inside.
</div>
"""
