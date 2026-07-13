#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from typing import Any

from app.core.db import fetch_all
from app.services.middkips_site_flow_page import (
    SITE_SPECS,
    classify_site_link_role,
    classify_site_role,
    device_name,
    discover_site_devices,
    map_type_for_module,
)


def clean(value: Any) -> str:
    return str(value or "").strip()


def norm(value: Any) -> str:
    return clean(value).lower()


def canonical_link_hash(
    map_type: str,
    device_a: str,
    port_a: str,
    device_b: str,
    port_b: str,
) -> str:
    left, right = sorted([
        (norm(device_a), norm(port_a)),
        (norm(device_b), norm(port_b)),
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
      source VARCHAR(64) NOT NULL DEFAULT 'dynamic-xdp',
      last_status VARCHAR(32) NOT NULL DEFAULT 'unknown',
      last_alerted TIMESTAMP NULL DEFAULT NULL,
      UNIQUE KEY uniq_expected_link_hash (map_type, link_hash),
      KEY idx_expected_local_device (local_device),
      KEY idx_expected_remote_device (remote_device),
      KEY idx_expected_status (last_status),
      KEY idx_expected_role (link_role)
    )
    """)


def is_management_port(port_name: str) -> bool:
    name = norm(port_name)
    return (
        name.startswith("em")
        or name.startswith("fxp")
        or name.startswith("me0")
        or name == "mgmt"
        or "management" in name
    )


def current_status(row: dict[str, Any]) -> str:
    if (
        int(row.get("local_device_status") or 0) != 1
        or int(row.get("remote_device_status") or 0) != 1
    ):
        return "device-down"

    local = norm(row.get("local_oper"))
    remote = norm(row.get("remote_oper"))

    if local == "up" and remote == "up":
        return "up"
    if local == "down" or remote == "down":
        return "down"
    return "unknown"


def upsert_link(
    *,
    map_type: str,
    link_hash: str,
    local_device: str,
    local_port: str,
    remote_device: str,
    remote_port: str,
    link_role: str,
    status: str,
    source: str,
) -> None:
    fetch_all("""
    INSERT INTO middkips_expected_fabric_links
      (
        map_type,
        link_hash,
        local_device,
        local_port,
        remote_device,
        remote_port,
        link_role,
        last_seen,
        last_status,
        source
      )
    VALUES
      (%s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s)
    ON DUPLICATE KEY UPDATE
      local_device = VALUES(local_device),
      local_port = VALUES(local_port),
      remote_device = VALUES(remote_device),
      remote_port = VALUES(remote_port),
      link_role = VALUES(link_role),
      last_seen = NOW(),
      last_status = VALUES(last_status),
      source = VALUES(source)
    """, [
        map_type,
        link_hash,
        local_device,
        local_port,
        remote_device,
        remote_port,
        link_role,
        status,
        source,
    ])


def sync_module(module: str) -> None:
    map_type = map_type_for_module(module)
    devices = discover_site_devices(module)
    by_device_id = {
        int(row["device_id"]): row
        for row in devices
        if row.get("device_id") is not None
    }
    current_hashes: set[str] = set()

    if by_device_id:
        ids = ",".join(str(device_id) for device_id in by_device_id)
        rows = fetch_all(f"""
        SELECT
          ld.device_id AS local_device_id,
          ld.status AS local_device_status,
          COALESCE(lp.ifName, lp.ifDescr, '') AS local_port,
          lp.ifOperStatus AS local_oper,
          rd.device_id AS remote_device_id,
          rd.status AS remote_device_status,
          COALESCE(rp.ifName, rp.ifDescr, '') AS remote_port,
          rp.ifOperStatus AS remote_oper
        FROM links l
        JOIN ports lp ON lp.port_id = l.local_port_id
        JOIN devices ld ON ld.device_id = lp.device_id
        JOIN ports rp ON rp.port_id = l.remote_port_id
        JOIN devices rd ON rd.device_id = rp.device_id
        WHERE ld.device_id IN ({ids})
          AND rd.device_id IN ({ids})
        """)

        for row in rows:
            local_id = int(row.get("local_device_id"))
            remote_id = int(row.get("remote_device_id"))
            if local_id == remote_id:
                continue

            local_row = by_device_id.get(local_id)
            remote_row = by_device_id.get(remote_id)
            if not local_row or not remote_row:
                continue

            local_device = clean(local_row.get("_site_name")) or device_name(local_row)
            remote_device = clean(remote_row.get("_site_name")) or device_name(remote_row)
            local_port = clean(row.get("local_port"))
            remote_port = clean(row.get("remote_port"))

            if is_management_port(local_port) or is_management_port(remote_port):
                continue

            link_hash = canonical_link_hash(
                map_type,
                local_device,
                local_port,
                remote_device,
                remote_port,
            )
            if link_hash in current_hashes:
                continue
            current_hashes.add(link_hash)

            local_role = clean(local_row.get("_site_role")) or classify_site_role(
                module, local_device
            )
            remote_role = clean(remote_row.get("_site_role")) or classify_site_role(
                module, remote_device
            )

            upsert_link(
                map_type=map_type,
                link_hash=link_hash,
                local_device=local_device,
                local_port=local_port,
                remote_device=remote_device,
                remote_port=remote_port,
                link_role=classify_site_link_role(module, local_role, remote_role),
                status=current_status(row),
                source=f"site-xdp:{module}",
            )

    expected_rows = fetch_all("""
    SELECT link_hash, local_device, remote_device
    FROM middkips_expected_fabric_links
    WHERE map_type = %s
      AND source = %s
    """, [map_type, f"site-xdp:{module}"])

    by_name = {
        norm(row.get("_site_name") or device_name(row)): row
        for row in devices
    }
    missing = 0

    for row in expected_rows:
        link_hash = clean(row.get("link_hash"))
        if link_hash in current_hashes:
            continue

        local = by_name.get(norm(row.get("local_device")))
        remote = by_name.get(norm(row.get("remote_device")))
        endpoints_up = (
            local is not None
            and remote is not None
            and int(local.get("status") or 0) == 1
            and int(remote.get("status") or 0) == 1
        )
        status = "missing" if endpoints_up else "device-down"

        fetch_all("""
        UPDATE middkips_expected_fabric_links
        SET last_status = %s
        WHERE map_type = %s
          AND link_hash = %s
        """, [status, map_type, link_hash])

        if status == "missing":
            missing += 1

    status_rows = fetch_all("""
    SELECT last_status, COUNT(*) AS count
    FROM middkips_expected_fabric_links
    WHERE map_type = %s
    GROUP BY last_status
    """, [map_type])

    counts = Counter({
        str(row.get("last_status") or "unknown"): int(row.get("count") or 0)
        for row in status_rows
    })

    print(
        module,
        f"devices={len(devices)}",
        f"current_links={len(current_hashes)}",
        f"missing={missing}",
        f"status_counts={dict(counts)}",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("module", choices=[*SITE_SPECS.keys(), "all"])
    args = parser.parse_args()

    ensure_table()
    modules = list(SITE_SPECS) if args.module == "all" else [args.module]
    for module in modules:
        sync_module(module)


if __name__ == "__main__":
    main()
