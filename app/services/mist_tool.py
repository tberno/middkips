import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


_CACHE: dict[str, tuple[float, Any]] = {}
CACHE_TTL_SECONDS = 90


def mist_cfg() -> dict[str, str]:
    return {
        "base_url": os.environ.get("MIST_BASE_URL", "https://api.mist.com").rstrip("/"),
        "org_id": os.environ.get("MIST_ORG_ID", "").strip(),
        "token": os.environ.get("MIST_API_TOKEN", "").strip(),
    }


def mist_configured() -> bool:
    c = mist_cfg()
    return bool(c["base_url"] and c["org_id"] and c["token"])


def mist_get(path: str, params: Optional[dict[str, Any]] = None, use_cache: bool = True) -> Any:
    c = mist_cfg()

    if not mist_configured():
        raise RuntimeError("Missing MIST_BASE_URL, MIST_ORG_ID, or MIST_API_TOKEN")

    url = f"{c['base_url']}{path}"

    if params:
        url += "?" + urllib.parse.urlencode(params)

    cache_key = url
    now = time.time()

    if use_cache and cache_key in _CACHE:
        ts, payload = _CACHE[cache_key]
        if now - ts < CACHE_TTL_SECONDS:
            return payload

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Token {c['token']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=18) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

    try:
        payload = json.loads(body)
    except Exception:
        raise RuntimeError(f"HTTP {status} returned non-JSON: {body[:300]}")

    if use_cache:
        _CACHE[cache_key] = (now, payload)

    return payload


def as_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    if isinstance(payload, dict):
        for key in ("results", "data", "items", "rows", "clients", "devices", "switches"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]

    return []


def safe_get(obj: Any, key: str, default: str = "") -> str:
    if isinstance(obj, dict):
        val = obj.get(key, default)
        if val is None:
            return ""
        return str(val)
    return default


def pick(row: dict[str, Any], keys: list[str]) -> str:
    lower = {str(k).lower(): k for k in row.keys()}
    for key in keys:
        real = lower.get(key.lower())
        if real is not None and row.get(real) not in (None, ""):
            return str(row.get(real))
    return ""



def fmt_mac(value: str) -> str:
    clean = norm_mac(value)
    if len(clean) == 12:
        return ":".join(clean[i:i+2] for i in range(0, 12, 2))
    return str(value or "")


def fmt_epoch(value: str) -> str:
    import datetime

    if value in (None, ""):
        return ""

    try:
        ts = float(value)
    except Exception:
        return str(value)

    # Mist sometimes returns ms timestamps.
    if ts > 20000000000:
        ts = ts / 1000

    try:
        return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(value)


def maybe_count(value) -> str:
    if isinstance(value, dict):
        for k in ("total", "num_wired_clients", "num_clients", "count"):
            v = value.get(k)
            if isinstance(v, int):
                return str(v)
        return ""
    if isinstance(value, list):
        return str(len(value))
    if value is None:
        return ""
    return str(value)


def norm_mac(value: str) -> str:
    return str(value or "").lower().replace(":", "").replace("-", "").replace(".", "").strip()


def is_online(row: dict[str, Any]) -> bool:
    for key in ("status", "state"):
        value = str(row.get(key, "")).lower()
        if value in ("connected", "online", "up"):
            return True
        if value in ("disconnected", "offline", "down"):
            return False

    for key in ("connected", "online", "up"):
        value = row.get(key)
        if isinstance(value, bool):
            return value

    return False


def device_type(row: dict[str, Any]) -> str:
    text = " ".join([
        pick(row, ["type", "device_type", "kind"]),
        pick(row, ["model"]),
        pick(row, ["name", "hostname"]),
    ]).lower()

    if "switch" in text:
        return "Switch"

    model = pick(row, ["model"]).upper()
    if model.startswith(("EX", "QFX")):
        return "Switch"

    if "gateway" in text or "srx" in text:
        return "Gateway"

    if "ap" in text or model.startswith("AP"):
        return "AP"

    return pick(row, ["type", "device_type"]) or "Device"


def is_switch(row: dict[str, Any]) -> bool:
    return device_type(row) == "Switch"


def pod_bucket(site_name: str) -> str:
    name = site_name.strip()

    if "pod" in name.lower():
        return name

    if name.startswith("DFL"):
        return "DFL"

    if "Voter" in name:
        return "Voter Hall"

    if "Homer" in name:
        return "Homer Noble"

    return "Other"


def compact_id(value: str) -> str:
    value = str(value or "")
    if len(value) > 18:
        return value[:8] + "..." + value[-6:]
    return value


def row_matches(row: dict[str, Any], q: str) -> bool:
    q = str(q or "").strip().lower()
    if not q:
        return False

    q_mac = norm_mac(q)

    for key, value in row.items():
        if value is None:
            continue

        text = str(value).lower()

        if q in text:
            return True

        if q_mac and len(q_mac) >= 6 and q_mac in norm_mac(text):
            return True

    return False


def client_row(site: str, row: dict[str, Any], ap_names: dict[str, str] | None = None, switch_names: dict[str, str] | None = None) -> dict[str, Any]:
    ap_names = ap_names or {}
    switch_names = switch_names or {}

    ap_key = pick(row, ["ap", "ap_name", "ap_id", "device_id", "device_name"])
    sw_key = pick(row, ["switch", "switch_name", "switch_mac", "wired_device_name", "wired_device_id"])

    ap_label = pick(row, ["ap_name", "device_name"]) or ap_names.get(ap_key, ap_names.get(norm_mac(ap_key), ap_key))
    switch_label = pick(row, ["switch_name", "wired_device_name"]) or switch_names.get(sw_key, switch_names.get(norm_mac(sw_key), sw_key))

    return {
        "site": site,
        "username": pick(row, ["username", "user", "user_name"]),
        "hostname": pick(row, ["hostname", "host_name", "name", "device_name"]),
        "mac": fmt_mac(pick(row, ["mac", "client_mac", "client_mac_addr"])),
        "mac_clean": norm_mac(pick(row, ["mac", "client_mac", "client_mac_addr"])),
        "ip": pick(row, ["ip", "ip_addr", "client_ip"]),
        "ssid": pick(row, ["ssid", "wlan", "wlan_name"]),
        "vlan": pick(row, ["vlan", "vlan_id"]),
        "ap": ap_label,
        "switch": switch_label,
        "port": pick(row, ["port", "port_id", "port_name", "ifname"]),
        "rssi": pick(row, ["rssi"]),
        "snr": pick(row, ["snr"]),
        "status": pick(row, ["status", "state"]),
        "last_seen": fmt_epoch(pick(row, ["last_seen", "last_seen_time", "timestamp"])),
    }


def device_row(site: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "site": site,
        "type": device_type(row),
        "name": pick(row, ["name", "hostname", "device_name"]),
        "model": pick(row, ["model"]),
        "mac": pick(row, ["mac", "device_mac"]),
        "serial": pick(row, ["serial", "serial_number"]),
        "ip": pick(row, ["ip", "ip_addr"]),
        "status": pick(row, ["status", "state"]) or ("online" if is_online(row) else "offline"),
        "clients": pick(row, ["num_clients", "client_count", "clients"]),
        "version": pick(row, ["version", "fwupdate", "firmware_version"]),
    }


def switch_row(site: str, row: dict[str, Any], site_id: str = "") -> dict[str, Any]:
    clients_stats = row.get("clients_stats") if isinstance(row.get("clients_stats"), dict) else {}
    total_clients = ""

    if isinstance(clients_stats, dict):
        total = clients_stats.get("total")
        if isinstance(total, dict):
            total_clients = str(total.get("num_wired_clients", "") or total.get("num_clients", "") or "")

    if not total_clients:
        total_clients = pick(row, ["num_clients", "client_count", "clients"])

    if_stat = row.get("if_stat")
    up_ports = ""
    total_ports = ""

    if isinstance(if_stat, dict):
        ports = [v for v in if_stat.values() if isinstance(v, dict) and v.get("port_id")]
        total_ports = str(len(ports))
        up_ports = str(sum(1 for v in ports if v.get("up") is True))

    mac_raw = pick(row, ["mac", "device_mac", "switch_mac", "chassis_mac"])

    return {
        "site": site,
        "site_id": site_id,
        "name": pick(row, ["hostname", "name", "device_name", "switch_name"]),
        "role": pick(row, ["role"]),
        "model": pick(row, ["model", "chassis_model"]),
        "mac": fmt_mac(mac_raw),
        "mac_clean": norm_mac(mac_raw),
        "serial": pick(row, ["serial", "serial_number", "chassis_serial"]),
        "ip": pick(row, ["ip", "ext_ip", "ip_addr", "router_id"]),
        "status": pick(row, ["status", "state"]) or ("connected" if is_online(row) else "offline"),
        "up_ports": up_ports,
        "ports": total_ports or pick(row, ["num_ports", "ports", "port_count"]),
        "clients": total_clients,
        "macs": pick(row, ["mac_table_count"]),
        "version": pick(row, ["version", "firmware_version"]),
        "last_seen": fmt_epoch(pick(row, ["last_seen"])),
    }


def dedupe(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    out = []
    seen = set()

    for row in rows:
        key = tuple(str(row.get(k, "")).lower() for k in keys)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)

    return out


def mist_context(q: str = "", limit: int = 50) -> dict[str, Any]:
    c = mist_cfg()
    q = (q or "").strip()

    try:
        limit = int(limit)
    except Exception:
        limit = 50

    limit = max(1, min(limit, 500))

    context: dict[str, Any] = {
        "q": q,
        "limit": limit,
        "limit_options": [25, 50, 100, 250, 500],
        "base_url_set": bool(c["base_url"]),
        "org_id_set": bool(c["org_id"]),
        "token_set": bool(c["token"]),
        "configured": mist_configured(),
        "base_url": c["base_url"],
        "org_id": c["org_id"],
        "self_ok": False,
        "sites_ok": False,
        "stats_ok": False,
        "switches_ok": False,
        "self_summary": {},
        "sites": [],
        "pod_rows": [],
        "site_count": 0,
        "total_devices": 0,
        "online_devices": 0,
        "offline_devices": 0,
        "total_switches": 0,
        "online_switches": 0,
        "offline_switches": 0,
        "total_clients": 0,
        "client_matches": [],
        "device_matches": [],
        "switch_matches": [],
        "switch_rows": [],
        "pod_cards": [],
        "errors": [],
        "cache_ttl": CACHE_TTL_SECONDS,
    }

    if not context["configured"]:
        return context

    try:
        self_data = mist_get("/api/v1/self")
        context["self_ok"] = True
        context["self_summary"] = {
            "name": safe_get(self_data, "name"),
            "email": safe_get(self_data, "email"),
            "privileges": str(len(self_data.get("privileges", []) if isinstance(self_data, dict) else [])),
        }
    except Exception as exc:
        context["errors"].append({"section": "Self", "error": str(exc)})

    try:
        sites_data = mist_get(f"/api/v1/orgs/{c['org_id']}/sites")
        if not isinstance(sites_data, list):
            raise RuntimeError(f"Expected list from sites endpoint, got {type(sites_data).__name__}")

        context["sites_ok"] = True
        context["site_count"] = len(sites_data)

        site_rows = []
        pod_map: dict[str, dict[str, Any]] = {}
        all_client_matches = []
        all_device_matches = []
        all_switch_matches = []
        all_switch_rows = []
        switch_endpoint_successes = 0

        for site in sites_data:
            if not isinstance(site, dict):
                continue

            site_id = safe_get(site, "id")
            site_name = safe_get(site, "name")
            bucket = pod_bucket(site_name)

            device_stats = []
            client_stats = []
            switch_stats = []

            try:
                device_stats = as_rows(mist_get(f"/api/v1/sites/{site_id}/stats/devices"))
            except Exception as exc:
                context["errors"].append({"section": f"Device Stats - {site_name}", "error": str(exc)})

            try:
                client_stats = as_rows(mist_get(f"/api/v1/sites/{site_id}/stats/clients"))
            except Exception as exc:
                context["errors"].append({"section": f"Client Stats - {site_name}", "error": str(exc)})

            # Dedicated switch metrics endpoint. If unavailable for a tenant/site, fall back to switch-like rows from device stats.
            try:
                # Mist wired switches are exposed as device stats filtered by type=switch in this tenant.
                switch_stats = as_rows(mist_get(f"/api/v1/sites/{site_id}/stats/devices", {"type": "switch"}))
                switch_endpoint_successes += 1
            except Exception as exc:
                context["errors"].append({"section": f"Switch Stats - {site_name}", "error": str(exc)})
                switch_stats = []

            try:
                # Config/inventory view has role, port_config, and sometimes better naming.
                switch_configs = as_rows(mist_get(f"/api/v1/sites/{site_id}/devices", {"type": "switch", "limit": 100}))
            except Exception:
                switch_configs = []

            # Merge switch stats and config rows by MAC. Stats wins for live status; config fills role/name.
            by_mac: dict[str, dict[str, Any]] = {}
            for sw in switch_configs:
                mac = norm_mac(pick(sw, ["mac", "device_mac", "switch_mac", "chassis_mac"]))
                if mac:
                    by_mac[mac] = dict(sw)

            for sw in switch_stats:
                mac = norm_mac(pick(sw, ["mac", "device_mac", "switch_mac", "chassis_mac"]))
                if mac:
                    merged = by_mac.get(mac, {})
                    merged.update(sw)
                    by_mac[mac] = merged

            switch_like_from_devices = [d for d in device_stats if is_switch(d)]
            for sw in switch_like_from_devices:
                mac = norm_mac(pick(sw, ["mac", "device_mac", "switch_mac", "chassis_mac"]))
                if mac and mac not in by_mac:
                    by_mac[mac] = sw

            combined_switches = list(by_mac.values())

            device_count = len(device_stats)
            device_online = sum(1 for d in device_stats if is_online(d))
            device_offline = max(0, device_count - device_online)

            switch_rows = [switch_row(site_name, s, site_id) for s in combined_switches]
            switch_count = len(switch_rows)
            switch_online = sum(1 for s in combined_switches if is_online(s))
            switch_offline = max(0, switch_count - switch_online)

            client_count = len(client_stats)

            context["total_devices"] += device_count
            context["online_devices"] += device_online
            context["offline_devices"] += device_offline
            context["total_switches"] += switch_count
            context["online_switches"] += switch_online
            context["offline_switches"] += switch_offline
            context["total_clients"] += client_count

            all_switch_rows.extend(switch_rows)

            ap_names: dict[str, str] = {}
            for dev in device_stats:
                name = pick(dev, ["hostname", "name", "device_name"])
                for key in [
                    pick(dev, ["id", "_id"]),
                    pick(dev, ["mac", "device_mac"]),
                ]:
                    if key and name:
                        ap_names[key] = name
                        ap_names[norm_mac(key)] = name

            switch_names: dict[str, str] = {}
            for sw in combined_switches:
                name = pick(sw, ["hostname", "name", "device_name", "switch_name"])
                for key in [
                    pick(sw, ["id", "_id"]),
                    pick(sw, ["mac", "device_mac", "switch_mac", "chassis_mac"]),
                ]:
                    if key and name:
                        switch_names[key] = name
                        switch_names[norm_mac(key)] = name

            if q:
                for client in client_stats:
                    enriched = dict(client)
                    enriched["_site"] = site_name
                    if row_matches(enriched, q):
                        all_client_matches.append(client_row(site_name, client, ap_names, switch_names))

                for device in device_stats:
                    enriched = dict(device)
                    enriched["_site"] = site_name
                    if row_matches(enriched, q):
                        all_device_matches.append(device_row(site_name, device))

                for sw in combined_switches:
                    enriched = dict(sw)
                    enriched["_site"] = site_name
                    if row_matches(enriched, q):
                        all_switch_matches.append(switch_row(site_name, sw, site_id))

            site_rows.append({
                "site": site_name,
                "pod": bucket,
                "devices": device_count,
                "online_devices": device_online,
                "offline_devices": device_offline,
                "switches": switch_count,
                "clients": client_count,
                "address": safe_get(site, "address"),
                "timezone": safe_get(site, "timezone"),
                "country": safe_get(site, "country_code") or safe_get(site, "country"),
                "site_id": site_id,
                "short_id": compact_id(site_id),
            })

            pod = pod_map.setdefault(bucket, {
                "pod": bucket,
                "sites": 0,
                "devices": 0,
                "online_devices": 0,
                "offline_devices": 0,
                "switches": 0,
                "clients": 0,
            })

            pod["sites"] += 1
            pod["devices"] += device_count
            pod["online_devices"] += device_online
            pod["offline_devices"] += device_offline
            pod["switches"] += switch_count
            pod["clients"] += client_count

        site_rows.sort(key=lambda r: (r["pod"].lower(), r["site"].lower()))
        context["sites"] = site_rows

        context["pod_rows"] = sorted(
            pod_map.values(),
            key=lambda r: (
                0 if "pod" in r["pod"].lower() else 1,
                r["pod"].lower(),
            ),
        )

        context["switches_ok"] = switch_endpoint_successes > 0 or context["total_switches"] > 0
        context["stats_ok"] = not any(
            row["section"].startswith(("Device Stats", "Client Stats"))
            for row in context["errors"]
        )

        context["switch_rows"] = dedupe(all_switch_rows, ["site", "mac", "serial", "name"])[:250]
        context["pod_cards"] = build_pod_cards(context)
        context["client_matches"] = dedupe(all_client_matches, ["site", "mac", "ip", "username"])[:limit]
        context["device_matches"] = dedupe(all_device_matches, ["site", "mac", "serial", "name"])[:limit]
        context["switch_matches"] = dedupe(all_switch_matches, ["site", "mac", "serial", "name"])[:limit]

    except Exception as exc:
        context["errors"].append({"section": "Sites", "error": str(exc)})

    return context


def get_site(site_id: str) -> dict[str, Any]:
    c = mist_cfg()
    sites = mist_get(f"/api/v1/orgs/{c['org_id']}/sites")
    for site in as_rows(sites):
        if safe_get(site, "id") == site_id:
            return site
    raise RuntimeError(f"Site not found: {site_id}")


def load_switches_for_site(site_id: str, site_name: str) -> list[dict[str, Any]]:
    switch_stats = as_rows(mist_get(f"/api/v1/sites/{site_id}/stats/devices", {"type": "switch"}))

    try:
        switch_configs = as_rows(mist_get(f"/api/v1/sites/{site_id}/devices", {"type": "switch", "limit": 500}))
    except Exception:
        switch_configs = []

    by_mac: dict[str, dict[str, Any]] = {}

    for sw in switch_configs:
        mac = norm_mac(pick(sw, ["mac", "device_mac", "switch_mac", "chassis_mac"]))
        if mac:
            by_mac[mac] = dict(sw)

    for sw in switch_stats:
        mac = norm_mac(pick(sw, ["mac", "device_mac", "switch_mac", "chassis_mac"]))
        if mac:
            merged = by_mac.get(mac, {})
            merged.update(sw)
            by_mac[mac] = merged

    rows = [switch_row(site_name, sw, site_id) for sw in by_mac.values()]
    rows.sort(key=lambda r: (r.get("role", ""), r.get("name", "")))
    return rows


def load_raw_switch_for_site(site_id: str, switch_mac: str) -> dict[str, Any]:
    switch_mac_clean = norm_mac(switch_mac)

    switch_stats = as_rows(mist_get(f"/api/v1/sites/{site_id}/stats/devices", {"type": "switch"}))

    try:
        switch_configs = as_rows(mist_get(f"/api/v1/sites/{site_id}/devices", {"type": "switch", "limit": 500}))
    except Exception:
        switch_configs = []

    by_mac: dict[str, dict[str, Any]] = {}

    for sw in switch_configs:
        mac = norm_mac(pick(sw, ["mac", "device_mac", "switch_mac", "chassis_mac"]))
        if mac:
            by_mac[mac] = dict(sw)

    for sw in switch_stats:
        mac = norm_mac(pick(sw, ["mac", "device_mac", "switch_mac", "chassis_mac"]))
        if mac:
            merged = by_mac.get(mac, {})
            merged.update(sw)
            by_mac[mac] = merged

    if switch_mac_clean in by_mac:
        return by_mac[switch_mac_clean]

    raise RuntimeError(f"Switch not found in site {site_id}: {switch_mac}")


def port_rows_from_switch(sw: dict[str, Any]) -> list[dict[str, Any]]:
    if_stat = sw.get("if_stat") if isinstance(sw.get("if_stat"), dict) else {}
    port_config = sw.get("port_config") if isinstance(sw.get("port_config"), dict) else {}

    rows = []

    for ifname, stat in sorted(if_stat.items()):
        if not isinstance(stat, dict):
            continue

        port_id = str(stat.get("port_id") or ifname).replace(".0", "")
        cfg = {}

        # Match direct port_config keys or comma-separated groups.
        if port_id in port_config and isinstance(port_config[port_id], dict):
            cfg = port_config[port_id]
        else:
            for key, value in port_config.items():
                if not isinstance(value, dict):
                    continue
                parts = [x.strip() for x in str(key).split(",")]
                if port_id in parts:
                    cfg = value
                    break

        ips = stat.get("ips")
        if isinstance(ips, list):
            ips_s = ", ".join(str(x) for x in ips)
        else:
            ips_s = ""

        rows.append({
            "port": port_id,
            "unit": ifname,
            "status": "up" if stat.get("up") is True else "down",
            "usage": cfg.get("usage", ""),
            "ae": cfg.get("ae_idx", ""),
            "critical": "yes" if cfg.get("critical") else "",
            "rx_pkts": stat.get("rx_pkts", ""),
            "tx_pkts": stat.get("tx_pkts", ""),
            "ips": ips_s,
        })

    # Put physical / ae / irb / loopback in a better order.
    def sort_key(row):
        port = row.get("port", "")
        if port.startswith(("ge-", "xe-", "et-", "mge-")):
            bucket = 0
        elif port.startswith("ae"):
            bucket = 1
        elif port.startswith("irb"):
            bucket = 2
        elif port.startswith(("lo", "me", "vme")):
            bucket = 3
        else:
            bucket = 4
        return (bucket, port)

    rows.sort(key=sort_key)
    return rows


def switch_health_rows(sw: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []

    cpu = sw.get("cpu_stat")
    if isinstance(cpu, dict):
        rows.append({
            "item": "CPU",
            "value": f"user {cpu.get('user', '')}% / system {cpu.get('system', '')}% / idle {cpu.get('idle', '')}%",
        })

    mem = sw.get("memory_stat")
    if isinstance(mem, dict):
        rows.append({"item": "Memory", "value": f"{mem.get('usage', '')}% used"})

    mac_table = sw.get("mac_table_stats")
    if isinstance(mac_table, dict):
        rows.append({
            "item": "MAC Table",
            "value": f"{mac_table.get('mac_table_count', '')} / {mac_table.get('max_mac_entries_supported', '')}",
        })

    clients = sw.get("clients_stats")
    if isinstance(clients, dict):
        rows.append({"item": "Clients", "value": json.dumps(clients, default=str)[:250]})

    rows.extend([
        {"item": "Config Status", "value": pick(sw, ["config_status"])},
        {"item": "Version", "value": pick(sw, ["version", "firmware_version"])},
        {"item": "Uptime", "value": pick(sw, ["uptime"])},
        {"item": "Last Seen", "value": fmt_epoch(pick(sw, ["last_seen"]))},
    ])

    return [r for r in rows if r.get("value") not in ("", None)]


def mist_site_detail_context(site_id: str, limit: int = 100) -> dict[str, Any]:
    try:
        limit = int(limit)
    except Exception:
        limit = 100
    limit = max(1, min(limit, 500))

    site = get_site(site_id)
    site_name = safe_get(site, "name")

    device_stats = as_rows(mist_get(f"/api/v1/sites/{site_id}/stats/devices"))
    client_stats = as_rows(mist_get(f"/api/v1/sites/{site_id}/stats/clients"))
    switches = load_switches_for_site(site_id, site_name)

    ap_rows = [device_row(site_name, d) for d in device_stats]
    ap_rows.sort(key=lambda r: r.get("name", ""))

    switch_name_map = {norm_mac(r.get("mac", "")): r.get("name", "") for r in switches}

    ap_names = {}
    for d in device_stats:
        name = pick(d, ["hostname", "name", "device_name"])
        for key in [pick(d, ["id", "_id"]), pick(d, ["mac", "device_mac"])]:
            if key and name:
                ap_names[key] = name
                ap_names[norm_mac(key)] = name

    client_rows = [client_row(site_name, c, ap_names, switch_name_map) for c in client_stats[:limit]]

    return {
        "site": site,
        "site_id": site_id,
        "site_name": site_name,
        "address": safe_get(site, "address"),
        "timezone": safe_get(site, "timezone"),
        "switches": switches,
        "aps": ap_rows,
        "clients": client_rows,
        "switch_count": len(switches),
        "ap_count": len(ap_rows),
        "client_count": len(client_stats),
        "limit": limit,
        "cache_ttl": CACHE_TTL_SECONDS,
    }


def mist_switch_detail_context(site_id: str, mac: str) -> dict[str, Any]:
    site = get_site(site_id)
    site_name = safe_get(site, "name")
    sw = load_raw_switch_for_site(site_id, mac)

    summary = switch_row(site_name, sw, site_id)
    ports = port_rows_from_switch(sw)
    health = switch_health_rows(sw)

    return {
        "site": site,
        "site_id": site_id,
        "site_name": site_name,
        "switch": summary,
        "raw_mac": mac,
        "ports": ports,
        "health": health,
        "port_count": len(ports),
        "up_port_count": sum(1 for p in ports if p.get("status") == "up"),
        "cache_ttl": CACHE_TTL_SECONDS,
    }


def build_pod_cards(context: dict[str, Any]) -> list[dict[str, Any]]:
    sites = context.get("sites", []) or []
    switches = context.get("switch_rows", []) or []
    pod_rows = context.get("pod_rows", []) or []

    site_to_pod = {}
    site_to_id = {}

    for site in sites:
        site_name = str(site.get("site", ""))
        site_to_pod[site_name] = str(site.get("pod", "") or site_name)
        site_to_id[site_name] = str(site.get("site_id", ""))

    cards = []

    for pod in pod_rows:
        pod_name = str(pod.get("pod", ""))

        pod_sites = [
            site for site in sites
            if str(site.get("pod", "")) == pod_name
        ]

        pod_site_names = {str(site.get("site", "")) for site in pod_sites}

        pod_switches = [
            sw for sw in switches
            if str(sw.get("site", "")) in pod_site_names
        ]

        role_counts: dict[str, int] = {}
        for sw in pod_switches:
            role = str(sw.get("role", "") or "unknown")
            role_counts[role] = role_counts.get(role, 0) + 1

        role_summary = ", ".join(
            f"{role} {count}"
            for role, count in sorted(role_counts.items(), key=lambda x: (-x[1], x[0]))
            if role
        )

        preview_switches = sorted(
            pod_switches,
            key=lambda r: (
                0 if str(r.get("role", "")).lower() in ("core", "distribution", "services", "internet-switch") else 1,
                str(r.get("role", "")),
                str(r.get("name", "")),
            ),
        )[:8]

        first_site_id = ""
        if pod_sites:
            first_site_id = str(pod_sites[0].get("site_id", ""))

        cards.append({
            "pod": pod_name,
            "site_id": first_site_id,
            "sites": pod.get("sites", 0),
            "devices": pod.get("devices", pod.get("aps", 0)),
            "online": pod.get("online_devices", pod.get("online_aps", 0)),
            "offline": pod.get("offline_devices", pod.get("offline_aps", 0)),
            "switches": pod.get("switches", 0),
            "clients": pod.get("clients", 0),
            "role_summary": role_summary,
            "site_names": ", ".join(sorted(pod_site_names)),
            "preview_switches": preview_switches,
        })

    cards.sort(key=lambda r: (
        0 if "pod" in str(r.get("pod", "")).lower() else 1,
        str(r.get("pod", "")).lower(),
    ))

    return cards
