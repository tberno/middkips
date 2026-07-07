from typing import Any

from app.core.db import fetch_all
from app.services.universal_lookup import (
    device_anchor,
    empty_section,
    error_section,
    h,
    lookup_devices,
    lookup_interfaces,
    lookup_ips,
    lookup_macs,
    lookup_vlans,
    table_columns,
)


COMPONENTS = [
    {
        "key": "devices",
        "title": "Device Lookup",
        "short": "Devices",
        "placeholder": "hostname, sysName, IP, OS, hardware",
    },
    {
        "key": "interfaces",
        "title": "Interface Lookup",
        "short": "Interfaces",
        "placeholder": "interface, description, title, device",
    },
    {
        "key": "ips",
        "title": "IP Lookup",
        "short": "IPs",
        "placeholder": "IP address, prefix, hostname, MAC",
    },
    {
        "key": "macs",
        "title": "MAC / FDB Lookup",
        "short": "MACs",
        "placeholder": "MAC address, VLAN, interface, switch",
    },
    {
        "key": "vlans",
        "title": "VLAN Lookup",
        "short": "VLANs",
        "placeholder": "VLAN number, name, domain, device",
    },
    {
        "key": "events",
        "title": "Event Lookup",
        "short": "Events",
        "placeholder": "event text, device, type, user",
    },
]


def component_map() -> dict[str, dict[str, str]]:
    return {c["key"]: c for c in COMPONENTS}


def first_existing(cols: set[str], names: list[str]) -> str | None:
    for name in names:
        if name in cols:
            return name
    return None


def lookup_events(prefix: str, q: str, limit: int) -> dict[str, Any]:
    event_cols = table_columns("eventlog")

    if not event_cols:
        return empty_section("Events")

    id_col = first_existing(event_cols, ["event_id", "id"])
    device_col = "device_id" if "device_id" in event_cols else None
    time_col = first_existing(event_cols, ["datetime", "timestamp", "time", "created_at", "updated_at"])
    message_col = first_existing(event_cols, ["message", "msg", "event", "details"])
    type_col = first_existing(event_cols, ["type", "event_type", "entity_type"])
    severity_col = first_existing(event_cols, ["severity", "level", "priority"])
    username_col = first_existing(event_cols, ["username", "user", "uid"])

    if not time_col or not message_col:
        return {
            "title": "Events lookup error",
            "columns": [
                {"key": "section", "label": "Section"},
                {"key": "error", "label": "Error"},
            ],
            "rows": [
                {
                    "section": "Events",
                    "error": "Could not find expected time/message columns in eventlog. Columns: " + ", ".join(sorted(event_cols)),
                }
            ],
            "is_error": True,
        }

    select_parts = [
        f"e.{time_col} AS event_time",
        f"e.{message_col} AS message",
        f"e.{id_col} AS event_id" if id_col else "NULL AS event_id",
        f"e.{device_col} AS device_id" if device_col else "NULL AS device_id",
        f"e.{type_col} AS event_type" if type_col else "NULL AS event_type",
        f"e.{severity_col} AS severity" if severity_col else "NULL AS severity",
        f"e.{username_col} AS username" if username_col else "NULL AS username",
    ]

    if device_col:
        select_parts.append("COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device")
        join_sql = f"LEFT JOIN devices d ON d.device_id = e.{device_col}"
    else:
        select_parts.append("NULL AS device")
        join_sql = ""

    clauses = [
        f"CAST(e.{message_col} AS CHAR) LIKE %s",
        f"CAST(e.{time_col} AS CHAR) LIKE %s",
    ]
    params: list[Any] = [f"%{q}%", f"%{q}%"]

    if device_col:
        clauses.extend(["d.hostname LIKE %s", "d.sysName LIKE %s"])
        params.extend([f"%{q}%", f"%{q}%"])

    if type_col:
        clauses.append(f"CAST(e.{type_col} AS CHAR) LIKE %s")
        params.append(f"%{q}%")

    if severity_col:
        clauses.append(f"CAST(e.{severity_col} AS CHAR) LIKE %s")
        params.append(f"%{q}%")

    if username_col:
        clauses.append(f"CAST(e.{username_col} AS CHAR) LIKE %s")
        params.append(f"%{q}%")

    order_parts = [f"e.{time_col} DESC"]
    if id_col:
        order_parts.append(f"e.{id_col} DESC")

    params.append(limit)

    rows = fetch_all(
        f"""
        SELECT
            {", ".join(select_parts)}
        FROM eventlog e
        {join_sql}
        WHERE {" OR ".join(clauses)}
        ORDER BY {", ".join(order_parts)}
        LIMIT %s
        """,
        tuple(params),
    )

    out = []
    for row in rows:
        out.append({
            "time": row.get("event_time") or "",
            "device": device_anchor(prefix, row) if row.get("device_id") else "",
            "severity": row.get("severity") or "",
            "type": row.get("event_type") or "",
            "message": row.get("message") or "",
            "user": row.get("username") or "",
            "id": row.get("event_id") or "",
        })

    return {
        "title": "Events",
        "columns": [
            {"key": "time", "label": "Time"},
            {"key": "device", "label": "Device", "html": True},
            {"key": "severity", "label": "Severity"},
            {"key": "type", "label": "Type"},
            {"key": "message", "label": "Message"},
            {"key": "user", "label": "User"},
            {"key": "id", "label": "Event ID"},
        ],
        "rows": out,
    }


LOOKUP_FUNCS = {
    "devices": lookup_devices,
    "interfaces": lookup_interfaces,
    "ips": lookup_ips,
    "macs": lookup_macs,
    "vlans": lookup_vlans,
    "events": lookup_events,
}


def lookup_hub_context(prefix: str) -> dict[str, Any]:
    return {
        "title": "Lookup Hub",
        "subtitle": "Test each lookup component independently before building universal lookup.",
        "components": [
            {
                **c,
                "href": f"{prefix}/tools/lookup/{c['key']}",
            }
            for c in COMPONENTS
        ],
    }


def component_lookup_context(prefix: str, component: str, q: str = "", limit: int = 50) -> dict[str, Any]:
    component = (component or "").strip().lower()
    q = (q or "").strip()

    try:
        limit = int(limit)
    except Exception:
        limit = 50

    limit = max(1, min(limit, 500))

    components = component_map()
    meta = components.get(component)

    if not meta:
        meta = {
            "key": component,
            "title": "Unknown Lookup",
            "short": component,
            "placeholder": "search",
        }
        result = {
            "title": "Unknown component",
            "columns": [
                {"key": "error", "label": "Error"},
            ],
            "rows": [
                {"error": f"Unknown lookup component: {h(component)}"},
            ],
            "is_error": True,
        }
    elif not q:
        result = {
            "title": meta["short"],
            "columns": [],
            "rows": [],
        }
    else:
        func = LOOKUP_FUNCS[component]
        try:
            result = func(prefix, q, limit)
        except Exception as exc:
            result = error_section(meta["short"], exc)

    return {
        "title": meta["title"],
        "subtitle": "Standalone component lookup",
        "component": component,
        "components": [
            {
                **c,
                "href": f"{prefix}/tools/lookup/{c['key']}",
                "active": c["key"] == component,
            }
            for c in COMPONENTS
        ],
        "placeholder": meta["placeholder"],
        "q": q,
        "limit": limit,
        "limit_options": [25, 50, 100, 250, 500],
        "reset_url": f"{prefix}/tools/lookup/{component}",
        "section": result,
    }
