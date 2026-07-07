from html import escape
import json

from app.services.mist_tool import mist_context


def h(value):
    return escape(str(value if value is not None else ""))


def render_table(title, rows, max_rows=100):
    rows = rows or []

    if not rows:
        return f'''
        <section class="panel">
          <h2>{h(title)}</h2>
          <p class="muted">No matching Mist clients found.</p>
        </section>
        '''

    preferred = [
        "name", "hostname", "user", "username", "ip", "mac", "ap", "ap_name",
        "switch", "switch_name", "port", "ssid", "site", "vlan", "device_type",
        "os", "model", "manufacturer", "last_seen", "rssi", "snr"
    ]

    keys = []

    for key in preferred:
        for row in rows[:max_rows]:
            if isinstance(row, dict) and key in row and key not in keys:
                keys.append(key)

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

    return f'''
    <section class="panel">
      <h2>{h(title)} <span class="muted">({h(len(rows))})</span></h2>
      <div class="table-wrap">
        <table class="report">
          <thead><tr>{head}</tr></thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </section>
    '''


def render_mist_clients_v2_page(q="", limit=50):
    q = (q or "").strip()

    try:
        limit = int(limit or 50)
    except Exception:
        limit = 50

    form = f'''
    <section class="panel">
      <h1>Mist Client Search v2</h1>
      <form class="unused-controls" method="get" action="/tools/mist/clients-v2">
        <input name="q" value="{h(q)}" placeholder="Client name, username, MAC, IP, hostname, SSID, AP, or site">
        <input name="limit" value="{h(limit)}" style="max-width:90px">
        <button class="button" type="submit">Search Clients</button>
        <a class="button" href="/tools/mist/clients-v2">Clear</a>
        <a class="button" href="/tools/topology-v2?q={h(q)}">Topology v2</a>
      </form>
      <p class="muted">Client data is searched here only. It is not mixed into topology.</p>
    </section>
    '''

    if not q:
        return form + '''
        <section class="panel">
          <h2>Enter a client search</h2>
          <p class="muted">Search by MAC, IP, hostname, username, SSID, AP, or site.</p>
        </section>
        '''

    ctx = mist_context(q=q, limit=limit)
    clients = ctx.get("client_matches") or []

    cards = ""
    cards += f'<article class="card"><div class="card-title">Client Matches</div><div class="card-value">{h(len(clients))}</div></article>'
    cards += f'<article class="card"><div class="card-title">Errors</div><div class="card-value">{h(len(ctx.get("errors") or []))}</div></article>'

    body = form + f'<section class="cards">{cards}</section>'
    body += render_table("Mist Client Matches", clients, max_rows=limit)

    if ctx.get("errors"):
        body += render_table("Mist Client Search Errors", ctx.get("errors"), max_rows=25)

    return body
