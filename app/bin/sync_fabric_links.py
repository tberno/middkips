#!/usr/bin/env python3

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from app.core.db import fetch_all


MAP_TYPE = "mist-pods"


def clean(v: Any) -> str:
    return str(v or "").strip()


def norm(v: Any) -> str:
    return clean(v).lower()


def role(name: str) -> str:
    n = norm(name)

    if "fabric-core" in n:
        return "core"
    if n in ("dfl-core.middlebury.edu", "vtr-core.middlebury.edu"):
        return "core"
    if n.startswith("dist-") or "dist-" in n:
        return "distribution"
    if n.startswith("svcs-") or n.startswith("services-"):
        return "service"
    if any(x in n for x in ("edgefw", "vpn-fw", "enclave-fw", "miis-fw", "dc-fw")):
        return "edge"
    if n.startswith("700") or "racktop" in n or "-dc-" in n or "dc-" in n:
        return "datacenter"
    return "access"


def wanted_pair(a: str, b: str) -> bool:
    pair = {role(a), role(b)}
    return (
        pair == {"access", "distribution"}
        or pair == {"distribution", "core"}
        or pair == {"service", "core"}
    )


def endpoint_sort_key(device: str, port: str) -> tuple[str, str]:
    return (norm(device), norm(port))


def link_hash(map_type: str, local_device: str, local_port: str, remote_device: str, remote_port: str) -> str:
    left, right = sorted([
        endpoint_sort_key(local_device, local_port),
        endpoint_sort_key(remote_device, remote_port),
    ])
    raw = "|".join([map_type, left[0], left[1], right[0], right[1]])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_table() -> None:
    fetch_all("""
    CREATE TABLE IF NOT EXISTS middkips_expected_fabric_links (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      map_type VARCHAR(64) NOT NULL DEFAULT 'mist-pods',
      link_hash CHAR(64) NOT NULL,
      local_device VARCHAR(255) NOT NULL,
      local_port VARCHAR(128) DEFAULT '',
      remote_device VARCHAR(255) NOT NULL,
      remote_port VARCHAR(128) DEFAULT '',
      link_role VARCHAR(64) NOT NULL DEFAULT 'fabric-uplink',
      first_seen TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
      last_seen TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
      last_status VARCHAR(32) NOT NULL DEFAULT 'unknown',
      last_alerted TIMESTAMP NULL DEFAULT NULL,
      source VARCHAR(64) NOT NULL DEFAULT 'dynamic-xdp',
      UNIQUE KEY uniq_expected_link_hash (map_type, link_hash),
      KEY idx_expected_local_device (local_device),
      KEY idx_expected_remote_device (remote_device),
      KEY idx_expected_status (last_status),
      KEY idx_expected_role (link_role)
    )
    """)

    # For tables created earlier before status columns existed. Databases, where migrations go to shed tears.
    for sql in (
        "ALTER TABLE middkips_expected_fabric_links ADD COLUMN last_status VARCHAR(32) NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE middkips_expected_fabric_links ADD COLUMN last_alerted TIMESTAMP NULL DEFAULT NULL",
    ):
        try:
            fetch_all(sql)
        except Exception:
            pass


def current_fabric_links() -> list[dict[str, Any]]:
    rows = fetch_all("""
    SELECT
      COALESCE(ld.display, ld.sysName, ld.hostname) AS local_device,
      COALESCE(lp.ifName, lp.ifDescr) AS local_port,
      lp.ifOperStatus AS local_oper,
      ld.status AS local_device_status,
      COALESCE(rd.display, rd.sysName, rd.hostname) AS remote_device,
      COALESCE(rp.ifName, rp.ifDescr) AS remote_port,
      rp.ifOperStatus AS remote_oper,
      rd.status AS remote_device_status
    FROM links l
    LEFT JOIN ports lp ON lp.port_id = l.local_port_id
    LEFT JOIN devices ld ON ld.device_id = lp.device_id
    LEFT JOIN ports rp ON rp.port_id = l.remote_port_id
    LEFT JOIN devices rd ON rd.device_id = rp.device_id
    WHERE
      COALESCE(ld.display, ld.sysName, ld.hostname) IS NOT NULL
      AND COALESCE(rd.display, rd.sysName, rd.hostname) IS NOT NULL
    LIMIT 20000
    """)

    out = []
    seen = set()

    for r in rows:
        local_device = clean(r.get("local_device"))
        local_port = clean(r.get("local_port"))
        remote_device = clean(r.get("remote_device"))
        remote_port = clean(r.get("remote_port"))

        if not local_device or not remote_device:
            continue

        if not wanted_pair(local_device, remote_device):
            continue

        h = link_hash(MAP_TYPE, local_device, local_port, remote_device, remote_port)
        if h in seen:
            continue
        seen.add(h)

        local_oper = norm(r.get("local_oper"))
        remote_oper = norm(r.get("remote_oper"))

        if local_oper == "up" and remote_oper == "up":
            status = "up"
        elif local_oper == "down" or remote_oper == "down":
            status = "down"
        else:
            status = "unknown"

        out.append({
            "hash": h,
            "local_device": local_device,
            "local_port": local_port,
            "remote_device": remote_device,
            "remote_port": remote_port,
            "status": status,
        })

    return out


def sync() -> None:
    ensure_table()

    links = current_fabric_links()
    current_hashes = {l["hash"] for l in links}

    for l in links:
        fetch_all("""
        INSERT INTO middkips_expected_fabric_links
          (map_type, link_hash, local_device, local_port, remote_device, remote_port, link_role, last_seen, last_status, source)
        VALUES
          (%s, %s, %s, %s, %s, %s, 'fabric-uplink', NOW(), %s, 'dynamic-xdp')
        ON DUPLICATE KEY UPDATE
          local_device = VALUES(local_device),
          local_port = VALUES(local_port),
          remote_device = VALUES(remote_device),
          remote_port = VALUES(remote_port),
          last_seen = NOW(),
          last_status = VALUES(last_status),
          source = 'dynamic-xdp'
        """, [
            MAP_TYPE,
            l["hash"],
            l["local_device"],
            l["local_port"],
            l["remote_device"],
            l["remote_port"],
            l["status"],
        ])

    # Anything expected but not currently present in xDP becomes missing.
    # Later map/alert logic can suppress if one of the devices is actually down.
    expected = fetch_all("""
    SELECT link_hash
    FROM middkips_expected_fabric_links
    WHERE map_type = %s
    """, [MAP_TYPE])

    missing = 0
    for row in expected:
        h = row.get("link_hash")
        if h not in current_hashes:
            fetch_all("""
            UPDATE middkips_expected_fabric_links
            SET last_status = 'missing'
            WHERE map_type = %s AND link_hash = %s
            """, [MAP_TYPE, h])
            missing += 1

    print(f"{datetime.now().isoformat(timespec='seconds')} learned_or_updated={len(links)} missing={missing}")


if __name__ == "__main__":
    sync()
