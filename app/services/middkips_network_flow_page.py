from __future__ import annotations

import json
import re
from collections import defaultdict
from html import escape
from typing import Any

from app.core.db import fetch_all


def h(v: Any) -> str:
    return escape(str(v or ""))


def slug(v: Any) -> str:
    s = str(v or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "node"


def role(name: Any) -> str:
    n = str(name or "").strip().lower()

    if "fabric-core" in n or n in ("dfl-core.middlebury.edu", "vtr-core.middlebury.edu"):
        return "core"
    if n.startswith("dist-") or "dist-" in n:
        return "distribution"
    if n.startswith("svcs-") or n.startswith("services-"):
        return "service"
    if any(x in n for x in ("edgefw", "vpn-fw", "enclave-fw", "miis-fw", "dc-fw", "enclave-fw")):
        return "edge"
    if any(x in n for x in ("racktop", "aggregation", "arista", "dc-", "-dc-", "700", "pve-core", "hci", "ceph")):
        return "datacenter"
    return "access"


def pod_name(*names: Any) -> str:
    text = " ".join(str(n or "").lower() for n in names)

    if "eastpod" in text or "east-pod" in text:
        return "East POD"
    if "northpod" in text or "north-pod" in text:
        return "North POD"
    if "southpod" in text or "south-pod" in text:
        return "South POD"
    if "westpod" in text or "west-pod" in text:
        return "West POD"
    if "testpod" in text or "test-pod" in text:
        return "Test POD"
    if "dfl" in text:
        return "DFL"
    if "voter" in text or "vtr" in text:
        return "Voter"
    return "Other"


def status_class(status: Any) -> str:
    s = str(status or "unknown").lower()
    if s not in ("up", "down", "missing", "unknown"):
        s = "unknown"
    return s


def mist_expected_links(limit: int = 2000) -> list[dict[str, Any]]:
    return fetch_all("""
    SELECT
      e.local_device,
      e.local_port,
      e.remote_device,
      e.remote_port,
      e.last_status,
      e.last_seen,
      e.link_role,

      ld.device_id AS local_device_id,
      rd.device_id AS remote_device_id
    FROM middkips_expected_fabric_links e
    LEFT JOIN devices ld
      ON LOWER(e.local_device) = LOWER(ld.display)
      OR LOWER(e.local_device) = LOWER(ld.sysName)
      OR LOWER(e.local_device) = LOWER(ld.hostname)
    LEFT JOIN devices rd
      ON LOWER(e.remote_device) = LOWER(rd.display)
      OR LOWER(e.remote_device) = LOWER(rd.sysName)
      OR LOWER(e.remote_device) = LOWER(rd.hostname)
    WHERE e.map_type = 'mist-pods'
    ORDER BY e.local_device, e.local_port, e.remote_device, e.remote_port
    LIMIT %s
    """, [int(limit)])


def flow_summary_from_links(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {"up": 0, "down": 0, "missing": 0, "unknown": 0, "total": len(rows), "problems": 0}
    for r in rows:
        s = status_class(r.get("last_status"))
        out[s] = out.get(s, 0) + 1
        if s in ("down", "missing", "unknown"):
            out["problems"] += 1
    return out


def node_html(node: dict[str, Any]) -> str:
    classes = [
        "nf-node",
        f"nf-role-{h(node.get('role'))}",
        f"nf-status-{h(node.get('status', 'up'))}",
    ]

    style = f"--x:{node['x']};--y:{node['y']};--w:{node.get('w', 150)};"
    subtitle = node.get("subtitle") or ""
    device_id = node.get("device_id")
    url = f"/device/device={device_id}" if device_id else ""

    inner = f"""
      <strong>{h(node.get("label"))}</strong>
      <small>{h(subtitle)}</small>
    """

    if url:
        inner = f'<a href="{h(url)}">{inner}</a>'

    return f"""
    <div
      class="{' '.join(classes)}"
      style="{h(style)}"
      data-node="{h(node['id'])}"
      title="{h(node.get('label'))}"
    >
      {inner}
    </div>
    """


def path_html(link: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> str:
    a = nodes.get(link["source"])
    b = nodes.get(link["target"])
    if not a or not b:
        return ""

    x1 = float(a["x"]) + float(a.get("w", 150)) / 2
    y1 = float(a["y"]) + 32
    x2 = float(b["x"]) + float(b.get("w", 150)) / 2
    y2 = float(b["y"]) + 32

    mid_y = min(y1, y2) - 80
    if abs(y1 - y2) < 100:
        mid_y = (y1 + y2) / 2 - 120

    d = f"M{x1},{y1} C{x1},{mid_y} {x2},{mid_y} {x2},{y2}"
    status = status_class(link.get("status"))

    label = f"{link.get('local_port', '')} ↔ {link.get('remote_port', '')}"

    return f"""
    <path
      class="nf-link nf-link-{h(status)}"
      d="{h(d)}"
      data-source="{h(link['source'])}"
      data-target="{h(link['target'])}"
    >
      <title>{h(label)}</title>
    </path>
    """


def render_shell(
    title: str,
    subtitle: str,
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]],
    summary: dict[str, Any],
    module_links: bool = True,
    module: str = "mist",
    editable: bool = False,
) -> str:
    node_map = {n["id"]: n for n in nodes}

    max_x = max([float(n.get("x", 0)) + float(n.get("w", 150)) + 240 for n in nodes] + [1800])
    max_y = max([float(n.get("y", 0)) + 180 for n in nodes] + [900])
    width = int(max_x)
    height = int(max_y)

    nav = ""
    if module_links:
        nav = """
        <div class="nf-module-nav">
          <a href="/tools/network-flow/mist">Mist Fabric</a>
          <a href="/tools/network-flow/edge">Edge</a>
          <a href="/tools/network-flow/datacenter">Old Datacenter</a>
          <a href="/tools/network-flow/hci">HCI Cluster</a>
          <a href="/tv/network-flow">TV Dashboard</a>
        </div>
        """

    edit_url = f"/tools/network-flow/{module}?edit=1"
    view_url = f"/tools/network-flow/{module}"

    edit_controls = ""
    if editable:
        edit_controls = f"""
        <button class="button" id="nf-save-layout">Save Layout</button>
        <button class="button" id="nf-reset-layout">Reset Layout</button>
        <a class="button" href="{h(view_url)}">View Mode</a>
        """
    else:
        edit_controls = f"""
        <a class="button" href="{h(edit_url)}">Edit Layout</a>
        """

    return f"""
    <div class="nf-page" data-module="{h(module)}" data-editable="{str(editable).lower()}">
      <div class="nf-topbar">
        <div>
          <h1>{h(title)}</h1>
          <p>{h(subtitle)}</p>
        </div>
        <div class="nf-actions">
          <button class="button" id="nf-fit">Fit</button>
          <button class="button" id="nf-zoom-out">−</button>
          <button class="button" id="nf-zoom-in">+</button>
          <button class="button" id="nf-compact">Compact</button>
          {edit_controls}
        </div>
      </div>

      {nav}

      <div class="nf-summary">
        <div class="nf-stat nf-stat-problems"><span>Problems</span><strong>{h(summary.get("problems", 0))}</strong></div>
        <div class="nf-stat"><span>Total Links</span><strong>{h(summary.get("total", 0))}</strong></div>
        <div class="nf-stat nf-stat-up"><span>Up</span><strong>{h(summary.get("up", 0))}</strong></div>
        <div class="nf-stat nf-stat-down"><span>Down</span><strong>{h(summary.get("down", 0))}</strong></div>
        <div class="nf-stat nf-stat-missing"><span>Missing</span><strong>{h(summary.get("missing", 0))}</strong></div>
        <div class="nf-stat nf-stat-unknown"><span>Unknown</span><strong>{h(summary.get("unknown", 0))}</strong></div>
      </div>

      <div class="nf-edit-help {'is-visible' if editable else ''}">
        Drag nodes to rearrange the module. Click <strong>Save Layout</strong> when it stops looking like the network fell down the stairs.
      </div>

      <div class="nf-canvas-wrap">
        <div class="nf-zoom-stage">
          <div class="nf-canvas" style="--canvas-w:{width}px;--canvas-h:{height}px;">
            <svg class="nf-svg" viewBox="0 0 {width} {height}" preserveAspectRatio="xMinYMin meet">
              <defs>
                <filter id="nf-glow-green" x="-40%" y="-40%" width="180%" height="180%">
                  <feGaussianBlur stdDeviation="5" result="coloredBlur"/>
                  <feMerge>
                    <feMergeNode in="coloredBlur"/>
                    <feMergeNode in="SourceGraphic"/>
                  </feMerge>
                </filter>
                <filter id="nf-glow-pink" x="-40%" y="-40%" width="180%" height="180%">
                  <feGaussianBlur stdDeviation="5" result="coloredBlur"/>
                  <feMerge>
                    <feMergeNode in="coloredBlur"/>
                    <feMergeNode in="SourceGraphic"/>
                  </feMerge>
                </filter>
              </defs>
              {''.join(path_html(l, node_map) for l in links)}
            </svg>

            {''.join(node_html(n) for n in nodes)}
          </div>
        </div>
      </div>
    </div>

    <script>
    (() => {{
      const page = document.querySelector(".nf-page");
      const stage = document.querySelector(".nf-zoom-stage");
      const canvas = document.querySelector(".nf-canvas");
      const wrap = document.querySelector(".nf-canvas-wrap");
      if (!page || !stage || !canvas || !wrap) return;

      const editable = page.dataset.editable === "true";
      const moduleName = page.dataset.module || "mist";
      let zoom = 1;

      const setZoom = (z) => {{
        zoom = Math.max(0.25, Math.min(1.75, z));
        stage.style.transform = `scale(${{zoom}})`;
        stage.style.transformOrigin = "top left";
        wrap.dataset.zoom = zoom.toFixed(2);
      }};

      const fit = () => {{
        const canvasW = canvas.offsetWidth || 1800;
        const wrapW = wrap.clientWidth || window.innerWidth;
        const z = Math.min(1, Math.max(0.25, (wrapW - 24) / canvasW));
        setZoom(z);
      }};

      document.getElementById("nf-fit")?.addEventListener("click", fit);
      document.getElementById("nf-zoom-in")?.addEventListener("click", () => setZoom(zoom + 0.1));
      document.getElementById("nf-zoom-out")?.addEventListener("click", () => setZoom(zoom - 0.1));
      document.getElementById("nf-compact")?.addEventListener("click", () => {{
        page.classList.toggle("nf-compact-mode");
      }});

      let hotEls = [];

      const clear = () => {{
        hotEls.forEach(el => el.classList.remove("nf-hot"));
        hotEls = [];
      }};

      const hotNode = (node) => {{
        clear();
        const id = node.dataset.node;
        node.classList.add("nf-hot");
        hotEls.push(node);

        canvas.querySelectorAll(`.nf-link[data-source="${{id}}"], .nf-link[data-target="${{id}}"]`).forEach(link => {{
          link.classList.add("nf-hot");
          hotEls.push(link);

          const other = link.dataset.source === id ? link.dataset.target : link.dataset.source;
          const otherNode = canvas.querySelector(`.nf-node[data-node="${{other}}"]`);
          if (otherNode) {{
            otherNode.classList.add("nf-hot");
            hotEls.push(otherNode);
          }}
        }});
      }};

      canvas.querySelectorAll(".nf-node").forEach(node => {{
        node.addEventListener("mouseenter", () => hotNode(node));
        node.addEventListener("mouseleave", clear);
      }});

      if (editable) {{
        canvas.classList.add("nf-editable");

        let dragging = null;

        const getPoint = (ev) => {{
          const rect = canvas.getBoundingClientRect();
          return {{
            x: (ev.clientX - rect.left) / zoom,
            y: (ev.clientY - rect.top) / zoom,
          }};
        }};

        canvas.querySelectorAll(".nf-node").forEach(node => {{
          node.addEventListener("pointerdown", (ev) => {{
            ev.preventDefault();
            const pt = getPoint(ev);
            dragging = {{
              node,
              startX: pt.x,
              startY: pt.y,
              origX: parseFloat(node.style.getPropertyValue("--x") || "0"),
              origY: parseFloat(node.style.getPropertyValue("--y") || "0"),
            }};
            node.setPointerCapture(ev.pointerId);
            node.classList.add("nf-dragging");
          }});

          node.addEventListener("pointermove", (ev) => {{
            if (!dragging || dragging.node !== node) return;
            const pt = getPoint(ev);
            const dx = pt.x - dragging.startX;
            const dy = pt.y - dragging.startY;
            const nx = Math.max(0, Math.round(dragging.origX + dx));
            const ny = Math.max(0, Math.round(dragging.origY + dy));
            node.style.setProperty("--x", nx);
            node.style.setProperty("--y", ny);
          }});

          node.addEventListener("pointerup", (ev) => {{
            if (!dragging || dragging.node !== node) return;
            node.classList.remove("nf-dragging");
            dragging = null;
          }});
        }});

        document.getElementById("nf-save-layout")?.addEventListener("click", async () => {{
          const positions = Array.from(canvas.querySelectorAll(".nf-node")).map(node => ({{
            node_id: node.dataset.node,
            x: parseFloat(node.style.getPropertyValue("--x") || "0"),
            y: parseFloat(node.style.getPropertyValue("--y") || "0"),
            w: parseFloat(node.style.getPropertyValue("--w") || "150"),
          }}));

          const res = await fetch(`/api/network-flow/${{moduleName}}/positions`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{positions}})
          }});

          const data = await res.json();
          alert(`Saved ${{data.saved || 0}} node positions`);
        }});

        document.getElementById("nf-reset-layout")?.addEventListener("click", async () => {{
          if (!confirm("Reset saved node positions for this module?")) return;
          await fetch(`/api/network-flow/${{moduleName}}/positions/reset`, {{method: "POST"}});
          window.location.reload();
        }});
      }}

      setTimeout(fit, 150);
    }})();
    </script>
    """


def build_mist_flow():
    rows = mist_expected_links()
    summary = flow_summary_from_links(rows)

    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    pod_columns = {
        "East POD": 60,
        "North POD": 360,
        "South POD": 660,
        "West POD": 960,
        "Test POD": 1260,
    }

    dist_seen = defaultdict(int)
    access_seen = defaultdict(int)

    core_x = {
        "fabric-core-dfl": 560,
        "fabric-core-voter": 860,
        "dfl-core.middlebury.edu": 1500,
        "vtr-core.middlebury.edu": 1720,
    }

    for r in rows:
        local = str(r.get("local_device") or "")
        remote = str(r.get("remote_device") or "")
        local_role = role(local)
        remote_role = role(remote)

        for device, dev_role, device_id in (
            (local, local_role, r.get("local_device_id")),
            (remote, remote_role, r.get("remote_device_id")),
        ):
            node_id = slug(device)
            if node_id in nodes:
                continue

            pod = pod_name(device)

            if dev_role == "core":
                x = core_x.get(device.lower(), 680 + len([n for n in nodes.values() if n["role"] == "core"]) * 260)
                y = 70
                w = 190
                subtitle = "Mist / campus core"
            elif dev_role == "distribution":
                x = pod_columns.get(pod, 60)
                idx = dist_seen[pod]
                dist_seen[pod] += 1
                y = 260 + idx * 95
                w = 220
                subtitle = "Distribution"
            else:
                x = pod_columns.get(pod, 60) + 10
                idx = access_seen[pod]
                access_seen[pod] += 1
                y = 430 + idx * 72
                w = 210
                subtitle = "Access / leaf"

            nodes[node_id] = {
                "id": node_id,
                "label": device,
                "subtitle": subtitle,
                "role": dev_role,
                "status": status_class(r.get("last_status")),
                "x": x,
                "y": y,
                "w": w,
                "device_id": device_id,
            }

    for r in rows:
        source = slug(r.get("local_device"))
        target = slug(r.get("remote_device"))
        if source == target:
            continue
        links.append({
            "source": source,
            "target": target,
            "status": status_class(r.get("last_status")),
            "local_port": r.get("local_port"),
            "remote_port": r.get("remote_port"),
        })

    return list(nodes.values()), links, summary


def render_mist_flow(editable: bool = False) -> str:
    nodes, links, summary = build_mist_flow()
    nodes = apply_saved_positions("mist", nodes)
    return render_shell(
        "Mist Fabric Flow",
        "Dynamic flow built from expected fabric uplinks. Hover a switch to light up its paths.",
        nodes,
        links,
        summary,
        module="mist",
        editable=editable,
    )


def device_rows_for_module(module: str) -> list[dict[str, Any]]:
    if module == "edge":
        patterns = ["edgefw", "vpn-fw", "enclave-fw", "miis-fw", "dc-fw", "firewall"]
    elif module == "datacenter":
        patterns = ["racktop", "aggregation", "arista", "dc-", "-dc-", "old", "legacy"]
    elif module == "hci":
        patterns = ["pve-core", "hci", "ceph", "700", "calamari", "bonefish"]
    else:
        patterns = []

    where = " OR ".join([
        "LOWER(COALESCE(display, sysName, hostname)) LIKE %s"
        for _ in patterns
    ])

    if not where:
        return []

    params = [f"%{p}%" for p in patterns]

    return fetch_all(f"""
    SELECT
      device_id,
      COALESCE(display, sysName, hostname) AS name,
      hardware,
      os,
      status
    FROM devices
    WHERE {where}
    ORDER BY name
    LIMIT 80
    """, params)


def build_simple_module(module: str):
    rows = device_rows_for_module(module)
    nodes = []
    links = []

    title_role = {
        "edge": "edge",
        "datacenter": "datacenter",
        "hci": "datacenter",
    }.get(module, "datacenter")

    center_id = f"{module}-module-center"
    center_label = {
        "edge": "Network Edge",
        "datacenter": "Old Datacenter",
        "hci": "HCI / DC Cluster",
    }.get(module, module)

    nodes.append({
        "id": center_id,
        "label": center_label,
        "subtitle": "Module anchor",
        "role": "core",
        "status": "up",
        "x": 980,
        "y": 120,
        "w": 240,
        "device_id": None,
    })

    cols = [260, 620, 980, 1340, 1700]
    for i, row in enumerate(rows):
        x = cols[i % len(cols)]
        y = 340 + (i // len(cols)) * 120
        node_id = slug(row.get("name"))
        stat = "up" if int(row.get("status") or 0) == 1 else "down"

        nodes.append({
            "id": node_id,
            "label": row.get("name"),
            "subtitle": row.get("hardware") or row.get("os") or title_role,
            "role": title_role,
            "status": stat,
            "x": x,
            "y": y,
            "w": 240,
            "device_id": row.get("device_id"),
        })

        links.append({
            "source": center_id,
            "target": node_id,
            "status": stat,
            "local_port": "",
            "remote_port": "",
        })

    summary = {
        "total": len(links),
        "up": sum(1 for l in links if l["status"] == "up"),
        "down": sum(1 for l in links if l["status"] == "down"),
        "missing": 0,
        "unknown": 0,
        "problems": sum(1 for l in links if l["status"] != "up"),
    }

    return nodes, links, summary


def render_module_flow(module: str, editable: bool = False) -> str:
    labels = {
        "edge": ("Network Edge Flow", "Edge firewall/VPN/enclave view. Dynamic devices now, expected links next."),
        "datacenter": ("Old Datacenter Flow", "Legacy datacenter module. Dynamic device discovery now, expected links next."),
        "hci": ("HCI / DC Cluster Flow", "HCI and datacenter cluster module. Dynamic device discovery now, expected links next."),
    }

    title, subtitle = labels.get(module, ("Network Flow", "Network module flow."))
    nodes, links, summary = build_simple_module(module)
    nodes = apply_saved_positions(module, nodes)

    return render_shell(title, subtitle, nodes, links, summary, module=module, editable=editable)


def render_network_flow_home() -> str:
    return """
    <div class="nf-page">
      <div class="nf-topbar">
        <div>
          <h1>Network Flow Modules</h1>
          <p>Separate topology modules for PiSignage dashboards and focused operational views.</p>
        </div>
      </div>

      <div class="nf-module-grid">
        <a class="nf-module-card nf-module-mist" href="/tools/network-flow/mist">
          <strong>Mist Fabric</strong>
          <span>Fabric cores, distribution, access uplinks, expected-link status.</span>
        </a>
        <a class="nf-module-card nf-module-edge" href="/tools/network-flow/edge">
          <strong>Network Edge</strong>
          <span>Firewalls, VPN, external edge blocks.</span>
        </a>
        <a class="nf-module-card nf-module-dc" href="/tools/network-flow/datacenter">
          <strong>Old Datacenter</strong>
          <span>Legacy datacenter switching and aggregation.</span>
        </a>
        <a class="nf-module-card nf-module-hci" href="/tools/network-flow/hci">
          <strong>HCI / DC Cluster</strong>
          <span>PVE, HCI, storage, Ceph/DC cluster paths.</span>
        </a>
      </div>
    </div>
    """


def render_tv_network_flow() -> str:
    return """
    <div class="nf-tv-grid">
      <iframe src="/tools/network-flow/mist"></iframe>
      <iframe src="/tools/network-flow/edge"></iframe>
      <iframe src="/tools/network-flow/datacenter"></iframe>
      <iframe src="/tools/network-flow/hci"></iframe>
    </div>
    """


def ensure_position_table() -> None:
    fetch_all("""
    CREATE TABLE IF NOT EXISTS middkips_network_flow_positions (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      module VARCHAR(64) NOT NULL,
      node_id VARCHAR(255) NOT NULL,
      x INT NOT NULL,
      y INT NOT NULL,
      w INT DEFAULT NULL,
      updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uniq_module_node (module, node_id),
      KEY idx_module (module)
    )
    """)


def load_positions(module: str) -> dict[str, dict[str, int]]:
    ensure_position_table()
    rows = fetch_all("""
    SELECT node_id, x, y, w
    FROM middkips_network_flow_positions
    WHERE module = %s
    """, [module])

    out = {}
    for r in rows:
        out[str(r.get("node_id"))] = {
            "x": int(r.get("x") or 0),
            "y": int(r.get("y") or 0),
            "w": int(r.get("w") or 0),
        }
    return out


def save_positions(module: str, positions: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_position_table()

    saved = 0
    for p in positions:
        node_id = str(p.get("node_id") or "").strip()
        if not node_id:
            continue

        x = int(float(p.get("x") or 0))
        y = int(float(p.get("y") or 0))
        w = p.get("w")
        w = int(float(w)) if w not in (None, "") else None

        fetch_all("""
        INSERT INTO middkips_network_flow_positions
          (module, node_id, x, y, w)
        VALUES
          (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          x = VALUES(x),
          y = VALUES(y),
          w = VALUES(w)
        """, [module, node_id, x, y, w])

        saved += 1

    return {"saved": saved, "module": module}


def reset_positions(module: str) -> dict[str, Any]:
    ensure_position_table()
    fetch_all("""
    DELETE FROM middkips_network_flow_positions
    WHERE module = %s
    """, [module])
    return {"reset": True, "module": module}


def apply_saved_positions(module: str, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    positions = load_positions(module)

    for node in nodes:
        pos = positions.get(str(node.get("id")))
        if not pos:
            continue

        node["x"] = pos["x"]
        node["y"] = pos["y"]
        if pos.get("w"):
            node["w"] = pos["w"]

    return nodes


# ---------------------------------------------------------------------------
# Fast aggregate Mist flow override.
# This intentionally replaces the earlier per-access-node Mist flow because
# rendering hundreds of draggable glowing DOM nodes is browser cruelty.
# ---------------------------------------------------------------------------

def _worst_status(statuses: list[str]) -> str:
    clean = [str(s or "unknown").lower() for s in statuses]
    if "missing" in clean:
        return "missing"
    if "down" in clean:
        return "down"
    if "unknown" in clean:
        return "unknown"
    return "up"


def _access_summary_label(items: list[dict[str, Any]]) -> str:
    counts = {"up": 0, "down": 0, "missing": 0, "unknown": 0}
    for item in items:
        st = status_class(item.get("last_status"))
        counts[st] = counts.get(st, 0) + 1

    return (
        f"{len(items)} access links · "
        f"{counts.get('up', 0)} up · "
        f"{counts.get('down', 0)} down · "
        f"{counts.get('missing', 0)} missing"
    )


def build_mist_flow_fast():
    rows = mist_expected_links()
    summary = flow_summary_from_links(rows)

    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    pod_columns = {
        "East POD": 80,
        "North POD": 430,
        "South POD": 780,
        "West POD": 1130,
        "Test POD": 1480,
        "DFL": 1830,
        "Voter": 2080,
        "Other": 2280,
    }

    core_positions = {
        "fabric-core-dfl": (620, 80),
        "fabric-core-voter": (920, 80),
        "dfl-core.middlebury.edu": (1220, 80),
        "vtr-core.middlebury.edu": (1520, 80),
    }

    dist_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    core_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    access_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for r in rows:
        local = str(r.get("local_device") or "")
        remote = str(r.get("remote_device") or "")
        local_role = role(local)
        remote_role = role(remote)

        if local_role == "distribution":
            dist = local
            other = remote
            other_role = remote_role
        elif remote_role == "distribution":
            dist = remote
            other = local
            other_role = local_role
        else:
            continue

        dist_rows[dist].append(r)

        if other_role == "core":
            core_rows[dist].append(r)
        else:
            access_rows[dist].append(r)

    # Core nodes only if actually referenced.
    seen_cores = set()
    for dist, items in core_rows.items():
        for r in items:
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")
            core = local if role(local) == "core" else remote
            if not core:
                continue
            seen_cores.add(core)

    for idx, core in enumerate(sorted(seen_cores)):
        x, y = core_positions.get(core.lower(), (520 + idx * 260, 80))
        nodes[slug(core)] = {
            "id": slug(core),
            "label": core,
            "subtitle": "Core",
            "role": "core",
            "status": "up",
            "x": x,
            "y": y,
            "w": 230,
            "device_id": None,
        }

    pod_dist_index: dict[str, int] = defaultdict(int)

    for dist in sorted(dist_rows):
        pod = pod_name(dist)
        idx = pod_dist_index[pod]
        pod_dist_index[pod] += 1

        x = pod_columns.get(pod, 80)
        y = 290 + idx * 170

        statuses = [status_class(r.get("last_status")) for r in dist_rows[dist]]
        dist_status = _worst_status(statuses)

        dist_id = slug(dist)
        nodes[dist_id] = {
            "id": dist_id,
            "label": dist,
            "subtitle": f"{pod} distribution",
            "role": "distribution",
            "status": dist_status,
            "x": x,
            "y": y,
            "w": 250,
            "device_id": None,
        }

        # Core-to-dist links stay individual because those matter.
        for r in core_rows.get(dist, []):
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")

            core = local if role(local) == "core" else remote
            if not core:
                continue

            links.append({
                "source": slug(core),
                "target": dist_id,
                "status": status_class(r.get("last_status")),
                "local_port": r.get("local_port"),
                "remote_port": r.get("remote_port"),
            })

        # Aggregate access block per distribution.
        access_items = access_rows.get(dist, [])
        if access_items:
            agg_id = f"{dist_id}-access-summary"
            agg_status = _worst_status([status_class(r.get("last_status")) for r in access_items])

            nodes[agg_id] = {
                "id": agg_id,
                "label": "Access / Leaf",
                "subtitle": _access_summary_label(access_items),
                "role": "access",
                "status": agg_status,
                "x": x,
                "y": y + 95,
                "w": 250,
                "device_id": None,
            }

            links.append({
                "source": dist_id,
                "target": agg_id,
                "status": agg_status,
                "local_port": "",
                "remote_port": f"{len(access_items)} learned links",
            })

    return list(nodes.values()), links, summary


def render_mist_flow(editable: bool = False) -> str:
    nodes, links, summary = build_mist_flow_fast()
    nodes = apply_saved_positions("mist", nodes)
    return render_shell(
        "Mist Fabric Flow",
        "Fast aggregate flow. Core and distribution links are explicit; access/leaf links are summarized per distribution.",
        nodes,
        links,
        summary,
        module="mist",
        editable=editable,
    )


# ---------------------------------------------------------------------------
# Organized Mist flow override:
# cores top, all distribution in one row, access aggregate below each dist.
# Shows each upstream link separately, with calm colors and problem emphasis.
# ---------------------------------------------------------------------------

def path_html(link: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> str:
    a = nodes.get(link["source"])
    b = nodes.get(link["target"])
    if not a or not b:
        return ""

    x1 = float(a["x"]) + float(a.get("w", 150)) / 2
    y1 = float(a["y"]) + 32
    x2 = float(b["x"]) + float(b.get("w", 150)) / 2
    y2 = float(b["y"]) + 32

    lane = float(link.get("lane") or 0)
    status = status_class(link.get("status"))

    # Mostly vertical links get a slight horizontal lane offset so parallel links
    # are visibly separate instead of pretending to be one cable. Very bold,
    # drawing the second line we actually care about.
    if abs(x1 - x2) < 80:
        x1 += lane
        x2 += lane
        mid_y = (y1 + y2) / 2
        d = f"M{x1},{y1} C{x1},{mid_y} {x2},{mid_y} {x2},{y2}"
    else:
        mid_y = min(y1, y2) - 70
        d = f"M{x1},{y1} C{x1},{mid_y + lane} {x2},{mid_y - lane} {x2},{y2}"

    label = f"{link.get('local_port', '')} ↔ {link.get('remote_port', '')}"

    return f"""
    <path
      class="nf-link nf-link-{h(status)}"
      d="{h(d)}"
      data-source="{h(link['source'])}"
      data-target="{h(link['target'])}"
    >
      <title>{h(label)}</title>
    </path>
    """


def build_mist_flow_fast():
    rows = mist_expected_links()
    summary = flow_summary_from_links(rows)

    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    # Keep the page deterministic: cores top, dist row, access row.
    dist_order = [
        "dist-eastpod-dfl",
        "dist-eastpod-twilight",
        "dist-northpod-carr",
        "dist-northpod-mbh",
        "dist-southpod-emmaw",
        "dist-southpod-mac",
        "dist-westpod-stewart",
        "dist-westpod-voter",
        "dist-testpod-1",
        "dist-testpod-2",
    ]

    x_start = 90
    x_gap = 300
    y_core = 70
    y_dist = 300
    y_access = 430

    core_positions = {
        "fabric-core-dfl": (620, y_core),
        "fabric-core-voter": (1020, y_core),
        "dfl-core.middlebury.edu": (1420, y_core),
        "vtr-core.middlebury.edu": (1720, y_core),
    }

    dist_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    core_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    access_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for r in rows:
        local = str(r.get("local_device") or "")
        remote = str(r.get("remote_device") or "")
        local_role = role(local)
        remote_role = role(remote)

        if local_role == "distribution":
            dist = local
            other = remote
            other_role = remote_role
        elif remote_role == "distribution":
            dist = remote
            other = local
            other_role = local_role
        else:
            continue

        dist_rows[dist].append(r)

        if other_role == "core":
            core_rows[dist].append(r)
        else:
            access_rows[dist].append(r)

    # Core nodes, only if referenced by the expected-link data.
    seen_cores = set()
    for items in core_rows.values():
        for r in items:
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")
            core = local if role(local) == "core" else remote
            if core:
                seen_cores.add(core)

    for idx, core in enumerate(sorted(seen_cores)):
        x, y = core_positions.get(core.lower(), (520 + idx * 300, y_core))
        nodes[slug(core)] = {
            "id": slug(core),
            "label": core,
            "subtitle": "Core",
            "role": "core",
            "status": "up",
            "x": x,
            "y": y,
            "w": 230,
            "device_id": None,
        }

    known_dists = [d for d in dist_order if d in dist_rows]
    extra_dists = sorted([d for d in dist_rows if d not in known_dists])
    all_dists = known_dists + extra_dists

    for idx, dist in enumerate(all_dists):
        x = x_start + idx * x_gap

        statuses = [status_class(r.get("last_status")) for r in dist_rows[dist]]
        dist_status = _worst_status(statuses)

        dist_id = slug(dist)
        nodes[dist_id] = {
            "id": dist_id,
            "label": dist,
            "subtitle": f"{pod_name(dist)} distribution",
            "role": "distribution",
            "status": dist_status,
            "x": x,
            "y": y_dist,
            "w": 245,
            "device_id": None,
        }

        # Separate upstream links. Do not bundle these. Humans need to see both.
        upstream = core_rows.get(dist, [])
        for link_idx, r in enumerate(upstream):
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")
            core = local if role(local) == "core" else remote
            if not core:
                continue

            lane = (link_idx - ((len(upstream) - 1) / 2)) * 22

            links.append({
                "source": slug(core),
                "target": dist_id,
                "status": status_class(r.get("last_status")),
                "local_port": r.get("local_port"),
                "remote_port": r.get("remote_port"),
                "lane": lane,
            })

        # Aggregate access block below each dist.
        access_items = access_rows.get(dist, [])
        if access_items:
            agg_id = f"{dist_id}-access-summary"
            agg_status = _worst_status([status_class(r.get("last_status")) for r in access_items])

            nodes[agg_id] = {
                "id": agg_id,
                "label": "Access / Leaf",
                "subtitle": _access_summary_label(access_items),
                "role": "access",
                "status": agg_status,
                "x": x,
                "y": y_access,
                "w": 245,
                "device_id": None,
            }

            links.append({
                "source": dist_id,
                "target": agg_id,
                "status": agg_status,
                "local_port": "",
                "remote_port": f"{len(access_items)} learned links",
                "lane": 0,
            })

    return list(nodes.values()), links, summary


def render_mist_flow(editable: bool = False) -> str:
    nodes, links, summary = build_mist_flow_fast()
    nodes = apply_saved_positions("mist", nodes)
    return render_shell(
        "Mist Fabric Flow",
        "Organized row layout: cores on top, distribution in one row, access/leaf summaries below. Upstream links remain individually visible.",
        nodes,
        links,
        summary,
        module="mist",
        editable=editable,
    )


# ---------------------------------------------------------------------------
# Cleaner layered path renderer:
# - core -> distribution exits bottom of core, enters top of distribution
# - distribution -> access exits bottom of distribution, enters top of access
# - parallel links get visible offsets
# ---------------------------------------------------------------------------

def _node_center_x(node: dict[str, Any]) -> float:
    return float(node.get("x", 0)) + float(node.get("w", 150)) / 2


def _node_top_y(node: dict[str, Any]) -> float:
    return float(node.get("y", 0))


def _node_bottom_y(node: dict[str, Any]) -> float:
    return float(node.get("y", 0)) + float(node.get("h", 58))


def _anchor_points(source: dict[str, Any], target: dict[str, Any], lane: float = 0) -> tuple[float, float, float, float]:
    source_role = str(source.get("role") or "")
    target_role = str(target.get("role") or "")

    sx = _node_center_x(source) + lane
    tx = _node_center_x(target) + lane

    # Top-down layout. Stop letting links crawl out of the top of core nodes
    # like some kind of haunted cable tray.
    if source_role == "core" and target_role == "distribution":
        sy = _node_bottom_y(source)
        ty = _node_top_y(target)
    elif source_role == "distribution" and target_role == "core":
        sy = _node_top_y(source)
        ty = _node_bottom_y(target)
    elif source_role == "distribution" and target_role == "access":
        sy = _node_bottom_y(source)
        ty = _node_top_y(target)
    elif source_role == "access" and target_role == "distribution":
        sy = _node_top_y(source)
        ty = _node_bottom_y(target)
    else:
        # Fallback for edge/DC/HCI module pages.
        sy = _node_bottom_y(source) if float(source.get("y", 0)) <= float(target.get("y", 0)) else _node_top_y(source)
        ty = _node_top_y(target) if float(source.get("y", 0)) <= float(target.get("y", 0)) else _node_bottom_y(target)

    return sx, sy, tx, ty


def path_html(link: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> str:
    a = nodes.get(link["source"])
    b = nodes.get(link["target"])
    if not a or not b:
        return ""

    lane = float(link.get("lane") or 0)
    status = status_class(link.get("status"))

    x1, y1, x2, y2 = _anchor_points(a, b, lane)

    vertical_gap = abs(y2 - y1)

    # Keep the path mostly vertical and readable. For same-column links,
    # this creates two separated redundant strands instead of one fake cable.
    if abs(x1 - x2) < 90:
        mid_y = (y1 + y2) / 2
        d = f"M{x1},{y1} C{x1},{mid_y} {x2},{mid_y} {x2},{y2}"
    else:
        # Smooth top-down curve, control points between layers instead of
        # above the core. This is the important part.
        c1y = y1 + max(50, vertical_gap * 0.38)
        c2y = y2 - max(50, vertical_gap * 0.38)
        d = f"M{x1},{y1} C{x1},{c1y} {x2},{c2y} {x2},{y2}"

    label = f"{link.get('local_port', '')} ↔ {link.get('remote_port', '')}"

    return f"""
    <path
      class="nf-link nf-link-{h(status)}"
      d="{h(d)}"
      data-source="{h(link['source'])}"
      data-target="{h(link['target'])}"
    >
      <title>{h(label)}</title>
    </path>
    """


# ---------------------------------------------------------------------------
# Mist layout spacing override:
# fewer giant arcs, clearer rows, better default canvas.
# ---------------------------------------------------------------------------

def build_mist_flow_fast():
    rows = mist_expected_links()
    summary = flow_summary_from_links(rows)

    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    dist_order = [
        "dist-eastpod-dfl",
        "dist-eastpod-twilight",
        "dist-northpod-carr",
        "dist-northpod-mbh",
        "dist-southpod-emmaw",
        "dist-southpod-mac",
        "dist-westpod-stewart",
        "dist-westpod-voter",
        "dist-testpod-1",
        "dist-testpod-2",
    ]

    x_start = 90
    x_gap = 290

    y_core = 70
    y_dist = 330
    y_access = 475

    core_positions = {
        "fabric-core-dfl": (560, y_core),
        "fabric-core-voter": (1260, y_core),
        "dfl-core.middlebury.edu": (1660, y_core),
        "vtr-core.middlebury.edu": (1950, y_core),
    }

    dist_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    core_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    access_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for r in rows:
        local = str(r.get("local_device") or "")
        remote = str(r.get("remote_device") or "")
        local_role = role(local)
        remote_role = role(remote)

        if local_role == "distribution":
            dist = local
            other = remote
            other_role = remote_role
        elif remote_role == "distribution":
            dist = remote
            other = local
            other_role = local_role
        else:
            continue

        dist_rows[dist].append(r)

        if other_role == "core":
            core_rows[dist].append(r)
        else:
            access_rows[dist].append(r)

    seen_cores = set()
    for items in core_rows.values():
        for r in items:
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")
            core = local if role(local) == "core" else remote
            if core:
                seen_cores.add(core)

    for idx, core in enumerate(sorted(seen_cores)):
        x, y = core_positions.get(core.lower(), (560 + idx * 340, y_core))
        nodes[slug(core)] = {
            "id": slug(core),
            "label": core,
            "subtitle": "Core",
            "role": "core",
            "status": "up",
            "x": x,
            "y": y,
            "w": 230,
            "h": 58,
            "device_id": None,
        }

    known_dists = [d for d in dist_order if d in dist_rows]
    extra_dists = sorted([d for d in dist_rows if d not in known_dists])
    all_dists = known_dists + extra_dists

    for idx, dist in enumerate(all_dists):
        x = x_start + idx * x_gap

        statuses = [status_class(r.get("last_status")) for r in dist_rows[dist]]
        dist_status = _worst_status(statuses)

        dist_id = slug(dist)
        nodes[dist_id] = {
            "id": dist_id,
            "label": dist,
            "subtitle": f"{pod_name(dist)} distribution",
            "role": "distribution",
            "status": dist_status,
            "x": x,
            "y": y_dist,
            "w": 245,
            "h": 58,
            "device_id": None,
        }

        upstream = core_rows.get(dist, [])

        # Sort so link lanes remain consistent across reloads.
        upstream = sorted(
            upstream,
            key=lambda r: (
                str(r.get("local_device") or ""),
                str(r.get("local_port") or ""),
                str(r.get("remote_device") or ""),
                str(r.get("remote_port") or ""),
            ),
        )

        for link_idx, r in enumerate(upstream):
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")
            core = local if role(local) == "core" else remote
            if not core:
                continue

            # For dual links, use clear left/right lanes.
            lane = (link_idx - ((len(upstream) - 1) / 2)) * 18

            links.append({
                "source": slug(core),
                "target": dist_id,
                "status": status_class(r.get("last_status")),
                "local_port": r.get("local_port"),
                "remote_port": r.get("remote_port"),
                "lane": lane,
            })

        access_items = access_rows.get(dist, [])
        if access_items:
            agg_id = f"{dist_id}-access-summary"
            agg_status = _worst_status([status_class(r.get("last_status")) for r in access_items])

            nodes[agg_id] = {
                "id": agg_id,
                "label": "Access / Leaf",
                "subtitle": _access_summary_label(access_items),
                "role": "access",
                "status": agg_status,
                "x": x,
                "y": y_access,
                "w": 245,
                "h": 58,
                "device_id": None,
            }

            links.append({
                "source": dist_id,
                "target": agg_id,
                "status": agg_status,
                "local_port": "",
                "remote_port": f"{len(access_items)} learned links",
                "lane": 0,
            })

    return list(nodes.values()), links, summary


# ---------------------------------------------------------------------------
# Interactive node rendering override:
# nodes carry detail JSON so clicking can show a side panel.
# ---------------------------------------------------------------------------

def node_html(node: dict[str, Any]) -> str:
    classes = [
        "nf-node",
        f"nf-role-{h(node.get('role'))}",
        f"nf-status-{h(node.get('status', 'up'))}",
    ]

    style = f"--x:{node['x']};--y:{node['y']};--w:{node.get('w', 150)};"
    subtitle = node.get("subtitle") or ""
    detail = node.get("detail") or {}
    detail_json = h(json.dumps(detail))

    return f"""
    <div
      class="{' '.join(classes)}"
      style="{h(style)}"
      data-node="{h(node['id'])}"
      data-role="{h(node.get('role'))}"
      data-detail="{detail_json}"
      title="{h(node.get('label'))}"
    >
      <strong>{h(node.get("label"))}</strong>
      <small>{h(subtitle)}</small>
    </div>
    """


def _endpoint_for_role(row: dict[str, Any], wanted_role: str) -> tuple[str, str]:
    local = str(row.get("local_device") or "")
    remote = str(row.get("remote_device") or "")

    if role(local) == wanted_role:
        return local, str(row.get("local_port") or "")
    if role(remote) == wanted_role:
        return remote, str(row.get("remote_port") or "")

    return "", ""


def _other_endpoint(row: dict[str, Any], device: str) -> tuple[str, str]:
    local = str(row.get("local_device") or "")
    remote = str(row.get("remote_device") or "")

    if local == device:
        return remote, str(row.get("remote_port") or "")
    return local, str(row.get("local_port") or "")


def _link_detail(row: dict[str, Any], dist: str = "") -> dict[str, Any]:
    other, other_port = _other_endpoint(row, dist) if dist else ("", "")
    return {
        "local_device": row.get("local_device"),
        "local_port": row.get("local_port"),
        "remote_device": row.get("remote_device"),
        "remote_port": row.get("remote_port"),
        "status": status_class(row.get("last_status")),
        "last_seen": str(row.get("last_seen") or ""),
        "other_device": other,
        "other_port": other_port,
    }


def _access_summary_detail(dist: str, access_items: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"up": 0, "down": 0, "missing": 0, "unknown": 0}
    leaves = []

    for row in access_items:
        st = status_class(row.get("last_status"))
        counts[st] = counts.get(st, 0) + 1
        leaf, leaf_port = _other_endpoint(row, dist)
        leaves.append({
            "device": leaf,
            "port": leaf_port,
            "status": st,
            "last_seen": str(row.get("last_seen") or ""),
            "local_device": row.get("local_device"),
            "local_port": row.get("local_port"),
            "remote_device": row.get("remote_device"),
            "remote_port": row.get("remote_port"),
        })

    leaves.sort(key=lambda x: (x["status"] != "missing", x["status"] != "down", x["device"]))

    return {
        "title": f"Access / Leaf under {dist}",
        "type": "access-summary",
        "dist": dist,
        "counts": counts,
        "total": len(access_items),
        "leaves": leaves,
    }


def _dist_detail(dist: str, upstream: list[dict[str, Any]], access_items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "title": dist,
        "type": "distribution",
        "pod": pod_name(dist),
        "core_links": [_link_detail(r, dist) for r in upstream],
        "access_total": len(access_items),
        "access_counts": _access_summary_detail(dist, access_items)["counts"],
    }


def build_mist_flow_fast():
    rows = mist_expected_links()
    summary = flow_summary_from_links(rows)

    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    dist_order = [
        "dist-eastpod-dfl",
        "dist-eastpod-twilight",
        "dist-northpod-carr",
        "dist-northpod-mbh",
        "dist-southpod-emmaw",
        "dist-southpod-mac",
        "dist-westpod-stewart",
        "dist-westpod-voter",
        "dist-testpod-1",
        "dist-testpod-2",
    ]

    # More breathing room. The browser has pixels. Let's use them like adults.
    x_start = 90
    x_gap = 360

    y_core = 70
    y_dist = 360
    y_access = 535

    core_positions = {
        "fabric-core-dfl": (740, y_core),
        "fabric-core-voter": (1420, y_core),
        "dfl-core.middlebury.edu": (1980, y_core),
        "vtr-core.middlebury.edu": (2280, y_core),
    }

    dist_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    core_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    access_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for r in rows:
        local = str(r.get("local_device") or "")
        remote = str(r.get("remote_device") or "")
        local_role = role(local)
        remote_role = role(remote)

        if local_role == "distribution":
            dist = local
            other = remote
            other_role = remote_role
        elif remote_role == "distribution":
            dist = remote
            other = local
            other_role = local_role
        else:
            continue

        dist_rows[dist].append(r)

        if other_role == "core":
            core_rows[dist].append(r)
        else:
            access_rows[dist].append(r)

    seen_cores = set()
    core_detail_links: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for dist, items in core_rows.items():
        for r in items:
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")
            core = local if role(local) == "core" else remote
            if core:
                seen_cores.add(core)
                core_detail_links[core].append(_link_detail(r, core))

    for idx, core in enumerate(sorted(seen_cores)):
        x, y = core_positions.get(core.lower(), (740 + idx * 360, y_core))
        statuses = [d["status"] for d in core_detail_links.get(core, [])]
        nodes[slug(core)] = {
            "id": slug(core),
            "label": core,
            "subtitle": f"{len(core_detail_links.get(core, []))} distribution uplinks",
            "role": "core",
            "status": _worst_status(statuses) if statuses else "up",
            "x": x,
            "y": y,
            "w": 260,
            "h": 62,
            "device_id": None,
            "detail": {
                "title": core,
                "type": "core",
                "links": core_detail_links.get(core, []),
            },
        }

    known_dists = [d for d in dist_order if d in dist_rows]
    extra_dists = sorted([d for d in dist_rows if d not in known_dists])
    all_dists = known_dists + extra_dists

    for idx, dist in enumerate(all_dists):
        x = x_start + idx * x_gap

        upstream = sorted(
            core_rows.get(dist, []),
            key=lambda r: (
                str(r.get("local_device") or ""),
                str(r.get("local_port") or ""),
                str(r.get("remote_device") or ""),
                str(r.get("remote_port") or ""),
            ),
        )
        access_items = access_rows.get(dist, [])

        statuses = [status_class(r.get("last_status")) for r in dist_rows[dist]]
        dist_status = _worst_status(statuses)

        dist_id = slug(dist)
        nodes[dist_id] = {
            "id": dist_id,
            "label": dist,
            "subtitle": f"{pod_name(dist)} distribution",
            "role": "distribution",
            "status": dist_status,
            "x": x,
            "y": y_dist,
            "w": 285,
            "h": 62,
            "device_id": None,
            "detail": _dist_detail(dist, upstream, access_items),
        }

        # Each upstream link stays separate.
        for link_idx, r in enumerate(upstream):
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")
            core = local if role(local) == "core" else remote
            if not core:
                continue

            lane = (link_idx - ((len(upstream) - 1) / 2)) * 24

            links.append({
                "source": slug(core),
                "target": dist_id,
                "status": status_class(r.get("last_status")),
                "local_port": r.get("local_port"),
                "remote_port": r.get("remote_port"),
                "lane": lane,
            })

        # Access block is clickable and expandable in the side panel.
        if access_items:
            agg_id = f"{dist_id}-access-summary"
            agg_status = _worst_status([status_class(r.get("last_status")) for r in access_items])

            nodes[agg_id] = {
                "id": agg_id,
                "label": "Access / Leaf",
                "subtitle": _access_summary_label(access_items),
                "role": "access",
                "status": agg_status,
                "x": x,
                "y": y_access,
                "w": 285,
                "h": 62,
                "device_id": None,
                "detail": _access_summary_detail(dist, access_items),
            }

            links.append({
                "source": dist_id,
                "target": agg_id,
                "status": agg_status,
                "local_port": "",
                "remote_port": f"{len(access_items)} learned links",
                "lane": 0,
            })

    return list(nodes.values()), links, summary


def render_shell(
    title: str,
    subtitle: str,
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]],
    summary: dict[str, Any],
    module_links: bool = True,
    module: str = "mist",
    editable: bool = False,
) -> str:
    node_map = {n["id"]: n for n in nodes}

    max_x = max([float(n.get("x", 0)) + float(n.get("w", 150)) + 260 for n in nodes] + [1800])
    max_y = max([float(n.get("y", 0)) + 220 for n in nodes] + [900])
    width = int(max_x)
    height = int(max_y)

    nav = ""
    if module_links:
        nav = """
        <div class="nf-module-nav">
          <a href="/tools/network-flow/mist">Mist Fabric</a>
          <a href="/tools/network-flow/edge">Edge</a>
          <a href="/tools/network-flow/datacenter">Old Datacenter</a>
          <a href="/tools/network-flow/hci">HCI Cluster</a>
          <a href="/tv/network-flow">TV Dashboard</a>
        </div>
        """

    edit_url = f"/tools/network-flow/{module}?edit=1"
    view_url = f"/tools/network-flow/{module}"

    edit_controls = ""
    if editable:
        edit_controls = f"""
        <button class="button" id="nf-save-layout">Save Layout</button>
        <button class="button" id="nf-reset-layout">Reset Layout</button>
        <a class="button" href="{h(view_url)}">View Mode</a>
        """
    else:
        edit_controls = f"""
        <a class="button" href="{h(edit_url)}">Edit Layout</a>
        """

    return f"""
    <div class="nf-page" data-module="{h(module)}" data-editable="{str(editable).lower()}">
      <div class="nf-topbar">
        <div>
          <h1>{h(title)}</h1>
          <p>{h(subtitle)}</p>
        </div>
        <div class="nf-actions">
          <button class="button" id="nf-fit">Fit</button>
          <button class="button" id="nf-zoom-out">−</button>
          <button class="button" id="nf-zoom-in">+</button>
          <button class="button" id="nf-compact">Compact</button>
          {edit_controls}
        </div>
      </div>

      {nav}

      <div class="nf-summary">
        <div class="nf-stat nf-stat-problems"><span>Problems</span><strong>{h(summary.get("problems", 0))}</strong></div>
        <div class="nf-stat"><span>Total Links</span><strong>{h(summary.get("total", 0))}</strong></div>
        <div class="nf-stat nf-stat-up"><span>Up</span><strong>{h(summary.get("up", 0))}</strong></div>
        <div class="nf-stat nf-stat-down"><span>Down</span><strong>{h(summary.get("down", 0))}</strong></div>
        <div class="nf-stat nf-stat-missing"><span>Missing</span><strong>{h(summary.get("missing", 0))}</strong></div>
        <div class="nf-stat nf-stat-unknown"><span>Unknown</span><strong>{h(summary.get("unknown", 0))}</strong></div>
      </div>

      <div class="nf-edit-help {'is-visible' if editable else ''}">
        Drag nodes to rearrange the module. Click <strong>Save Layout</strong> when it stops looking like a topology map drawn during an outage.
      </div>

      <div class="nf-layout">
        <div class="nf-canvas-wrap">
          <div class="nf-zoom-stage">
            <div class="nf-canvas" style="--canvas-w:{width}px;--canvas-h:{height}px;">
              <svg class="nf-svg" viewBox="0 0 {width} {height}" preserveAspectRatio="xMinYMin meet">
                {''.join(path_html(l, node_map) for l in links)}
              </svg>

              {''.join(node_html(n) for n in nodes)}
            </div>
          </div>
        </div>

        <aside class="nf-detail-panel" id="nf-detail-panel">
          <div class="nf-detail-empty">
            <h2>Node Details</h2>
            <p>Click a core, distribution switch, or access block to inspect links.</p>
          </div>
        </aside>
      </div>
    </div>

    <script>
    (() => {{
      const page = document.querySelector(".nf-page");
      const stage = document.querySelector(".nf-zoom-stage");
      const canvas = document.querySelector(".nf-canvas");
      const wrap = document.querySelector(".nf-canvas-wrap");
      const panel = document.getElementById("nf-detail-panel");
      if (!page || !stage || !canvas || !wrap || !panel) return;

      const editable = page.dataset.editable === "true";
      const moduleName = page.dataset.module || "mist";
      let zoom = 1;

      const esc = (s) => String(s ?? "").replace(/[&<>"']/g, ch => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }}[ch]));

      const pill = (status) => `<span class="nf-detail-pill nf-detail-${{esc(status || "unknown")}}">${{esc(status || "unknown")}}</span>`;

      const linkRow = (l) => `
        <div class="nf-detail-link">
          <div>${{pill(l.status)}}</div>
          <div>
            <strong>${{esc(l.local_device)}} <code>${{esc(l.local_port)}}</code></strong>
            <span>⇄</span>
            <strong>${{esc(l.remote_device)}} <code>${{esc(l.remote_port)}}</code></strong>
            <small>Last seen: ${{esc(l.last_seen)}}</small>
          </div>
        </div>
      `;

      const leafRow = (l) => `
        <div class="nf-detail-leaf nf-detail-leaf-${{esc(l.status)}}">
          <div>
            <strong>${{esc(l.device)}}</strong>
            <code>${{esc(l.port)}}</code>
          </div>
          ${{pill(l.status)}}
        </div>
      `;

      const renderDetail = (detail) => {{
        if (!detail || !detail.type) {{
          panel.innerHTML = `<div class="nf-detail-empty"><h2>Node Details</h2><p>No detail available.</p></div>`;
          return;
        }}

        if (detail.type === "leaf-redundancy") {{
          const counts = detail.counts || {{}};
          const leafs = detail.leafs || [];

          const uplinkRow = (u) => `
            <div class="nf-detail-uplink">
              ${{pill(u.status)}}
              <span>${{esc(u.dist)}}</span>
              <code>${{esc(u.leaf_port)}}</code>
            </div>
          `;

          const leafCard = (leaf) => `
            <details class="nf-leaf-card nf-leaf-${{esc(leaf.health)}}" ${{leaf.health !== "up" ? "open" : ""}}>
              <summary>
                <span>
                  <strong>${{esc(leaf.device)}}</strong>
                  <small>${{esc(leaf.up_count)}}/${{esc(leaf.expected_count)}} uplinks up</small>
                </span>
                ${{pill(leaf.health)}}
              </summary>
              <div class="nf-leaf-uplinks">
                ${{(leaf.uplinks || []).map(uplinkRow).join("")}}
              </div>
            </details>
          `;

          panel.innerHTML = `
            <div class="nf-detail-head">
              <h2>${{esc(detail.title)}}</h2>
              <p>${{esc(detail.total_leafs)}} leaf/access switches grouped by redundancy.</p>
            </div>
            <div class="nf-detail-counts">
              <span>${{counts.dual_homed || 0}} dual-homed</span>
              <span>${{counts.single_homed || 0}} single-homed</span>
              <span>${{counts.degraded || 0}} degraded</span>
              <span>${{counts.critical || 0}} critical</span>
            </div>
            <div class="nf-detail-leaf-list">
              ${{leafs.length ? leafs.map(leafCard).join("") : "<p>No leaf switches learned.</p>"}}
            </div>
          `;
          return;
        }}

        if (detail.type === "access-summary") {{
          const counts = detail.counts || {{}};
          const leaves = detail.leaves || [];
          panel.innerHTML = `
            <div class="nf-detail-head">
              <h2>${{esc(detail.title)}}</h2>
              <p>${{esc(detail.total)}} learned access links</p>
            </div>
            <div class="nf-detail-counts">
              <span>${{counts.up || 0}} up</span>
              <span>${{counts.down || 0}} down</span>
              <span>${{counts.missing || 0}} missing</span>
              <span>${{counts.unknown || 0}} unknown</span>
            </div>
            <details open class="nf-detail-expand">
              <summary>Leaf switches</summary>
              <div class="nf-detail-leaf-list">
                ${{leaves.map(leafRow).join("")}}
              </div>
            </details>
          `;
          return;
        }}

        if (detail.type === "distribution") {{
          const coreLinks = detail.core_links || [];
          const counts = detail.access_counts || {{}};
          panel.innerHTML = `
            <div class="nf-detail-head">
              <h2>${{esc(detail.title)}}</h2>
              <p>${{esc(detail.pod)}} distribution</p>
            </div>
            <h3>Core uplinks</h3>
            <div class="nf-detail-links">
              ${{coreLinks.length ? coreLinks.map(linkRow).join("") : "<p>No core links learned.</p>"}}
            </div>
            <h3>Access summary</h3>
            <div class="nf-detail-counts">
              <span>${{detail.access_total || 0}} total</span>
              <span>${{counts.up || 0}} up</span>
              <span>${{counts.down || 0}} down</span>
              <span>${{counts.missing || 0}} missing</span>
              <span>${{counts.unknown || 0}} unknown</span>
            </div>
          `;
          return;
        }}

        if (detail.type === "core") {{
          const links = detail.links || [];
          panel.innerHTML = `
            <div class="nf-detail-head">
              <h2>${{esc(detail.title)}}</h2>
              <p>${{links.length}} distribution uplinks</p>
            </div>
            <div class="nf-detail-links">
              ${{links.length ? links.map(linkRow).join("") : "<p>No links learned.</p>"}}
            </div>
          `;
          return;
        }}

        panel.innerHTML = `<div class="nf-detail-empty"><h2>${{esc(detail.title || "Node Details")}}</h2><p>No renderer for this detail type.</p></div>`;
      }};

      const setZoom = (z) => {{
        zoom = Math.max(0.25, Math.min(1.75, z));
        stage.style.transform = `scale(${{zoom}})`;
        stage.style.transformOrigin = "top left";
        wrap.dataset.zoom = zoom.toFixed(2);
      }};

      const fit = () => {{
        const canvasW = canvas.offsetWidth || 1800;
        const wrapW = wrap.clientWidth || window.innerWidth;
        const z = Math.min(1, Math.max(0.25, (wrapW - 24) / canvasW));
        setZoom(z);
      }};

      document.getElementById("nf-fit")?.addEventListener("click", fit);
      document.getElementById("nf-zoom-in")?.addEventListener("click", () => setZoom(zoom + 0.1));
      document.getElementById("nf-zoom-out")?.addEventListener("click", () => setZoom(zoom - 0.1));
      document.getElementById("nf-compact")?.addEventListener("click", () => page.classList.toggle("nf-compact-mode"));

      let hotEls = [];

      const clear = () => {{
        hotEls.forEach(el => el.classList.remove("nf-hot"));
        hotEls = [];
      }};

      const hotNode = (node) => {{
        clear();
        const id = node.dataset.node;
        node.classList.add("nf-hot");
        hotEls.push(node);

        canvas.querySelectorAll(`.nf-link[data-source="${{id}}"], .nf-link[data-target="${{id}}"]`).forEach(link => {{
          link.classList.add("nf-hot");
          hotEls.push(link);

          const other = link.dataset.source === id ? link.dataset.target : link.dataset.source;
          const otherNode = canvas.querySelector(`.nf-node[data-node="${{other}}"]`);
          if (otherNode) {{
            otherNode.classList.add("nf-hot");
            hotEls.push(otherNode);
          }}
        }});
      }};

      canvas.querySelectorAll(".nf-node").forEach(node => {{
        node.addEventListener("mouseenter", () => hotNode(node));
        node.addEventListener("mouseleave", clear);
        node.addEventListener("click", () => {{
          canvas.querySelectorAll(".nf-node").forEach(n => n.classList.remove("nf-selected"));
          node.classList.add("nf-selected");
          try {{
            renderDetail(JSON.parse(node.dataset.detail || "{{}}"));
          }} catch (e) {{
            renderDetail(null);
          }}
        }});
      }});

      if (editable) {{
        canvas.classList.add("nf-editable");
        let dragging = null;

        const getPoint = (ev) => {{
          const rect = canvas.getBoundingClientRect();
          return {{
            x: (ev.clientX - rect.left) / zoom,
            y: (ev.clientY - rect.top) / zoom,
          }};
        }};

        canvas.querySelectorAll(".nf-node").forEach(node => {{
          node.addEventListener("pointerdown", (ev) => {{
            ev.preventDefault();
            const pt = getPoint(ev);
            dragging = {{
              node,
              startX: pt.x,
              startY: pt.y,
              origX: parseFloat(node.style.getPropertyValue("--x") || "0"),
              origY: parseFloat(node.style.getPropertyValue("--y") || "0"),
            }};
            node.setPointerCapture(ev.pointerId);
            node.classList.add("nf-dragging");
          }});

          node.addEventListener("pointermove", (ev) => {{
            if (!dragging || dragging.node !== node) return;
            const pt = getPoint(ev);
            const dx = pt.x - dragging.startX;
            const dy = pt.y - dragging.startY;
            const nx = Math.max(0, Math.round(dragging.origX + dx));
            const ny = Math.max(0, Math.round(dragging.origY + dy));
            node.style.setProperty("--x", nx);
            node.style.setProperty("--y", ny);
          }});

          node.addEventListener("pointerup", () => {{
            if (!dragging || dragging.node !== node) return;
            node.classList.remove("nf-dragging");
            dragging = null;
          }});
        }});

        document.getElementById("nf-save-layout")?.addEventListener("click", async () => {{
          const positions = Array.from(canvas.querySelectorAll(".nf-node")).map(node => ({{
            node_id: node.dataset.node,
            x: parseFloat(node.style.getPropertyValue("--x") || "0"),
            y: parseFloat(node.style.getPropertyValue("--y") || "0"),
            w: parseFloat(node.style.getPropertyValue("--w") || "150"),
          }}));

          const res = await fetch(`/api/network-flow/${{moduleName}}/positions`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{positions}})
          }});

          const data = await res.json();
          alert(`Saved ${{data.saved || 0}} node positions`);
        }});

        document.getElementById("nf-reset-layout")?.addEventListener("click", async () => {{
          if (!confirm("Reset saved node positions for this module?")) return;
          await fetch(`/api/network-flow/${{moduleName}}/positions/reset`, {{method: "POST"}});
          window.location.reload();
        }});
      }}

      setTimeout(fit, 150);
    }})();
    </script>
    """


# ---------------------------------------------------------------------------
# Mist fabric model override:
# represent access/leaf as shared pod layers, not one access block per dist.
# This reflects dual-homed leafs: leaf -> dist A and leaf -> dist B.
# ---------------------------------------------------------------------------

def _leaf_name_from_access_link(row: dict[str, Any], dist: str) -> tuple[str, str]:
    other, other_port = _other_endpoint(row, dist)
    return other, other_port


def _leaf_redundancy_detail(pod: str, pod_access_rows: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    leafs: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for dist, row in pod_access_rows:
        leaf, leaf_port = _leaf_name_from_access_link(row, dist)
        if not leaf:
            continue

        leafs[leaf].append({
            "dist": dist,
            "leaf_port": leaf_port,
            "status": status_class(row.get("last_status")),
            "last_seen": str(row.get("last_seen") or ""),
            "local_device": row.get("local_device"),
            "local_port": row.get("local_port"),
            "remote_device": row.get("remote_device"),
            "remote_port": row.get("remote_port"),
        })

    leaf_rows = []
    counts = {"dual_homed": 0, "single_homed": 0, "degraded": 0, "critical": 0, "up": 0}

    for leaf, uplinks in leafs.items():
        statuses = [u["status"] for u in uplinks]
        up_count = sum(1 for s in statuses if s == "up")
        expected_count = max(2, len(uplinks))
        health = "up"

        if up_count >= 2:
            counts["dual_homed"] += 1
            counts["up"] += 1
            health = "up"
        elif up_count == 1:
            counts["single_homed"] += 1
            counts["degraded"] += 1
            health = "down"
        else:
            counts["critical"] += 1
            health = "missing"

        leaf_rows.append({
            "device": leaf,
            "health": health,
            "up_count": up_count,
            "expected_count": expected_count,
            "uplinks": sorted(uplinks, key=lambda x: x.get("dist") or ""),
        })

    leaf_rows.sort(key=lambda x: (
        x["health"] == "up",
        x["device"],
    ))

    return {
        "title": f"{pod} Access / Leaf Redundancy",
        "type": "leaf-redundancy",
        "pod": pod,
        "total_leafs": len(leaf_rows),
        "counts": counts,
        "leafs": leaf_rows,
    }


def _pod_access_subtitle(detail: dict[str, Any]) -> str:
    counts = detail.get("counts") or {}
    return (
        f"{detail.get('total_leafs', 0)} leafs · "
        f"{counts.get('dual_homed', 0)} dual · "
        f"{counts.get('degraded', 0)} degraded · "
        f"{counts.get('critical', 0)} critical"
    )


def _pod_access_status(detail: dict[str, Any]) -> str:
    counts = detail.get("counts") or {}
    if counts.get("critical", 0):
        return "missing"
    if counts.get("degraded", 0) or counts.get("single_homed", 0):
        return "down"
    return "up"


def build_mist_flow_fast():
    rows = mist_expected_links()
    summary = flow_summary_from_links(rows)

    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    dist_order = [
        "dist-eastpod-dfl",
        "dist-eastpod-twilight",
        "dist-northpod-carr",
        "dist-northpod-mbh",
        "dist-southpod-emmaw",
        "dist-southpod-mac",
        "dist-westpod-stewart",
        "dist-westpod-voter",
        "dist-testpod-1",
        "dist-testpod-2",
    ]

    x_start = 100
    x_gap = 390

    y_core = 70
    y_dist = 360
    y_leaf = 555

    core_positions = {
        "fabric-core-dfl": (850, y_core),
        "fabric-core-voter": (1580, y_core),
        "dfl-core.middlebury.edu": (2200, y_core),
        "vtr-core.middlebury.edu": (2540, y_core),
    }

    dist_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    core_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    access_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for r in rows:
        local = str(r.get("local_device") or "")
        remote = str(r.get("remote_device") or "")
        local_role = role(local)
        remote_role = role(remote)

        if local_role == "distribution":
            dist = local
            other_role = remote_role
        elif remote_role == "distribution":
            dist = remote
            other_role = local_role
        else:
            continue

        dist_rows[dist].append(r)

        if other_role == "core":
            core_rows[dist].append(r)
        else:
            access_rows[dist].append(r)

    # Core nodes.
    seen_cores = set()
    core_detail_links: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for dist, items in core_rows.items():
        for r in items:
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")
            core = local if role(local) == "core" else remote
            if core:
                seen_cores.add(core)
                core_detail_links[core].append(_link_detail(r, core))

    for idx, core in enumerate(sorted(seen_cores)):
        x, y = core_positions.get(core.lower(), (850 + idx * 380, y_core))
        statuses = [d["status"] for d in core_detail_links.get(core, [])]
        nodes[slug(core)] = {
            "id": slug(core),
            "label": core,
            "subtitle": f"{len(core_detail_links.get(core, []))} dist uplinks",
            "role": "core",
            "status": _worst_status(statuses) if statuses else "up",
            "x": x,
            "y": y,
            "w": 285,
            "h": 64,
            "device_id": None,
            "detail": {
                "title": core,
                "type": "core",
                "links": core_detail_links.get(core, []),
            },
        }

    known_dists = [d for d in dist_order if d in dist_rows]
    extra_dists = sorted([d for d in dist_rows if d not in known_dists])
    all_dists = known_dists + extra_dists

    dist_x: dict[str, int] = {}

    for idx, dist in enumerate(all_dists):
        x = x_start + idx * x_gap
        dist_x[dist] = x

        upstream = sorted(
            core_rows.get(dist, []),
            key=lambda r: (
                str(r.get("local_device") or ""),
                str(r.get("local_port") or ""),
                str(r.get("remote_device") or ""),
                str(r.get("remote_port") or ""),
            ),
        )
        access_items = access_rows.get(dist, [])

        statuses = [status_class(r.get("last_status")) for r in dist_rows[dist]]
        dist_status = _worst_status(statuses)

        dist_id = slug(dist)
        nodes[dist_id] = {
            "id": dist_id,
            "label": dist,
            "subtitle": f"{pod_name(dist)} distribution",
            "role": "distribution",
            "status": dist_status,
            "x": x,
            "y": y_dist,
            "w": 300,
            "h": 64,
            "device_id": None,
            "detail": _dist_detail(dist, upstream, access_items),
        }

        # Separate upstream links.
        for link_idx, r in enumerate(upstream):
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")
            core = local if role(local) == "core" else remote
            if not core:
                continue

            lane = (link_idx - ((len(upstream) - 1) / 2)) * 28

            links.append({
                "source": slug(core),
                "target": dist_id,
                "status": status_class(r.get("last_status")),
                "local_port": r.get("local_port"),
                "remote_port": r.get("remote_port"),
                "lane": lane,
            })

    # Shared leaf/access layer per pod. This is the missing redundancy model.
    pod_access: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    pod_dists: dict[str, list[str]] = defaultdict(list)

    for dist in all_dists:
        pod = pod_name(dist)
        if dist not in pod_dists[pod]:
            pod_dists[pod].append(dist)
        for r in access_rows.get(dist, []):
            pod_access[pod].append((dist, r))

    for pod, items in pod_access.items():
        dists = [d for d in pod_dists.get(pod, []) if d in dist_x]
        if not dists:
            continue

        min_x = min(dist_x[d] for d in dists)
        max_x = max(dist_x[d] for d in dists)
        center_x = int((min_x + max_x) / 2)

        detail = _leaf_redundancy_detail(pod, items)
        access_status = _pod_access_status(detail)
        access_id = f"{slug(pod)}-leaf-redundancy"

        nodes[access_id] = {
            "id": access_id,
            "label": f"{pod} Leaf Layer",
            "subtitle": _pod_access_subtitle(detail),
            "role": "access",
            "status": access_status,
            "x": center_x,
            "y": y_leaf,
            "w": 330,
            "h": 70,
            "device_id": None,
            "detail": detail,
        }

        # Dist to shared leaf layer. One visible link from each dist into the
        # shared layer, status is worst status of that dist's leaf uplinks.
        for dist in dists:
            dist_access_status = _worst_status([
                status_class(r.get("last_status"))
                for r in access_rows.get(dist, [])
            ]) if access_rows.get(dist) else "unknown"

            lane = (dists.index(dist) - ((len(dists) - 1) / 2)) * 24

            links.append({
                "source": slug(dist),
                "target": access_id,
                "status": dist_access_status,
                "local_port": "",
                "remote_port": f"{len(access_rows.get(dist, []))} leaf uplinks",
                "lane": lane,
            })

    return list(nodes.values()), links, summary


# ---------------------------------------------------------------------------
# Mist fabric model override:
# show real leaf/access switches underneath each POD leaf layer.
# Each leaf gets individual uplink paths back to its distribution switches.
# ---------------------------------------------------------------------------

def _leaf_health_from_uplinks(uplinks: list[dict[str, Any]]) -> str:
    statuses = [status_class(u.get("status")) for u in uplinks]
    up_count = sum(1 for s in statuses if s == "up")

    if up_count >= 2:
        return "up"
    if up_count == 1:
        return "down"
    return "missing"


def _leaf_node_detail(leaf: str, uplinks: list[dict[str, Any]]) -> dict[str, Any]:
    up_count = sum(1 for u in uplinks if status_class(u.get("status")) == "up")
    expected_count = max(2, len(uplinks))

    return {
        "title": leaf,
        "type": "leaf-redundancy",
        "pod": "",
        "total_leafs": 1,
        "counts": {
            "dual_homed": 1 if up_count >= 2 else 0,
            "single_homed": 1 if up_count == 1 else 0,
            "degraded": 1 if up_count == 1 else 0,
            "critical": 1 if up_count == 0 else 0,
        },
        "leafs": [{
            "device": leaf,
            "health": _leaf_health_from_uplinks(uplinks),
            "up_count": up_count,
            "expected_count": expected_count,
            "uplinks": sorted(uplinks, key=lambda x: x.get("dist") or ""),
        }],
    }


def build_mist_flow_fast():
    rows = mist_expected_links()
    summary = flow_summary_from_links(rows)

    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    dist_order = [
        "dist-eastpod-dfl",
        "dist-eastpod-twilight",
        "dist-northpod-carr",
        "dist-northpod-mbh",
        "dist-southpod-emmaw",
        "dist-southpod-mac",
        "dist-westpod-stewart",
        "dist-westpod-voter",
        "dist-testpod-1",
        "dist-testpod-2",
    ]

    x_start = 100
    x_gap = 420

    y_core = 70
    y_dist = 360
    y_pod_leaf = 555
    y_leaf_start = 720
    leaf_x_gap = 170
    leaf_y_gap = 86
    leafs_per_row = 5

    core_positions = {
        "fabric-core-dfl": (900, y_core),
        "fabric-core-voter": (1660, y_core),
        "dfl-core.middlebury.edu": (2300, y_core),
        "vtr-core.middlebury.edu": (2640, y_core),
    }

    dist_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    core_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    access_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for r in rows:
        local = str(r.get("local_device") or "")
        remote = str(r.get("remote_device") or "")
        local_role = role(local)
        remote_role = role(remote)

        if local_role == "distribution":
            dist = local
            other_role = remote_role
        elif remote_role == "distribution":
            dist = remote
            other_role = local_role
        else:
            continue

        dist_rows[dist].append(r)

        if other_role == "core":
            core_rows[dist].append(r)
        else:
            access_rows[dist].append(r)

    # Core nodes.
    seen_cores = set()
    core_detail_links: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for dist, items in core_rows.items():
        for r in items:
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")
            core = local if role(local) == "core" else remote
            if core:
                seen_cores.add(core)
                core_detail_links[core].append(_link_detail(r, core))

    for idx, core in enumerate(sorted(seen_cores)):
        x, y = core_positions.get(core.lower(), (900 + idx * 380, y_core))
        statuses = [d["status"] for d in core_detail_links.get(core, [])]

        nodes[slug(core)] = {
            "id": slug(core),
            "label": core,
            "subtitle": f"{len(core_detail_links.get(core, []))} dist uplinks",
            "role": "core",
            "status": _worst_status(statuses) if statuses else "up",
            "x": x,
            "y": y,
            "w": 300,
            "h": 64,
            "device_id": None,
            "detail": {
                "title": core,
                "type": "core",
                "links": core_detail_links.get(core, []),
            },
        }

    known_dists = [d for d in dist_order if d in dist_rows]
    extra_dists = sorted([d for d in dist_rows if d not in known_dists])
    all_dists = known_dists + extra_dists

    dist_x: dict[str, int] = {}

    for idx, dist in enumerate(all_dists):
        x = x_start + idx * x_gap
        dist_x[dist] = x

        upstream = sorted(
            core_rows.get(dist, []),
            key=lambda r: (
                str(r.get("local_device") or ""),
                str(r.get("local_port") or ""),
                str(r.get("remote_device") or ""),
                str(r.get("remote_port") or ""),
            ),
        )
        access_items = access_rows.get(dist, [])

        statuses = [status_class(r.get("last_status")) for r in dist_rows[dist]]
        dist_status = _worst_status(statuses)

        dist_id = slug(dist)
        nodes[dist_id] = {
            "id": dist_id,
            "label": dist,
            "subtitle": f"{pod_name(dist)} distribution",
            "role": "distribution",
            "status": dist_status,
            "x": x,
            "y": y_dist,
            "w": 320,
            "h": 64,
            "device_id": None,
            "detail": _dist_detail(dist, upstream, access_items),
        }

        # Core -> distribution links. Keep both explicit.
        for link_idx, r in enumerate(upstream):
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")
            core = local if role(local) == "core" else remote
            if not core:
                continue

            lane = (link_idx - ((len(upstream) - 1) / 2)) * 30

            links.append({
                "source": slug(core),
                "target": dist_id,
                "status": status_class(r.get("last_status")),
                "local_port": r.get("local_port"),
                "remote_port": r.get("remote_port"),
                "lane": lane,
            })

    # Build shared leaf data by POD.
    pod_access: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    pod_dists: dict[str, list[str]] = defaultdict(list)

    for dist in all_dists:
        pod = pod_name(dist)
        if dist not in pod_dists[pod]:
            pod_dists[pod].append(dist)
        for r in access_rows.get(dist, []):
            pod_access[pod].append((dist, r))

    for pod, items in pod_access.items():
        dists = [d for d in pod_dists.get(pod, []) if d in dist_x]
        if not dists:
            continue

        min_x = min(dist_x[d] for d in dists)
        max_x = max(dist_x[d] for d in dists)
        center_x = int((min_x + max_x) / 2)

        detail = _leaf_redundancy_detail(pod, items)
        access_status = _pod_access_status(detail)
        pod_layer_id = f"{slug(pod)}-leaf-redundancy"

        nodes[pod_layer_id] = {
            "id": pod_layer_id,
            "label": f"{pod} Leaf Layer",
            "subtitle": _pod_access_subtitle(detail),
            "role": "access",
            "status": access_status,
            "x": center_x,
            "y": y_pod_leaf,
            "w": 360,
            "h": 72,
            "device_id": None,
            "detail": detail,
        }

        # Group actual leaf uplinks.
        leaf_uplinks: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for dist, row in items:
            leaf, leaf_port = _leaf_name_from_access_link(row, dist)
            if not leaf:
                continue

            leaf_uplinks[leaf].append({
                "dist": dist,
                "leaf_port": leaf_port,
                "status": status_class(row.get("last_status")),
                "last_seen": str(row.get("last_seen") or ""),
                "local_device": row.get("local_device"),
                "local_port": row.get("local_port"),
                "remote_device": row.get("remote_device"),
                "remote_port": row.get("remote_port"),
            })

        # Sort problem leafs first, then name.
        leaf_names = sorted(
            leaf_uplinks,
            key=lambda leaf: (
                _leaf_health_from_uplinks(leaf_uplinks[leaf]) == "up",
                leaf,
            ),
        )

        pod_width = max(leafs_per_row * leaf_x_gap, 760)
        leaf_start_x = center_x - int(pod_width / 2)

        for idx, leaf in enumerate(leaf_names):
            uplinks = leaf_uplinks[leaf]
            health = _leaf_health_from_uplinks(uplinks)

            row_num = idx // leafs_per_row
            col_num = idx % leafs_per_row

            leaf_x = leaf_start_x + col_num * leaf_x_gap
            leaf_y = y_leaf_start + row_num * leaf_y_gap

            leaf_id = slug(leaf)

            up_count = sum(1 for u in uplinks if status_class(u.get("status")) == "up")
            expected_count = max(2, len(uplinks))

            nodes[leaf_id] = {
                "id": leaf_id,
                "label": leaf,
                "subtitle": f"{up_count}/{expected_count} uplinks up",
                "role": "leaf",
                "status": health,
                "x": leaf_x,
                "y": leaf_y,
                "w": 155,
                "h": 56,
                "device_id": None,
                "detail": _leaf_node_detail(leaf, uplinks),
            }

            # Pod layer -> leaf node, so the layer visibly expands downward.
            links.append({
                "source": pod_layer_id,
                "target": leaf_id,
                "status": health,
                "local_port": "",
                "remote_port": f"{up_count}/{expected_count}",
                "lane": 0,
            })

            # Actual distribution -> leaf uplinks. These show the redundancy.
            # Two uplinks should draw as two visible strands into each leaf.
            for link_idx, u in enumerate(sorted(uplinks, key=lambda x: x.get("dist") or "")):
                lane = (link_idx - ((len(uplinks) - 1) / 2)) * 22
                links.append({
                    "source": slug(u["dist"]),
                    "target": leaf_id,
                    "status": status_class(u.get("status")),
                    "local_port": "",
                    "remote_port": u.get("leaf_port"),
                    "lane": lane,
                })

    return list(nodes.values()), links, summary


# ---------------------------------------------------------------------------
# Mist fabric model override:
# default is collapsed POD leaf layers.
# optional expanded leaf view with show_leafs=True.
# ---------------------------------------------------------------------------

def build_mist_flow_fast(show_leafs: bool = False):
    rows = mist_expected_links()
    summary = flow_summary_from_links(rows)

    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    dist_order = [
        "dist-eastpod-dfl",
        "dist-eastpod-twilight",
        "dist-northpod-carr",
        "dist-northpod-mbh",
        "dist-southpod-emmaw",
        "dist-southpod-mac",
        "dist-westpod-stewart",
        "dist-westpod-voter",
        "dist-testpod-1",
        "dist-testpod-2",
    ]

    x_start = 100
    x_gap = 420

    y_core = 70
    y_dist = 360
    y_pod_leaf = 555
    y_leaf_start = 735

    leaf_x_gap = 170
    leaf_y_gap = 86
    leafs_per_row = 5

    core_positions = {
        "fabric-core-dfl": (900, y_core),
        "fabric-core-voter": (1660, y_core),
        "dfl-core.middlebury.edu": (2300, y_core),
        "vtr-core.middlebury.edu": (2640, y_core),
    }

    dist_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    core_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    access_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for r in rows:
        local = str(r.get("local_device") or "")
        remote = str(r.get("remote_device") or "")
        local_role = role(local)
        remote_role = role(remote)

        if local_role == "distribution":
            dist = local
            other_role = remote_role
        elif remote_role == "distribution":
            dist = remote
            other_role = local_role
        else:
            continue

        dist_rows[dist].append(r)

        if other_role == "core":
            core_rows[dist].append(r)
        else:
            access_rows[dist].append(r)

    # Core nodes.
    seen_cores = set()
    core_detail_links: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for dist, items in core_rows.items():
        for r in items:
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")
            core = local if role(local) == "core" else remote
            if core:
                seen_cores.add(core)
                core_detail_links[core].append(_link_detail(r, core))

    for idx, core in enumerate(sorted(seen_cores)):
        x, y = core_positions.get(core.lower(), (900 + idx * 380, y_core))
        statuses = [d["status"] for d in core_detail_links.get(core, [])]

        nodes[slug(core)] = {
            "id": slug(core),
            "label": core,
            "subtitle": f"{len(core_detail_links.get(core, []))} dist uplinks",
            "role": "core",
            "status": _worst_status(statuses) if statuses else "up",
            "x": x,
            "y": y,
            "w": 300,
            "h": 64,
            "device_id": None,
            "detail": {
                "title": core,
                "type": "core",
                "links": core_detail_links.get(core, []),
            },
        }

    known_dists = [d for d in dist_order if d in dist_rows]
    extra_dists = sorted([d for d in dist_rows if d not in known_dists])
    all_dists = known_dists + extra_dists

    dist_x: dict[str, int] = {}

    for idx, dist in enumerate(all_dists):
        x = x_start + idx * x_gap
        dist_x[dist] = x

        upstream = sorted(
            core_rows.get(dist, []),
            key=lambda r: (
                str(r.get("local_device") or ""),
                str(r.get("local_port") or ""),
                str(r.get("remote_device") or ""),
                str(r.get("remote_port") or ""),
            ),
        )
        access_items = access_rows.get(dist, [])

        statuses = [status_class(r.get("last_status")) for r in dist_rows[dist]]
        dist_status = _worst_status(statuses)

        dist_id = slug(dist)
        nodes[dist_id] = {
            "id": dist_id,
            "label": dist,
            "subtitle": f"{pod_name(dist)} distribution",
            "role": "distribution",
            "status": dist_status,
            "x": x,
            "y": y_dist,
            "w": 320,
            "h": 64,
            "device_id": None,
            "detail": _dist_detail(dist, upstream, access_items),
        }

        # Core -> distribution links. Keep redundant upstream links explicit.
        for link_idx, r in enumerate(upstream):
            local = str(r.get("local_device") or "")
            remote = str(r.get("remote_device") or "")
            core = local if role(local) == "core" else remote
            if not core:
                continue

            lane = (link_idx - ((len(upstream) - 1) / 2)) * 30

            links.append({
                "source": slug(core),
                "target": dist_id,
                "status": status_class(r.get("last_status")),
                "local_port": r.get("local_port"),
                "remote_port": r.get("remote_port"),
                "lane": lane,
            })

    # Shared leaf data by POD.
    pod_access: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    pod_dists: dict[str, list[str]] = defaultdict(list)

    for dist in all_dists:
        pod = pod_name(dist)
        if dist not in pod_dists[pod]:
            pod_dists[pod].append(dist)
        for r in access_rows.get(dist, []):
            pod_access[pod].append((dist, r))

    for pod, items in pod_access.items():
        dists = [d for d in pod_dists.get(pod, []) if d in dist_x]
        if not dists:
            continue

        min_x = min(dist_x[d] for d in dists)
        max_x = max(dist_x[d] for d in dists)
        center_x = int((min_x + max_x) / 2)

        detail = _leaf_redundancy_detail(pod, items)
        access_status = _pod_access_status(detail)
        pod_layer_id = f"{slug(pod)}-leaf-redundancy"

        nodes[pod_layer_id] = {
            "id": pod_layer_id,
            "label": f"{pod} Leaf Layer",
            "subtitle": _pod_access_subtitle(detail),
            "role": "access",
            "status": access_status,
            "x": center_x,
            "y": y_pod_leaf,
            "w": 380,
            "h": 72,
            "device_id": None,
            "detail": detail,
        }

        # Dist -> shared POD leaf layer.
        # This is the clean default redundancy view.
        for dist in dists:
            dist_access_status = _worst_status([
                status_class(r.get("last_status"))
                for r in access_rows.get(dist, [])
            ]) if access_rows.get(dist) else "unknown"

            lane = (dists.index(dist) - ((len(dists) - 1) / 2)) * 30

            links.append({
                "source": slug(dist),
                "target": pod_layer_id,
                "status": dist_access_status,
                "local_port": "",
                "remote_port": f"{len(access_rows.get(dist, []))} leaf uplinks",
                "lane": lane,
            })

        if not show_leafs:
            continue

        # Expanded view: actual leaf nodes underneath the POD layer.
        leaf_uplinks: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for dist, row in items:
            leaf, leaf_port = _leaf_name_from_access_link(row, dist)
            if not leaf:
                continue

            leaf_uplinks[leaf].append({
                "dist": dist,
                "leaf_port": leaf_port,
                "status": status_class(row.get("last_status")),
                "last_seen": str(row.get("last_seen") or ""),
                "local_device": row.get("local_device"),
                "local_port": row.get("local_port"),
                "remote_device": row.get("remote_device"),
                "remote_port": row.get("remote_port"),
            })

        leaf_names = sorted(
            leaf_uplinks,
            key=lambda leaf: (
                _leaf_health_from_uplinks(leaf_uplinks[leaf]) == "up",
                leaf,
            ),
        )

        pod_width = max(leafs_per_row * leaf_x_gap, 760)
        leaf_start_x = center_x - int(pod_width / 2)

        for idx, leaf in enumerate(leaf_names):
            uplinks = leaf_uplinks[leaf]
            health = _leaf_health_from_uplinks(uplinks)

            row_num = idx // leafs_per_row
            col_num = idx % leafs_per_row

            leaf_x = leaf_start_x + col_num * leaf_x_gap
            leaf_y = y_leaf_start + row_num * leaf_y_gap

            leaf_id = slug(leaf)

            up_count = sum(1 for u in uplinks if status_class(u.get("status")) == "up")
            expected_count = max(2, len(uplinks))

            nodes[leaf_id] = {
                "id": leaf_id,
                "label": leaf,
                "subtitle": f"{up_count}/{expected_count} uplinks up",
                "role": "leaf",
                "status": health,
                "x": leaf_x,
                "y": leaf_y,
                "w": 155,
                "h": 56,
                "device_id": None,
                "detail": _leaf_node_detail(leaf, uplinks),
            }

            # POD layer -> leaf node.
            links.append({
                "source": pod_layer_id,
                "target": leaf_id,
                "status": health,
                "local_port": "",
                "remote_port": f"{up_count}/{expected_count}",
                "lane": 0,
            })

            # Actual distribution -> leaf uplinks. Expanded mode only.
            for link_idx, u in enumerate(sorted(uplinks, key=lambda x: x.get("dist") or "")):
                lane = (link_idx - ((len(uplinks) - 1) / 2)) * 22
                links.append({
                    "source": slug(u["dist"]),
                    "target": leaf_id,
                    "status": status_class(u.get("status")),
                    "local_port": "",
                    "remote_port": u.get("leaf_port"),
                    "lane": lane,
                })

    return list(nodes.values()), links, summary


def render_mist_flow(editable: bool = False, show_leafs: bool = False) -> str:
    nodes, links, summary = build_mist_flow_fast(show_leafs=show_leafs)
    nodes = apply_saved_positions("mist", nodes)

    mode = "expanded leaf-node view" if show_leafs else "collapsed POD leaf-layer view"

    return render_shell(
        "Mist Fabric Flow",
        f"Organized row layout using {mode}. Click a POD Leaf Layer for the full leaf redundancy list.",
        nodes,
        links,
        summary,
        module="mist",
        editable=editable,
    )


# ---------------------------------------------------------------------------
# Render override to add Mist collapsed/expanded toggle into action bar.
# Wrap the current render_mist_flow output by injecting small links.
# ---------------------------------------------------------------------------

_render_mist_flow_base = render_mist_flow

def render_mist_flow(editable: bool = False, show_leafs: bool = False) -> str:
    html = _render_mist_flow_base(editable=editable, show_leafs=show_leafs)

    collapsed_class = "" if show_leafs else "active"
    expanded_class = "active" if show_leafs else ""

    toggle = f"""
    <span class="nf-leaf-toggle">
      <a class="{collapsed_class}" href="/tools/network-flow/mist">Collapsed Leafs</a>
      <a class="{expanded_class}" href="/tools/network-flow/mist?leafs=1">Expanded Leafs</a>
    </span>
    """

    return html.replace(
        '<a class="button" href="/tools/fabric-links/flow">Old Flow</a>',
        '<a class="button" href="/tools/fabric-links/flow">Old Flow</a>' + toggle,
        1,
    )


# ---------------------------------------------------------------------------
# Edge module override:
# deterministic left/right edge layout instead of link-spaghetti placement.
# ---------------------------------------------------------------------------

def _edge_name(row: dict[str, Any]) -> str:
    for k in ("display", "sysName", "hostname"):
        v = row.get(k)
        if v:
            return str(v)
    return str(row.get("device_id") or "unknown")


def _edge_slug(name: str) -> str:
    return slug(name)


def _edge_side(name: str) -> str:
    n = name.lower()
    if any(x in n for x in ("vtr", "voter", "chester")):
        return "vtr"
    if any(x in n for x in ("dfl", "daisy", "zuma")):
        return "dfl"
    return "middle"


def _edge_role(name: str, hardware: str = "") -> str:
    n = name.lower()
    h = hardware.lower()

    if any(x in n for x in ("zuma", "chester")) and "fw" not in n:
        return "provider"
    if any(x in n for x in ("daisy", "chas")):
        return "handoff"
    if any(x in n for x in ("edgefw", "vpn-fw", "enclave-fw", "miis-fw", "fw")) or "pa-" in h:
        return "firewall"
    if "core" in n:
        return "core"
    if any(x in n for x in ("epl", "comcast", "isp", "internet")):
        return "provider"
    return "edge"


def _edge_status(row: dict[str, Any]) -> str:
    st = row.get("status")
    try:
        return "up" if int(st) == 1 else "down"
    except Exception:
        return "unknown"


def _edge_devices() -> list[dict[str, Any]]:
    sql = """
    SELECT
      device_id,
      hostname,
      sysName,
      display,
      hardware,
      type,
      status
    FROM devices
    WHERE
      LOWER(COALESCE(display, sysName, hostname, '')) REGEXP
      'edgefw|vpn-fw|enclave-fw|miis-fw|zuma|chester|daisy|epl|comcast|dfl-core|vtr-core|df1-core|core'
    ORDER BY COALESCE(display, sysName, hostname)
    """
    return fetch_all(sql)


def _edge_links(device_ids: list[int]) -> list[dict[str, Any]]:
    if not device_ids:
        return []

    ids = ",".join(str(int(x)) for x in device_ids)

    sql = f"""
    SELECT
      ld.device_id AS local_device_id,
      COALESCE(ld.display, ld.sysName, ld.hostname) AS local_device,
      COALESCE(lp.ifName, lp.ifDescr, lp.ifAlias, '') AS local_port,
      rd.device_id AS remote_device_id,
      COALESCE(rd.display, rd.sysName, rd.hostname) AS remote_device,
      COALESCE(rp.ifName, rp.ifDescr, rp.ifAlias, '') AS remote_port
    FROM links l
      JOIN ports lp ON lp.port_id = l.local_port_id
      JOIN devices ld ON ld.device_id = lp.device_id
      JOIN ports rp ON rp.port_id = l.remote_port_id
      JOIN devices rd ON rd.device_id = rp.device_id
    WHERE ld.device_id IN ({ids})
       OR rd.device_id IN ({ids})
    LIMIT 500
    """
    return fetch_all(sql)


def _edge_node_detail(name: str, row: dict[str, Any], links: list[dict[str, Any]]) -> dict[str, Any]:
    related = []
    for l in links:
        if str(l.get("local_device")) == name or str(l.get("remote_device")) == name:
            related.append({
                "local_device": l.get("local_device"),
                "local_port": l.get("local_port"),
                "remote_device": l.get("remote_device"),
                "remote_port": l.get("remote_port"),
                "status": "up",
                "last_seen": "",
            })

    return {
        "title": name,
        "type": "core",
        "links": related,
        "hardware": row.get("hardware") or "",
        "device_id": row.get("device_id"),
    }


def build_edge_flow():
    rows = _edge_devices()

    wanted = []
    for r in rows:
        name = _edge_name(r)
        lname = name.lower()

        # Keep edge focused. Do not drag the whole campus core into this thing,
        # because apparently LLDP believes every topology should become soup.
        if any(x in lname for x in (
            "edgefw",
            "vpn-fw",
            "enclave-fw",
            "miis-fw",
            "zuma",
            "chester",
            "daisy",
            "epl",
            "comcast",
            "dfl-core",
            "vtr-core",
            "df1-core",
        )):
            wanted.append(r)

    device_ids = [int(r["device_id"]) for r in wanted if r.get("device_id") is not None]
    raw_links = _edge_links(device_ids)

    by_name = {_edge_name(r): r for r in wanted}

    # Fixed positions. Left is DFL-ish, right is VTR/Voter-ish.
    row_y = {
        "provider": 80,
        "handoff": 260,
        "firewall": 450,
        "core": 650,
        "edge": 450,
    }

    side_x = {
        "dfl": 320,
        "vtr": 1020,
        "middle": 670,
    }

    role_offsets = {
        "provider": 0,
        "handoff": 0,
        "firewall": 0,
        "core": 0,
        "edge": 0,
    }

    nodes: list[dict[str, Any]] = []

    # Stable role/side ordering.
    ordered = sorted(
        wanted,
        key=lambda r: (
            {"provider": 0, "handoff": 1, "firewall": 2, "edge": 3, "core": 4}.get(_edge_role(_edge_name(r), str(r.get("hardware") or "")), 9),
            {"dfl": 0, "middle": 1, "vtr": 2}.get(_edge_side(_edge_name(r)), 9),
            _edge_name(r),
        ),
    )

    # Count per row/side so same layer devices do not overlap.
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in ordered:
        name = _edge_name(r)
        role_name = _edge_role(name, str(r.get("hardware") or ""))
        side = _edge_side(name)
        buckets[(role_name, side)].append(r)

    for (role_name, side), items in buckets.items():
        base_x = side_x.get(side, side_x["middle"])
        base_y = row_y.get(role_name, row_y["edge"])

        spread = 230
        start_x = base_x - int((len(items) - 1) * spread / 2)

        for idx, r in enumerate(items):
            name = _edge_name(r)
            node_role = "distribution" if role_name == "handoff" else role_name
            if role_name == "provider":
                node_role = "provider"
            if role_name == "firewall":
                node_role = "firewall"
            if role_name == "core":
                node_role = "core"

            nodes.append({
                "id": _edge_slug(name),
                "label": name,
                "subtitle": str(r.get("hardware") or role_name),
                "role": node_role,
                "status": _edge_status(r),
                "x": start_x + idx * spread,
                "y": base_y,
                "w": 260,
                "h": 64,
                "device_id": r.get("device_id"),
                "detail": _edge_node_detail(name, r, raw_links),
            })

    node_ids = {n["id"] for n in nodes}
    node_names = {n["label"]: n["id"] for n in nodes}

    links: list[dict[str, Any]] = []
    seen = set()

    for l in raw_links:
        a_name = str(l.get("local_device") or "")
        b_name = str(l.get("remote_device") or "")

        a = node_names.get(a_name)
        b = node_names.get(b_name)

        if not a or not b or a == b:
            continue

        key = tuple(sorted([a, b]) + sorted([str(l.get("local_port") or ""), str(l.get("remote_port") or "")]))
        if key in seen:
            continue
        seen.add(key)

        links.append({
            "source": a,
            "target": b,
            "status": "up",
            "local_port": l.get("local_port"),
            "remote_port": l.get("remote_port"),
            "lane": 0,
        })

    summary = {
        "total": len(links),
        "up": len(links),
        "down": 0,
        "missing": 0,
        "unknown": 0,
        "problems": 0,
    }

    return nodes, links, summary


_render_module_flow_base = render_module_flow

def render_module_flow(module: str, editable: bool = False) -> str:
    if module == "edge":
        nodes, links, summary = build_edge_flow()
        nodes = apply_saved_positions("edge", nodes)
        return render_shell(
            "Edge Flow",
            "Organized edge layout: providers and edge routers on top, handoff switches next, firewalls in the middle, core handoff at the bottom.",
            nodes,
            links,
            summary,
            module="edge",
            editable=editable,
        )

    return _render_module_flow_base(module, editable=editable)


# ---------------------------------------------------------------------------
# Edge module override:
# deterministic left/right edge layout instead of link-spaghetti placement.
# ---------------------------------------------------------------------------

def _edge_name(row: dict[str, Any]) -> str:
    for k in ("display", "sysName", "hostname"):
        v = row.get(k)
        if v:
            return str(v)
    return str(row.get("device_id") or "unknown")


def _edge_slug(name: str) -> str:
    return slug(name)


def _edge_side(name: str) -> str:
    n = name.lower()
    if any(x in n for x in ("vtr", "voter", "chester")):
        return "vtr"
    if any(x in n for x in ("dfl", "daisy", "zuma")):
        return "dfl"
    return "middle"


def _edge_role(name: str, hardware: str = "") -> str:
    n = name.lower()
    h = hardware.lower()

    if any(x in n for x in ("zuma", "chester")) and "fw" not in n:
        return "provider"
    if any(x in n for x in ("daisy", "chas")):
        return "handoff"
    if any(x in n for x in ("edgefw", "vpn-fw", "enclave-fw", "miis-fw", "fw")) or "pa-" in h:
        return "firewall"
    if "core" in n:
        return "core"
    if any(x in n for x in ("epl", "comcast", "isp", "internet")):
        return "provider"
    return "edge"


def _edge_status(row: dict[str, Any]) -> str:
    st = row.get("status")
    try:
        return "up" if int(st) == 1 else "down"
    except Exception:
        return "unknown"


def _edge_devices() -> list[dict[str, Any]]:
    sql = """
    SELECT
      device_id,
      hostname,
      sysName,
      display,
      hardware,
      type,
      status
    FROM devices
    WHERE
      LOWER(COALESCE(display, sysName, hostname, '')) REGEXP
      'edgefw|vpn-fw|enclave-fw|miis-fw|zuma|chester|daisy|epl|comcast|dfl-core|vtr-core|df1-core|core'
    ORDER BY COALESCE(display, sysName, hostname)
    """
    return fetch_all(sql)


def _edge_links(device_ids: list[int]) -> list[dict[str, Any]]:
    if not device_ids:
        return []

    ids = ",".join(str(int(x)) for x in device_ids)

    sql = f"""
    SELECT
      ld.device_id AS local_device_id,
      COALESCE(ld.display, ld.sysName, ld.hostname) AS local_device,
      COALESCE(lp.ifName, lp.ifDescr, lp.ifAlias, '') AS local_port,
      rd.device_id AS remote_device_id,
      COALESCE(rd.display, rd.sysName, rd.hostname) AS remote_device,
      COALESCE(rp.ifName, rp.ifDescr, rp.ifAlias, '') AS remote_port
    FROM links l
      JOIN ports lp ON lp.port_id = l.local_port_id
      JOIN devices ld ON ld.device_id = lp.device_id
      JOIN ports rp ON rp.port_id = l.remote_port_id
      JOIN devices rd ON rd.device_id = rp.device_id
    WHERE ld.device_id IN ({ids})
       OR rd.device_id IN ({ids})
    LIMIT 500
    """
    return fetch_all(sql)


def _edge_node_detail(name: str, row: dict[str, Any], links: list[dict[str, Any]]) -> dict[str, Any]:
    related = []
    for l in links:
        if str(l.get("local_device")) == name or str(l.get("remote_device")) == name:
            related.append({
                "local_device": l.get("local_device"),
                "local_port": l.get("local_port"),
                "remote_device": l.get("remote_device"),
                "remote_port": l.get("remote_port"),
                "status": "up",
                "last_seen": "",
            })

    return {
        "title": name,
        "type": "core",
        "links": related,
        "hardware": row.get("hardware") or "",
        "device_id": row.get("device_id"),
    }


def build_edge_flow():
    rows = _edge_devices()

    wanted = []
    for r in rows:
        name = _edge_name(r)
        lname = name.lower()

        # Keep edge focused. Do not drag the whole campus core into this thing,
        # because apparently LLDP believes every topology should become soup.
        if any(x in lname for x in (
            "edgefw",
            "vpn-fw",
            "enclave-fw",
            "miis-fw",
            "zuma",
            "chester",
            "daisy",
            "epl",
            "comcast",
            "dfl-core",
            "vtr-core",
            "df1-core",
        )):
            wanted.append(r)

    device_ids = [int(r["device_id"]) for r in wanted if r.get("device_id") is not None]
    raw_links = _edge_links(device_ids)

    by_name = {_edge_name(r): r for r in wanted}

    # Fixed positions. Left is DFL-ish, right is VTR/Voter-ish.
    row_y = {
        "provider": 80,
        "handoff": 260,
        "firewall": 450,
        "core": 650,
        "edge": 450,
    }

    side_x = {
        "dfl": 320,
        "vtr": 1020,
        "middle": 670,
    }

    role_offsets = {
        "provider": 0,
        "handoff": 0,
        "firewall": 0,
        "core": 0,
        "edge": 0,
    }

    nodes: list[dict[str, Any]] = []

    # Stable role/side ordering.
    ordered = sorted(
        wanted,
        key=lambda r: (
            {"provider": 0, "handoff": 1, "firewall": 2, "edge": 3, "core": 4}.get(_edge_role(_edge_name(r), str(r.get("hardware") or "")), 9),
            {"dfl": 0, "middle": 1, "vtr": 2}.get(_edge_side(_edge_name(r)), 9),
            _edge_name(r),
        ),
    )

    # Count per row/side so same layer devices do not overlap.
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in ordered:
        name = _edge_name(r)
        role_name = _edge_role(name, str(r.get("hardware") or ""))
        side = _edge_side(name)
        buckets[(role_name, side)].append(r)

    for (role_name, side), items in buckets.items():
        base_x = side_x.get(side, side_x["middle"])
        base_y = row_y.get(role_name, row_y["edge"])

        spread = 230
        start_x = base_x - int((len(items) - 1) * spread / 2)

        for idx, r in enumerate(items):
            name = _edge_name(r)
            node_role = "distribution" if role_name == "handoff" else role_name
            if role_name == "provider":
                node_role = "provider"
            if role_name == "firewall":
                node_role = "firewall"
            if role_name == "core":
                node_role = "core"

            nodes.append({
                "id": _edge_slug(name),
                "label": name,
                "subtitle": str(r.get("hardware") or role_name),
                "role": node_role,
                "status": _edge_status(r),
                "x": start_x + idx * spread,
                "y": base_y,
                "w": 260,
                "h": 64,
                "device_id": r.get("device_id"),
                "detail": _edge_node_detail(name, r, raw_links),
            })

    node_ids = {n["id"] for n in nodes}
    node_names = {n["label"]: n["id"] for n in nodes}

    links: list[dict[str, Any]] = []
    seen = set()

    for l in raw_links:
        a_name = str(l.get("local_device") or "")
        b_name = str(l.get("remote_device") or "")

        a = node_names.get(a_name)
        b = node_names.get(b_name)

        if not a or not b or a == b:
            continue

        key = tuple(sorted([a, b]) + sorted([str(l.get("local_port") or ""), str(l.get("remote_port") or "")]))
        if key in seen:
            continue
        seen.add(key)

        links.append({
            "source": a,
            "target": b,
            "status": "up",
            "local_port": l.get("local_port"),
            "remote_port": l.get("remote_port"),
            "lane": 0,
        })

    summary = {
        "total": len(links),
        "up": len(links),
        "down": 0,
        "missing": 0,
        "unknown": 0,
        "problems": 0,
    }

    return nodes, links, summary


_render_module_flow_base = render_module_flow

def render_module_flow(module: str, editable: bool = False) -> str:
    if module == "edge":
        nodes, links, summary = build_edge_flow()
        nodes = apply_saved_positions("edge", nodes)
        return render_shell(
            "Edge Flow",
            "Organized edge layout: providers and edge routers on top, handoff switches next, firewalls in the middle, core handoff at the bottom.",
            nodes,
            links,
            summary,
            module="edge",
            editable=editable,
        )

    return _render_module_flow_base(module, editable=editable)
