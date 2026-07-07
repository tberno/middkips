from html import escape
import json

from app.services.mist_tool import (
    mist_context,
    mist_site_detail_context,
    mist_switch_detail_context,
)


def h(value):
    return escape("" if value is None else str(value), quote=True)


def first_present(row, names):
    if not isinstance(row, dict):
        return ""
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def link(label, href):
    return f'<a href="{h(href)}">{h(label)}</a>'


def normalize_mac(value):
    value = "" if value is None else str(value)
    return value.replace(":", "").replace("-", "").replace(".", "").lower()


def mist_drilldown_cell(key, value, row):
    """
    Add drilldowns without depending on one exact Mist schema.
    Site rows get site links.
    Switch/AP rows get switch detail links when site_id and mac are available.
    IP/MAC/name cells also get universal lookup links.
    """
    if value is None:
        return ""

    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, default=str)

    raw = str(value)
    if raw.lstrip().startswith(("<a ", "<span ", "<code ", "<strong ", "<em ")):
        return raw

    k = str(key or "").lower()
    site_id = first_present(row, ["site_id", "siteId", "siteid"])
    row_id = first_present(row, ["id", "site_id", "siteId", "siteid"])
    mac = first_present(row, ["mac", "mac_address", "device_mac", "switch_mac", "ap_mac"])
    mac_norm = normalize_mac(mac)

    # Site drilldown. Usually site rows have id/name or site_id/site_name.
    if k in ("site", "site_name", "site_name_display", "name") and row_id and ("site" in row or "site_id" in row or "siteId" in row or "siteid" in row):
        return link(raw, f"/tools/mist/site/{row_id}")

    if k in ("site_id", "siteid") and raw:
        return link(raw, f"/tools/mist/site/{raw}")

    # Switch/AP drilldown. Requires both site_id and mac.
    if site_id and mac_norm and k in ("mac", "mac_address", "device_mac", "switch_mac", "ap_mac", "name", "hostname", "device", "model"):
        return link(raw, f"/tools/mist/switch/{site_id}/{mac_norm}")

    # Useful universal lookup links.
    if k in ("ip", "ip_addr", "ip_address", "hostname", "host", "device", "name", "mac", "mac_address") and raw:
        return link(raw, f"/lookup?q={raw}")

    return h(raw)


def safe_cell(value):
    # Backward-compatible simple escaping when no row/key context is available.
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, default=str)
    s = str(value)
    if s.lstrip().startswith(("<a ", "<span ", "<code ", "<strong ", "<em ")):
        return s
    return h(s)


def render_table(title, rows, max_rows=100):
    rows = rows or []
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return f"""
        <section class="panel">
          <h2>{h(title)}</h2>
          <pre>{h(rows)}</pre>
        </section>
        """

    if not rows:
        return ""

    dict_rows = [r for r in rows if isinstance(r, dict)]
    if not dict_rows:
        return f"""
        <section class="panel">
          <h2>{h(title)} <span class="muted">({h(len(rows))})</span></h2>
          <pre>{h(rows[:max_rows])}</pre>
        </section>
        """

    keys = []
    for row in dict_rows[:max_rows]:
        for key in row.keys():
            if key not in keys:
                keys.append(key)

    head = "".join(f"<th>{h(k.replace('_', ' ').title())}</th>" for k in keys)

    body = ""
    for row in dict_rows[:max_rows]:
        body += "<tr>"
        for key in keys:
            body += f"<td>{mist_drilldown_cell(key, row.get(key, ''), row)}</td>"
        body += "</tr>"

    note = ""
    if len(rows) > max_rows:
        note = f'<div class="muted">Showing {h(max_rows)} of {h(len(rows))} rows.</div>'

    return f"""
    <section class="panel lookup-section">
      <h2>{h(title)} <span class="muted">({h(len(rows))})</span></h2>
      {note}
      <div class="table-wrap">
        <table class="report">
          <thead><tr>{head}</tr></thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </section>
    """


def render_cards(ctx):
    cards = [
        ("Configured", ctx.get("configured")),
        ("Sites", ctx.get("site_count")),
        ("Switches", ctx.get("total_switches")),
        ("Devices/APs", ctx.get("total_devices")),
        ("Clients", ctx.get("total_clients")),
        ("Online Switches", ctx.get("online_switches")),
        ("Offline Switches", ctx.get("offline_switches")),
        ("Online Devices", ctx.get("online_devices")),
        ("Offline Devices", ctx.get("offline_devices")),
        ("Errors", len(ctx.get("errors") or [])),
    ]

    html = '<section class="cards">'
    for title, value in cards:
        if value is None:
            continue
        html += f"""
        <article class="card">
          <div class="card-title">{h(title)}</div>
          <div class="card-value">{h(value)}</div>
        </article>
        """
    html += "</section>"
    return html


def render_errors(ctx):
    errors = ctx.get("errors") or []
    if not errors:
        return ""
    rows = []
    for e in errors:
        if isinstance(e, dict):
            rows.append(e)
        else:
            rows.append({"error": str(e)})
    return render_table("Mist Errors / Warnings", rows)


def render_mist_lookup_page(q="", limit=50):
    q = (q or "").strip()
    try:
        limit = int(limit or 50)
    except Exception:
        limit = 50

    ctx = mist_context(q=q, limit=limit)

    limit_options = sorted(set([10, 25, 50, 100, 250, limit]))
    limit_select = "".join(
        f'<option value="{n}" {"selected" if n == limit else ""}>{n}</option>'
        for n in limit_options
    )

    body = f"""
    <section class="panel">
      <h1>Mist Lookup{": " + h(q) if q else ""}</h1>
      <form class="unused-controls" method="get" action="/tools/mist">
        <input name="q" value="{h(q)}" placeholder="Search Mist AP, switch, site, client, MAC, hostname">
        <select name="limit">{limit_select}</select>
        <button class="button" type="submit">Lookup</button>
        <a class="button" href="/tools/mist">Clear</a>
      </form>
    </section>
    """

    body += render_cards(ctx)
    body += render_errors(ctx)

    for key, title in [
        ("device_matches", "Mist Device / AP Matches"),
        ("switch_matches", "Mist Switch Matches"),
        ("client_matches", "Mist Client Matches"),
        ("sites", "Mist Sites"),
        ("switch_rows", "Mist Switch Inventory"),
        ("pod_rows", "Mist Pod / Site Summary"),
    ]:
        body += render_table(title, ctx.get(key) or [], max_rows=limit)

    if not q:
        body += """
        <section class="panel">
          <p class="muted">Enter a Mist site, AP, switch, hostname, IP, MAC, or client search above.</p>
        </section>
        """

    return body


def render_generic_context_page(title, ctx):
    body = f"""
    <section class="panel">
      <h1>{h(title)}</h1>
    </section>
    """

    scalar_rows = []
    for key, value in sorted(ctx.items()):
        if isinstance(value, (list, dict, tuple)):
            continue
        if key in ("base_url", "token", "api_token"):
            continue
        scalar_rows.append({"field": key, "value": value})

    body += render_table("Summary", scalar_rows)

    for key, value in sorted(ctx.items()):
        if isinstance(value, list) and value:
            body += render_table(key.replace("_", " ").title(), value, max_rows=200)
        elif isinstance(value, dict) and value:
            body += render_table(key.replace("_", " ").title(), value, max_rows=200)

    return body


def render_mist_site_page(site_id, limit=100):
    ctx = mist_site_detail_context(site_id=site_id, limit=limit)
    return render_generic_context_page(f"Mist Site: {site_id}", ctx)


def render_mist_switch_page(site_id, mac):
    ctx = mist_switch_detail_context(site_id=site_id, mac=mac)
    return render_generic_context_page(f"Mist Switch: {mac}", ctx)
