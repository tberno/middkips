import importlib
import html
import re
import inspect
from typing import Any


COMPONENTS = [
    ("devices", "LibreNMS Devices", "/tools/lookup/devices"),
    ("interfaces", "LibreNMS Interfaces", "/tools/lookup/interfaces"),
    ("ips", "LibreNMS IPs", "/tools/lookup/ips"),
    ("macs", "LibreNMS MAC / FDB", "/tools/lookup/macs"),
    ("vlans", "LibreNMS VLANs", "/tools/lookup/vlans"),
    ("events", "LibreNMS Events", "/tools/lookup/events"),
]




def clean_html_value(value: Any) -> Any:
    if value is None:
        return ""

    if not isinstance(value, str):
        return value

    text = value.strip()

    # Component lookups sometimes return already-rendered HTML anchor/span strings.
    # Universal Lookup uses normal escaped tables, so convert those to readable text.
    anchor = re.search(r"<a\b[^>]*>(.*?)</a>", text, flags=re.I | re.S)
    if anchor:
        text = anchor.group(1)

    badge = re.search(r"<span\b[^>]*>(.*?)</span>", text, flags=re.I | re.S)
    if badge:
        text = badge.group(1)

    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def clean_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        cleaned = {}
        for key, value in row.items():
            if key.startswith("_"):
                continue
            cleaned[key] = clean_html_value(value)

        out.append(cleaned)

    return out


def clamp_limit(value: Any, default: int = 50) -> int:
    try:
        limit = int(value)
    except Exception:
        limit = default
    return max(1, min(limit, 500))




def looks_like_column_metadata(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False

    columnish = 0
    for item in value:
        if isinstance(item, dict) and "key" in item and "label" in item:
            # A component column definition usually has key/label/html/sort/etc,
            # but no real lookup object fields.
            columnish += 1

    return columnish == len(value)


def first_rows(obj: Any) -> list[dict[str, Any]]:
    if looks_like_column_metadata(obj):
        return []

    if isinstance(obj, list):
        if looks_like_column_metadata(obj):
            return []
        return [x for x in obj if isinstance(x, dict)]

    if isinstance(obj, dict):
        for key in ("rows", "results", "items", "data", "devices", "interfaces", "ips", "macs", "vlans", "events"):
            val = obj.get(key)
            if isinstance(val, list):
                if looks_like_column_metadata(val):
                    return []
                return [x for x in val if isinstance(x, dict)]

    return []


def columns_for(rows: list[dict[str, Any]], preferred: list[str] | None = None) -> list[dict[str, str]]:
    preferred = preferred or []
    seen = []
    keys = []

    for key in preferred:
        if any(key in row for row in rows):
            keys.append(key)
            seen.append(key)

    for row in rows:
        for key in row.keys():
            if key.startswith("_"):
                continue
            if key not in seen:
                keys.append(key)
                seen.append(key)
            if len(keys) >= 12:
                break
        if len(keys) >= 12:
            break

    return [{"key": key, "label": key.replace("_", " ").title()} for key in keys]


def section(title: str, source: str, rows: list[dict[str, Any]], link: str = "", preferred: list[str] | None = None) -> dict[str, Any]:
    raw_rows = rows or []
    cleaned_rows = clean_rows(raw_rows)

    display_rows = cleaned_rows[:100]
    preview_rows = cleaned_rows[:8]

    return {
        "title": title,
        "source": source,
        "rows": display_rows,
        "preview_rows": preview_rows,
        "count": len(cleaned_rows),
        "shown": len(display_rows),
        "preview_shown": len(preview_rows),
        "columns": columns_for(display_rows, preferred),
        "preview_columns": columns_for(preview_rows, preferred),
        "link": link,
    }


def normalize_component_context(title: str, source: str, ctx: Any, link: str) -> list[dict[str, Any]]:
    sections = []

    # Preferred component shape:
    # {
    #   "columns": [...],
    #   "rows": [...]
    # }
    # Never treat columns as rows.
    if isinstance(ctx, dict) and isinstance(ctx.get("rows"), list):
        rows = first_rows(ctx)
        if rows:
            return [section(title, source, rows, link)]
        return []

    # Multi-section shape:
    # {
    #   "sections": [
    #      {"title": "...", "columns": [...], "rows": [...]}
    #   ]
    # }
    if isinstance(ctx, dict) and isinstance(ctx.get("sections"), list):
        for sub in ctx["sections"]:
            if not isinstance(sub, dict):
                continue

            rows = first_rows(sub)
            if not rows:
                continue

            sections.append(section(
                sub.get("title") or title,
                source,
                rows,
                link,
            ))

        return sections

    rows = first_rows(ctx)
    if rows:
        return [section(title, source, rows, link)]

    # Fallback: only accept dict list values that are actual result rows.
    # Skip component/table metadata like columns.
    if isinstance(ctx, dict):
        skip_keys = {
            "columns",
            "column",
            "limit_options",
            "request",
            "q",
            "query",
            "limit",
            "title",
            "subtitle",
            "error",
            "errors",
            "meta",
        }

        for key, val in ctx.items():
            if key in skip_keys:
                continue

            if isinstance(val, list) and val and isinstance(val[0], dict):
                if looks_like_column_metadata(val):
                    continue

                rows = first_rows(val)
                if rows:
                    sections.append(section(f"{title} - {key}", source, rows, link))

    return sections


def try_call(fn: Any, component: str, q: str, limit: int) -> Any:
    attempts = [
        lambda: fn(component=component, q=q, limit=limit),
        lambda: fn(kind=component, q=q, limit=limit),
        lambda: fn(name=component, q=q, limit=limit),
        lambda: fn(lookup_type=component, q=q, limit=limit),
        lambda: fn(component, q, limit),
        lambda: fn(q=q, limit=limit),
        lambda: fn(q, limit),
    ]

    last_exc = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last_exc = exc
            continue

    if last_exc:
        raise last_exc

    raise RuntimeError("No callable signature worked")


def component_lookup_sections(q: str, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    sections = []
    errors = []

    try:
        mod = importlib.import_module("app.services.component_lookup")
    except Exception as exc:
        return [], [{"source": "LibreNMS", "error": f"component_lookup import failed: {exc}"}]

    generic_names = [
        "component_lookup_context",
        "lookup_component_context",
        "lookup_context",
        "build_component_context",
        "component_context",
    ]

    specific_names = {
        "devices": ["device_lookup_context", "devices_lookup_context", "lookup_devices"],
        "interfaces": ["interface_lookup_context", "interfaces_lookup_context", "lookup_interfaces"],
        "ips": ["ip_lookup_context", "ips_lookup_context", "lookup_ips"],
        "macs": ["mac_lookup_context", "macs_lookup_context", "lookup_macs"],
        "vlans": ["vlan_lookup_context", "vlans_lookup_context", "lookup_vlans"],
        "events": ["event_lookup_context", "events_lookup_context", "lookup_events"],
    }

    for component, title, link in COMPONENTS:
        ctx = None
        used = None
        err = None

        for name in specific_names.get(component, []) + generic_names:
            fn = getattr(mod, name, None)
            if not callable(fn):
                continue

            try:
                ctx = try_call(fn, component, q, limit)
                used = name
                break
            except Exception as exc:
                err = exc
                continue

        if ctx is None:
            errors.append({
                "source": title,
                "error": f"No component context function worked" + (f": {err}" if err else ""),
            })
            continue

        found = normalize_component_context(title, f"LibreNMS/{component}", ctx, f"{link}?q={q}&limit={limit}")

        if found:
            sections.extend(found)

    return sections, errors


def solidserver_sections(q: str, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    try:
        from app.services.solidserver_tool import solidserver_context
    except Exception as exc:
        return [], [{"source": "SolidServer", "error": f"import failed: {exc}"}]

    try:
        ctx = solidserver_context(prefix="", q=q, limit=limit)
    except TypeError:
        try:
            ctx = solidserver_context("", q=q, limit=limit)
        except Exception as exc:
            return [], [{"source": "SolidServer", "error": str(exc)}]
    except Exception as exc:
        return [], [{"source": "SolidServer", "error": str(exc)}]

    sections = []

    for sub in ctx.get("sections", []) if isinstance(ctx, dict) else []:
        if not isinstance(sub, dict):
            continue
        rows = first_rows(sub)
        if rows:
            sections.append(section(
                sub.get("title") or "SolidServer",
                "SolidServer",
                rows,
                f"/tools/solidserver?q={q}&limit={limit}",
            ))

    return sections, []


def mist_sections(q: str, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    try:
        from app.services.mist_tool import mist_context
    except Exception as exc:
        return [], [{"source": "Mist", "error": f"import failed: {exc}"}]

    try:
        ctx = mist_context(q=q, limit=limit)
    except TypeError:
        try:
            ctx = mist_context(q, limit)
        except Exception as exc:
            return [], [{"source": "Mist", "error": str(exc)}]
    except Exception as exc:
        return [], [{"source": "Mist", "error": str(exc)}]

    sections = []

    mapping = [
        ("client_matches", "Mist Client Matches", ["site", "username", "hostname", "mac", "ip", "ssid", "vlan", "ap", "switch", "port"]),
        ("device_matches", "Mist Device / AP Matches", ["site", "type", "name", "model", "ip", "status", "clients", "mac", "serial"]),
        ("switch_matches", "Mist Switch Matches", ["site", "name", "role", "model", "ip", "status", "up_ports", "ports", "clients", "version"]),
    ]

    for key, title, preferred in mapping:
        rows = ctx.get(key, []) if isinstance(ctx, dict) else []
        if rows:
            sections.append(section(
                title,
                "Mist",
                rows,
                f"/tools/mist?q={q}&limit={limit}",
                preferred,
            ))

    errors = []
    for err in ctx.get("errors", []) if isinstance(ctx, dict) else []:
        if isinstance(err, dict):
            errors.append({"source": "Mist", "error": f"{err.get('section', '')}: {err.get('error', '')}"})

    return sections, errors




# --- Entity-first Universal Lookup helpers ---

def norm_text(value: Any) -> str:
    return str(value or "").strip()


def norm_lookup_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def norm_mac_key(value: Any) -> str:
    return re.sub(r"[^0-9a-f]", "", str(value or "").lower())


def norm_ip_key(value: Any) -> str:
    text = str(value or "").strip()
    if "/" in text:
        text = text.split("/", 1)[0]
    return text


def row_get(row: dict[str, Any], candidates: list[str]) -> str:
    normalized = {
        re.sub(r"[^a-z0-9]", "", str(k).lower()): k
        for k in row.keys()
    }

    for candidate in candidates:
        key = re.sub(r"[^a-z0-9]", "", candidate.lower())
        real = normalized.get(key)
        if real is not None and row.get(real) not in (None, ""):
            return str(row.get(real)).strip()

    return ""


def compact_row_summary(row: dict[str, Any]) -> str:
    preferred = [
        "name", "device", "switch", "hostname", "interface", "ip", "mac",
        "site", "role", "model", "status", "title", "description", "ssid",
        "vlan", "port", "username",
    ]

    parts = []
    for key in preferred:
        value = row_get(row, [key])
        if value:
            parts.append(f"{key}={value}")

    if not parts:
        for key, value in row.items():
            if value not in (None, ""):
                parts.append(f"{key}={value}")
            if len(parts) >= 6:
                break

    return " | ".join(parts[:8])


def infer_entity_kind(section_title: str, row: dict[str, Any]) -> str:
    title = section_title.lower()

    model = row_get(row, ["model", "hardware"])
    role = row_get(row, ["role"])
    name = row_get(row, ["name", "device", "switch", "hostname"])

    model_l = model.lower()
    role_l = role.lower()
    name_l = name.lower()

    if "vlan" in title or row_get(row, ["vlan"]):
        return "VLAN"

    if "vlan" in title or row_get(row, ["vlan"]):
        return "VLAN"

    if "client" in title or row_get(row, ["username", "ssid"]):
        return "Client"

    if "ap" in title or model_l.startswith("ap") or "-ul-" in name_l or "-ll-" in name_l:
        return "AP"

    if "switch" in title or model_l.startswith(("ex", "qfx")) or role_l in ("access", "core", "distribution", "services", "internet-switch"):
        return "Switch"

    if "interface" in title or row_get(row, ["interface"]):
        return "Interface"

    if "dns" in title or row_get(row, ["zone", "type", "value"]):
        return "DNS"

    if "ip" in title or row_get(row, ["ip"]):
        return "IP"

    return "Device"


def entity_candidate_keys(row: dict[str, Any]) -> list[str]:
    keys = []

    serial = row_get(row, ["serial", "serial number"])
    mac = row_get(row, ["mac", "learned mac"])
    ip = row_get(row, ["ip", "address"])
    name = row_get(row, ["name", "device", "switch", "hostname"])

    mac_key = norm_mac_key(mac)
    ip_key = norm_ip_key(ip)
    name_key = norm_lookup_key(name)

    if serial:
        keys.append("serial:" + norm_lookup_key(serial))

    if mac_key and len(mac_key) == 12:
        keys.append("mac:" + mac_key)

    if ip_key and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip_key):
        keys.append("ip:" + ip_key)

    if name_key and len(name_key) > 2:
        keys.append("name:" + name_key)

        # Also bridge fqdn and shortname.
        if "." in name_key:
            keys.append("name:" + name_key.split(".", 1)[0])

    return keys


def merge_entity_field(entity: dict[str, Any], field: str, value: str, prefer_existing: bool = True) -> None:
    value = norm_text(value)
    if not value:
        return

    if prefer_existing and entity.get(field):
        return

    entity[field] = value


def build_universal_entities(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    key_index: dict[str, dict[str, Any]] = {}

    for sec in sections:
        title = sec.get("title", "")
        source = sec.get("source", "")
        link = sec.get("link", "")

        for row in sec.get("rows", []) or []:
            if not isinstance(row, dict):
                continue

            keys = entity_candidate_keys(row)

            existing = None
            for key in keys:
                if key in key_index:
                    existing = key_index[key]
                    break

            if existing is None:
                existing = {
                    "kind": infer_entity_kind(title, row),
                    "name": "",
                    "status": "",
                    "ip": "",
                    "mac": "",
                    "serial": "",
                    "site": "",
                    "role": "",
                    "model": "",
                    "version": "",
                    "sources": set(),
                    "links": [],
                    "facts": [],
                    "_keys": set(),
                }
                entities.append(existing)

            for key in keys:
                existing["_keys"].add(key)
                key_index[key] = existing

            existing["sources"].add(source)

            if link and link not in existing["links"]:
                existing["links"].append(link)

            kind = infer_entity_kind(title, row)
            if existing.get("kind") in ("Device", "IP", "DNS") and kind not in ("Device", "IP", "DNS"):
                existing["kind"] = kind

            merge_entity_field(existing, "name", row_get(row, ["name", "device", "switch", "hostname"]))
            merge_entity_field(existing, "status", row_get(row, ["status", "oper", "admin", "state"]))
            merge_entity_field(existing, "ip", row_get(row, ["ip", "address"]))
            merge_entity_field(existing, "mac", row_get(row, ["mac", "learned mac"]))
            merge_entity_field(existing, "serial", row_get(row, ["serial", "serial number"]))
            merge_entity_field(existing, "site", row_get(row, ["site", "location"]))
            merge_entity_field(existing, "role", row_get(row, ["role"]))
            merge_entity_field(existing, "model", row_get(row, ["model", "hardware"]))
            merge_entity_field(existing, "version", row_get(row, ["version", "os"]))

            existing["facts"].append({
                "source": source,
                "section": title,
                "summary": compact_row_summary(row),
            })

    out = []

    for ent in entities:
        ent["sources"] = sorted(x for x in ent.get("sources", set()) if x)
        ent["source_count"] = len(ent["sources"])
        ent["links"] = ent.get("links", [])[:6]
        ent["facts"] = ent.get("facts", [])[:20]
        ent.pop("_keys", None)

        if not ent.get("name"):
            ent["name"] = ent.get("ip") or ent.get("mac") or ent.get("serial") or "Unknown"

        out.append(ent)

    out.sort(key=lambda e: (
        {"Switch": 0, "Device": 1, "AP": 2, "Client": 3, "Interface": 4, "IP": 5, "DNS": 6}.get(e.get("kind"), 9),
        -e.get("source_count", 0),
        str(e.get("name", "")).lower(),
    ))

    return out




def is_vlan_query(q: str) -> bool:
    ql = str(q or "").strip().lower()
    return ql.startswith("vlan") or ql.isdigit()


def entity_score(ent: dict[str, Any], q: str) -> int:
    ql = str(q or "").strip().lower()
    kind = ent.get("kind", "")
    name = str(ent.get("name", "")).lower()
    ip = str(ent.get("ip", "")).lower()
    mac = norm_mac_key(ent.get("mac", ""))
    serial = str(ent.get("serial", "")).lower()
    sources = ent.get("sources", []) or []

    score = 0

    # Multi-source objects are usually the most useful.
    score += int(ent.get("source_count", 0)) * 25

    # Prefer operational entities over raw supporting records.
    score += {
        "Switch": 45,
        "AP": 35,
        "Client": 35,
        "Device": 25,
        "Interface": 8,
        "IP": 6,
        "DNS": 4,
        "VLAN": -25,
    }.get(kind, 0)

    if "Mist" in sources:
        score += 18

    if any(str(src).startswith("LibreNMS") for src in sources):
        score += 12

    if "SolidServer" in sources:
        score += 10

    # Query relevance.
    if ql:
        if name == ql:
            score += 80
        elif ql in name:
            score += 45

        if ip and ql == ip:
            score += 80
        elif ip and ql in ip:
            score += 30

        qmac = norm_mac_key(ql)
        if qmac and mac and qmac == mac:
            score += 90
        elif qmac and mac and qmac in mac:
            score += 45

        if serial and ql == serial:
            score += 70
        elif serial and ql in serial:
            score += 35

    # Do not let broad VLAN hits dominate normal word searches.
    if kind == "VLAN" and not is_vlan_query(q):
        score -= 60

    # DNS/IP-only objects are useful, but supporting unless they are exact IP/DNS searches.
    if kind in ("DNS", "IP") and ql and ql not in name and ql not in ip:
        score -= 10

    return score


def organize_universal_entities(entities: list[dict[str, Any]], q: str) -> dict[str, Any]:
    primary = []
    supporting = []
    hidden = []

    for ent in entities:
        score = entity_score(ent, q)
        ent["score"] = score

        kind = ent.get("kind", "")
        sources = ent.get("sources", []) or []

        is_good_entity = kind in ("Switch", "AP", "Client", "Device")
        is_multisource = len(sources) > 1
        is_good_score = score >= 45

        if is_good_entity and (is_good_score or is_multisource):
            primary.append(ent)
        elif kind == "VLAN" and not is_vlan_query(q):
            hidden.append(ent)
        else:
            supporting.append(ent)

    primary.sort(key=lambda e: (-e.get("score", 0), e.get("kind", ""), str(e.get("name", "")).lower()))
    supporting.sort(key=lambda e: (-e.get("score", 0), e.get("kind", ""), str(e.get("name", "")).lower()))
    hidden.sort(key=lambda e: (-e.get("score", 0), e.get("kind", ""), str(e.get("name", "")).lower()))

    # Keep the top view readable.
    return {
        "primary_entities": primary[:30],
        "supporting_entities": supporting[:80],
        "hidden_entities": hidden,
        "hidden_count": len(hidden),
    }




def universal_entity_rows(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []

    for ent in entities:
        rows.append({
            "kind": ent.get("kind", ""),
            "name": ent.get("name", ""),
            "sources": ", ".join(ent.get("sources", []) or []),
            "status": ent.get("status", ""),
            "ip": ent.get("ip", ""),
            "mac": ent.get("mac", ""),
            "site": ent.get("site", ""),
            "role": ent.get("role", ""),
            "model": ent.get("model", ""),
            "version": ent.get("version", ""),
            "serial": ent.get("serial", ""),
        })

    rows.sort(key=lambda r: (
        {"Switch": 0, "Device": 1, "AP": 2, "Client": 3, "Interface": 4, "IP": 5, "DNS": 6, "VLAN": 7}.get(r.get("kind"), 9),
        str(r.get("name", "")).lower(),
    ))

    return rows


def universal_context(q: str = "", limit: int = 50) -> dict[str, Any]:
    q = (q or "").strip()
    limit = clamp_limit(limit)

    context = {
        "q": q,
        "limit": limit,
        "limit_options": [25, 50, 100, 250, 500],
        "sections": [],
        "errors": [],
        "quick_links": [],
        "entities": [],
        "primary_entities": [],
        "supporting_entities": [],
        "hidden_entities": [],
        "hidden_count": 0,
        "entity_rows": [],
    }

    if not q:
        return context

    sources = [
        component_lookup_sections,
        solidserver_sections,
        mist_sections,
    ]

    for source in sources:
        secs, errs = source(q, limit)
        context["sections"].extend(secs)
        context["errors"].extend(errs)

    context["quick_links"] = [
        {"name": "Mist", "url": f"/tools/mist?q={q}&limit={limit}"},
        {"name": "SolidServer", "url": f"/tools/solidserver?q={q}&limit={limit}"},
        {"name": "Devices", "url": f"/tools/lookup/devices?q={q}&limit={limit}"},
        {"name": "Interfaces", "url": f"/tools/lookup/interfaces?q={q}&limit={limit}"},
        {"name": "IPs", "url": f"/tools/lookup/ips?q={q}&limit={limit}"},
        {"name": "MAC/FDB", "url": f"/tools/lookup/macs?q={q}&limit={limit}"},
        {"name": "Events", "url": f"/tools/lookup/events?q={q}&limit={limit}"},
    ]

    context["entities"] = build_universal_entities(context["sections"])
    context["entity_rows"] = universal_entity_rows(context["entities"])
    context.update(organize_universal_entities(context["entities"], q))

    return context

# --- Correlation v2 override ---
# Later function definitions override the earlier versions above.

def _s(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _s(value).lower()


def _clean_name(value: Any) -> str:
    text = _lower(clean_html_value(value))
    text = text.strip()
    text = text.replace(".middlebury.edu", "")
    text = text.split("/", 1)[0] if "/" in text and not text.startswith("http") else text
    return text


def _short_name(value: Any) -> str:
    text = _clean_name(value)
    if "." in text:
        return text.split(".", 1)[0]
    return text


def _clean_mac(value: Any) -> str:
    return re.sub(r"[^0-9a-f]", "", _lower(value))


def _fmt_mac(value: Any) -> str:
    clean = _clean_mac(value)
    if len(clean) == 12:
        return ":".join(clean[i:i+2] for i in range(0, 12, 2))
    return _s(value)


def _clean_ip(value: Any) -> str:
    text = _s(value)
    if "/" in text:
        text = text.split("/", 1)[0]
    return text.strip()


def _is_ipv4(value: Any) -> bool:
    return bool(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", _clean_ip(value)))


def _row_get(row: dict[str, Any], candidates: list[str]) -> str:
    normalized = {
        re.sub(r"[^a-z0-9]", "", str(k).lower()): k
        for k in row.keys()
    }

    for candidate in candidates:
        key = re.sub(r"[^a-z0-9]", "", candidate.lower())
        real = normalized.get(key)
        if real is not None and row.get(real) not in (None, ""):
            return _s(row.get(real))

    return ""


def _row_any(row: dict[str, Any], candidates: list[str]) -> str:
    val = _row_get(row, candidates)
    if val:
        return val

    # Fuzzy fallback for columns like "Learned MAC", "IP Address", etc.
    wanted = [re.sub(r"[^a-z0-9]", "", c.lower()) for c in candidates]
    for k, v in row.items():
        nk = re.sub(r"[^a-z0-9]", "", str(k).lower())
        if any(w in nk or nk in w for w in wanted):
            if v not in (None, ""):
                return _s(v)

    return ""


def _infer_kind(title: str, source: str, row: dict[str, Any]) -> str:
    title_l = _lower(title)
    source_l = _lower(source)

    model = _row_any(row, ["model", "hardware"])
    role = _row_any(row, ["role"])
    name = _row_any(row, ["name", "device", "switch", "hostname", "host"])
    interface = _row_any(row, ["interface", "ifname", "port"])
    ssid = _row_any(row, ["ssid"])
    username = _row_any(row, ["username", "user"])
    vlan = _row_any(row, ["vlan", "vlan id", "vlanid"])
    rr_type = _row_any(row, ["type", "rr type"])
    zone = _row_any(row, ["zone", "dns zone"])

    model_l = model.lower()
    role_l = role.lower()
    name_l = name.lower()

    if "event" in title_l:
        return "Event"

    if "dns" in title_l or zone or rr_type in ("A", "AAAA", "CNAME", "TXT", "MX", "NS", "PTR", "SOA"):
        return "DNS"

    if "vlan" in title_l or (vlan and not interface):
        return "VLAN"

    if "client" in title_l or ssid or username:
        return "Client"

    if "interface" in title_l or interface:
        return "Interface"

    if "mac" in title_l or "fdb" in title_l:
        return "MAC/FDB"

    if "ip" in title_l and not ("mist" in source_l):
        return "IP"

    if "ap" in title_l or model_l.startswith("ap") or name_l.startswith("ap-"):
        return "AP"

    if (
        "switch" in title_l
        or model_l.startswith(("ex", "qfx"))
        or role_l in ("access", "core", "distribution", "services", "internet-switch")
    ):
        return "Switch"

    return "Device"


def _extract_identifiers(title: str, source: str, row: dict[str, Any]) -> dict[str, Any]:
    kind = _infer_kind(title, source, row)

    name = _row_any(row, [
        "name", "device", "switch", "hostname", "host", "fqdn",
        "dns name", "record", "rr", "label"
    ])

    # For interfaces, parent device is the important object name.
    parent_device = _row_any(row, ["device", "switch", "hostname", "host"])

    interface = _row_any(row, ["interface", "ifname", "port", "if"])
    ip = _row_any(row, ["ip", "ip address", "address", "ipaddr"])
    mac = _row_any(row, ["mac", "learned mac", "client mac", "device mac", "switch mac"])
    serial = _row_any(row, ["serial", "serial number"])
    site = _row_any(row, ["site", "location", "pod"])
    role = _row_any(row, ["role"])
    model = _row_any(row, ["model", "hardware"])
    version = _row_any(row, ["version", "os", "firmware"])
    status = _row_any(row, ["status", "state", "oper", "admin"])
    vlan = _row_any(row, ["vlan", "vlan id", "vlanid"])
    username = _row_any(row, ["username", "user"])
    ssid = _row_any(row, ["ssid"])
    description = _row_any(row, ["description", "descr", "title", "alias"])

    # SolidServer/DNS often has name/value fields.
    value = _row_any(row, ["value", "data", "target", "rdata"])
    rr_type = _row_any(row, ["type", "rr type"])
    zone = _row_any(row, ["zone", "dns zone"])

    # Prefer meaningful parent name for interface/MAC/FDB/IP evidence.
    entity_name = name
    if kind in ("Interface", "MAC/FDB", "IP") and parent_device:
        entity_name = parent_device

    # DNS A/PTR/value rows can attach by IP or hostname.
    if kind == "DNS":
        if _is_ipv4(value) and not ip:
            ip = value
        if not entity_name:
            entity_name = name or value

    # IPAM/DHCP rows often include hostname as name and IP separately.
    if not entity_name and ip:
        entity_name = ip

    return {
        "kind": kind,
        "name": clean_html_value(entity_name),
        "parent_device": clean_html_value(parent_device),
        "interface": clean_html_value(interface),
        "ip": _clean_ip(ip),
        "mac": _fmt_mac(mac),
        "mac_key": _clean_mac(mac),
        "serial": clean_html_value(serial),
        "site": clean_html_value(site),
        "role": clean_html_value(role),
        "model": clean_html_value(model),
        "version": clean_html_value(version),
        "status": clean_html_value(status),
        "vlan": clean_html_value(vlan),
        "username": clean_html_value(username),
        "ssid": clean_html_value(ssid),
        "description": clean_html_value(description),
        "dns_value": clean_html_value(value),
        "dns_type": clean_html_value(rr_type),
        "zone": clean_html_value(zone),
    }


def _entity_keys(info: dict[str, Any]) -> list[str]:
    keys = []

    kind = info.get("kind", "")
    name = info.get("name", "")
    parent = info.get("parent_device", "")
    ip = info.get("ip", "")
    mac_key = info.get("mac_key", "")
    serial = info.get("serial", "")

    if serial:
        keys.append("serial:" + _clean_name(serial))

    if mac_key and len(mac_key) == 12:
        keys.append("mac:" + mac_key)

    if ip and _is_ipv4(ip):
        keys.append("ip:" + _clean_ip(ip))

    for n in (name, parent):
        short = _short_name(n)
        full = _clean_name(n)
        if full and len(full) > 2:
            keys.append("name:" + full)
        if short and len(short) > 2:
            keys.append("name:" + short)

    # Clients should not merge with switches just because they are on the same AP/switch name.
    if kind == "Client":
        client_keys = []
        if mac_key and len(mac_key) == 12:
            client_keys.append("client-mac:" + mac_key)
        if ip and _is_ipv4(ip):
            client_keys.append("client-ip:" + _clean_ip(ip))
        username = _clean_name(info.get("username", ""))
        if username:
            client_keys.append("client-user:" + username)
        return client_keys or keys

    return list(dict.fromkeys(keys))


def _new_entity(info: dict[str, Any]) -> dict[str, Any]:
    name = info.get("name") or info.get("parent_device") or info.get("ip") or info.get("mac") or "Unknown"

    return {
        "kind": info.get("kind") or "Device",
        "name": name,
        "sources": set(),
        "source_sections": set(),
        "status": "",
        "ip": "",
        "mac": "",
        "site": "",
        "role": "",
        "model": "",
        "version": "",
        "serial": "",
        "interfaces": set(),
        "vlans": set(),
        "dns": set(),
        "clients": set(),
        "facts": [],
        "_keys": set(),
    }


def _prefer_kind(old: str, new: str) -> str:
    priority = {
        "Switch": 100,
        "AP": 90,
        "Client": 85,
        "Device": 70,
        "Interface": 40,
        "IP": 35,
        "MAC/FDB": 30,
        "DNS": 25,
        "VLAN": 10,
        "Event": 5,
    }

    return new if priority.get(new, 0) > priority.get(old, 0) else old


def _set_field(ent: dict[str, Any], key: str, value: Any) -> None:
    value = clean_html_value(value)
    if value in (None, ""):
        return

    if not ent.get(key):
        ent[key] = value


def _summary_from_info(info: dict[str, Any]) -> str:
    parts = []

    for key in [
        "name", "parent_device", "interface", "ip", "mac", "site", "role",
        "model", "status", "vlan", "username", "ssid", "dns_type",
        "dns_value", "description",
    ]:
        val = info.get(key)
        if val:
            parts.append(f"{key}={val}")

    return " | ".join(parts[:10])


def build_universal_entities(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    key_index: dict[str, dict[str, Any]] = {}

    # Pass 1: create/merge by strong identifiers.
    pending_infos = []

    for sec in sections:
        title = sec.get("title", "")
        source = sec.get("source", "")

        for row in sec.get("rows", []) or []:
            if not isinstance(row, dict):
                continue

            info = _extract_identifiers(title, source, row)
            keys = _entity_keys(info)

            ent = None
            for key in keys:
                if key in key_index:
                    ent = key_index[key]
                    break

            if ent is None:
                ent = _new_entity(info)
                entities.append(ent)

            for key in keys:
                ent["_keys"].add(key)
                key_index[key] = ent

            ent["kind"] = _prefer_kind(ent.get("kind", ""), info.get("kind", ""))

            ent["sources"].add(source)
            ent["source_sections"].add(title)

            _set_field(ent, "name", info.get("name") or info.get("parent_device"))
            _set_field(ent, "status", info.get("status"))
            _set_field(ent, "ip", info.get("ip"))
            _set_field(ent, "mac", info.get("mac"))
            _set_field(ent, "site", info.get("site"))
            _set_field(ent, "role", info.get("role"))
            _set_field(ent, "model", info.get("model"))
            _set_field(ent, "version", info.get("version"))
            _set_field(ent, "serial", info.get("serial"))

            if info.get("interface"):
                ent["interfaces"].add(info["interface"])

            if info.get("vlan"):
                ent["vlans"].add(info["vlan"])

            if info.get("dns_type") or info.get("dns_value") or info.get("zone"):
                dns_bits = []
                if info.get("dns_type"):
                    dns_bits.append(info["dns_type"])
                if info.get("name"):
                    dns_bits.append(info["name"])
                if info.get("dns_value"):
                    dns_bits.append("-> " + info["dns_value"])
                if info.get("zone"):
                    dns_bits.append("(" + info["zone"] + ")")
                ent["dns"].add(" ".join(dns_bits))

            if info.get("username") or info.get("ssid"):
                client_bits = []
                if info.get("username"):
                    client_bits.append(info["username"])
                if info.get("ssid"):
                    client_bits.append("ssid=" + info["ssid"])
                if info.get("ip"):
                    client_bits.append("ip=" + info["ip"])
                ent["clients"].add(" | ".join(client_bits))

            ent["facts"].append({
                "source": source,
                "section": title,
                "summary": _summary_from_info(info),
            })

            pending_infos.append((ent, info))

    # Pass 2: bridge weak supporting rows to already known devices by shortname/IP.
    # This helps attach interface/IP/DNS evidence that initially became its own object.
    canonical = {}

    for ent in entities:
        for key in ent.get("_keys", set()):
            canonical[key] = ent

    merged_away_ids = set()

    for ent in list(entities):
        if id(ent) in merged_away_ids:
            continue

        possible = set(ent.get("_keys", set()))

        if ent.get("name"):
            possible.add("name:" + _short_name(ent["name"]))
            possible.add("name:" + _clean_name(ent["name"]))

        if ent.get("ip"):
            possible.add("ip:" + _clean_ip(ent["ip"]))

        if ent.get("mac"):
            mac_key = _clean_mac(ent["mac"])
            if mac_key:
                possible.add("mac:" + mac_key)

        target = None

        for key in possible:
            candidate = canonical.get(key)
            if candidate is not None and candidate is not ent:
                # Prefer merging support types into operational objects, not the reverse.
                if candidate.get("kind") in ("Switch", "AP", "Device", "Client"):
                    target = candidate
                    break

        if not target:
            continue

        # Merge ent into target.
        target["kind"] = _prefer_kind(target.get("kind", ""), ent.get("kind", ""))

        for field in ["status", "ip", "mac", "site", "role", "model", "version", "serial"]:
            _set_field(target, field, ent.get(field))

        for set_field in ["sources", "source_sections", "interfaces", "vlans", "dns", "clients", "_keys"]:
            target[set_field].update(ent.get(set_field, set()))

        target["facts"].extend(ent.get("facts", []))
        merged_away_ids.add(id(ent))

    final = []

    for ent in entities:
        if id(ent) in merged_away_ids:
            continue

        ent["sources"] = sorted(x for x in ent.get("sources", set()) if x)
        ent["source_sections"] = sorted(x for x in ent.get("source_sections", set()) if x)
        ent["source_count"] = len(ent["sources"])
        ent["interfaces"] = ", ".join(sorted(x for x in ent.get("interfaces", set()) if x)[:12])
        ent["vlans"] = ", ".join(sorted(x for x in ent.get("vlans", set()) if x)[:12])
        ent["dns"] = "; ".join(sorted(x for x in ent.get("dns", set()) if x)[:8])
        ent["clients"] = "; ".join(sorted(x for x in ent.get("clients", set()) if x)[:8])
        ent["facts"] = ent.get("facts", [])[:40]
        ent.pop("_keys", None)

        if not ent.get("name") or ent.get("name") == "Unknown":
            ent["name"] = ent.get("ip") or ent.get("mac") or ent.get("serial") or "Unknown"

        final.append(ent)

    final.sort(key=lambda e: (
        {"Switch": 0, "AP": 1, "Client": 2, "Device": 3, "Interface": 4, "IP": 5, "MAC/FDB": 6, "DNS": 7, "VLAN": 8, "Event": 9}.get(e.get("kind"), 99),
        -e.get("source_count", 0),
        str(e.get("name", "")).lower(),
    ))

    return final


def universal_entity_rows(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []

    for ent in entities:
        rows.append({
            "kind": ent.get("kind", ""),
            "name": ent.get("name", ""),
            "sources": ", ".join(ent.get("sources", []) or []),
            "status": ent.get("status", ""),
            "ip": ent.get("ip", ""),
            "mac": ent.get("mac", ""),
            "site": ent.get("site", ""),
            "role": ent.get("role", ""),
            "model": ent.get("model", ""),
            "version": ent.get("version", ""),
            "serial": ent.get("serial", ""),
            "interfaces": ent.get("interfaces", ""),
            "vlans": ent.get("vlans", ""),
            "dns": ent.get("dns", ""),
            "clients": ent.get("clients", ""),
        })

    return rows


# --- Correlation cleanup v3 override ---
# Keep raw result sections intact, but omit low-signal Unknown rows from Correlated Summary.

def _infer_kind(title: str, source: str, row: dict[str, Any]) -> str:
    title_l = _lower(title)
    source_l = _lower(source)

    model = _row_any(row, ["model", "hardware"])
    role = _row_any(row, ["role"])
    name = _row_any(row, ["name", "device", "switch", "hostname", "host"])
    interface = _row_any(row, ["interface", "ifname", "port"])
    ssid = _row_any(row, ["ssid"])
    username = _row_any(row, ["username", "user"])
    vlan = _row_any(row, ["vlan", "vlan id", "vlanid"])
    ip = _row_any(row, ["ip", "ip address", "address", "ipaddr"])
    mac = _row_any(row, ["mac", "learned mac", "client mac", "device mac", "switch mac"])
    rr_type = _row_any(row, ["type", "rr type"])
    zone = _row_any(row, ["zone", "dns zone"])

    model_l = model.lower()
    role_l = role.lower()
    name_l = name.lower()

    if "event" in title_l:
        return "Event"

    if "dns" in title_l or zone or rr_type in ("A", "AAAA", "CNAME", "TXT", "MX", "NS", "PTR", "SOA"):
        return "DNS"

    # Mist rows with client/user/wireless fields should be clients before VLAN.
    if "client" in title_l or ssid or username:
        return "Client"

    # Mist client matches sometimes only expose IP/MAC/site/name plus VLAN.
    if "mist" in source_l and ip and mac and not (
        "switch" in title_l
        or model_l.startswith(("ex", "qfx", "ap"))
        or role_l in ("access", "core", "distribution", "services", "internet-switch")
    ):
        return "Client"

    if "ap" in title_l or model_l.startswith("ap") or name_l.startswith("ap-"):
        return "AP"

    if (
        "switch" in title_l
        or model_l.startswith(("ex", "qfx"))
        or role_l in ("access", "core", "distribution", "services", "internet-switch")
    ):
        return "Switch"

    if "interface" in title_l or interface:
        return "Interface"

    if "mac" in title_l or "fdb" in title_l:
        return "MAC/FDB"

    if "vlan" in title_l or (vlan and not interface and "mist" not in source_l):
        return "VLAN"

    if "ip" in title_l:
        return "IP"

    return "Device"


def _is_unknown_name(value: Any) -> bool:
    text = _lower(value)
    return text in ("", "unknown", "none", "null", "n/a", "-")


def _meaningful_correlated_entity(ent: dict[str, Any]) -> bool:
    name = ent.get("name", "")
    kind = ent.get("kind", "")

    # Strong identity means keep it even if the friendly name is bad.
    strong_fields = [
        ent.get("ip"),
        ent.get("mac"),
        ent.get("serial"),
        ent.get("model"),
        ent.get("site"),
        ent.get("interfaces"),
        ent.get("dns"),
        ent.get("clients"),
    ]

    has_strong_field = any(_s(v) for v in strong_fields)

    if not _is_unknown_name(name):
        return True

    # Events with no real object identity are noise in the summary.
    if kind == "Event" and not has_strong_field:
        return False

    # Anything Unknown with no useful fields should be omitted from summary.
    if not has_strong_field:
        return False

    return True


def universal_entity_rows(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []

    for ent in entities:
        if not _meaningful_correlated_entity(ent):
            continue

        name = ent.get("name", "")

        # If the name is Unknown but we have a better identifier, display that.
        if _is_unknown_name(name):
            name = ent.get("ip") or ent.get("mac") or ent.get("serial") or ent.get("dns") or "Unknown"

        rows.append({
            "kind": ent.get("kind", ""),
            "name": name,
            "sources": ", ".join(ent.get("sources", []) or []),
            "status": ent.get("status", ""),
            "ip": ent.get("ip", ""),
            "mac": ent.get("mac", ""),
            "site": ent.get("site", ""),
            "role": ent.get("role", ""),
            "model": ent.get("model", ""),
            "version": ent.get("version", ""),
            "serial": ent.get("serial", ""),
            "interfaces": ent.get("interfaces", ""),
            "vlans": ent.get("vlans", ""),
            "dns": ent.get("dns", ""),
            "clients": ent.get("clients", ""),
        })

    rows.sort(key=lambda r: (
        {"Switch": 0, "AP": 1, "Client": 2, "Device": 3, "Interface": 4, "IP": 5, "MAC/FDB": 6, "DNS": 7, "VLAN": 8, "Event": 9}.get(r.get("kind"), 99),
        str(r.get("name", "")).lower(),
    ))

    return rows

