from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Any

from app.core.db import fetch_all


def h(v: Any) -> str:
    return escape(str(v or ""))


def _pod_name(*names: Any) -> str:
    text = " ".join(str(n or "").lower() for n in names)

    if "eastpod" in text or "east-pod" in text:
        return "East Pod"
    if "northpod" in text or "north-pod" in text:
        return "North Pod"
    if "southpod" in text or "south-pod" in text:
        return "South Pod"
    if "westpod" in text or "west-pod" in text:
        return "West Pod"
    if "testpod" in text or "test-pod" in text:
        return "Test Pod"
    if "fabric-core" in text or "dfl-core" in text or "vtr-core" in text:
        return "Core / Services"
    if "svcs-" in text or "services-" in text:
        return "Core / Services"
    if "dfl" in text:
        return "DFL Local"
    if "voter" in text or "vtr" in text:
        return "Voter Local"

    return "Other Fabric Links"


def fabric_link_rows(problems_only: bool = False, limit: int = 1000):
    where = ["e.map_type = 'mist-pods'"]
    params = []

    if problems_only:
        where.append("e.last_status IN ('down', 'missing')")
        where.append("COALESCE(ld.status, 1) = 1")
        where.append("COALESCE(rd.status, 1) = 1")

    sql = f"""
    SELECT
      e.id,
      e.local_device,
      e.local_port,
      e.remote_device,
      e.remote_port,
      e.link_role,
      e.last_status,
      e.last_seen,
      e.first_seen,
      e.source,

      ld.device_id AS local_device_id,
      ld.status AS local_device_status,
      rd.device_id AS remote_device_id,
      rd.status AS remote_device_status
    FROM middkips_expected_fabric_links e
    LEFT JOIN devices ld
      ON LOWER(e.local_device) = LOWER(ld.display)
      OR LOWER(e.local_device) = LOWER(ld.sysName)
      OR LOWER(e.local_device) = LOWER(ld.hostname)
    LEFT JOIN devices rd
      ON LOWER(e.remote_device) = LOWER(rd.display)
      OR LOWER(e.remote_device) = LOWER(rd.sysName)
      OR LOWER(e.remote_device) = LOWER(rd.hostname)
    WHERE {" AND ".join(where)}
    ORDER BY
      FIELD(e.last_status, 'missing', 'down', 'unknown', 'up'),
      e.local_device,
      e.local_port,
      e.remote_device,
      e.remote_port
    LIMIT %s
    """

    params.append(int(limit))
    return fetch_all(sql, params)


def fabric_link_summary():
    rows = fetch_all("""
    SELECT last_status, COUNT(*) AS count
    FROM middkips_expected_fabric_links
    WHERE map_type = 'mist-pods'
    GROUP BY last_status
    ORDER BY last_status
    """)

    out = {"up": 0, "down": 0, "missing": 0, "unknown": 0}
    for r in rows:
        out[str(r.get("last_status") or "unknown")] = int(r.get("count") or 0)

    problems = fabric_link_rows(problems_only=True, limit=1000)
    out["problems"] = len(problems)
    out["total"] = sum(out.get(k, 0) for k in ("up", "down", "missing", "unknown"))
    return out


def fabric_link_problems():
    rows = fabric_link_rows(problems_only=True, limit=1000)
    out = []

    for r in rows:
        status = str(r.get("last_status") or "unknown")
        severity = "critical" if status == "missing" else "warning"

        out.append({
            "severity": severity,
            "status": status,
            "local_device": r.get("local_device"),
            "local_port": r.get("local_port"),
            "remote_device": r.get("remote_device"),
            "remote_port": r.get("remote_port"),
            "last_seen": str(r.get("last_seen") or ""),
            "local_device_status": r.get("local_device_status"),
            "remote_device_status": r.get("remote_device_status"),
        })

    return out


def _stat_card(label: str, value: Any, status: str = "") -> str:
    return f"""
    <div class="fabric-stat-card fabric-stat-{h(status)}">
      <div class="fabric-stat-label">{h(label)}</div>
      <div class="fabric-stat-value">{h(value)}</div>
    </div>
    """


def _status_pill(status: Any) -> str:
    s = str(status or "unknown").lower()
    return f'<span class="fabric-status fabric-status-{h(s)}">{h(s)}</span>'


def _link_card(r: dict[str, Any]) -> str:
    status = str(r.get("last_status") or "unknown").lower()
    local_url = f"/dashboard?device_id={r.get('local_device_id')}" if r.get("local_device_id") else ""
    remote_url = f"/dashboard?device_id={r.get('remote_device_id')}" if r.get("remote_device_id") else ""

    local_name = h(r.get("local_device"))
    remote_name = h(r.get("remote_device"))

    if local_url:
        local_name = f'<a href="{h(local_url)}">{local_name}</a>'
    if remote_url:
        remote_name = f'<a href="{h(remote_url)}">{remote_name}</a>'

    return f"""
    <article class="fabric-link-card fabric-link-{h(status)}">
      <div class="fabric-link-top">
        {_status_pill(status)}
        <span class="fabric-link-role">{h(r.get("link_role") or "fabric-uplink")}</span>
      </div>

      <div class="fabric-link-path">
        <div class="fabric-endpoint">
          <div class="fabric-device">{local_name}</div>
          <div class="fabric-port">{h(r.get("local_port"))}</div>
        </div>

        <div class="fabric-line" aria-hidden="true">
          <span></span>
          <b>⇄</b>
          <span></span>
        </div>

        <div class="fabric-endpoint fabric-endpoint-right">
          <div class="fabric-device">{remote_name}</div>
          <div class="fabric-port">{h(r.get("remote_port"))}</div>
        </div>
      </div>

      <div class="fabric-link-meta">
        Last seen: {h(r.get("last_seen"))}
      </div>
    </article>
    """


def _problem_card(p: dict[str, Any]) -> str:
    return f"""
    <article class="fabric-problem-card fabric-problem-{h(p.get("status"))}">
      <div class="fabric-problem-head">
        {_status_pill(p.get("status"))}
        <strong>{h(p.get("severity"))}</strong>
      </div>
      <div class="fabric-problem-path">
        <span>{h(p.get("local_device"))}</span>
        <code>{h(p.get("local_port"))}</code>
        <b>⇄</b>
        <span>{h(p.get("remote_device"))}</span>
        <code>{h(p.get("remote_port"))}</code>
      </div>
      <div class="fabric-link-meta">Last seen: {h(p.get("last_seen"))}</div>
    </article>
    """


def render_fabric_link_monitor() -> str:
    summary = fabric_link_summary()
    problems = fabric_link_problems()
    rows = fabric_link_rows(problems_only=False, limit=1000)

    grouped = defaultdict(list)
    for r in rows:
        grouped[_pod_name(r.get("local_device"), r.get("remote_device"))].append(r)

    pod_order = [
        "Core / Services",
        "East Pod",
        "North Pod",
        "South Pod",
        "West Pod",
        "Test Pod",
        "DFL Local",
        "Voter Local",
        "Other Fabric Links",
    ]

    if problems:
        problem_html = f"""
        <section class="fabric-section fabric-section-problems">
          <div class="fabric-section-head">
            <h2>Problems</h2>
            <p>Expected uplinks that are down or missing while both endpoint switches are still up.</p>
          </div>
          <div class="fabric-problem-grid">
            {''.join(_problem_card(p) for p in problems)}
          </div>
        </section>
        """
    else:
        problem_html = """
        <section class="fabric-ok-banner">
          <div>
            <strong>No fabric link problems</strong>
            <span>All learned expected uplinks are currently up.</span>
          </div>
        </section>
        """

    sections = []
    for pod in pod_order:
        items = grouped.get(pod, [])
        if not items:
            continue

        counts = defaultdict(int)
        for item in items:
            counts[str(item.get("last_status") or "unknown").lower()] += 1

        sections.append(f"""
        <section class="fabric-section">
          <div class="fabric-section-head">
            <h2>{h(pod)}</h2>
            <p>
              {len(items)} links ·
              {counts.get("up", 0)} up ·
              {counts.get("down", 0)} down ·
              {counts.get("missing", 0)} missing ·
              {counts.get("unknown", 0)} unknown
            </p>
          </div>
          <div class="fabric-link-grid">
            {''.join(_link_card(r) for r in items)}
          </div>
        </section>
        """)

    return f"""
    <div class="fabric-hero">
      <div>
        <h1>Fabric Link Monitor</h1>
        <p>
          Dynamic visual baseline learned from LibreNMS xDP. New switches are learned automatically once their fabric uplinks appear.
        </p>
      </div>
      <div class="fabric-hero-actions">
        <a class="button" href="/api/fabric-links/problems">Problem JSON</a>
        <a class="button" href="/api/fabric-links/summary">Summary JSON</a>
        <a class="button" href="/tools/maps/mist-pods">Mist Pod Map</a>
      </div>
    </div>

    <div class="fabric-stat-grid">
      {_stat_card("Problems", summary.get("problems", 0), "problem" if summary.get("problems") else "ok")}
      {_stat_card("Total Links", summary.get("total", 0), "neutral")}
      {_stat_card("Up", summary.get("up", 0), "ok")}
      {_stat_card("Down", summary.get("down", 0), "warn")}
      {_stat_card("Missing", summary.get("missing", 0), "problem")}
      {_stat_card("Unknown", summary.get("unknown", 0), "unknown")}
    </div>

    {problem_html}

    {''.join(sections)}
    """


def _flow_role(name: Any) -> str:
    n = str(name or "").strip().lower()

    if "fabric-core" in n or n in ("dfl-core.middlebury.edu", "vtr-core.middlebury.edu"):
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


def _flow_pod_name(*names: Any) -> str:
    text = " ".join(str(n or "").lower() for n in names)

    if "eastpod" in text or "east-pod" in text:
        return "East Pod"
    if "northpod" in text or "north-pod" in text:
        return "North Pod"
    if "southpod" in text or "south-pod" in text:
        return "South Pod"
    if "westpod" in text or "west-pod" in text:
        return "West Pod"
    if "testpod" in text or "test-pod" in text:
        return "Test Pod"
    if "dfl" in text:
        return "DFL Local"
    if "voter" in text or "vtr" in text:
        return "Voter Local"

    return "Other"


def _flow_endpoint(row: dict[str, Any], role: str) -> tuple[str, str]:
    local_device = row.get("local_device")
    local_port = row.get("local_port")
    remote_device = row.get("remote_device")
    remote_port = row.get("remote_port")

    if _flow_role(local_device) == role:
        return str(local_device or ""), str(local_port or "")
    if _flow_role(remote_device) == role:
        return str(remote_device or ""), str(remote_port or "")

    return "", ""


def _flow_other_endpoint(row: dict[str, Any], device: str) -> tuple[str, str]:
    if str(row.get("local_device") or "") == device:
        return str(row.get("remote_device") or ""), str(row.get("remote_port") or "")
    return str(row.get("local_device") or ""), str(row.get("local_port") or "")


def _flow_link_chip(row: dict[str, Any], label_device: str, label_port: str) -> str:
    status = str(row.get("last_status") or "unknown").lower()
    return f"""
    <div class="flow-link-chip flow-link-{h(status)}">
      <span class="flow-dot"></span>
      <div class="flow-chip-text">
        <strong>{h(label_device)}</strong>
        <code>{h(label_port)}</code>
      </div>
      {_status_pill(status)}
    </div>
    """


def _flow_dist_card(dist: str, rows: list[dict[str, Any]]) -> str:
    upstream = []
    downstream = []
    service = []
    other = []

    for row in rows:
        other_device, other_port = _flow_other_endpoint(row, dist)
        other_role = _flow_role(other_device)

        if other_role == "core":
            upstream.append((row, other_device, other_port))
        elif other_role in ("access", "distribution"):
            downstream.append((row, other_device, other_port))
        elif other_role == "service":
            service.append((row, other_device, other_port))
        else:
            other.append((row, other_device, other_port))

    access_items = downstream + service + other

    statuses = [str(r.get("last_status") or "unknown").lower() for r in rows]
    problems = sum(1 for st in statuses if st in ("down", "missing", "unknown"))
    missing = sum(1 for st in statuses if st == "missing")
    down = sum(1 for st in statuses if st == "down")
    unknown = sum(1 for st in statuses if st == "unknown")
    up = sum(1 for st in statuses if st == "up")

    health = "ok"
    if missing:
        health = "critical"
    elif down or unknown or problems:
        health = "warning"

    def is_problem(item):
        row, _, _ = item
        return str(row.get("last_status") or "unknown").lower() in ("down", "missing", "unknown")

    access_problems = [item for item in access_items if is_problem(item)]
    access_healthy = [item for item in access_items if not is_problem(item)]

    upstream_problems = [item for item in upstream if is_problem(item)]
    upstream_healthy = [item for item in upstream if not is_problem(item)]

    def render_chip_list(items, empty="None learned."):
        if not items:
            return f"<p class='flow-empty'>{h(empty)}</p>"
        return "".join(_flow_link_chip(row, dev, port) for row, dev, port in items)

    access_problem_html = ""
    if access_problems:
        access_problem_html = f"""
        <div class="flow-problem-subsection">
          <h5>Problem access links</h5>
          {render_chip_list(access_problems)}
        </div>
        """

    upstream_problem_html = ""
    if upstream_problems:
        upstream_problem_html = f"""
        <div class="flow-problem-subsection">
          <h5>Problem core uplinks</h5>
          {render_chip_list(upstream_problems)}
        </div>
        """

    access_summary = f"{len(access_healthy)} healthy access links"
    if access_problems:
        access_summary = f"{len(access_problems)} problem · {len(access_healthy)} healthy"

    core_summary = f"{len(upstream_healthy)} healthy core uplinks"
    if upstream_problems:
        core_summary = f"{len(upstream_problems)} problem · {len(upstream_healthy)} healthy"

    return f"""
    <article class="flow-dist-card flow-health-{h(health)}">
      <div class="flow-dist-head">
        <div>
          <h3>{h(dist)}</h3>
          <p>{up} up · {down} down · {missing} missing · {unknown} unknown</p>
        </div>
        <span class="flow-health-badge flow-health-badge-{h(health)}">{h(health)}</span>
      </div>

      <div class="flow-dist-grid">
        <section class="flow-column flow-core-column">
          <h4>Core uplinks</h4>
          {upstream_problem_html}
          <details class="flow-collapse" {"open" if upstream_problems else ""}>
            <summary>{h(core_summary)}</summary>
            {render_chip_list(upstream_healthy, "No healthy core uplinks.")}
          </details>
        </section>

        <section class="flow-column flow-dist-column">
          <h4>Distribution</h4>
          <div class="flow-device-node">
            <span class="flow-node-icon">⇅</span>
            <strong>{h(dist)}</strong>
            <small>{len(upstream)} core · {len(access_items)} downstream</small>
          </div>
        </section>

        <section class="flow-column flow-access-column">
          <h4>Access / leaf links</h4>
          {access_problem_html}
          <details class="flow-collapse" {"open" if access_problems else ""}>
            <summary>{h(access_summary)}</summary>
            {render_chip_list(access_healthy, "No healthy access links.")}
          </details>
        </section>
      </div>
    </article>
    """


def render_fabric_link_flow() -> str:
    summary = fabric_link_summary()
    rows = fabric_link_rows(problems_only=False, limit=2000)
    problems = fabric_link_problems()

    dist_map: dict[str, list[dict[str, Any]]] = {}
    orphan_rows: list[dict[str, Any]] = []

    for row in rows:
        local_device = str(row.get("local_device") or "")
        remote_device = str(row.get("remote_device") or "")

        local_role = _flow_role(local_device)
        remote_role = _flow_role(remote_device)

        dist = ""
        if local_role == "distribution":
            dist = local_device
        elif remote_role == "distribution":
            dist = remote_device

        if dist:
            dist_map.setdefault(dist, []).append(row)
        else:
            orphan_rows.append(row)

    pods: dict[str, list[str]] = {}
    for dist in dist_map:
        pod = _flow_pod_name(dist)
        pods.setdefault(pod, []).append(dist)

    pod_order = [
        "East Pod",
        "North Pod",
        "South Pod",
        "West Pod",
        "Test Pod",
        "DFL Local",
        "Voter Local",
        "Other",
    ]

    pod_sections = []
    for pod in pod_order:
        dists = sorted(pods.get(pod, []))
        if not dists:
            continue

        pod_rows = []
        for dist in dists:
            pod_rows.extend(dist_map.get(dist, []))

        pod_statuses = [str(r.get("last_status") or "unknown").lower() for r in pod_rows]
        pod_up = sum(1 for s in pod_statuses if s == "up")
        pod_down = sum(1 for s in pod_statuses if s == "down")
        pod_missing = sum(1 for s in pod_statuses if s == "missing")
        pod_unknown = sum(1 for s in pod_statuses if s == "unknown")

        pod_sections.append(f"""
        <section class="flow-pod-section">
          <div class="flow-pod-head">
            <div>
              <h2>{h(pod)}</h2>
              <p>{len(dists)} distribution nodes · {len(pod_rows)} expected links</p>
            </div>
            <div class="flow-pod-stats">
              <span class="flow-mini flow-mini-up">{pod_up} up</span>
              <span class="flow-mini flow-mini-down">{pod_down} down</span>
              <span class="flow-mini flow-mini-missing">{pod_missing} missing</span>
              <span class="flow-mini flow-mini-unknown">{pod_unknown} unknown</span>
            </div>
          </div>

          <div class="flow-dist-stack">
            {''.join(_flow_dist_card(dist, dist_map.get(dist, [])) for dist in dists)}
          </div>
        </section>
        """)

    if problems:
        problem_banner = f"""
        <section class="flow-problem-banner">
          <h2>{len(problems)} Fabric Link Problems</h2>
          <p>One or more expected uplinks are down or missing while both endpoint switches are up.</p>
          <a class="button" href="/api/fabric-links/problems">View problem JSON</a>
        </section>
        """
    else:
        problem_banner = """
        <section class="flow-ok-banner">
          <h2>No Fabric Link Problems</h2>
          <p>All dynamically learned expected fabric uplinks are currently up.</p>
        </section>
        """

    return f"""
    <div class="flow-hero">
      <div>
        <h1>Fabric Flow Monitor</h1>
        <p>
          Flow-chart view of expected fabric uplinks. Core links on the left, distribution in the middle,
          access/leaf links on the right. Missing links stay visible instead of vanishing with xDP.
        </p>
      </div>
      <div class="flow-actions">
        <a class="button" href="/tools/fabric-links">Card View</a>
        <a class="button" href="/tools/maps/mist-pods">Mist Pod Map</a>
        <a class="button" href="/api/fabric-links/summary">Summary JSON</a>
      </div>
    </div>

    <div class="fabric-stat-grid">
      {_stat_card("Problems", summary.get("problems", 0), "problem" if summary.get("problems") else "ok")}
      {_stat_card("Total Links", summary.get("total", 0), "neutral")}
      {_stat_card("Up", summary.get("up", 0), "ok")}
      {_stat_card("Down", summary.get("down", 0), "warn")}
      {_stat_card("Missing", summary.get("missing", 0), "problem")}
      {_stat_card("Unknown", summary.get("unknown", 0), "unknown")}
    </div>

    {problem_banner}

    {''.join(pod_sections)}
    """
