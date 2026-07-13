
function fmtBits(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return "0 bps";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " Gbps";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + " Mbps";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + " Kbps";
  return Math.round(n) + " bps";
}

function fmtDurationSeconds(value) {
  const s = Number(value || 0);
  if (!Number.isFinite(s) || s <= 0) return "unknown";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return d + "d " + h + "h";
  if (h > 0) return h + "h " + m + "m";
  return m + "m";
}

function edgeEscHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function providerHealthBadge(value) {
  const v = String(value || "unknown").toLowerCase();
  return `<span class="edge-badge edge-badge-${edgeEscHtml(v)}">${edgeEscHtml(v.toUpperCase())}</span>`;
}

function renderProviderLinkHealth(link) {
  if (!link || !link.peer_ip) return "";

  return `
    <div class="edge-detail-section">
      <div class="edge-detail-heading">Provider Health</div>
      <div class="edge-detail-grid">
        <div>Health</div><div>${providerHealthBadge(link.provider_health || link.status)}</div>
        <div>Reason</div><div>${edgeEscHtml(link.provider_reason || link.bgp_reason || "unknown")}</div>
        <div>Physical</div><div>${edgeEscHtml(String(link.status || "unknown").toUpperCase())}</div>
        <div>Router Port</div><div><code>${edgeEscHtml(link.target_port || "")}</code></div>
        <div>Peer IP</div><div><code>${edgeEscHtml(link.peer_ip || "")}</code></div>
        <div>Local IP</div><div><code>${edgeEscHtml(link.local_ip || link.bgp_local_ip || "")}</code></div>
      </div>
    </div>

    <div class="edge-detail-section">
      <div class="edge-detail-heading">BGP</div>
      <div class="edge-detail-grid">
        <div>State</div><div>${providerHealthBadge(link.bgp_state || "unknown")}</div>
        <div>Admin</div><div><code>${edgeEscHtml(link.bgp_admin_status || "unknown")}</code></div>
        <div>ASN</div><div><code>${edgeEscHtml(link.bgp_remote_as || "")}</code></div>
        <div>AS Name</div><div>${edgeEscHtml(link.bgp_remote_as_text || "")}</div>
        <div>Established</div><div>${edgeEscHtml(fmtDurationSeconds(link.bgp_established_seconds))}</div>
      </div>
    </div>

    <div class="edge-detail-section">
      <div class="edge-detail-heading">Traffic</div>
      <div class="edge-detail-grid">
        <div>A → B</div><div><code>${edgeEscHtml(fmtBits(link.source_to_target_bps))}</code></div>
        <div>B → A</div><div><code>${edgeEscHtml(fmtBits(link.target_to_source_bps))}</code></div>
        <div>Telemetry Age</div><div><code>${edgeEscHtml(link.poll_age_seconds ?? "unknown")}s</code></div>
        <div>Telemetry Stale</div><div><code>${edgeEscHtml(Boolean(link.telemetry_stale))}</code></div>
      </div>
    </div>
  `;
}


(() => {
  "use strict";

  const page = document.querySelector(".ef-page");
  const viewport = document.getElementById("ef-viewport");
  const stage = document.getElementById("ef-stage");
  const svg = document.getElementById("ef-svg");
  const nodesRoot = document.getElementById("ef-nodes");
  const details = document.getElementById("ef-details");
  const summaryRoot = document.getElementById("ef-summary");
  const refreshState = document.getElementById("ef-refresh-state");
  const dataElement = document.getElementById("ef-data");

  if (
    !page ||
    !viewport ||
    !stage ||
    !svg ||
    !nodesRoot ||
    !details ||
    !summaryRoot ||
    !dataElement
  ) {
    return;
  }

  let data = JSON.parse(dataElement.textContent || "{}");
  const editable = page.dataset.editable === "true";
  const apiUrl = page.dataset.apiUrl || "/api/network-flow/edge";
  const positionModule =
    page.dataset.positionModule || "edge-layout-v2";
  let zoom = 1;
  let selectedNode = "";
  let hotElements = [];
  let redrawPending = false;
  let trafficEnabled = true;

  const SVG_NS = "http://www.w3.org/2000/svg";

  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[character]);

  const statusPill = (status) => {
    const clean = String(status || "unknown").toLowerCase();

    return `
      <span class="ef-pill ef-pill-${escapeHtml(clean)}">
        ${escapeHtml(clean)}
      </span>
    `;
  };

  const nodeById = (nodeId) =>
    data.nodes.find((node) => node.id === nodeId);

  const renderSummary = () => {
    const summary = data.summary || {};

    const cards = [
      ["Problems", summary.problems || 0, "problems"],
      ["Total Links", summary.total || 0, ""],
      ["Up", summary.up || 0, "up"],
      ["Down", summary.down || 0, "down"],
      ["Missing", summary.missing || 0, "missing"],
      ["Unknown", summary.unknown || 0, "unknown"],
    ];

    summaryRoot.innerHTML = cards.map(([label, value, cssClass]) => `
      <div class="ef-stat ${cssClass ? `ef-stat-${cssClass}` : ""}">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
      </div>
    `).join("");
  };

  const renderNodes = () => {
    nodesRoot.innerHTML = "";

    data.nodes.forEach((node) => {
      const element = document.createElement("div");

      element.className = [
        "ef-node",
        `ef-role-${node.role}`,
        `ef-node-status-${node.status}`,
        editable ? "ef-editable-node" : "",
      ].join(" ");

      element.dataset.nodeId = node.id;
      element.style.left = `${node.x}px`;
      element.style.top = `${node.y}px`;
      element.style.width = `${node.w}px`;
      element.style.height = `${node.h}px`;

      const cloud = node.role === "provider"
        ? '<div class="ef-cloud-icon">☁</div>'
        : "";

      element.innerHTML = `
        ${cloud}
        <strong>${escapeHtml(node.label)}</strong>
        <small>${escapeHtml(node.subtitle || node.hardware || node.role)}</small>
      `;

      element.addEventListener("mouseenter", () => {
        highlightNode(node.id);
      });

      element.addEventListener("mouseleave", () => {
        clearHighlight();
      });

      element.addEventListener("click", (event) => {
        event.stopPropagation();
        selectNode(node.id);
      });

      if (editable) {
        attachDragHandlers(element, node);
      }

      nodesRoot.appendChild(element);
    });
  };

  const calculateAnchors = (source, target, laneOffset) => {
    const sourceCenterX = source.x + source.w / 2;
    const sourceCenterY = source.y + source.h / 2;
    const targetCenterX = target.x + target.w / 2;
    const targetCenterY = target.y + target.h / 2;

    const deltaX = targetCenterX - sourceCenterX;
    const deltaY = targetCenterY - sourceCenterY;

    if (Math.abs(deltaY) >= Math.abs(deltaX) * 0.45) {
      if (sourceCenterY <= targetCenterY) {
        return {
          x1: sourceCenterX + laneOffset,
          y1: source.y + source.h,
          x2: targetCenterX + laneOffset,
          y2: target.y,
          orientation: "vertical",
        };
      }

      return {
        x1: sourceCenterX + laneOffset,
        y1: source.y,
        x2: targetCenterX + laneOffset,
        y2: target.y + target.h,
        orientation: "vertical",
      };
    }

    if (sourceCenterX <= targetCenterX) {
      return {
        x1: source.x + source.w,
        y1: sourceCenterY + laneOffset,
        x2: target.x,
        y2: targetCenterY + laneOffset,
        orientation: "horizontal",
      };
    }

    return {
      x1: source.x,
      y1: sourceCenterY + laneOffset,
      x2: target.x + target.w,
      y2: targetCenterY + laneOffset,
      orientation: "horizontal",
    };
  };

  const calculatePath = (source, target, link, extraLaneOffset = 0) => {
    const total = Math.max(1, Number(link.parallel_total || 1));
    const index = Number(link.parallel_index || 0);
    const laneOffset = (index - (total - 1) / 2) * 14 + extraLaneOffset;

    const anchor = calculateAnchors(source, target, laneOffset);

    if (anchor.orientation === "vertical") {
      const distance = Math.abs(anchor.y2 - anchor.y1);
      const bend = Math.max(70, distance * 0.42);

      if (anchor.y1 <= anchor.y2) {
        return [
          `M ${anchor.x1} ${anchor.y1}`,
          `C ${anchor.x1} ${anchor.y1 + bend}`,
          `${anchor.x2} ${anchor.y2 - bend}`,
          `${anchor.x2} ${anchor.y2}`,
        ].join(" ");
      }

      return [
        `M ${anchor.x1} ${anchor.y1}`,
        `C ${anchor.x1} ${anchor.y1 - bend}`,
        `${anchor.x2} ${anchor.y2 + bend}`,
        `${anchor.x2} ${anchor.y2}`,
      ].join(" ");
    }

    const distance = Math.abs(anchor.x2 - anchor.x1);
    const bend = Math.max(80, distance * 0.4);

    if (anchor.x1 <= anchor.x2) {
      return [
        `M ${anchor.x1} ${anchor.y1}`,
        `C ${anchor.x1 + bend} ${anchor.y1}`,
        `${anchor.x2 - bend} ${anchor.y2}`,
        `${anchor.x2} ${anchor.y2}`,
      ].join(" ");
    }

    return [
      `M ${anchor.x1} ${anchor.y1}`,
      `C ${anchor.x1 - bend} ${anchor.y1}`,
      `${anchor.x2 + bend} ${anchor.y2}`,
      `${anchor.x2} ${anchor.y2}`,
    ].join(" ");
  };

  const formatBps = (bps) => {
    const value = Number(bps || 0);
    const units = ["bps", "Kbps", "Mbps", "Gbps", "Tbps"];
    let scaled = value;
    let index = 0;

    while (scaled >= 1000 && index < units.length - 1) {
      scaled /= 1000;
      index += 1;
    }

    const digits = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2;
    return `${scaled.toFixed(digits)} ${units[index]}`;
  };

  const formatPercent = (value) => `${Number(value || 0).toFixed(2)}%`;

  const trafficDuration = (bps) => {
    const value = Math.max(0, Number(bps || 0));
    if (value <= 0) return 8;
    const log = Math.log10(value + 1);
    return Math.max(0.55, Math.min(7, 8 - log * 0.82));
  };

  const trafficWidth = (utilization) => {
    const value = Math.max(0, Number(utilization || 0));
    return Math.max(1.8, Math.min(8, 1.8 + Math.sqrt(value) * 0.72));
  };

  const attachLinkInteractions = (element, link) => {
    element.dataset.linkId = link.id;
    element.dataset.source = link.source;
    element.dataset.target = link.target;

    element.addEventListener("mouseenter", () => {
      highlightLink(link.id);
    });

    element.addEventListener("mouseleave", () => {
      clearHighlight();
    });

    element.addEventListener("click", (event) => {
      event.stopPropagation();
      showLinkDetails(link);
    });
  };

  const drawLinks = () => {
    svg.innerHTML = "";

    data.links.forEach((link) => {
      const source = nodeById(link.source);
      const target = nodeById(link.target);

      if (!source || !target) {
        return;
      }

      const basePath = document.createElementNS(SVG_NS, "path");
      basePath.setAttribute("d", calculatePath(source, target, link, 0));
      basePath.classList.add(
        "ef-link",
        `ef-link-role-${link.role}`,
        `ef-link-status-${link.status}`,
      );

      if (link.telemetry_stale && link.status === "up") {
        basePath.classList.add("ef-link-stale");
      }

      const baseTitle = document.createElementNS(SVG_NS, "title");
      baseTitle.textContent = [
        `${source.label} ${link.source_port}`,
        "↔",
        `${target.label} ${link.target_port}`,
        `[${link.status}]`,
        `${formatBps(link.source_to_target_bps)} →`,
        `${formatBps(link.target_to_source_bps)} ←`,
      ].join(" ");
      basePath.appendChild(baseTitle);
      attachLinkInteractions(basePath, link);
      svg.appendChild(basePath);

      if (
        !trafficEnabled ||
        link.status !== "up" ||
        link.telemetry_stale
      ) {
        return;
      }

      const directions = [
        {
          cssClass: "ef-traffic-forward",
          bps: Number(link.source_to_target_bps || 0),
          utilization: Number(link.source_utilization_pct || 0),
          lane: -5,
          title: `${source.label} → ${target.label}`,
        },
        {
          cssClass: "ef-traffic-reverse",
          bps: Number(link.target_to_source_bps || 0),
          utilization: Number(link.target_utilization_pct || 0),
          lane: 5,
          title: `${target.label} → ${source.label}`,
        },
      ];

      directions.forEach((direction) => {
        if (direction.bps <= 0) return;

        const trafficPath = document.createElementNS(SVG_NS, "path");
        trafficPath.setAttribute(
          "d",
          calculatePath(source, target, link, direction.lane)
        );
        trafficPath.classList.add("ef-traffic", direction.cssClass);
        trafficPath.style.setProperty(
          "--ef-traffic-duration",
          `${trafficDuration(direction.bps)}s`
        );
        trafficPath.style.strokeWidth = trafficWidth(direction.utilization);

        const trafficTitle = document.createElementNS(SVG_NS, "title");
        trafficTitle.textContent = [
          direction.title,
          formatBps(direction.bps),
          formatPercent(direction.utilization),
        ].join(" · ");
        trafficPath.appendChild(trafficTitle);
        attachLinkInteractions(trafficPath, link);
        svg.appendChild(trafficPath);
      });
    });
  };

  const scheduleDraw = () => {
    if (redrawPending) {
      return;
    }

    redrawPending = true;

    window.requestAnimationFrame(() => {
      redrawPending = false;
      drawLinks();
    });
  };

  const clearHighlight = () => {
    hotElements.forEach((element) => {
      element.classList.remove(
        "ef-node-hot",
        "ef-node-dim",
        "ef-link-hot",
        "ef-link-dim",
      );
    });

    hotElements = [];
  };

  const highlightNode = (nodeId) => {
    clearHighlight();

    const relatedLinkIds = new Set(
      data.links
        .filter((link) =>
          link.source === nodeId || link.target === nodeId
        )
        .map((link) => link.id)
    );

    document.querySelectorAll(".ef-node").forEach((element) => {
      if (element.dataset.nodeId === nodeId) {
        element.classList.add("ef-node-hot");
      } else {
        const connected = data.links.some((link) =>
          relatedLinkIds.has(link.id) &&
          (
            link.source === element.dataset.nodeId ||
            link.target === element.dataset.nodeId
          )
        );

        element.classList.add(
          connected ? "ef-node-hot" : "ef-node-dim"
        );
      }

      hotElements.push(element);
    });

    document.querySelectorAll(".ef-link").forEach((element) => {
      if (relatedLinkIds.has(element.dataset.linkId)) {
        element.classList.add("ef-link-hot");
      } else {
        element.classList.add("ef-link-dim");
      }

      hotElements.push(element);
    });
  };

  const highlightLink = (linkId) => {
    clearHighlight();

    const link = data.links.find((item) => item.id === linkId);

    if (!link) {
      return;
    }

    document.querySelectorAll(".ef-link").forEach((element) => {
      element.classList.add(
        element.dataset.linkId === linkId
          ? "ef-link-hot"
          : "ef-link-dim"
      );
      hotElements.push(element);
    });

    document.querySelectorAll(".ef-node").forEach((element) => {
      const isEndpoint = (
        element.dataset.nodeId === link.source ||
        element.dataset.nodeId === link.target
      );

      element.classList.add(
        isEndpoint ? "ef-node-hot" : "ef-node-dim"
      );
      hotElements.push(element);
    });
  };

  const showNodeDetails = (node) => {
    details.classList.add("ef-details-open");
    const deviceLink = node.device_id
      ? `<a class="button" href="/dashboard?device_id=${encodeURIComponent(node.device_id)}">Open Device</a>`
      : "";

    const links = (node.links || []).map((link) => `
      <div class="ef-detail-link">
        ${statusPill(link.status)}
        <strong>${escapeHtml(node.label)} → ${escapeHtml(link.peer_label)}</strong>
        <code>${escapeHtml(link.local_port)} ↔ ${escapeHtml(link.peer_port)}</code>
        <small>${escapeHtml(link.role)} · last seen ${escapeHtml(link.last_seen || "unknown")}</small>
        ${renderProviderLinkHealth(link)}
      </div>
    `).join("");

    details.innerHTML = `
      <h2>${escapeHtml(node.label)}</h2>
      <p>${escapeHtml(node.hardware || node.subtitle || node.role)}</p>
      ${statusPill(node.status)}
      ${deviceLink}
      <h3>Connected Paths</h3>
      ${links || "<p>No learned links.</p>"}
    `;
  };

  const showLinkDetails = (link) => {
    details.classList.add("ef-details-open");
    const source = nodeById(link.source);
    const target = nodeById(link.target);
    const staleText = link.telemetry_stale
      ? `<p class="ef-live-warning">Telemetry is stale (${escapeHtml(link.poll_age_seconds ?? "unknown")} seconds old).</p>`
      : "";

    details.innerHTML = `
      <h2>Live Link Details</h2>
      ${statusPill(link.status)}
      ${staleText}
      <div class="ef-detail-link">
        <strong>${escapeHtml(source?.label || link.source)}</strong>
        <code>${escapeHtml(link.source_port)}</code>
        <small>Oper: ${escapeHtml(link.source_port_oper_status || "unknown")}</small>
      </div>
      <div class="ef-live-direction">
        <strong>${escapeHtml(source?.label || link.source)} → ${escapeHtml(target?.label || link.target)}</strong>
        <span>${formatBps(link.source_to_target_bps)}</span>
        <span>${formatPercent(link.source_utilization_pct)}</span>
        <small>Errors/sec: ${escapeHtml(link.source_to_target_errors_rate || 0)}</small>
      </div>
      <div class="ef-live-direction ef-live-reverse">
        <strong>${escapeHtml(target?.label || link.target)} → ${escapeHtml(source?.label || link.source)}</strong>
        <span>${formatBps(link.target_to_source_bps)}</span>
        <span>${formatPercent(link.target_utilization_pct)}</span>
        <small>Errors/sec: ${escapeHtml(link.target_to_source_errors_rate || 0)}</small>
      </div>
      <div class="ef-detail-link">
        <strong>${escapeHtml(target?.label || link.target)}</strong>
        <code>${escapeHtml(link.target_port)}</code>
        <small>Oper: ${escapeHtml(link.target_port_oper_status || "unknown")}</small>
      </div>
      <p>
        Capacity: ${formatBps(link.capacity_bps)}<br>
        Role: ${escapeHtml(link.role)}<br>
        Last learned: ${escapeHtml(link.last_seen || "unknown")}<br>
        Poll age: ${escapeHtml(link.poll_age_seconds ?? "unknown")} seconds
      </p>
    `;
  };

  const selectNode = (nodeId) => {
    selectedNode = nodeId;

    document.querySelectorAll(".ef-node").forEach((element) => {
      element.classList.toggle(
        "ef-node-selected",
        element.dataset.nodeId === nodeId,
      );
    });

    const node = nodeById(nodeId);

    if (node) {
      showNodeDetails(node);
    }
  };

  const setZoom = (newZoom) => {
    zoom = Math.max(0.3, Math.min(1.8, newZoom));
    stage.style.transform = `scale(${zoom})`;
  };

  const fit = () => {
    const widthScale = (viewport.clientWidth - 24) / data.canvas.width;
    const heightScale = (viewport.clientHeight - 24) / data.canvas.height;

    setZoom(Math.min(2, widthScale, heightScale));
  };

  const attachDragHandlers = (element, node) => {
    let dragging = null;

    element.addEventListener("pointerdown", (event) => {
      event.preventDefault();

      dragging = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        nodeX: node.x,
        nodeY: node.y,
      };

      element.setPointerCapture(event.pointerId);
      element.classList.add("ef-node-dragging");
    });

    element.addEventListener("pointermove", (event) => {
      if (!dragging || dragging.pointerId !== event.pointerId) {
        return;
      }

      node.x = Math.max(
        0,
        Math.round(dragging.nodeX + (event.clientX - dragging.startX) / zoom)
      );

      node.y = Math.max(
        0,
        Math.round(dragging.nodeY + (event.clientY - dragging.startY) / zoom)
      );

      element.style.left = `${node.x}px`;
      element.style.top = `${node.y}px`;

      scheduleDraw();
    });

    element.addEventListener("pointerup", (event) => {
      if (!dragging || dragging.pointerId !== event.pointerId) {
        return;
      }

      dragging = null;
      element.classList.remove("ef-node-dragging");
    });
  };

  const saveLayout = async () => {
    const positions = data.nodes.map((node) => ({
      node_id: node.id,
      x: node.x,
      y: node.y,
      w: node.w,
    }));

    const response = await fetch(
      `${"/api/network-flow/"}${encodeURIComponent(positionModule)}/positions`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ positions }),
      }
    );

    const result = await response.json();

    refreshState.textContent = `Saved ${result.saved || 0} edge node positions.`;
  };

  const resetLayout = async () => {
    if (!window.confirm("Reset the saved edge layout?")) {
      return;
    }

    await fetch(
      `${"/api/network-flow/"}${encodeURIComponent(positionModule)}/positions/reset`,
      {
        method: "POST",
      }
    );

    window.location.reload();
  };

  const refreshData = async (manual = false) => {
    try {
      refreshState.textContent = manual
        ? "Refreshing edge state..."
        : "Auto-refreshing edge state...";

      const response = await fetch(
        apiUrl,
        {
          cache: "no-store",
        }
      );

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      data = await response.json();
      renderAll(false);

      const now = new Date().toLocaleTimeString();
      refreshState.textContent = `Live edge status refreshed ${now}.`;
    } catch (error) {
      refreshState.textContent = `Refresh failed: ${error}`;
    }
  };

  const renderAll = (fitAfter = false) => {
    stage.style.width = `${data.canvas.width}px`;
    stage.style.height = `${data.canvas.height}px`;
    svg.setAttribute(
      "viewBox",
      `0 0 ${data.canvas.width} ${data.canvas.height}`
    );

    renderSummary();
    renderNodes();
    drawLinks();

    if (selectedNode && nodeById(selectedNode)) {
      selectNode(selectedNode);
    }

    if (fitAfter) {
      window.setTimeout(fit, 100);
    }
  };

  document.getElementById("ef-fit")?.addEventListener("click", fit);
  document.getElementById("ef-zoom-in")?.addEventListener("click", () => {
    setZoom(zoom + 0.1);
  });
  document.getElementById("ef-zoom-out")?.addEventListener("click", () => {
    setZoom(zoom - 0.1);
  });
  document.getElementById("ef-refresh")?.addEventListener("click", () => {
    refreshData(true);
  });
  document.getElementById("ef-traffic-toggle")?.addEventListener("click", (event) => {
    trafficEnabled = !trafficEnabled;
    event.currentTarget.textContent = trafficEnabled ? "Traffic On" : "Traffic Off";
    drawLinks();
  });
  document.getElementById("ef-save")?.addEventListener("click", saveLayout);
  document.getElementById("ef-reset")?.addEventListener("click", resetLayout);

  viewport.addEventListener("click", () => {
    selectedNode = "";

    document.querySelectorAll(".ef-node").forEach((element) => {
      element.classList.remove("ef-node-selected");
    });

    details.classList.remove("ef-details-open");
  });

  if (editable) {
    page.classList.add("ef-editable");
  }


  let edgeFitResizeTimer = null;

  window.addEventListener("resize", () => {
    window.clearTimeout(edgeFitResizeTimer);

    edgeFitResizeTimer = window.setTimeout(() => {
      fit();
    }, 150);
  });


  let edgeResponsiveFitTimer = null;

  window.addEventListener("resize", () => {
    window.clearTimeout(edgeResponsiveFitTimer);

    edgeResponsiveFitTimer = window.setTimeout(() => {
      fit();
    }, 150);
  });

  renderAll(true);

  if (!editable) {
    window.setInterval(() => {
      refreshData(false);
    }, 15000);
  }
})();
