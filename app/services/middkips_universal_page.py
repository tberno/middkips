from html import escape
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.services.universal_tool import universal_context


def h(value):
    return escape("" if value is None else str(value), quote=True)


def safe_cell(value):
    if value is None:
        return ""
    s = str(value)
    if s.lstrip().startswith(("<a ", "<span ", "<code ", "<strong ", "<em ")):
        return s
    return h(s)


def col_key(col):
    if isinstance(col, dict):
        return col.get("key") or col.get("field") or col.get("name") or col.get("label")
    return str(col)


def col_label(col):
    if isinstance(col, dict):
        return col.get("label") or col.get("name") or col.get("key") or ""
    return str(col)


def render_table(columns, rows):
    columns = columns or []
    rows = rows or []

    if not columns:
        return '<div class="muted">No columns.</div>'

    head = "".join(f"<th>{h(col_label(c))}</th>" for c in columns)

    if not rows:
        return (
            '<div class="table-wrap"><table class="report">'
            f"<thead><tr>{head}</tr></thead>"
            f'<tbody><tr><td colspan="{len(columns)}" class="muted">No results found.</td></tr></tbody>'
            "</table></div>"
        )

    body = ""
    for row in rows:
        body += "<tr>"
        for c in columns:
            key = col_key(c)
            value = row.get(key, "") if isinstance(row, dict) else ""
            body += f"<td>{safe_cell(value)}</td>"
        body += "</tr>"

    return (
        '<div class="table-wrap"><table class="report">'
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table></div>"
    )


def middkips_source_link(link):
    if not link:
        return ""

    parts = urlsplit(str(link))
    path = parts.path or ""
    query = dict(parse_qsl(parts.query, keep_blank_values=True))

    mapping = {
        "/tools/lookup/devices": "devices",
        "/tools/lookup/interfaces": "reports/interface-statistics",
        "/tools/lookup/ips": "reports/arp-ip",
        "/tools/lookup/macs": "reports/mac-table",
        "/tools/lookup/vlans": "reports/vlans",
        "/tools/lookup/events": "reports/events",
        "/tools/solidserver": "tools/solidserver",
        "/tools/mist": "tools/mist",
    }

    new_path = mapping.get(path, path.lstrip("/"))

    if new_path.startswith("reports/") or new_path == "devices":
        query.pop("limit", None)

    new_query = urlencode(query)
    return urlunsplit(("", "", new_path, new_query, parts.fragment))


def split_correlated_rows(rows):
    primary = []
    related = []

    for row in rows or []:
        if not isinstance(row, dict):
            related.append(row)
            continue

        sources = str(row.get("sources", "") or "").lower()
        kind = str(row.get("kind", "") or "").lower()
        ip = str(row.get("ip", "") or "").strip()
        status = str(row.get("status", "") or "").strip()
        model = str(row.get("model", "") or "").strip()
        serial = str(row.get("serial", "") or "").strip()
        role = str(row.get("role", "") or "").strip()

        vlan_only = sources.strip() == "librenms/vlans"
        empty_identity = not any([ip, status, model, serial, role])

        if vlan_only and kind in ("client", "vlan", "") and empty_identity:
            related.append(row)
            continue

        primary.append(row)

    def score(row):
        if not isinstance(row, dict):
            return 999

        sources = str(row.get("sources", "") or "").lower()
        kind = str(row.get("kind", "") or "").lower()
        value = 100

        if "mist" in sources:
            value -= 40
        if "solidserver" in sources:
            value -= 35
        if "librenms/devices" in sources:
            value -= 30
        if "librenms/interfaces" in sources:
            value -= 25
        if "librenms/ips" in sources:
            value -= 20
        if "librenms/mac" in sources:
            value -= 15

        if kind in ("device", "switch", "ap"):
            value -= 20
        elif kind in ("interface", "client", "dns"):
            value -= 10

        if row.get("ip"):
            value -= 10
        if row.get("status"):
            value -= 5

        return value

    return sorted(primary, key=score), related


def source_cards_from_sections(sections):
    totals = {}

    for sec in sections or []:
        title = str(sec.get("title") or "")
        count = sec.get("count", 0)

        if title.startswith("LibreNMS"):
            group = "LibreNMS"
        elif title.startswith("SolidServer"):
            group = "SolidServer"
        elif title.startswith("Mist"):
            group = "Mist"
        else:
            group = "Other"

        try:
            n = int(count or 0)
        except Exception:
            n = 0

        totals[group] = totals.get(group, 0) + n

    html = '<section class="cards source-cards">'
    for group in ["LibreNMS", "SolidServer", "Mist", "Other"]:
        if group in totals:
            html += (
                '<article class="card">'
                f'<div class="card-title">{h(group)}</div>'
                f'<div class="card-value">{h(totals[group])}</div>'
                '</article>'
            )
    html += "</section>"
    return html


def render_section(sec, idx):
    title = sec.get("title") or f"Section {idx}"
    count = sec.get("count", 0)
    preview_rows = sec.get("preview_rows") or sec.get("rows") or []
    preview_columns = sec.get("preview_columns") or sec.get("columns") or []
    rows = sec.get("rows") or []
    columns = sec.get("columns") or preview_columns

    link = middkips_source_link(sec.get("link") or "")
    link_html = f'<a class="button" href="{h(link)}">Open source lookup</a>' if link else ""

    full_html = ""
    if rows and len(rows) > len(preview_rows):
        full_html = (
            '<details class="lookup-details">'
            f'<summary>Show full loaded table ({h(len(rows))} rows)</summary>'
            f'{render_table(columns, rows)}'
            '</details>'
        )

    return f"""
    <section class="panel lookup-section" id="sec-{idx}">
      <div class="panel-head">
        <h2>{h(title)} <span class="muted">({h(count)})</span></h2>
        <div class="actions">{link_html}</div>
      </div>
      {render_table(preview_columns, preview_rows)}
      {full_html}
    </section>
    """


def render_universal_lookup_page(q="", limit=50):
    q = (q or "").strip()

    try:
        limit = int(limit or 50)
    except Exception:
        limit = 50

    limit_options = sorted(set([10, 25, 50, 100, 250, limit]))
    limit_select = "".join(
        f'<option value="{n}" {"selected" if n == limit else ""}>{n}</option>'
        for n in limit_options
    )

    form = f"""
    <section class="panel">
      <h1>Universal Lookup{": " + h(q) if q else ""}</h1>
      <form class="unused-controls" method="get" action="/lookup">
        <input name="q" value="{h(q)}" placeholder="Search device, switch, IP, MAC, VLAN, interface, DNS record, username, AP">
        <select name="limit">{limit_select}</select>
        <button class="button" type="submit">Lookup</button>
        <a class="button" href="/lookup">Clear</a>
      </form>
    </section>
    """

    report_actions = f"""
    <section class="panel report-actions">
      <div class="actions">
        <a class="button" href="lookup.csv?{urlencode({'q': q, 'limit': limit})}">Download CSV</a>
        <button class="button" type="button" onclick="window.print()">Print / Save PDF</button>
        <button class="button" type="button" onclick="navigator.clipboard && navigator.clipboard.writeText(window.location.href)">Copy Report Link</button>
      </div>
    </section>
    """

    if not q:
        return form + report_actions + """
        <section class="panel">
          <p>Search anything: switch name, interface, IP, MAC, VLAN, DNS name, DHCP record, AP, username, or event text.</p>
        </section>
        """

    ctx = universal_context(q=q, limit=limit)
    sections = ctx.get("sections", []) or []
    errors = ctx.get("errors", []) or []
    entity_rows = ctx.get("entity_rows", []) or []

    primary_entity_rows, related_entity_rows = split_correlated_rows(entity_rows)

    summary = f"""
    <section class="cards">
      <article class="card"><div class="card-title">Primary Correlations</div><div class="card-value">{h(len(primary_entity_rows))}</div></article>
      <article class="card"><div class="card-title">Related / Low Confidence</div><div class="card-value">{h(len(related_entity_rows))}</div></article>
      <article class="card"><div class="card-title">Sections</div><div class="card-value">{h(len(sections))}</div></article>
      <article class="card"><div class="card-title">Errors</div><div class="card-value">{h(len(errors))}</div></article>
    </section>
    """ + source_cards_from_sections(sections)

    toc = ""
    for i, sec in enumerate(sections, start=1):
        toc += f'<a class="button" href="#sec-{i}">{h(sec.get("title"))} ({h(sec.get("count", 0))})</a>'

    toc_html = f"""
    <section class="panel">
      <h2>Result Sections</h2>
      <div class="actions">{toc or '<span class="muted">No result sections.</span>'}</div>
    </section>
    """

    correlated_columns = [
        {"key": "kind", "label": "Kind"},
        {"key": "name", "label": "Name"},
        {"key": "sources", "label": "Sources"},
        {"key": "status", "label": "Status"},
        {"key": "ip", "label": "IP"},
        {"key": "mac", "label": "MAC"},
        {"key": "site", "label": "Site"},
        {"key": "role", "label": "Role"},
        {"key": "model", "label": "Model"},
        {"key": "version", "label": "Version / OS"},
        {"key": "serial", "label": "Serial"},
    ]

    correlated = f"""
    <section class="panel lookup-section">
      <h2>Primary Correlated Summary <span class="muted">({h(len(primary_entity_rows))})</span></h2>
      {render_table(correlated_columns, primary_entity_rows[:25])}
    </section>
    """

    if len(primary_entity_rows) > 25:
        correlated += f"""
        <section class="panel lookup-section">
          <details class="lookup-details">
            <summary>Show all primary correlations ({h(len(primary_entity_rows))})</summary>
            {render_table(correlated_columns, primary_entity_rows)}
          </details>
        </section>
        """

    if related_entity_rows:
        correlated += f"""
        <section class="panel lookup-section">
          <details class="lookup-details">
            <summary>Related / Low Confidence Matches ({h(len(related_entity_rows))})</summary>
            <p class="muted">Usually broad VLAN/name matches. Kept for discovery, hidden from the primary summary.</p>
            {render_table(correlated_columns, related_entity_rows)}
          </details>
        </section>
        """

    sections_html = "".join(render_section(sec, i) for i, sec in enumerate(sections, start=1))

    errors_html = ""
    if errors:
        errors_html = f"""
        <section class="panel lookup-section">
          <h2>Source Errors / Warnings <span class="muted">({h(len(errors))})</span></h2>
          {render_table([
            {"key": "source", "label": "Source"},
            {"key": "error", "label": "Error"},
          ], errors)}
        </section>
        """

    return form + report_actions + summary + correlated + toc_html + sections_html + errors_html
