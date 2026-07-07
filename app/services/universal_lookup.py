from typing import Any

from app.core.db import fetch_all
from app.services.interface_stats import fmt_speed, h, status_badge


def table_columns(table: str) -> set[str]:
    try:
        rows = fetch_all(f"SHOW COLUMNS FROM {table}")
        return {str(r.get("Field")) for r in rows if r.get("Field")}
    except Exception:
        return set()


def table_exists(table: str) -> bool:
    return bool(table_columns(table))


def clean_mac(value: Any) -> str:
    text = str(value or "").strip()

    if not text:
        return ""

    raw = text.lower().replace(":", "").replace("-", "").replace(".", "")

    if len(raw) == 12:
        return ":".join(raw[i:i + 2] for i in range(0, 12, 2))

    return text


def clean_query_mac(value: str) -> str:
    return str(value or "").lower().replace(":", "").replace("-", "").replace(".", "").strip()


def norm_sql(expr: str) -> str:
    return f"LOWER(REPLACE(REPLACE(REPLACE(CAST({expr} AS CHAR), ':', ''), '-', ''), '.', ''))"


def sql_col(alias: str, col: str, cols: set[str], fallback: str = "NULL") -> str:
    if col in cols:
        return f"{alias}.{col}"
    return fallback


def device_display_expr(cols: set[str], alias: str = "d") -> str:
    parts = []

    if "sysName" in cols:
        parts.append(f"NULLIF({alias}.sysName,'')")

    if "hostname" in cols:
        parts.append(f"NULLIF({alias}.hostname,'')")

    if "ip" in cols:
        parts.append(f"NULLIF(INET6_NTOA({alias}.ip),'')")

    parts.append(f"CAST({alias}.device_id AS CHAR)")

    return "COALESCE(" + ", ".join(parts) + ")"


def device_ip_expr(cols: set[str], alias: str = "d") -> str:
    if "ip" in cols:
        return f"INET6_NTOA({alias}.ip)"
    return "NULL"


def device_anchor(prefix: str, row: dict[str, Any]) -> str:
    did = row.get("device_id")
    label = row.get("device") or row.get("hostname") or row.get("sysName") or row.get("ip_addr") or did or ""

    if did:
        return f'<a href="{prefix}/device/{h(did)}">{h(label)}</a>'

    return h(label)


def interface_anchor(prefix: str, row: dict[str, Any]) -> str:
    port_id = row.get("port_id")
    label = row.get("ifName") or row.get("ifDescr") or row.get("ifIndex") or ""

    if port_id:
        return f'<a href="{prefix}/interface/{h(port_id)}">{h(label)}</a>'

    return h(label)


def error_section(title: str, exc: Exception) -> dict[str, Any]:
    return {
        "title": title + " lookup error",
        "columns": [
            {"key": "section", "label": "Section"},
            {"key": "error", "label": "Error"},
        ],
        "rows": [
            {"section": title, "error": str(exc)}
        ],
        "is_error": True,
    }


def empty_section(title: str) -> dict[str, Any]:
    return {
        "title": title,
        "columns": [],
        "rows": [],
    }


def like_clause(alias: str, col: str, cols: set[str], q: str):
    if col not in cols:
        return None, []

    return f"CAST({alias}.{col} AS CHAR) LIKE %s", [f"%{q}%"]


def lookup_devices(prefix: str, q: str, limit: int) -> dict[str, Any]:
    dev_cols = table_columns("devices")

    if not dev_cols or "device_id" not in dev_cols:
        return empty_section("Devices")

    device_expr = device_display_expr(dev_cols)
    ip_expr = device_ip_expr(dev_cols)

    select_parts = [
        "d.device_id",
        f"{sql_col('d', 'hostname', dev_cols)} AS hostname",
        f"{sql_col('d', 'sysName', dev_cols)} AS sysName",
        f"{ip_expr} AS ip_addr",
        f"{sql_col('d', 'os', dev_cols)} AS os",
        f"{sql_col('d', 'hardware', dev_cols)} AS hardware",
        f"{sql_col('d', 'version', dev_cols)} AS version",
        f"{sql_col('d', 'status', dev_cols)} AS status",
        f"{sql_col('d', 'location', dev_cols)} AS location",
        f"{device_expr} AS device",
    ]

    clauses = ["CAST(d.device_id AS CHAR) LIKE %s"]
    params = [f"%{q}%"]

    for col in ("hostname", "sysName", "os", "hardware", "version", "location", "purpose", "notes"):
        clause, vals = like_clause("d", col, dev_cols, q)
        if clause:
            clauses.append(clause)
            params.extend(vals)

    if "ip" in dev_cols:
        clauses.append("CAST(INET6_NTOA(d.ip) AS CHAR) LIKE %s")
        params.append(f"%{q}%")

    params.append(limit)

    rows = fetch_all(
        f"""
        SELECT
            {", ".join(select_parts)}
        FROM devices d
        WHERE {" OR ".join(clauses)}
        ORDER BY {device_expr}
        LIMIT %s
        """,
        tuple(params),
    )

    out = []
    for row in rows:
        out.append({
            "device": device_anchor(prefix, row),
            "ip": row.get("ip_addr") or "",
            "status": status_badge(row.get("status")),
            "os": row.get("os") or "",
            "hardware": row.get("hardware") or "",
            "version": row.get("version") or "",
            "location": row.get("location") or "",
        })

    return {
        "title": "Devices",
        "columns": [
            {"key": "device", "label": "Device", "html": True},
            {"key": "ip", "label": "IP"},
            {"key": "status", "label": "Status", "html": True},
            {"key": "os", "label": "OS"},
            {"key": "hardware", "label": "Hardware"},
            {"key": "version", "label": "Version"},
            {"key": "location", "label": "Location"},
        ],
        "rows": out,
    }


def lookup_interfaces(prefix: str, q: str, limit: int) -> dict[str, Any]:
    dev_cols = table_columns("devices")
    port_cols = table_columns("ports")

    if not dev_cols or not port_cols or "device_id" not in dev_cols or "device_id" not in port_cols or "port_id" not in port_cols:
        return empty_section("Interfaces")

    device_expr = device_display_expr(dev_cols)

    select_parts = [
        f"{device_expr} AS device",
        "d.device_id",
        "p.port_id",
        f"{sql_col('p', 'ifIndex', port_cols)} AS ifIndex",
        f"{sql_col('p', 'ifName', port_cols)} AS ifName",
        f"{sql_col('p', 'ifDescr', port_cols)} AS ifDescr",
        f"{sql_col('p', 'ifAlias', port_cols)} AS ifAlias",
        f"{sql_col('p', 'ifAdminStatus', port_cols)} AS ifAdminStatus",
        f"{sql_col('p', 'ifOperStatus', port_cols)} AS ifOperStatus",
        f"{sql_col('p', 'ifSpeed', port_cols, '0')} AS ifSpeed",
    ]

    clauses = []
    params = []

    for col in ("hostname", "sysName"):
        clause, vals = like_clause("d", col, dev_cols, q)
        if clause:
            clauses.append(clause)
            params.extend(vals)

    for col in ("ifName", "ifDescr", "ifAlias", "ifType"):
        clause, vals = like_clause("p", col, port_cols, q)
        if clause:
            clauses.append(clause)
            params.extend(vals)

    if not clauses:
        return empty_section("Interfaces")

    params.append(limit)

    rows = fetch_all(
        f"""
        SELECT
            {", ".join(select_parts)}
        FROM ports p
        JOIN devices d ON d.device_id = p.device_id
        WHERE {" OR ".join(clauses)}
        ORDER BY {device_expr}, p.ifIndex
        LIMIT %s
        """,
        tuple(params),
    )

    out = []
    for row in rows:
        out.append({
            "device": device_anchor(prefix, row),
            "interface": interface_anchor(prefix, row),
            "admin": status_badge(row.get("ifAdminStatus")),
            "oper": status_badge(row.get("ifOperStatus")),
            "speed": fmt_speed(row.get("ifSpeed")),
            "descr": row.get("ifDescr") or "",
            "title": row.get("ifAlias") or "",
        })

    return {
        "title": "Interfaces",
        "columns": [
            {"key": "device", "label": "Device", "html": True},
            {"key": "interface", "label": "Interface", "html": True},
            {"key": "admin", "label": "Admin", "html": True},
            {"key": "oper", "label": "Oper", "html": True},
            {"key": "speed", "label": "Speed"},
            {"key": "descr", "label": "Description"},
            {"key": "title", "label": "Title"},
        ],
        "rows": out,
    }


def lookup_macs(prefix: str, q: str, limit: int) -> dict[str, Any]:
    dev_cols = table_columns("devices")
    port_cols = table_columns("ports")
    fdb_cols = table_columns("ports_fdb")

    if "mac_address" not in fdb_cols or "port_id" not in fdb_cols or "port_id" not in port_cols:
        return empty_section("MACs")

    device_expr = device_display_expr(dev_cols)
    qmac = clean_query_mac(q)

    vlan_select = "f.vlan_id AS vlan" if "vlan_id" in fdb_cols else "NULL AS vlan"

    clauses = [
        "f.mac_address LIKE %s",
        f"{norm_sql('f.mac_address')} LIKE %s",
    ]
    params = [f"%{q}%", f"%{qmac}%"]

    for col in ("hostname", "sysName"):
        clause, vals = like_clause("d", col, dev_cols, q)
        if clause:
            clauses.append(clause)
            params.extend(vals)

    for col in ("ifName", "ifDescr", "ifAlias"):
        clause, vals = like_clause("p", col, port_cols, q)
        if clause:
            clauses.append(clause)
            params.extend(vals)

    if "vlan_id" in fdb_cols:
        clauses.append("CAST(f.vlan_id AS CHAR) LIKE %s")
        params.append(f"%{q}%")

    params.append(limit)

    rows = fetch_all(
        f"""
        SELECT
            {device_expr} AS device,
            d.device_id,
            p.port_id,
            {sql_col('p', 'ifIndex', port_cols)} AS ifIndex,
            {sql_col('p', 'ifName', port_cols)} AS ifName,
            {sql_col('p', 'ifDescr', port_cols)} AS ifDescr,
            {sql_col('p', 'ifAlias', port_cols)} AS ifAlias,
            f.mac_address AS mac,
            {vlan_select}
        FROM ports_fdb f
        JOIN ports p ON p.port_id = f.port_id
        JOIN devices d ON d.device_id = p.device_id
        WHERE {" OR ".join(clauses)}
        ORDER BY {device_expr}, p.ifIndex
        LIMIT %s
        """,
        tuple(params),
    )

    out = []
    for row in rows:
        out.append({
            "device": device_anchor(prefix, row),
            "interface": interface_anchor(prefix, row),
            "mac": clean_mac(row.get("mac")),
            "vlan": row.get("vlan") or "",
            "title": row.get("ifAlias") or "",
        })

    return {
        "title": "MACs",
        "columns": [
            {"key": "device", "label": "Device", "html": True},
            {"key": "interface", "label": "Interface", "html": True},
            {"key": "mac", "label": "MAC"},
            {"key": "vlan", "label": "VLAN"},
            {"key": "title", "label": "Title"},
        ],
        "rows": out,
    }


def lookup_ips(prefix: str, q: str, limit: int) -> dict[str, Any]:
    dev_cols = table_columns("devices")
    port_cols = table_columns("ports")
    ip_cols = table_columns("ipv4_addresses")
    mac_cols = table_columns("ipv4_mac")

    if "ipv4_address" not in ip_cols or "port_id" not in ip_cols or "port_id" not in port_cols:
        return empty_section("IPs")

    device_expr = device_display_expr(dev_cols)

    joins = ""
    mac_select = "NULL AS mac"
    mac_clauses = []
    mac_params = []

    if (
        "ipv4_address_id" in ip_cols
        and "ipv4_address_id" in mac_cols
        and "mac_address" in mac_cols
    ):
        joins = "LEFT JOIN ipv4_mac im ON im.ipv4_address_id = ia.ipv4_address_id"
        mac_select = "im.mac_address AS mac"
        mac_clauses = [
            "im.mac_address LIKE %s",
            f"{norm_sql('im.mac_address')} LIKE %s",
        ]
        mac_params = [f"%{q}%", f"%{clean_query_mac(q)}%"]

    clauses = ["CAST(ia.ipv4_address AS CHAR) LIKE %s"]
    params = [f"%{q}%"]

    for col in ("hostname", "sysName"):
        clause, vals = like_clause("d", col, dev_cols, q)
        if clause:
            clauses.append(clause)
            params.extend(vals)

    for col in ("ifName", "ifDescr", "ifAlias"):
        clause, vals = like_clause("p", col, port_cols, q)
        if clause:
            clauses.append(clause)
            params.extend(vals)

    clauses.extend(mac_clauses)
    params.extend(mac_params)

    params.append(limit)

    rows = fetch_all(
        f"""
        SELECT
            {device_expr} AS device,
            d.device_id,
            p.port_id,
            {sql_col('p', 'ifIndex', port_cols)} AS ifIndex,
            {sql_col('p', 'ifName', port_cols)} AS ifName,
            {sql_col('p', 'ifDescr', port_cols)} AS ifDescr,
            {sql_col('p', 'ifAlias', port_cols)} AS ifAlias,
            ia.ipv4_address AS ip_addr,
            {sql_col('ia', 'ipv4_prefixlen', ip_cols)} AS prefix_len,
            {mac_select}
        FROM ipv4_addresses ia
        JOIN ports p ON p.port_id = ia.port_id
        JOIN devices d ON d.device_id = p.device_id
        {joins}
        WHERE {" OR ".join(clauses)}
        ORDER BY INET_ATON(ia.ipv4_address), {device_expr}, p.ifIndex
        LIMIT %s
        """,
        tuple(params),
    )

    out = []
    for row in rows:
        ip_addr = row.get("ip_addr") or ""
        prefix_len = row.get("prefix_len")

        if prefix_len not in (None, ""):
            ip_display = f"{ip_addr}/{prefix_len}"
        else:
            ip_display = ip_addr

        out.append({
            "device": device_anchor(prefix, row),
            "interface": interface_anchor(prefix, row),
            "ip": ip_display,
            "mac": clean_mac(row.get("mac")),
            "title": row.get("ifAlias") or "",
        })

    return {
        "title": "IPs",
        "columns": [
            {"key": "device", "label": "Device", "html": True},
            {"key": "interface", "label": "Interface", "html": True},
            {"key": "ip", "label": "IP"},
            {"key": "mac", "label": "MAC"},
            {"key": "title", "label": "Title"},
        ],
        "rows": out,
    }


def lookup_vlans(prefix: str, q: str, limit: int) -> dict[str, Any]:
    dev_cols = table_columns("devices")
    vlan_cols = table_columns("vlans")

    if "vlan_vlan" not in vlan_cols or "device_id" not in vlan_cols:
        return empty_section("VLANs")

    device_expr = device_display_expr(dev_cols)

    clauses = ["CAST(v.vlan_vlan AS CHAR) LIKE %s"]
    params = [f"%{q}%"]

    for col in ("hostname", "sysName"):
        clause, vals = like_clause("d", col, dev_cols, q)
        if clause:
            clauses.append(clause)
            params.extend(vals)

    for col in ("vlan_name", "vlan_domain", "vlan_type"):
        clause, vals = like_clause("v", col, vlan_cols, q)
        if clause:
            clauses.append(clause)
            params.extend(vals)

    params.append(limit)

    rows = fetch_all(
        f"""
        SELECT
            {device_expr} AS device,
            d.device_id,
            v.vlan_vlan AS vlan,
            {sql_col('v', 'vlan_name', vlan_cols)} AS vlan_name,
            {sql_col('v', 'vlan_domain', vlan_cols)} AS vlan_domain,
            {sql_col('v', 'vlan_type', vlan_cols)} AS vlan_type
        FROM vlans v
        JOIN devices d ON d.device_id = v.device_id
        WHERE {" OR ".join(clauses)}
        ORDER BY {device_expr}, v.vlan_vlan
        LIMIT %s
        """,
        tuple(params),
    )

    out = []
    for row in rows:
        out.append({
            "device": device_anchor(prefix, row),
            "vlan": row.get("vlan") or "",
            "name": row.get("vlan_name") or "",
            "domain": row.get("vlan_domain") or "",
            "type": row.get("vlan_type") or "",
        })

    return {
        "title": "VLANs",
        "columns": [
            {"key": "device", "label": "Device", "html": True},
            {"key": "vlan", "label": "VLAN"},
            {"key": "name", "label": "Name"},
            {"key": "domain", "label": "Domain"},
            {"key": "type", "label": "Type"},
        ],
        "rows": out,
    }


def safe_lookup(title: str, func, prefix: str, q: str, limit: int) -> dict[str, Any]:
    try:
        return func(prefix, q, limit)
    except Exception as exc:
        return error_section(title, exc)


def universal_lookup_context(prefix: str, q: str = "", limit: int = 50) -> dict[str, Any]:
    q = (q or "").strip()

    try:
        limit = int(limit)
    except Exception:
        limit = 50

    limit = max(1, min(limit, 500))

    sections = []

    if q:
        for section in (
            safe_lookup("Devices", lookup_devices, prefix, q, limit),
            safe_lookup("Interfaces", lookup_interfaces, prefix, q, limit),
            safe_lookup("MACs", lookup_macs, prefix, q, limit),
            safe_lookup("IPs", lookup_ips, prefix, q, limit),
            safe_lookup("VLANs", lookup_vlans, prefix, q, limit),
        ):
            if section.get("rows"):
                sections.append(section)

    return {
        "title": "Universal Lookup",
        "subtitle": "Search devices, interfaces, MACs, IPs, and VLANs",
        "q": q,
        "limit": limit,
        "sections": sections,
        "limit_options": [25, 50, 100, 250, 500],
        "reset_url": f"{prefix}/tools/lookup",
    }
