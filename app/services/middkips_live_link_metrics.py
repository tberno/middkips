from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from app.core.db import fetch_all


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return _clean(value).lower()


def _actual_port_name(value: Any) -> str:
    """Extract an interface name from labels such as 'xe-0/1/7 | local ...'."""
    text = _clean(value)
    if not text:
        return ""
    return text.split("|", 1)[0].strip()


def _port_keys(value: Any) -> set[str]:
    text = _norm(value)
    if not text:
        return set()

    keys = {text}

    if text.endswith(".0"):
        keys.add(text[:-2])

    # A few platforms expose interface labels with incidental whitespace.
    keys.add(" ".join(text.split()))
    return {key for key in keys if key}


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _port_bps(port: dict[str, Any] | None, direction: str) -> int:
    if not port:
        return 0

    key = "ifOutOctets_rate" if direction == "out" else "ifInOctets_rate"
    # LibreNMS stores octet rates as bytes/second. Convert to bits/second.
    return max(0, int(_number(port.get(key)) * 8))


def _error_rate(port: dict[str, Any] | None, direction: str) -> int:
    if not port:
        return 0

    key = "ifOutErrors_rate" if direction == "out" else "ifInErrors_rate"
    return max(0, _integer(port.get(key)))


def _poll_age(port: dict[str, Any] | None, now: int) -> int | None:
    if not port:
        return None

    poll_time = _integer(port.get("poll_time")) or _integer(port.get("poll_prev"))
    if poll_time <= 0:
        return None
    return max(0, now - poll_time)


def _resolve_port(
    index: dict[int, dict[str, dict[str, Any]]],
    device_id: int | None,
    port_reference: Any,
) -> dict[str, Any] | None:
    if not device_id:
        return None

    device_ports = index.get(int(device_id), {})
    wanted = _actual_port_name(port_reference)

    for key in _port_keys(wanted):
        if key in device_ports:
            return device_ports[key]

    # Conservative fallback. Only accept a single prefix match.
    wanted_norm = _norm(wanted)
    if not wanted_norm:
        return None

    candidates: list[dict[str, Any]] = []
    seen: set[int] = set()

    for key, port in device_ports.items():
        if key.startswith(wanted_norm) or wanted_norm.startswith(key):
            port_id = _integer(port.get("port_id"))
            if port_id not in seen:
                candidates.append(port)
                seen.add(port_id)

    return candidates[0] if len(candidates) == 1 else None


def _load_port_index(device_ids: set[int]) -> dict[int, dict[str, dict[str, Any]]]:
    if not device_ids:
        return {}

    ids = ",".join(str(int(device_id)) for device_id in sorted(device_ids))

    rows = fetch_all(f"""
    SELECT
      p.port_id,
      p.device_id,
      p.ifName,
      p.ifDescr,
      p.ifAlias,
      p.ifSpeed,
      p.ifOperStatus,
      p.ifAdminStatus,
      p.ifLastChange,
      p.ifInOctets_rate,
      p.ifOutOctets_rate,
      p.ifInErrors_rate,
      p.ifOutErrors_rate,
      p.poll_time,
      p.poll_prev,
      p.poll_period,
      d.status AS device_status
    FROM ports p
    JOIN devices d
      ON d.device_id = p.device_id
    WHERE p.device_id IN ({ids})
    """)

    index: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)

    for row in rows:
        device_id = _integer(row.get("device_id"))
        for candidate in (row.get("ifName"), row.get("ifDescr")):
            for key in _port_keys(candidate):
                index[device_id].setdefault(key, row)

    return dict(index)


def _effective_status(
    expected_status: str,
    source_node: dict[str, Any] | None,
    target_node: dict[str, Any] | None,
    source_port: dict[str, Any] | None,
    target_port: dict[str, Any] | None,
) -> str:
    source_device_up = (
        source_node is None
        or source_node.get("device_id") is None
        or _integer(source_port.get("device_status") if source_port else source_node.get("status")) == 1
    )
    target_device_up = (
        target_node is None
        or target_node.get("device_id") is None
        or _integer(target_port.get("device_status") if target_port else target_node.get("status")) == 1
    )

    if not source_device_up or not target_device_up:
        return "device-down"

    # Missing means the expected xDP adjacency disappeared. Port state alone
    # must not paint that link green again.
    if expected_status == "missing":
        return "missing"

    ports = [port for port in (source_port, target_port) if port]
    oper_states = [_norm(port.get("ifOperStatus")) for port in ports]

    if any(state == "down" for state in oper_states):
        return "down"

    if ports and all(state == "up" for state in oper_states):
        return "up"

    if expected_status in {"up", "down", "unknown", "device-down"}:
        return expected_status

    return "unknown"


def enrich_links_with_live_metrics(
    links: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mutate and return flow links with current LibreNMS port telemetry."""
    nodes_by_id = {str(node.get("id")): node for node in nodes}
    device_ids = {
        int(node["device_id"])
        for node in nodes
        if node.get("device_id") is not None
    }

    port_index = _load_port_index(device_ids)
    now = int(time.time())

    for link in links:
        source_node = nodes_by_id.get(str(link.get("source")))
        target_node = nodes_by_id.get(str(link.get("target")))

        source_device_id = (
            int(source_node["device_id"])
            if source_node and source_node.get("device_id") is not None
            else None
        )
        target_device_id = (
            int(target_node["device_id"])
            if target_node and target_node.get("device_id") is not None
            else None
        )

        source_port = _resolve_port(
            port_index,
            source_device_id,
            link.get("source_port"),
        )
        target_port = _resolve_port(
            port_index,
            target_device_id,
            link.get("target_port"),
        )

        expected_status = _norm(link.get("status")) or "unknown"
        status = _effective_status(
            expected_status,
            source_node,
            target_node,
            source_port,
            target_port,
        )

        # Direction A -> B uses A egress, falling back to B ingress. Reverse is
        # B egress, falling back to A ingress.
        forward_bps = _port_bps(source_port, "out")
        if forward_bps <= 0:
            forward_bps = _port_bps(target_port, "in")

        reverse_bps = _port_bps(target_port, "out")
        if reverse_bps <= 0:
            reverse_bps = _port_bps(source_port, "in")

        speeds = [
            _integer(port.get("ifSpeed"))
            for port in (source_port, target_port)
            if port and _integer(port.get("ifSpeed")) > 0
        ]
        capacity_bps = min(speeds) if speeds else 0

        forward_util = (
            min(999.0, (forward_bps / capacity_bps) * 100.0)
            if capacity_bps > 0
            else 0.0
        )
        reverse_util = (
            min(999.0, (reverse_bps / capacity_bps) * 100.0)
            if capacity_bps > 0
            else 0.0
        )

        forward_errors = _error_rate(source_port, "out") + _error_rate(target_port, "in")
        reverse_errors = _error_rate(target_port, "out") + _error_rate(source_port, "in")

        ages = [
            age
            for age in (
                _poll_age(source_port, now),
                _poll_age(target_port, now),
            )
            if age is not None
        ]
        poll_age_seconds = max(ages) if ages else None

        poll_periods = [
            _integer(port.get("poll_period"))
            for port in (source_port, target_port)
            if port and _integer(port.get("poll_period")) > 0
        ]
        stale_threshold = max(180, 2 * max(poll_periods or [300]))
        telemetry_stale = (
            poll_age_seconds is None
            or poll_age_seconds > stale_threshold
        )

        link.update({
            "status": status,
            "status_source": "librenms-ports",
            "source_port_oper_status": _norm(source_port.get("ifOperStatus")) if source_port else "unknown",
            "target_port_oper_status": _norm(target_port.get("ifOperStatus")) if target_port else "unknown",
            "source_port_admin_status": _norm(source_port.get("ifAdminStatus")) if source_port else "unknown",
            "target_port_admin_status": _norm(target_port.get("ifAdminStatus")) if target_port else "unknown",
            "source_to_target_bps": int(forward_bps),
            "target_to_source_bps": int(reverse_bps),
            "capacity_bps": int(capacity_bps),
            "source_utilization_pct": round(forward_util, 3),
            "target_utilization_pct": round(reverse_util, 3),
            "source_to_target_errors_rate": int(forward_errors),
            "target_to_source_errors_rate": int(reverse_errors),
            "poll_age_seconds": poll_age_seconds,
            "telemetry_stale": bool(telemetry_stale),
            "telemetry_stale_threshold_seconds": int(stale_threshold),
            "source_port_id": _integer(source_port.get("port_id")) if source_port else None,
            "target_port_id": _integer(target_port.get("port_id")) if target_port else None,
        })

    return links


def librenms_bgp_peer_health(router_name, peer_ip):
    """
    Return BGP state for a provider peer from LibreNMS bgpPeers.
    This lets edge/provider links show degraded when the interface is up
    but BGP is not established.
    """
    router_name = str(router_name or "").strip()
    peer_ip = str(peer_ip or "").strip()

    if not router_name or not peer_ip:
        return {
            "bgp_state": "unknown",
            "bgp_admin_status": "unknown",
            "bgp_healthy": False,
            "bgp_reason": "missing router or peer ip",
        }

    short_router = router_name.split(".")[0]

    try:
        rows = fetch_all(
            """
            SELECT
              d.hostname,
              d.sysName,
              d.display,
              b.astext,
              b.bgpPeerIdentifier,
              b.bgpPeerRemoteAs,
              b.bgpPeerState,
              b.bgpPeerAdminStatus,
              b.bgpPeerLastErrorCode,
              b.bgpPeerLastErrorSubCode,
              b.bgpPeerLastErrorText,
              b.bgpLocalAddr,
              b.bgpPeerRemoteAddr,
              b.bgpPeerInUpdates,
              b.bgpPeerOutUpdates,
              b.bgpPeerInTotalMessages,
              b.bgpPeerOutTotalMessages,
              b.bgpPeerFsmEstablishedTime,
              b.bgpPeerInUpdateElapsedTime
            FROM bgpPeers b
            JOIN devices d ON d.device_id = b.device_id
            WHERE
              (
                d.hostname = %s
                OR d.sysName = %s
                OR d.display = %s
                OR d.hostname LIKE %s
                OR d.sysName LIKE %s
                OR d.display LIKE %s
              )
              AND (
                b.bgpPeerIdentifier = %s
                OR b.bgpLocalAddr = %s
                OR b.bgpPeerRemoteAddr = %s
              )
            ORDER BY
              CASE WHEN b.bgpPeerIdentifier = %s THEN 0 ELSE 1 END,
              d.status DESC,
              d.device_id
            LIMIT 1
            """,
            [
                router_name,
                router_name,
                router_name,
                "%" + short_router + "%",
                "%" + short_router + "%",
                "%" + short_router + "%",
                peer_ip,
                peer_ip,
                peer_ip,
                peer_ip,
            ],
        )
    except Exception as exc:
        return {
            "bgp_state": "unknown",
            "bgp_admin_status": "unknown",
            "bgp_healthy": False,
            "bgp_reason": "bgp query failed: {}".format(exc),
        }

    if not rows:
        return {
            "bgp_state": "unknown",
            "bgp_admin_status": "unknown",
            "bgp_healthy": False,
            "bgp_reason": "peer not found in LibreNMS bgpPeers",
        }

    row = rows[0]
    state = str(row.get("bgpPeerState") or "unknown").lower()
    admin = str(row.get("bgpPeerAdminStatus") or "unknown").lower()
    healthy = state == "established"

    return {
        "bgp_state": state,
        "bgp_admin_status": admin,
        "bgp_healthy": healthy,
        "bgp_reason": "established" if healthy else "bgp peer is not established",
        "bgp_peer_ip": row.get("bgpPeerIdentifier"),
        "bgp_local_ip": row.get("bgpLocalAddr"),
        "bgp_remote_as": row.get("bgpPeerRemoteAs"),
        "bgp_remote_as_text": row.get("astext"),
        "bgp_established_seconds": row.get("bgpPeerFsmEstablishedTime"),
        "bgp_last_error_code": row.get("bgpPeerLastErrorCode"),
        "bgp_last_error_subcode": row.get("bgpPeerLastErrorSubCode"),
        "bgp_last_error_text": row.get("bgpPeerLastErrorText"),
        "bgp_in_updates": row.get("bgpPeerInUpdates"),
        "bgp_out_updates": row.get("bgpPeerOutUpdates"),
        "bgp_in_update_elapsed_seconds": row.get("bgpPeerInUpdateElapsedTime"),
    }


def apply_provider_health(link):
    """
    Compute provider-facing health using physical/interface state plus BGP state.
    """
    status = str(link.get("status") or "unknown").lower()
    bgp_state = str(link.get("bgp_state") or "unknown").lower()

    if status in ("down", "missing", "device_down"):
        link["provider_health"] = "down"
        link["provider_reason"] = "physical/interface status is {}".format(status)
        return link

    if link.get("telemetry_stale"):
        link["provider_health"] = "stale"
        link["provider_reason"] = "LibreNMS telemetry is stale"
        return link

    if bgp_state not in ("established", "unknown"):
        link["provider_health"] = "degraded"
        link["provider_reason"] = "BGP peer is {}".format(bgp_state)
        return link

    if bgp_state == "unknown" and link.get("peer_ip"):
        link["provider_health"] = "unknown"
        link["provider_reason"] = link.get("bgp_reason") or "BGP peer state unknown"
        return link

    link["provider_health"] = "up"
    link["provider_reason"] = "physical and BGP healthy"
    return link
