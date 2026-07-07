from html import escape
from urllib.parse import urlencode


def h(value):
    return escape("" if value is None else str(value), quote=True)


def qlink(path, q):
    q = (q or "").strip()
    if not q:
        return path
    return path + "?" + urlencode({"q": q})


def render_lookup_hub_page(q=""):
    q = (q or "").strip()

    tools = [
        ("Universal Lookup", "/lookup", "One search across LibreNMS, SolidServer, Mist, IPs, MACs, VLANs, and events."),
        ("LibreNMS Devices", "/devices", "Manual device lookup."),
        ("LibreNMS Interfaces", "/reports/interface-statistics", "Manual interface / port lookup."),
        ("LibreNMS ARP / IP", "/reports/arp-ip", "Manual IP and ARP lookup."),
        ("LibreNMS MAC / FDB", "/reports/mac-table", "Manual MAC table lookup."),
        ("LibreNMS VLANs", "/reports/vlans", "Manual VLAN lookup."),
        ("LibreNMS Events", "/reports/events", "Manual event lookup."),
        ("SolidServer DDI", "/tools/solidserver", "Manual DNS / DHCP / IPAM lookup."),
        ("Mist Lookup", "/tools/mist", "Manual Mist AP / switch / client / site lookup."),
    ]

    cards = ""
    for title, path, desc in tools:
        cards += f"""
        <article class="card lookup-card">
          <div class="card-title">{h(title)}</div>
          <p class="muted">{h(desc)}</p>
          <a class="button" href="{h(qlink(path, q))}">Open</a>
        </article>
        """

    return f"""
    <section class="panel">
      <h1>Lookup Tools</h1>
      <form class="unused-controls" method="get" action="/tools/lookup">
        <input name="q" value="{h(q)}" placeholder="Optional search term to pass into each source lookup">
        <button class="button" type="submit">Set Search</button>
        <a class="button" href="/tools/lookup">Clear</a>
      </form>
    </section>

    <section class="cards lookup-card-grid">
      {cards}
    </section>
    """
