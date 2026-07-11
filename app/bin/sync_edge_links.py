#!/usr/bin/env python3

from __future__ import annotations

import os
from collections import Counter
from typing import Any

from app.core.db import fetch_all
from app.services.middkips_edge_flow_page import (
    EDGE_NODE_SPECS,
    EDGE_PROVIDER_LINKS,
    canonical_link_hash,
    classify_link_role,
    resolve_edge_devices,
)


MAP_TYPE = "edge"


def clean(value: Any) -> str:
    return str(value or "").strip()


def norm(value: Any) -> str:
    return clean(value).lower()


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
    p = norm(port_name)

    return (
        p.startswith("em")
        or p.startswith("fxp")
        or p.startswith("me0")
        or "management" in p
        or p == "mgmt"
    )


def link_status(
    device_a_status: Any,
    port_a_status: Any,
    device_b_status: Any,
    port_b_status: Any,
) -> str:
    if int(device_a_status or 0) != 1 or int(device_b_status or 0) != 1:
        return "device-down"

    a = norm(port_a_status)
    b = norm(port_b_status)

    if a == "up" and b == "up":
        return "up"

    if a == "down" or b == "down":
        return "down"

    return "unknown"


def upsert_link(
    *,
    link_hash: str,
    local_device: str,
    local_port: str,
    remote_device: str,
    remote_port: str,
    link_role: str,
    status: str,
    source: str,
    present: bool,
) -> None:
    if present:
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
            MAP_TYPE,
            link_hash,
            local_device,
            local_port,
            remote_device,
            remote_port,
            link_role,
            status,
            source,
        ])
    else:
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
            last_status,
            source
          )
        VALUES
          (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          local_device = VALUES(local_device),
          local_port = VALUES(local_port),
          remote_device = VALUES(remote_device),
          remote_port = VALUES(remote_port),
          link_role = VALUES(link_role),
          last_status = VALUES(last_status),
          source = VALUES(source)
        """, [
            MAP_TYPE,
            link_hash,
            local_device,
            local_port,
            remote_device,
            remote_port,
            link_role,
            status,
            source,
        ])


def sync_internal_links(
    resolved: dict[str, dict[str, Any]],
) -> set[str]:
    real_device_ids = {
        int(row["device_id"]): key
        for key, row in resolved.items()
        if row.get("device_id") is not None
    }

    if not real_device_ids:
        return set()

    ids = ",".join(str(device_id) for device_id in real_device_ids)

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
    JOIN ports lp
      ON lp.port_id = l.local_port_id
    JOIN devices ld
      ON ld.device_id = lp.device_id
    JOIN ports rp
      ON rp.port_id = l.remote_port_id
    JOIN devices rd
      ON rd.device_id = rp.device_id
    WHERE ld.device_id IN ({ids})
      AND rd.device_id IN ({ids})
    """)

    current_hashes: set[str] = set()

    for row in rows:
        local_key = real_device_ids.get(int(row.get("local_device_id")))
        remote_key = real_device_ids.get(int(row.get("remote_device_id")))

        if not local_key or not remote_key or local_key == remote_key:
            continue

        local_port = clean(row.get("local_port"))
        remote_port = clean(row.get("remote_port"))

        if is_management_port(local_port) or is_management_port(remote_port):
            continue

        role = classify_link_role(local_key, remote_key)
        if not role:
            continue

        link_hash = canonical_link_hash(
            MAP_TYPE,
            local_key,
            local_port,
            remote_key,
            remote_port,
        )

        if link_hash in current_hashes:
            continue

        current_hashes.add(link_hash)

        status = link_status(
            row.get("local_device_status"),
            row.get("local_oper"),
            row.get("remote_device_status"),
            row.get("remote_oper"),
        )

        upsert_link(
            link_hash=link_hash,
            local_device=local_key,
            local_port=local_port,
            remote_device=remote_key,
            remote_port=remote_port,
            link_role=role,
            status=status,
            source="edge-xdp",
            present=True,
        )

    return current_hashes


def find_router_port(
    device_id: int,
    port_name: str,
) -> dict[str, Any] | None:
    rows = fetch_all("""
    SELECT
      port_id,
      ifName,
      ifDescr,
      ifAlias,
      ifOperStatus
    FROM ports
    WHERE device_id = %s
      AND (
        ifName = %s
        OR ifDescr = %s
        OR ifName LIKE %s
      )
    ORDER BY
      CASE WHEN ifName = %s THEN 0 ELSE 1 END,
      port_id
    LIMIT 10
    """, [
        device_id,
        port_name,
        port_name,
        port_name + "%",
        port_name,
    ])

    return rows[0] if rows else None


def sync_provider_links(
    resolved: dict[str, dict[str, Any]],
) -> set[str]:
    current_hashes: set[str] = set()

    for config in EDGE_PROVIDER_LINKS:
        provider_key = config["provider"]
        router_key = config["router"]
        router_port_name = config["router_port"]
        peer_ip = config["peer_ip"]
        local_ip = config["local_ip"]
        speed = config["speed"]

        router = resolved.get(router_key)

        remote_port = (
            f"{router_port_name} | local {local_ip} | {speed}"
        )
        provider_port = f"peer {peer_ip}"

        link_hash = canonical_link_hash(
            MAP_TYPE,
            provider_key,
            provider_port,
            router_key,
            remote_port,
        )

        current_hashes.add(link_hash)

        if router is None:
            status = "missing"
            present = False
        elif int(router.get("status") or 0) != 1:
            status = "device-down"
            present = False
        else:
            port = find_router_port(
                int(router["device_id"]),
                router_port_name,
            )

            if port is None:
                status = "missing"
                present = False
            else:
                oper = norm(port.get("ifOperStatus"))

                if oper == "up":
                    status = "up"
                elif oper == "down":
                    status = "down"
                else:
                    status = "unknown"

                present = True

        upsert_link(
            link_hash=link_hash,
            local_device=provider_key,
            local_port=provider_port,
            remote_device=router_key,
            remote_port=remote_port,
            link_role="provider",
            status=status,
            source="edge-provider-config",
            present=present,
        )

    return current_hashes


def mark_missing_internal_links(
    resolved: dict[str, dict[str, Any]],
    current_hashes: set[str],
) -> int:
    rows = fetch_all("""
    SELECT
      link_hash,
      local_device,
      remote_device
    FROM middkips_expected_fabric_links
    WHERE map_type = %s
      AND source = 'edge-xdp'
    """, [MAP_TYPE])

    missing = 0

    for row in rows:
        link_hash = clean(row.get("link_hash"))

        if link_hash in current_hashes:
            continue

        local_key = clean(row.get("local_device"))
        remote_key = clean(row.get("remote_device"))

        local_device = resolved.get(local_key)
        remote_device = resolved.get(remote_key)

        endpoints_up = (
            local_device is not None
            and remote_device is not None
            and int(local_device.get("status") or 0) == 1
            and int(remote_device.get("status") or 0) == 1
        )

        status = "missing" if endpoints_up else "device-down"

        fetch_all("""
        UPDATE middkips_expected_fabric_links
        SET last_status = %s
        WHERE map_type = %s
          AND link_hash = %s
        """, [status, MAP_TYPE, link_hash])

        if status == "missing":
            missing += 1

    return missing


def main() -> None:
    ensure_table()

    if os.environ.get("RESET_EDGE_LINKS") == "1":
        fetch_all("""
        DELETE FROM middkips_expected_fabric_links
        WHERE map_type = 'edge'
        """)
        print("reset existing edge expected links")

    resolved = resolve_edge_devices()

    internal_hashes = sync_internal_links(resolved)
    provider_hashes = sync_provider_links(resolved)

    missing = mark_missing_internal_links(
        resolved,
        internal_hashes,
    )

    status_rows = fetch_all("""
    SELECT last_status, COUNT(*) AS count
    FROM middkips_expected_fabric_links
    WHERE map_type = 'edge'
    GROUP BY last_status
    """)

    counts = Counter()

    for row in status_rows:
        counts[str(row.get("last_status") or "unknown")] = int(
            row.get("count") or 0
        )

    print(
        "edge sync",
        f"resolved_devices={len(resolved)}",
        f"internal_current={len(internal_hashes)}",
        f"provider_paths={len(provider_hashes)}",
        f"missing={missing}",
        f"status_counts={dict(counts)}",
    )

    unresolved = [
        key
        for key, spec in EDGE_NODE_SPECS.items()
        if not spec.get("virtual") and key not in resolved
    ]

    if unresolved:
        print("unresolved edge devices:", ", ".join(unresolved))


if __name__ == "__main__":
    main()
