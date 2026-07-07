import base64
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def cfg() -> dict[str, str]:
    return {
        "base_url": os.environ.get("EIP_BASE_URL", "").rstrip("/"),
        "user": os.environ.get("EIP_USER", ""),
        "password": os.environ.get("EIP_PASS", ""),
    }


def html_escape(value: Any) -> str:
    import html
    return html.escape("" if value is None else str(value))


def pick(row: dict[str, Any], keys: list[str]) -> str:
    lower = {str(k).lower(): k for k in row.keys()}
    for key in keys:
        real = lower.get(key.lower())
        if real is not None and row.get(real) not in (None, ""):
            return str(row.get(real))
    return ""


def looks_ip(value: str) -> bool:
    parts = str(value or "").strip().split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except Exception:
        return False


def looks_mac(value: str) -> bool:
    clean = str(value or "").lower().replace(":", "").replace("-", "").replace(".", "").strip()
    return len(clean) >= 6 and all(c in "0123456789abcdef" for c in clean)


def quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def like(value: str) -> str:
    return "'%" + str(value).replace("'", "''") + "%'"


def clean_mac(value: str) -> str:
    return str(value or "").lower().replace(":", "").replace("-", "").replace(".", "").strip()


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("data", "result", "rows", "items", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            found = extract_rows(value)
            if found:
                return found

    for value in payload.values():
        if isinstance(value, list):
            rows = [x for x in value if isinstance(x, dict)]
            if rows:
                return rows
        if isinstance(value, dict):
            found = extract_rows(value)
            if found:
                return found

    if payload:
        return [payload]

    return []


def eip_request(endpoint: str, where: str, limit: int) -> list[dict[str, Any]]:
    c = cfg()

    if not c["base_url"] or not c["user"] or not c["password"]:
        raise RuntimeError("Missing EIP_BASE_URL, EIP_USER, or EIP_PASS in container environment")

    auth = base64.b64encode(f"{c['user']}:{c['password']}".encode()).decode()
    context = ssl._create_unverified_context()

    params = {
        "WHERE": where,
        "limit": str(limit),
    }

    post_data = urllib.parse.urlencode(params).encode()
    base_url = f"{c['base_url']}/rest/{endpoint}"

    attempts = [
        ("POST", base_url, post_data),
        ("GET", base_url + "?" + urllib.parse.urlencode(params), None),
    ]

    last_error = None

    for method, url, data in attempts:
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Basic {auth}",
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        try:
            with urllib.request.urlopen(req, context=context, timeout=12) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            last_error = f"{method} HTTP {exc.code}: {body}"
            continue
        except Exception as exc:
            last_error = f"{method} {type(exc).__name__}: {exc}"
            continue

        try:
            payload = json.loads(body)
        except Exception:
            # Some EfficientIP responses are not JSON unless the endpoint likes the query.
            # Keep this as an endpoint-level failure, not a page failure.
            last_error = f"{method} returned non-JSON: {body[:300]}"
            continue

        return extract_rows(payload)

    raise RuntimeError(last_error or "unknown SOLIDserver API error")


def dedupe_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    out = []
    seen = set()

    for row in rows:
        try:
            key = json.dumps(row, sort_keys=True, default=str)
        except Exception:
            key = str(row)

        if key in seen:
            continue

        seen.add(key)
        out.append(row)

        if len(out) >= limit:
            break

    return out


def try_field_queries(endpoint: str, tests: list[tuple[str, str]], limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for field, condition in tests:
        try:
            got = eip_request(endpoint, condition, limit)
            rows.extend(got)
        except Exception as exc:
            # Keep field-level errors out of the user table unless no query works.
            errors.append(f"{endpoint} {field}: {exc}")

    return dedupe_rows(rows, limit), errors


def build_tests(q: str, mode: str) -> list[tuple[str, str]]:
    is_ip = looks_ip(q)
    is_mac = looks_mac(q)
    mac = clean_mac(q)

    exact = quote(q)
    like_q = like(q)
    mac_like = like(mac if is_mac else q)

    tests: list[tuple[str, str]] = []

    # These are intentionally based on the working v3 SolidServer lookup fields.
    # Avoid random field probing here; bad WHERE fields cause SOLIDserver 400s.
    if mode == "ipam":
        if is_ip:
            tests.append(("hostaddr", f"hostaddr={exact}"))

        tests.extend([
            ("name", f"name LIKE {like_q}"),
            ("ip_alias", f"ip_alias LIKE {like_q}"),
            ("subnet_name", f"subnet_name LIKE {like_q}"),
            ("pool_name", f"pool_name LIKE {like_q}"),
        ])

        if is_mac:
            tests.append(("mac_addr", f"mac_addr LIKE {mac_like}"))

    elif mode == "dhcp_static":
        if is_ip:
            tests.append(("dhcphost_addr", f"dhcphost_addr={exact}"))

        tests.extend([
            ("dhcphost_name", f"dhcphost_name LIKE {like_q}"),
            ("db_hostname", f"db_hostname LIKE {like_q}"),
            ("dhcpscope_name", f"dhcpscope_name LIKE {like_q}"),
            ("dhcpsn_name", f"dhcpsn_name LIKE {like_q}"),
            ("dhcp_name", f"dhcp_name LIKE {like_q}"),
        ])

        if is_mac:
            tests.append(("dhcphost_mac_addr", f"dhcphost_mac_addr LIKE {mac_like}"))

    elif mode == "dhcp_scope":
        # v3 primarily used scope/network naming here. Keep it conservative.
        tests.extend([
            ("dhcpscope_name", f"dhcpscope_name LIKE {like_q}"),
            ("dhcpscope_alias", f"dhcpscope_alias LIKE {like_q}"),
            ("dhcp_name", f"dhcp_name LIKE {like_q}"),
            ("dhcpsn_name", f"dhcpsn_name LIKE {like_q}"),
            ("subnet_name", f"subnet_name LIKE {like_q}"),
        ])

        if is_ip:
            tests.append(("start_hostaddr", f"start_hostaddr={exact}"))
            tests.append(("end_hostaddr", f"end_hostaddr={exact}"))

    elif mode == "dhcp_range":
        tests.extend([
            ("dhcprange_name", f"dhcprange_name LIKE {like_q}"),
            ("dhcpscope_name", f"dhcpscope_name LIKE {like_q}"),
            ("dhcp_name", f"dhcp_name LIKE {like_q}"),
            ("dhcpsn_name", f"dhcpsn_name LIKE {like_q}"),
        ])

        if is_ip:
            tests.extend([
                ("start_hostaddr", f"start_hostaddr={exact}"),
                ("end_hostaddr", f"end_hostaddr={exact}"),
            ])

    elif mode == "dns_rr":
        if is_ip:
            tests.extend([
                ("rr_all_value", f"rr_all_value={exact}"),
                ("value1", f"value1={exact}"),
            ])

        tests.extend([
            ("rr_full_name", f"rr_full_name LIKE {like_q}"),
            ("rr_full_name_utf", f"rr_full_name_utf LIKE {like_q}"),
            ("rr_all_value", f"rr_all_value LIKE {like_q}"),
            ("value1", f"value1 LIKE {like_q}"),
            ("dnszone_name", f"dnszone_name LIKE {like_q}"),
            ("dnsview_name", f"dnsview_name LIKE {like_q}"),
        ])

    return tests


def normalize_ipam(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        out.append({
            "ip": pick(row, ["hostaddr", "ip_addr", "addr", "ip", "address"]),
            "name": pick(row, ["name", "host_name", "hostname"]),
            "alias": pick(row, ["ip_alias", "alias"]),
            "fqdn": pick(row, ["fqdn", "hostfqdn", "dns_name"]),
            "mac": pick(row, ["mac_addr", "mac", "hostmac"]),
            "description": pick(row, ["hostdescr", "description", "descr", "comment"]),
            "class": pick(row, ["class_name", "class", "objectclass", "type", "hostclass"]),
            "last_seen": pick(row, ["last_seen"]),
            "created_by": pick(row, ["trace_creation_usr_login"]),
            "updated": pick(row, ["trace_last_update_date", "updated_at", "mod_date", "mtime", "host_last_modif_date"]),
            "raw": compact_raw(row),
        })
    return out


def normalize_dhcp_static(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        out.append({
            "ip": pick(row, ["dhcphost_addr", "hostaddr", "ip_addr", "addr", "ip"]),
            "name": pick(row, ["dhcphost_name", "name", "hostname"]),
            "mac": pick(row, ["dhcphost_mac_addr", "mac_addr", "mac"]),
            "scope": pick(row, ["dhcpscope_name", "scope_name", "scope"]),
            "shared_network": pick(row, ["dhcpsn_name"]),
            "dhcp_server": pick(row, ["dhcp_name"]),
            "last_seen": pick(row, ["dhcphost_last_seen"]),
            "expire": pick(row, ["dhcphost_expire_time"]),
            "description": pick(row, ["dhcphost_description", "description", "descr", "comment"]),
            "raw": compact_raw(row),
        })
    return out


def normalize_dhcp_scope(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        out.append({
            "scope": pick(row, ["dhcpscope_name", "name", "scope_name"]),
            "network": pick(row, ["dhcpscope_net_addr", "subnet_addr", "network", "net_addr"]),
            "mask": pick(row, ["dhcpscope_net_mask", "mask", "netmask", "prefix"]),
            "server": pick(row, ["server_name", "dhcpserver_name", "server"]),
            "site": pick(row, ["site_name", "ip_site_name", "site"]),
            "description": pick(row, ["dhcpscope_description", "description", "descr", "comment"]),
            "raw": compact_raw(row),
        })
    return out


def normalize_dhcp_range(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        out.append({
            "range": pick(row, ["dhcprange_name", "name", "range_name"]),
            "start": pick(row, ["dhcprange_start_addr", "start_addr", "start"]),
            "end": pick(row, ["dhcprange_end_addr", "end_addr", "end"]),
            "scope": pick(row, ["dhcpscope_name", "scope_name", "scope"]),
            "description": pick(row, ["dhcprange_description", "description", "descr", "comment"]),
            "raw": compact_raw(row),
        })
    return out


def normalize_dns_rr(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        view = pick(row, ["dnsview_name", "view_name", "view"])
        zone_type = pick(row, ["dnszone_type"])

        # If dnsview_name is not present, infer a useful grouping from the zone type/name.
        if not view:
            if "internal" in str(row).lower():
                view = "Internal"
            elif "external" in str(row).lower():
                view = "External"
            else:
                view = ""

        value = pick(row, ["rr_all_value", "rr_value", "rr_value1", "value1", "value", "data", "rdata"])

        out.append({
            "view": view,
            "name": pick(row, ["rr_full_name", "rr_full_name_utf", "fqdn", "rr_name", "name"]),
            "type": pick(row, ["rr_type", "type"]),
            "value": value,
            "zone": pick(row, ["dnszone_name", "dnszone_sort_zone", "zone_name", "zone"]),
            "dns_server": pick(row, ["dns_name"]),
            "zone_type": zone_type,
            "ttl": pick(row, ["ttl"]),
            "updated_days": pick(row, ["rr_last_update_days"]),
            "raw": compact_raw(row),
        })
    return smart_dedupe_dns_rr(out)




def dns_view_bucket(value: str) -> str:
    text = str(value or "").strip().lower()
    if "internal" in text:
        return "Internal"
    if "external" in text:
        return "External"
    return str(value or "").strip() or "Unknown"


def smart_dedupe_dns_rr(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

    for row in rows:
        view = dns_view_bucket(row.get("view", ""))
        name = str(row.get("name") or "").strip()
        rr_type = str(row.get("type") or "").strip()
        value = str(row.get("value") or "").strip()
        zone = str(row.get("zone") or "").strip()

        # This removes duplicate master/slave/DNS-server rows while keeping
        # separate records for Internal vs External views.
        key = (
            view.lower(),
            zone.lower(),
            name.lower(),
            rr_type.upper(),
            value,
        )

        existing = grouped.get(key)
        dns_server = str(row.get("dns_server") or "").strip()
        zone_type = str(row.get("zone_type") or "").strip()

        if existing is None:
            new_row = dict(row)
            new_row["view"] = view
            new_row["_dns_servers"] = set([dns_server] if dns_server else [])
            new_row["_zone_types"] = set([zone_type] if zone_type else [])
            new_row["sources"] = "1"
            grouped[key] = new_row
        else:
            if dns_server:
                existing["_dns_servers"].add(dns_server)
            if zone_type:
                existing["_zone_types"].add(zone_type)

    out = []
    for row in grouped.values():
        servers = sorted(x for x in row.pop("_dns_servers", set()) if x)
        zone_types = sorted(x for x in row.pop("_zone_types", set()) if x)

        row["sources"] = str(max(1, len(servers) or len(zone_types) or 1))
        row["dns_server"] = ", ".join(servers[:3])
        if len(servers) > 3:
            row["dns_server"] += f" +{len(servers) - 3} more"

        row["zone_type"] = ", ".join(zone_types)

        out.append(row)

    out.sort(key=lambda r: (
        0 if r.get("view") == "External" else 1 if r.get("view") == "Internal" else 2,
        str(r.get("zone") or "").lower(),
        str(r.get("name") or "").lower(),
        str(r.get("type") or "").upper(),
        str(r.get("value") or "").lower(),
    ))

    return out


def compact_raw(row: dict[str, Any]) -> str:
    important = {}
    for key, value in row.items():
        if value in (None, ""):
            continue
        important[str(key)] = value
        if len(important) >= 8:
            break
    return json.dumps(important, default=str, ensure_ascii=False)


SECTIONS = [
    {
        "key": "ipam",
        "title": "SolidServer IPAM Address Records",
        "endpoint": "ip_address_list",
        "mode": "ipam",
        "normalizer": normalize_ipam,
        "columns": [
            {"key": "ip", "label": "IP"},
            {"key": "name", "label": "Name"},
            {"key": "alias", "label": "Alias"},
            {"key": "fqdn", "label": "FQDN"},
            {"key": "mac", "label": "MAC"},
            {"key": "description", "label": "Description"},
            {"key": "class", "label": "Class"},
            {"key": "last_seen", "label": "Last Seen"},
            {"key": "created_by", "label": "Created By"},
            {"key": "updated", "label": "Updated"},
            {"key": "raw", "label": "Raw"},
        ],
    },
    {
        "key": "dhcp_static",
        "title": "SolidServer DHCP Static / Reservations",
        "endpoint": "dhcp_static_list",
        "mode": "dhcp_static",
        "normalizer": normalize_dhcp_static,
        "columns": [
            {"key": "ip", "label": "IP"},
            {"key": "name", "label": "Name"},
            {"key": "mac", "label": "MAC"},
            {"key": "scope", "label": "Scope"},
            {"key": "shared_network", "label": "Shared Network"},
            {"key": "dhcp_server", "label": "DHCP Server"},
            {"key": "last_seen", "label": "Last Seen"},
            {"key": "expire", "label": "Expire"},
            {"key": "description", "label": "Description"},
            {"key": "raw", "label": "Raw"},
        ],
    },
    {
        "key": "dhcp_scope",
        "title": "SolidServer DHCP Scopes",
        "endpoint": "dhcp_scope_list",
        "mode": "dhcp_scope",
        "normalizer": normalize_dhcp_scope,
        "columns": [
            {"key": "scope", "label": "Scope"},
            {"key": "network", "label": "Network"},
            {"key": "mask", "label": "Mask"},
            {"key": "server", "label": "Server"},
            {"key": "site", "label": "Site"},
            {"key": "description", "label": "Description"},
            {"key": "raw", "label": "Raw"},
        ],
    },
    {
        "key": "dhcp_range",
        "title": "SolidServer DHCP Ranges",
        "endpoint": "dhcp_range_list",
        "mode": "dhcp_range",
        "normalizer": normalize_dhcp_range,
        "columns": [
            {"key": "range", "label": "Range"},
            {"key": "start", "label": "Start"},
            {"key": "end", "label": "End"},
            {"key": "scope", "label": "Scope"},
            {"key": "description", "label": "Description"},
            {"key": "raw", "label": "Raw"},
        ],
    },
    {
        "key": "dns_rr",
        "title": "SolidServer DNS Resource Records",
        "endpoint": "dns_rr_list",
        "mode": "dns_rr",
        "normalizer": normalize_dns_rr,
        "columns": [
            {"key": "view", "label": "View"},
            {"key": "name", "label": "Name"},
            {"key": "type", "label": "Type"},
            {"key": "value", "label": "Value"},
            {"key": "zone", "label": "Zone"},
            {"key": "sources", "label": "Sources"},
            {"key": "dns_server", "label": "DNS Servers"},
            {"key": "ttl", "label": "TTL"},
            {"key": "updated_days", "label": "Last Update Days"},
            {"key": "raw", "label": "Raw"},
        ],
    },
]


def solidserver_context(prefix: str, q: str = "", limit: int = 50, show_debug: bool = False) -> dict[str, Any]:
    q = (q or "").strip()

    try:
        limit = int(limit)
    except Exception:
        limit = 50

    limit = max(1, min(limit, 250))

    c = cfg()
    configured = bool(c["base_url"] and c["user"] and c["password"])

    context: dict[str, Any] = {
        "title": "SolidServer DDI Lookup",
        "subtitle": "Standalone SOLIDserver/IPAM/DHCP/DNS lookup. This is intentionally separate from Universal Lookup.",
        "q": q,
        "limit": limit,
        "limit_options": [25, 50, 100, 250],
        "show_debug": show_debug,
        "configured": configured,
        "base_url_set": bool(c["base_url"]),
        "user_set": bool(c["user"]),
        "password_set": bool(c["password"]),
        "sections": [],
        "debug_errors": [],
        "reset_url": f"{prefix}/tools/solidserver",
    }

    if not q:
        return context

    if not configured:
        context["sections"].append({
            "title": "Configuration Error",
            "is_error": True,
            "columns": [
                {"key": "item", "label": "Item"},
                {"key": "status", "label": "Status"},
            ],
            "rows": [
                {"item": "EIP_BASE_URL", "status": "set" if c["base_url"] else "missing"},
                {"item": "EIP_USER", "status": "set" if c["user"] else "missing"},
                {"item": "EIP_PASS", "status": "set" if c["password"] else "missing"},
            ],
        })
        return context

    for spec in SECTIONS:
        tests = build_tests(q, spec["mode"])
        rows, errors = try_field_queries(spec["endpoint"], tests, limit)
        context["debug_errors"].extend(errors)

        normalizer = spec["normalizer"]
        normalized = normalizer(rows)

        columns = list(spec["columns"])

        # Keep the noisy Raw JSON only when Debug is enabled.
        if not show_debug:
            columns = [col for col in columns if col.get("key") != "raw"]
            for row in normalized:
                row.pop("raw", None)

        if spec["key"] == "dns_rr":
            by_view = {
                "External": [],
                "Internal": [],
                "Unknown": [],
            }

            for row in normalized:
                bucket = dns_view_bucket(row.get("view", ""))
                if bucket not in by_view:
                    bucket = "Unknown"
                by_view[bucket].append(row)

            for bucket in ("External", "Internal", "Unknown"):
                rows_for_bucket = by_view[bucket]
                if not rows_for_bucket and bucket == "Unknown":
                    continue

                context["sections"].append({
                    "key": f"dns_rr_{bucket.lower()}",
                    "title": f"SolidServer DNS Resource Records - {bucket}",
                    "columns": columns,
                    "rows": rows_for_bucket,
                    "error_count": len(errors),
                })
        else:
            context["sections"].append({
                "key": spec["key"],
                "title": spec["title"],
                "columns": columns,
                "rows": normalized,
                "error_count": len(errors),
            })

    return context
