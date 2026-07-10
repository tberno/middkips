from __future__ import annotations
import datetime

import base64
import html
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

CLEARPASS_BASE_URL = os.getenv("CLEARPASS_BASE_URL", "https://cpauth1-ca.middlebury.edu").rstrip("/")
CLEARPASS_API_PREFIX = os.getenv("CLEARPASS_API_PREFIX", "/api").rstrip("/")
CLEARPASS_TOKEN_PATH = os.getenv("CLEARPASS_TOKEN_PATH", "/api/oauth").strip()
CLEARPASS_CLIENT_ID = os.getenv("CLEARPASS_CLIENT_ID", os.getenv("CLEARPASS_USERNAME", "")).strip()
CLEARPASS_CLIENT_SECRET = os.getenv("CLEARPASS_CLIENT_SECRET", os.getenv("CLEARPASS_PASSWORD", "")).strip()
CLEARPASS_AUTH_HEADER = os.getenv("CLEARPASS_AUTH_HEADER", "").strip()
CLEARPASS_VERIFY_SSL = os.getenv("CLEARPASS_VERIFY_SSL", "false").strip().lower() in {"1", "true", "yes", "on"}


def h(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _norm_mac(value: Any) -> str:
    return re.sub(r"[^0-9a-f]", "", str(value or "").lower())


def _looks_like_mac(value: Any) -> bool:
    return len(_norm_mac(value)) == 12


def _looks_like_username(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and ("@" in text or re.match(r"^[A-Za-z0-9_.-]{3,}$", text))


def _ssl_context() -> ssl.SSLContext:
    if CLEARPASS_VERIFY_SSL:
        return ssl.create_default_context()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _api_url(path: str, params: dict[str, Any] | None = None) -> str:
    path = "/" + str(path or "").lstrip("/")
    url = f"{CLEARPASS_BASE_URL}{CLEARPASS_API_PREFIX}{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return url


def _token_url() -> str:
    token_path = (CLEARPASS_TOKEN_PATH or "/api/oauth").strip()
    if token_path.startswith("http://") or token_path.startswith("https://"):
        return token_path
    normalized = "/" + token_path.lstrip("/")
    if normalized.rstrip("/") in {"/oauth", "/oauth/token", "/api/oauth/token"}:
        normalized = "/api/oauth"
    return f"{CLEARPASS_BASE_URL}{normalized}"


def _fetch_token() -> tuple[str | None, str | None]:
    if not CLEARPASS_CLIENT_ID or not CLEARPASS_CLIENT_SECRET:
        return None, "Missing CLEARPASS_CLIENT_ID or CLEARPASS_CLIENT_SECRET"

    body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8")
    auth = base64.b64encode(f"{CLEARPASS_CLIENT_ID}:{CLEARPASS_CLIENT_SECRET}".encode("utf-8")).decode("ascii")
    token_url = _token_url()
    # Print the final computed token URL so token-path mistakes are obvious in logs/tests.
    print(token_url)
    req = urllib.request.Request(
        token_url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {auth}",
        },
    )
    try:
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace") or "{}")
            token = payload.get("access_token") or payload.get("token")
            if not token:
                return None, f"Token response missing access_token: {payload}"
            return str(token), None
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return None, f"HTTP {exc.code} {exc.reason}: {body[:500]}"
    except Exception as exc:
        return None, str(exc)


def _request_json(path: str, params: dict[str, Any] | None = None) -> tuple[Any | None, str | None]:
    headers = {"Accept": "application/json"}
    auth_header = CLEARPASS_AUTH_HEADER
    if not auth_header:
        token, err = _fetch_token()
        if err:
            return None, f"auth: {err}"
        auth_header = f"Bearer {token}"
    if auth_header:
        headers["Authorization"] = auth_header

    req = urllib.request.Request(_api_url(path, params), headers=headers)
    try:
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=20) as resp:
            payload = resp.read().decode("utf-8", "replace")
            if not payload.strip():
                return {}, None
            return json.loads(payload), None
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        return None, f"HTTP {exc.code} {exc.reason}: {body[:500]}"
    except Exception as exc:
        return None, str(exc)


def _extract_records(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]

    if isinstance(obj, dict):
        for key in ("items", "results", "rows", "data", "endpoints", "endpoint", "sessions", "session", "users", "user", "guests", "guest", "local_users", "local-user", "entries", "objects"):
            val = obj.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]

        embedded = obj.get("_embedded")
        if isinstance(embedded, dict):
            records = []
            for key, val in embedded.items():
                if isinstance(val, list):
                    records.extend([x for x in val if isinstance(x, dict)])
                elif isinstance(val, dict):
                    for nested_key in ("items", "results", "rows", "data", "endpoints", "endpoint", "sessions", "session", "users", "user", "guests", "guest", "local_users", "local-user", "entries", "objects"):
                        nested_val = val.get(nested_key)
                        if isinstance(nested_val, list):
                            records.extend([x for x in nested_val if isinstance(x, dict)])
            if records:
                return records

    return []


def _fetch_endpoint_records() -> tuple[list[dict[str, Any]], list[str]]:
    data, err = _request_json("/endpoint")
    if err:
        return [], [f"Endpoint list: {err}"]
    return _extract_records(data), []


def _session_filter_paths(q: str, limit: int = 250) -> list[str]:
    q = (q or "").strip()
    if not q:
        return [f"/session?limit={int(limit)}"]

    clean = q.lower().replace(":", "").replace("-", "").strip()
    paths = []

    def add_filter(obj: dict[str, Any]) -> None:
        encoded = urllib.parse.quote(json.dumps(obj, separators=(",", ":")))
        paths.append(f"/session?limit={int(limit)}&filter={encoded}")

    # ClearPass session fields that actually work:
    #   username, callingstationid, mac_address, framedipaddress
    if "@" in q or q.lower().endswith("middlebury.edu"):
        add_filter({"username": q})
    elif "." not in q and ":" not in q and "-" not in q:
        add_filter({"username": q})
        add_filter({"username": q + "@middlebury.edu"})

    if _looks_like_mac(q):
        add_filter({"callingstationid": clean})
        add_filter({"mac_address": _format_mac_dash(clean)})

    if _looks_like_ip(q):
        add_filter({"framedipaddress": q})

    # Last fallback: bounded unfiltered search.
    paths.append(f"/session?limit={int(limit)}")
    return paths


def _format_mac_dash(clean: str) -> str:
    clean = (clean or "").lower().replace(":", "").replace("-", "")
    if len(clean) != 12:
        return clean
    return "-".join(clean[i:i+2] for i in range(0, 12, 2)).upper()


def _looks_like_ip(q: str) -> bool:
    parts = (q or "").strip().split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except Exception:
        return False


def _fetch_session_records(q: str = "", limit: int = 250) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()

    try:
        limit = max(1, min(int(limit or 250), 1000))
    except Exception:
        limit = 250

    paths = _session_filter_paths(q, limit=limit)

    # Treat the last unfiltered /session?limit=N path as fallback only.
    filtered_paths = [path for path in paths if "filter=" in path]
    fallback_paths = [path for path in paths if "filter=" not in path]

    def pull(path: str) -> None:
        data, err = _request_json(path)
        if err:
            errors.append(f"Session list {path}: {err}")
            return

        for row in _extract_records(data):
            key = str(row.get("id") or row.get("_links", {}).get("self", {}).get("href") or row)
            if key in seen:
                continue
            seen.add(key)
            records.append(row)

    for path in filtered_paths:
        pull(path)

    # Only fall back to newest sessions if no filtered lookup produced anything.
    if not records:
        for path in fallback_paths:
            pull(path)

    # Sort newest-ish first when ClearPass gives updated_at.
    def sort_key(row: dict[str, Any]) -> str:
        return str(row.get("updated_at") or row.get("acctstoptime") or row.get("acctstarttime") or "")

    records.sort(key=sort_key, reverse=True)

    return records[:limit], errors

def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, default=str, sort_keys=True)
        except Exception:
            return str(value)
    return str(value)


def _row_value(row: dict[str, Any], candidates: list[str]) -> str:
    normalized = {re.sub(r"[^a-z0-9]", "", str(k).lower()): k for k in row.keys()}
    for candidate in candidates:
        key = re.sub(r"[^a-z0-9]", "", candidate.lower())
        real = normalized.get(key)
        if real is not None:
            value = row.get(real)
            if value not in (None, ""):
                return _coerce_text(value).strip()
    return ""


def _search_score(row: dict[str, Any], q: str) -> int:
    text = q.strip().lower()
    score = 0
    for key in ["username", "user", "name", "mac", "mac_address", "ip", "host", "hostname", "description", "notes", "vlan", "role", "profile"]:
        value = _row_value(row, [key]).lower()
        if not value:
            continue
        if value == text:
            score += 30
        elif text in value:
            score += 12
    raw = _coerce_text(row).lower()
    if text and text in raw:
        score += 4
    return score


def _split_calledstationid(value: Any) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        return "", ""

    # ClearPass usually uses:
    #   70-90-41-71-0F-11:MiddleburyCollege
    if ":" in raw and "-" in raw.split(":", 1)[0]:
        bssid, ssid = raw.split(":", 1)
        return bssid.strip(), ssid.strip()

    clean = raw.lower().replace("-", "").replace(":", "")
    if len(clean) == 12:
        return raw.strip(), ""

    return "", ""


def _summarize_session(row: dict[str, Any]) -> dict[str, Any]:
    called = _row_value(row, ["calledstationid"])
    called_bssid, called_ssid = _split_calledstationid(called)

    ssid = _row_value(row, ["ssid", "essid"]) or called_ssid
    ap = _row_value(row, ["ap_name", "apname"]) or called

    if not called_bssid:
        called_bssid, fallback_ssid = _split_calledstationid(ap)
        if not ssid:
            ssid = fallback_ssid

    return {
        "username": _row_value(row, ["username", "user", "user_name", "user_id"]),
        "mac": _row_value(row, ["mac_address", "callingstationid", "mac", "station_mac"]),
        "ip": _row_value(row, ["framedipaddress", "ip", "ip_address", "address"]),
        "ssid": ssid,
        "ap": ap,
        "bssid": called_bssid,
        "nas": _row_value(row, ["nas_name", "nasipaddress", "nas_ip"]),
        "role": _row_value(row, ["arubauserrole", "tipsrole", "role_name", "role"]),
        "vlan": _row_value(row, ["arubauservlan", "vlan", "vlan_id"]),
        "service": _row_value(row, ["servicetype", "service", "service_type"]),
        "state": _row_value(row, ["state", "status"]),
        "updated_at": _row_value(row, ["updated_at", "acctstarttime", "timestamp", "time"]),
    }


def _summarize_endpoint(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "mac": _row_value(row, ["mac", "mac_address", "endpoint_mac", "hwaddr"]),
        "name": _row_value(row, ["name", "hostname", "host", "endpoint_name"]),
        "username": _row_value(row, ["username", "user", "user_name", "user_id", "owner"]),
        "ip": _row_value(row, ["ip", "ip_address", "address"]),
        "status": _row_value(row, ["status", "state", "health"]),
        "profile": _row_value(row, ["profile", "profiler", "device_profile"]),
        "role": _row_value(row, ["role", "device_role", "type"]),
        "vlan": _row_value(row, ["vlan", "vlan_id", "assigned_vlan"]),
        "description": _row_value(row, ["description", "desc", "notes", "comment"]),
        "last_seen": _row_value(row, ["last_seen", "lastseen", "updated_at", "modified_at", "timestamp", "time"]),
        "source": _row_value(row, ["source", "origin", "realm"]),
        "category": _row_value(row, ["category", "device_category", "type"]),
    }


def _render_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    head = "".join(f"<th>{h(c.replace('_', ' ').title())}</th>" for c in columns)
    if not rows:
        return f'<table class="report"><thead><tr>{head}</tr></thead><tbody><tr><td colspan="{len(columns)}" class="muted">No results found.</td></tr></tbody></table>'
    body = ""
    for row in rows:
        body += "<tr>"
        for col in columns:
            body += f"<td>{h(row.get(col, ''))}</td>"
        body += "</tr>"
    return f'<table class="report"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _render_record_details(row: dict[str, Any], index: int) -> str:
    open_attr = " open" if index == 1 else ""
    body = []
    for key in sorted(row.keys()):
        value = row.get(key)
        if value in (None, "", [], {}):
            continue
        body.append(f"<tr><th>{h(key)}</th><td>{h(_coerce_text(value))}</td></tr>")
    if not body:
        body = ["<tr><td class='muted'>Empty record.</td></tr>"]
    return (
        f'<details class="lookup-details"{open_attr}>'
        f'<summary>Record {index}</summary>'
        f'<table class="report compact-table"><tbody>{"".join(body)}</tbody></table>'
        "</details>"
    )


def _render_section(title: str, subtitle: str, rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    columns = columns or ["mac", "name", "username", "ip", "status", "profile", "role", "vlan", "description", "last_seen"]
    summary_rows = [_summarize_endpoint(row) for row in rows[:100]]
    detail_html = "".join(_render_record_details(row, idx) for idx, row in enumerate(rows[:8], start=1))
    return f"""
    <section class="panel lookup-section">
      <h2>{h(title)} <span class="muted">({len(rows)})</span></h2>
      <p class="muted">{h(subtitle)}</p>
      {_render_session_table(rows)}
      {detail_html}
    </section>
    """


def _find_matching_records(records: list[dict[str, Any]], q: str, limit: int) -> list[dict[str, Any]]:
    scored = []
    for row in records:
        score = _search_score(row, q)
        if score <= 0:
            continue
        scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], _coerce_text(item[1]).lower()))
    return [row for _, row in scored[:limit]]


def _find_matching_endpoints(records: list[dict[str, Any]], q: str, limit: int) -> list[dict[str, Any]]:
    scored = []
    for row in records:
        score = _search_score(row, q)
        if score <= 0:
            continue
        scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], _coerce_text(item[1]).lower()))
    return [row for _, row in scored[:limit]]


def clearpass_lookup_context(q: str = "", limit: int = 50) -> dict[str, Any]:
    q = (q or "").strip()
    try:
        limit = max(1, min(int(limit or 50), 250))
    except Exception:
        limit = 50

    context: dict[str, Any] = {
        "q": q,
        "limit": limit,
        "endpoint_rows": [],
        "session_rows": [],
        "guest_rows": [],
        "local_rows": [],
        "errors": [],
    }

    if not q:
        return context

    endpoint_records, endpoint_errors = _fetch_endpoint_records()
    context["errors"].extend(endpoint_errors)

    if endpoint_records:
        context["endpoint_rows"] = _find_matching_endpoints(endpoint_records, q, limit)

        if _looks_like_mac(q):
            mac = _norm_mac(q)
            exact = [row for row in endpoint_records if _norm_mac(_row_value(row, ["mac", "mac_address", "endpoint_mac", "hwaddr"])) == mac]
            for row in exact:
                if row not in context["endpoint_rows"]:
                    context["endpoint_rows"].insert(0, row)

    session_records, session_errors = _fetch_session_records(q=q, limit=limit)
    context["errors"].extend(session_errors)

    if session_records:
        context["session_rows"] = _find_matching_records(session_records, q, limit)

        if _looks_like_mac(q):
            mac = _norm_mac(q)
            exact = [
                row for row in session_records
                if _norm_mac(_row_value(row, ["mac_address", "callingstationid", "mac", "station_mac"])) == mac
            ]
            for row in exact:
                if row not in context["session_rows"]:
                    context["session_rows"].insert(0, row)

    # Guest and local-user username-specific lookup paths are disabled.
    # ClearPass user/device correlation is handled through /session using JSON filters:
    #   {"username": "..."}
    #   {"callingstationid": "..."}
    #   {"framedipaddress": "..."}

    # No matching ClearPass endpoint records is not an error.
    # Leave errors for actual API/auth/HTTP failures.
    return context




def _mist_lookup_link(label: Any, query: Any) -> str:
    label_s = str(label or "").strip()
    query_s = str(query or "").strip()
    if not label_s:
        return ""
    if not query_s:
        return h(label_s)
    return f'<a href="/tools/mist?q={urllib.parse.quote(query_s)}&limit=100">{h(label_s)}</a>'


def _session_summary_for_table(row: dict[str, Any]) -> dict[str, Any]:
    summary = _summarize_session(row)
    bssid = summary.get("bssid") or ""
    ap = summary.get("ap") or ""

    # If AP is really calledstationid, make it searchable in the local Mist tool.
    if bssid:
        summary["ap"] = _mist_lookup_link(ap or bssid, bssid)

    return summary




def _render_session_table(rows: list[dict[str, Any]]) -> str:
    columns = [
        ("username", "Username"),
        ("mac", "MAC"),
        ("ip", "IP"),
        ("ssid", "SSID"),
        ("ap", "AP"),
        ("bssid", "BSSID"),
        ("mist", "Mist"),
        ("nas", "NAS"),
        ("role", "Role"),
        ("vlan", "VLAN"),
        ("service", "Service"),
        ("state", "State"),
        ("updated_at", "Updated At"),
    ]

    trs = []
    for row in rows[:100]:
        summary = _summarize_session(row)
        bssid = summary.get("bssid") or ""
        mist_link = ""
        if bssid:
            mist_link = f'<a href="/tools/mist?q={urllib.parse.quote(str(bssid))}&limit=100">Mist lookup</a>'

        tds = []
        for key, label in columns:
            if key == "mist":
                tds.append(f"<td>{mist_link}</td>")
            else:
                tds.append(f"<td>{h(summary.get(key) or '')}</td>")
        trs.append("<tr>" + "".join(tds) + "</tr>")

    thead = "<thead><tr>" + "".join(f"<th>{h(label)}</th>" for _, label in columns) + "</tr></thead>"
    tbody = "<tbody>" + "".join(trs) + "</tbody>"
    return f'<table class="report">{thead}{tbody}</table>'


def _render_session_section(rows: list[dict[str, Any]]) -> str:
    columns = ["username", "mac", "ip", "ssid", "ap", "bssid", "nas", "role", "vlan", "service", "state", "updated_at"]
    summary_rows = [_session_summary_for_table(row) for row in rows[:100]]
    detail_html = "".join(_render_record_details(row, idx) for idx, row in enumerate(rows[:8], start=1))
    return f"""
    <section class="panel lookup-section">
      <h2>Session Matches <span class="muted">({len(rows)})</span></h2>
      <p class="muted">ClearPass session/accounting records from <code>/session</code>.</p>
      {_render_session_table(rows)}
      {detail_html}
    </section>
    """


def render_clearpass_lookup_page(q: str = "", limit: int = 50) -> str:
    q = (q or "").strip()
    ctx = clearpass_lookup_context(q=q, limit=limit)

    if not q:
        return f"""
        <section class="panel">
          <h1>ClearPass Lookup</h1>
          <p class="muted">Search ClearPass session and endpoint records from MiddKiPS.</p>
          <form class="unused-controls" method="get" action="/tools/clearpass">
            <input name="q" placeholder="MAC, hostname, endpoint, username">
            <select name="limit">
              <option value="25">25</option>
              <option value="50" selected>50</option>
              <option value="100">100</option>
              <option value="250">250</option>
            </select>
            <button class="button" type="submit">Search</button>
            <a class="button" href="/tools/lookup">Back to Lookup Hub</a>
          </form>
        </section>
        <section class="panel">
          <h2>What this pulls</h2>
          <ul>
            <li>OAuth2 client credentials token from ClearPass.</li>
            <li>Endpoint list from <code>/endpoint</code> with client-side matching.</li>
            <li>Exact MAC lookups when the search term looks like a MAC address.</li>
            <li>Session/accounting records from /session for username, MAC, IP, SSID, AP, role, and VLAN lookup.</li>
          </ul>
        </section>
        """

    summary = f"""
    <section class="cards">
      <article class="card"><span>Endpoint Matches</span><strong>{len(ctx['endpoint_rows'])}</strong></article>
      <article class="card"><span>Session Matches</span><strong>{len(ctx.get('session_rows') or [])}</strong></article>
      <article class="card"><span>Guest Disabled</span><strong>{len(ctx['guest_rows'])}</strong></article>
      <article class="card"><span>Local User Disabled</span><strong>{len(ctx['local_rows'])}</strong></article>
      <article class="card"><span>Errors</span><strong>{len(ctx['errors'])}</strong></article>
    </section>
    """

    session_section = ""
    if ctx.get("session_rows"):
        session_section = _render_session_section(ctx.get("session_rows") or [])

    endpoint_section = ""
    if ctx["endpoint_rows"]:
        endpoint_section = _render_section(
            "Endpoints",
            "Matched endpoint records. The detail blocks below show every non-empty field returned by ClearPass.",
            ctx["endpoint_rows"],
        )

    guest_section = ""
    if ctx["guest_rows"]:
        guest_cols = sorted({k for row in ctx["guest_rows"] for k in row.keys()})[:10] or ["username"]
        guest_section = _render_section(
            "Guest Users",
            "Guest account results from /guest/username/{username}.",
            ctx["guest_rows"],
            columns=guest_cols,
        )

    local_section = ""
    if ctx["local_rows"]:
        local_cols = sorted({k for row in ctx["local_rows"] for k in row.keys()})[:10] or ["user_id"]
        local_section = _render_section(
            "Local Users",
            "Local-user collection lookup is disabled until needed.",
            ctx["local_rows"],
            columns=local_cols,
        )

    errors = ""
    if ctx["errors"]:
        error_rows = [{"error": err} for err in ctx["errors"]]
        errors = f"""
        <section class="panel">
          <h2>Warnings / Errors</h2>
          {_render_table(error_rows, ['error'])}
        </section>
        """

    return f"""
    <section class="panel">
      <h1>ClearPass Lookup</h1>
      <p class="muted">Base URL: {h(CLEARPASS_BASE_URL)}{h(CLEARPASS_API_PREFIX)}</p>
      <form class="unused-controls" method="get" action="/tools/clearpass">
        <input name="q" value="{h(q)}" placeholder="MAC, hostname, endpoint, username">
        <select name="limit">
          <option value="25" {"selected" if limit == 25 else ""}>25</option>
          <option value="50" {"selected" if limit == 50 else ""}>50</option>
          <option value="100" {"selected" if limit == 100 else ""}>100</option>
          <option value="250" {"selected" if limit == 250 else ""}>250</option>
        </select>
        <button class="button" type="submit">Search</button>
        <a class="button" href="/tools/clearpass">Clear</a>
        <a class="button" href="/tools/lookup">Back to Lookup Hub</a>
      </form>
    </section>
    {summary}
    {session_section}
    {endpoint_section}
    {guest_section}
    {local_section}
    {errors}
    """


def render_clearpass_universal_section(q: str = "", limit: int = 50) -> str:
    q = (q or "").strip()
    if not q:
        return ""

    ctx = clearpass_lookup_context(q=q, limit=limit)
    rows = ctx.get("endpoint_rows") or []
    errors = [
        e for e in (ctx.get("errors") or [])
        if not str(e).lower().startswith(("guest user:", "local user:"))
    ]

    columns = ["mac", "name", "username", "ip", "status", "profile", "role", "vlan", "last_seen"]
    summary_rows = [_summarize_endpoint(row) for row in rows[:100]]
    table_html = _render_table(summary_rows, columns)

    detail_html = ""
    for idx, row in enumerate(rows[:5], start=1):
        detail_html += _render_record_details(row, idx)

    error_html = ""
    if errors:
        error_rows = "".join(f"<tr><td>{h(str(e))}</td></tr>" for e in errors)
        error_html = f"""
        <h3>ClearPass Warnings</h3>
        <table class="report compact-table">
          <thead><tr><th>Error</th></tr></thead>
          <tbody>{error_rows}</tbody>
        </table>
        """

    # In Universal Lookup, do not render an empty ClearPass section.
    # The standalone ClearPass page can still show endpoint-only no-match results.
    if not rows and not errors:
        return ""

    return f"""
    <section class="panel lookup-section">
      <h2>ClearPass Endpoint Results <span class="muted">({len(rows)})</span></h2>
      <p class="muted">
        Read-only ClearPass endpoint inventory results included directly in Universal Lookup.
        Username/session history still needs the real ClearPass session or access-tracker API path.
      </p>
      {table_html}
      {detail_html}
      {error_html}
      <p class="muted">
        <a class="button" href="/tools/clearpass?q={urllib.parse.quote(q)}&limit={int(limit or 50)}">Open full ClearPass lookup</a>
      </p>
    </section>
    """


# ---- ClearPass cached lookup support for Universal Lookup ----

def _clearpass_cache_path() -> str:
    return os.getenv("CLEARPASS_ENDPOINT_CACHE_FILE", "/data/clearpass_endpoint_cache.json")


def refresh_clearpass_endpoint_cache() -> tuple[int, list[str]]:
    """
    Pull ClearPass /endpoint once and cache it locally.
    Universal Lookup reads this cache so the page does not hang on live API calls.
    """
    records, errors = _fetch_endpoint_records()
    if errors:
        return 0, errors

    path = _clearpass_cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    payload = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "count": len(records),
        "records": records,
    }

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, default=str)
    os.replace(tmp, path)

    return len(records), []


def _load_clearpass_endpoint_cache() -> tuple[list[dict[str, Any]], list[str]]:
    path = _clearpass_cache_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return [], ["ClearPass endpoint cache has not been built yet."]
    except Exception as exc:
        return [], [f"ClearPass endpoint cache read failed: {exc}"]

    records = payload.get("records", [])
    if not isinstance(records, list):
        return [], ["ClearPass endpoint cache is invalid."]

    return [r for r in records if isinstance(r, dict)], []


def clearpass_cached_lookup_context(q: str = "", limit: int = 50) -> dict[str, Any]:
    q = (q or "").strip()
    try:
        limit = max(1, min(int(limit or 50), 250))
    except Exception:
        limit = 50

    context: dict[str, Any] = {
        "q": q,
        "limit": limit,
        "endpoint_rows": [],
        "errors": [],
    }

    if not q:
        return context

    records, errors = _load_clearpass_endpoint_cache()
    context["errors"].extend(errors)

    if records:
        context["endpoint_rows"] = _find_matching_endpoints(records, q, limit)

        if _looks_like_mac(q):
            mac = _norm_mac(q)
            exact = [
                row for row in records
                if _norm_mac(_row_value(row, ["mac", "mac_address", "endpoint_mac", "hwaddr"])) == mac
            ]
            for row in exact:
                if row not in context["endpoint_rows"]:
                    context["endpoint_rows"].insert(0, row)

    return context


def render_clearpass_cached_universal_section(q: str = "", limit: int = 50) -> str:
    q = (q or "").strip()
    if not q:
        return ""

    ctx = clearpass_cached_lookup_context(q=q, limit=limit)
    rows = ctx.get("endpoint_rows") or []
    errors = ctx.get("errors") or []

    # Do not clutter Universal Lookup with empty ClearPass sections.
    # Only render if we found records or if the cache is missing/broken.
    if not rows and not errors:
        return ""

    columns = ["mac", "name", "username", "ip", "status", "profile", "role", "vlan", "last_seen"]
    summary_rows = [_summarize_endpoint(row) for row in rows[:100]]
    table_html = _render_table(summary_rows, columns)

    detail_html = "".join(
        _render_record_details(row, idx)
        for idx, row in enumerate(rows[:5], start=1)
    )

    error_html = ""
    if errors:
        error_rows = "".join(f"<tr><td>{h(str(e))}</td></tr>" for e in errors)
        error_html = f"""
        <h3>ClearPass Cache Warnings</h3>
        <table class="report compact-table">
          <thead><tr><th>Warning</th></tr></thead>
          <tbody>{error_rows}</tbody>
        </table>
        """

    return f"""
    <section class="panel lookup-section">
      <h2>ClearPass Endpoint Results <span class="muted">({len(rows)})</span></h2>
      <p class="muted">
        Cached ClearPass endpoint inventory results. Live ClearPass lookup remains available below.
      </p>
      {table_html}
      {detail_html}
      {error_html}
      <p class="muted">
        <a class="button" href="/tools/clearpass?q={urllib.parse.quote(q)}&limit={int(limit or 50)}">Open full ClearPass lookup</a>
      </p>
    </section>
    """


def render_clearpass_session_universal_section(q: str = "", limit: int = 50) -> str:
    q = (q or "").strip()
    if not q:
        return ""

    try:
        limit = max(1, min(int(limit or 50), 250))
    except Exception:
        limit = 50

    rows, errors = _fetch_session_records(q=q, limit=limit)

    # Do not clutter Universal Lookup if ClearPass has nothing useful and no errors.
    if not rows and not errors:
        return ""

    detail_html = "".join(
        _render_record_details(row, idx)
        for idx, row in enumerate(rows[:5], start=1)
    )

    error_html = ""
    if errors:
        error_rows = [{"error": err} for err in errors]
        error_html = f"""
        <section class="panel">
          <h3>ClearPass Session Warnings</h3>
          {_render_table(error_rows, ["error"])}
        </section>
        """

    return f"""
    <section class="panel lookup-section">
      <h2>ClearPass Session Matches <span class="muted">({len(rows)})</span></h2>
      <p class="muted">
        Filtered ClearPass <code>/session</code> results using username, MAC, or IP JSON filters.
      </p>
      {_render_session_table(rows)}
      {detail_html}
      {error_html}
      <p class="muted">
        <a class="button" href="/tools/clearpass?q={urllib.parse.quote(q)}&limit={int(limit)}">Open full ClearPass lookup</a>
      </p>
    </section>
    """


def clearpass_universal_summary(q: str = "", limit: int = 50) -> dict[str, Any]:
    q = (q or "").strip()
    if not q:
        return {"count": 0, "rows": [], "summary": None, "errors": []}

    try:
        limit = max(1, min(int(limit or 50), 250))
    except Exception:
        limit = 50

    rows, errors = _fetch_session_records(q=q, limit=limit)
    summaries = [_summarize_session(row) for row in rows]

    best = None
    if summaries:
        # For Universal, "last AP" should mean newest ClearPass session with an AP/BSSID,
        # not the row with the most populated fields. Shocking concept, apparently.
        def score(row: dict[str, Any]) -> tuple[int, str]:
            has_ap = 1 if (row.get("bssid") or row.get("ap")) else 0
            return has_ap, str(row.get("updated_at") or "")

        best = sorted(summaries, key=score, reverse=True)[0]

    return {
        "count": len(rows),
        "rows": summaries,
        "summary": best,
        "errors": errors,
    }


def render_clearpass_universal_correlation_card(q: str = "", limit: int = 50) -> str:
    data = clearpass_universal_summary(q=q, limit=limit)
    count = data.get("count") or 0
    summary = data.get("summary") or {}

    if not count:
        return ""

    username = h(summary.get("username") or "")
    mac = h(summary.get("mac") or "")
    ip = h(summary.get("ip") or "")
    ssid = h(summary.get("ssid") or "")
    ap = h(summary.get("ap") or "")
    bssid = str(summary.get("bssid") or "").strip()
    role = h(summary.get("role") or "")
    updated = h(summary.get("updated_at") or "")

    mist_link = ""
    if bssid:
        mist_link = f'<br><a href="/tools/mist?q={urllib.parse.quote(bssid)}&limit=100">Open AP/BSSID in Mist lookup</a>'

    return f"""
    <article class="card correlation-card">
      <span>ClearPass</span>
      <strong>{count} sessions</strong>
      <p class="muted">
        {username}<br>
        MAC: {mac}<br>
        IP: {ip}<br>
        SSID: {ssid}<br>
        AP/BSSID: {ap}<br>
        BSSID: {h(bssid)}<br>
        Role: {role}<br>
        Last: {updated}
        {mist_link}
      </p>
    </article>
    """

