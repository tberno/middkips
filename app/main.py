import time
import json
import ssl
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import urllib.parse
import html
import os
import socket
AKIPS_UNUSED_CACHE_FILE = os.getenv("AKIPS_UNUSED_CACHE_FILE", "/data/akips_unused_cache.json")
from typing import Any
from urllib.parse import quote_plus

import pymysql
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles


APP_NAME = os.getenv("PORTAL_NAME", "MiddKiPS")
APP_ROOT_PATH = os.getenv("APP_ROOT_PATH", "").rstrip("/")
LIBRENMS_BASE_URL = os.getenv("LIBRENMS_BASE_URL", "https://nms.jestertek.cc").rstrip("/")
OXIDIZED_BASE_URL = os.getenv("OXIDIZED_BASE_URL", "https://nms.jestertek.cc/oxidized").rstrip("/")
UNUSED_CACHE_FILE = os.getenv("UNUSED_CACHE_FILE", "/data/unused_interfaces_cache.json")


def h(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def fmt_ip(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        try:
            if len(raw) == 4:
                return socket.inet_ntop(socket.AF_INET, raw)
            if len(raw) == 16:
                return socket.inet_ntop(socket.AF_INET6, raw)
        except Exception:
            return ""

    text = str(value)
    if text.startswith("b'") or text.startswith('b"'):
        return ""

    return text


def looks_like_ip(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        socket.inet_pton(socket.AF_INET, text)
        return True
    except Exception:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, text)
        return True
    except Exception:
        return False


def device_label(row: dict[str, Any]) -> str:
    for key in ("display", "display_name", "sysName", "sys_name"):
        value = row.get(key)
        if value and not looks_like_ip(value):
            return str(value)

    for key in ("hostname", "device"):
        value = row.get(key)
        if value:
            return str(value)

    return fmt_ip(row.get("ip_addr") or row.get("ip"))


def db_conn():
    return pymysql.connect(
        host=os.getenv("LIBRENMS_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("LIBRENMS_DB_PORT", "3306")),
        user=os.getenv("LIBRENMS_DB_USER", os.getenv("MYSQL_USER", "librenms")),
        password=os.getenv("LIBRENMS_DB_PASSWORD", os.getenv("MYSQL_PASSWORD", "")),
        database=os.getenv("LIBRENMS_DB_NAME", os.getenv("MYSQL_DATABASE", "librenms")),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone() or {}


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def safe_query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return fetch_all(sql, params)
    except Exception:
        return []


def safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def fmt_speed(value: Any) -> str:
    bps = safe_float(value)
    if bps <= 0:
        return ""
    units = ["bps", "Kbps", "Mbps", "Gbps", "Tbps"]
    idx = 0
    while bps >= 1000 and idx < len(units) - 1:
        bps /= 1000
        idx += 1
    if bps >= 100:
        num = f"{bps:.0f}"
    elif bps >= 10:
        num = f"{bps:.1f}".rstrip("0").rstrip(".")
    else:
        num = f"{bps:.2f}".rstrip("0").rstrip(".")
    return f"{num} {units[idx]}"


def fmt_rate(octets_per_second: Any) -> str:
    return fmt_speed(safe_float(octets_per_second) * 8)


def fmt_mac(value: Any) -> str:
    raw = "".join(ch for ch in str(value or "") if ch.lower() in "0123456789abcdef")
    if len(raw) == 12:
        return ":".join(raw[i:i + 2] for i in range(0, 12, 2)).lower()
    return str(value or "")


def status_badge(value: Any) -> str:
    raw = str(value or "").strip().lower()
    label = h(raw or "unknown")
    if raw in ("up", "ok", "online", "active", "1"):
        cls = "status-pill status-up"
        label = "up"
    elif raw in ("down", "critical", "offline", "failed", "0"):
        cls = "status-pill status-down"
        label = "down"
    elif raw in ("disabled", "admin down", "shutdown"):
        cls = "status-pill status-admin"
    else:
        cls = "status-pill status-unknown"
    return f'<span class="{cls}">{label}</span>'


def device_anchor(row: dict[str, Any], label_key: str = "device") -> str:
    device_id = row.get("device_id") or row.get("dev_id")
    label = row.get(label_key) or device_label(row)
    if device_id:
        return f'<a href="/device/{h(device_id)}">{h(label)}</a>'
    return h(label)


def interface_anchor(row: dict[str, Any]) -> str:
    label = row.get("ifName") or row.get("ifDescr") or row.get("ifIndex") or ""
    port_id = row.get("port_id")
    if port_id:
        return f'<a href="/interface/{h(port_id)}">{h(label)}</a>'
    return h(label)



def card(label: str, value: Any) -> str:
    return f'<div class="card"><span>{h(label)}</span><strong>{h(value)}</strong></div>'


def table(headers: list[str], rows: str) -> str:
    th = "".join(f"<th>{h(x)}</th>" for x in headers)
    if not rows:
        rows = f'<tr><td colspan="{len(headers)}" class="muted">No results found.</td></tr>'
    return f'<table class="report"><thead><tr>{th}</tr></thead><tbody>{rows}</tbody></table>'


def layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html data-theme="dark">
<head>
  <link rel="stylesheet" href="/static/middkips.css">
  <meta charset="utf-8">
  <title>{h(title)} - {h(APP_NAME)}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script>
    (function () {{
      var saved = localStorage.getItem("itsPortalTheme") || "dark";
      document.documentElement.setAttribute("data-theme", saved);
    }})();
  </script>
  <style>
    :root {{
      --bg:#1f252b;
      --panel:#252d34;
      --deep:#20262c;
      --border:#39434d;
      --text:#edf2f6;
      --muted:#a8b3bd;
      --link:#8fd7ff;
      --good:#72d68b;
      --bad:#ff8a8a;
      --header:#151a1f;
      --table-head:#182027;
      --table-alt:#222b33;
      --table-hover:#2b3a45;
      --input-bg:#f5f7fa;
      --input-text:#111;
    }}
    :root[data-theme="light"] {{
      --bg:#f4f6f8;
      --panel:#ffffff;
      --deep:#eef2f6;
      --border:#cbd5e1;
      --text:#17212b;
      --muted:#64748b;
      --link:#075a9c;
      --good:#15803d;
      --bad:#b91c1c;
      --header:#ffffff;
      --table-head:#eef3f7;
      --table-alt:#f8fafc;
      --table-hover:#eaf3fb;
      --input-bg:#ffffff;
      --input-text:#111827;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      background:var(--bg);
      color:var(--text);
      font:13px/1.35 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      margin:0;
    }}
    a {{ color:var(--link); text-decoration:none; }}
    .topbar {{
      align-items:center;
      background:var(--header);
      border-bottom:1px solid var(--border);
      display:flex;
      gap:18px;
      padding:10px 14px;
      position:sticky;
      top:0;
      z-index:20;
    }}
    .brand a {{
      color:var(--text);
      font-size:18px;
      font-weight:800;
    }}
    nav {{
      display:flex;
      flex-wrap:wrap;
      gap:12px;
    }}
    nav a {{
      color:var(--text);
      opacity:.9;
    }}
    nav a:hover {{ color:var(--link); }}
    .top-search {{
      display:flex;
      gap:5px;
      margin-left:auto;
    }}
    .top-search input {{
      min-width:330px;
      padding:4px 7px;
    }}
    main {{ padding:14px; }}
    .hero {{ margin:16px 0 18px; }}
    .hero h1 {{ font-size:34px; margin:0 0 4px; }}
    .hero p {{ color:var(--muted); margin:0; }}
    .cards {{
      display:flex;
      flex-wrap:wrap;
      gap:8px;
      margin:0 0 14px;
    }}
    .card {{
      background:var(--panel);
      border:1px solid var(--border);
      border-radius:4px;
      min-width:150px;
      padding:10px 12px;
    }}
    .card span {{
      color:var(--muted);
      display:block;
      font-size:12px;
    }}
    .card strong {{ font-size:24px; }}
    .panel {{
      background:var(--panel);
      border:1px solid var(--border);
      border-radius:4px;
      margin-bottom:10px;
      overflow-x:auto;
      padding:10px;
    }}
    .panel-head {{
      align-items:center;
      display:flex;
      justify-content:space-between;
      gap:10px;
      margin-bottom:6px;
    }}
    h1,h2 {{ margin:0 0 8px; }}
    h1 {{ font-size:22px; }}
    h2 {{ font-size:16px; }}
    .center {{ text-align:center; }}
    .subtitle {{
      color:var(--muted);
      margin:-4px 0 8px;
      text-align:center;
    }}
    .toolbar {{
      display:flex;
      gap:6px;
      margin-bottom:10px;
    }}
    input,button,.button {{
      border-radius:3px;
      border:1px solid var(--border);
      padding:5px 8px;
    }}
    input {{
      background:var(--input-bg);
      color:var(--input-text);
      min-width:340px;
    }}
    button,.button {{
      background:#2d3741;
      color:#edf2f6;
      cursor:pointer;
      display:inline-block;
    }}
    :root[data-theme="light"] button,
    :root[data-theme="light"] .button {{
      background:#e2e8f0;
      color:#17212b;
    }}
    .actions {{
      display:flex;
      flex-wrap:wrap;
      gap:6px;
    }}
    .identity {{
      border-collapse:collapse;
      margin-bottom:10px;
      table-layout:fixed;
      width:100%;
    }}
    .identity th,.identity td {{
      border:1px solid var(--border);
      overflow:hidden;
      padding:4px 6px;
      text-overflow:ellipsis;
      white-space:nowrap;
    }}
    .identity th {{ background:var(--table-head); }}
    .dashboard-grid {{
      display:grid;
      gap:10px;
      grid-template-columns:330px 330px minmax(0,1fr);
    }}
    
.switch-main.hide-device-col table.report th:first-child,
.switch-main.hide-device-col table.report td:first-child {{ display:none; }}

.switch-layout {{
      display:grid;
      gap:10px;
      grid-template-columns:230px minmax(0,1fr);
    }}
    .switch-sidebar {{
      background:var(--panel);
      border:1px solid var(--border);
      border-radius:4px;
      max-height:calc(100vh - 86px);
      overflow:auto;
      padding:10px;
      position:sticky;
      top:58px;
    }}
    .sidebar-title {{
      font-size:16px;
      font-weight:800;
      margin-bottom:2px;
    }}
    .sidebar-subtitle {{
      color:var(--muted);
      font-size:12px;
      margin-bottom:6px;
    }}
    .sidebar-clear {{
      display:block;
      margin-bottom:6px;
      text-align:center;
    }}
    .switch-list {{
      display:flex;
      flex-direction:column;
      gap:2px;
    }}
    .switch-item {{
      border:1px solid transparent;
      border-radius:4px;
      color:var(--text);
      display:block;
      padding:4px 8px;
    }}
    .switch-item:hover {{
      background:var(--table-hover);
      border-color:var(--border);
    }}
    .switch-item.selected {{
      background:rgba(143,215,255,.12);
      border-color:rgba(143,215,255,.55);
    }}
    .switch-item span {{
      display:block;
      font-weight:700;
      line-height:1.15;
      overflow:hidden;
      text-overflow:ellipsis;
      white-space:nowrap;
    }}
    .switch-item small {{
      color:var(--muted);
      display:block;
      font-size:10px;
      line-height:1.1;
      overflow:hidden;
      text-overflow:ellipsis;
      white-space:nowrap;
    }}
    .switch-main {{
      min-width:0;
    }}
    table.report {{
      border-collapse:collapse;
      width:100%;
    }}
    .report th,.report td {{
      border:1px solid var(--border);
      padding:4px 7px;
      white-space:nowrap;
    }}
    .report th {{
      background:var(--table-head);
      cursor:pointer;
      text-align:left;
      user-select:none;
    }}
    .report tr:nth-child(even) td {{ background:var(--table-alt); }}
    .report tr:hover td {{ background:var(--table-hover); }}
    .compact {{ font-size:12px; }}
    .good {{ color:var(--good); font-weight:700; }}
    .bad {{ color:var(--bad); font-weight:700; }}
    .muted {{ color:var(--muted); }}
    .status-cell {{ text-align:center; }}
    .status-pill {{
      border-radius:3px;
      display:inline-block;
      font-size:11px;
      font-weight:800;
      line-height:1;
      min-width:46px;
      padding:3px 6px;
      text-align:center;
      text-transform:lowercase;
    }}
    .status-up {{
      background:rgba(114,214,139,.13);
      border:1px solid rgba(114,214,139,.45);
      color:var(--good);
    }}
    .status-down {{
      background:rgba(255,138,138,.13);
      border:1px solid rgba(255,138,138,.45);
      color:var(--bad);
    }}
    .status-admin {{
      background:rgba(168,179,189,.12);
      border:1px solid rgba(168,179,189,.35);
      color:var(--muted);
    }}
    .status-unknown {{
      background:rgba(143,215,255,.10);
      border:1px solid rgba(143,215,255,.30);
      color:var(--link);
    }}
    @media (max-width:1100px) {{
      .dashboard-grid {{ grid-template-columns:1fr; }}
      .switch-layout {{ grid-template-columns:1fr; }}
      .switch-sidebar {{ max-height:none; position:static; }}
    }}
    @media (max-width:900px) {{
      .topbar {{ align-items:flex-start; flex-direction:column; }}
      .top-search {{ margin-left:0; width:100%; }}
      .top-search input {{ min-width:0; width:100%; }}
      main {{ padding:7px; }}
      input {{ min-width:0; width:100%; }}
      .toolbar {{ display:grid; grid-template-columns:1fr auto auto; }}
      .hero h1 {{ font-size:24px; }}
    }}

  
    /* AKIPS-style compact switch selector */
    .switch-layout {{
      grid-template-columns:210px minmax(0,1fr);
    }}
    .switch-sidebar {{
      padding:6px;
    }}
    .switch-top {{
      align-items:center;
      display:flex;
      justify-content:space-between;
      margin-bottom:4px;
    }}
    .switch-top strong {{
      font-size:13px;
    }}
    .switch-top span {{
      color:var(--muted);
      font-size:11px;
    }}
    .switch-clear {{
      background:var(--deep);
      border:1px solid var(--border);
      border-radius:3px;
      color:var(--text);
      display:block;
      font-size:11px;
      margin-bottom:4px;
      padding:3px 6px;
      text-align:center;
    }}
    .switch-list {{
      gap:1px;
    }}
    .switch-row {{
      border:1px solid transparent;
      border-radius:2px;
      color:var(--text);
      display:block;
      font-size:12px;
      font-weight:700;
      line-height:1.15;
      overflow:hidden;
      padding:3px 5px;
      text-overflow:ellipsis;
      white-space:nowrap;
    }}
    .switch-row:hover {{
      background:var(--table-hover);
      border-color:var(--border);
    }}
    .switch-row.selected {{
      background:rgba(143,215,255,.16);
      border-color:rgba(143,215,255,.55);
      color:var(--link);
    }}

  
    .switch-filter {{
      background:var(--input-bg);
      border:1px solid var(--border);
      border-radius:3px;
      color:var(--input-text);
      font-size:12px;
      margin-bottom:4px;
      min-width:0;
      padding:4px 6px;
      width:100%;
    }}
    .switch-divider {{
      color:var(--muted);
      font-size:10px;
      font-weight:700;
      margin:5px 2px 2px;
      text-transform:uppercase;
    }}

  
    /* In AKIPS two-column mode, device identity lives in the left selector */
    


    /* AKIPS-style MiddKiPS header/dropdowns. Keep existing page/report CSS intact. */
    .topbar {{
      gap:0;
      padding:0 10px 0 0;
      min-height:42px;
      overflow:visible;
    }}
    .brand {{
      align-items:center;
      border-right:1px solid var(--border);
      display:flex;
      min-height:42px;
      padding:0 18px 0 14px;
    }}
    .brand a {{
      align-items:baseline;
      display:inline-flex;
      font-size:28px;
      font-weight:800;
      letter-spacing:-0.055em;
      line-height:1;
      opacity:.95;
    }}
    .brand a:hover {{
      color:var(--text);
      text-decoration:none;
    }}
    .brand-m {{
      color:#4d7fa8;
      font-weight:900;
    }}
    .brand-rest {{
      color:var(--muted);
    }}
    .main-nav {{
      align-items:stretch;
      display:flex;
      flex-wrap:nowrap;
      gap:0;
      min-height:42px;
    }}
    .nav-item {{
      position:relative;
    }}
    .nav-button {{
      align-items:center;
      color:var(--text);
      cursor:default;
      display:flex;
      font-size:15px;
      height:42px;
      opacity:.88;
      padding:0 14px;
      user-select:none;
    }}
    .nav-item:hover > .nav-button {{
      background:var(--panel);
      opacity:1;
    }}
    .nav-menu {{
      background:var(--panel);
      border:1px solid var(--border);
      box-shadow:0 8px 24px rgba(0,0,0,.28);
      display:none;
      left:0;
      min-width:220px;
      padding:7px 0;
      position:absolute;
      top:42px;
      z-index:9999;
    }}
    :root[data-theme="light"] .nav-menu {{
      box-shadow:0 8px 24px rgba(0,0,0,.16);
    }}
    .nav-item:hover > .nav-menu {{
      display:block;
    }}
    .nav-section {{
      border-top:1px solid var(--border);
      color:var(--muted);
      font-size:13px;
      margin-top:5px;
      padding:7px 14px 4px;
    }}
    .nav-section:first-child {{
      border-top:0;
      margin-top:0;
    }}
    .nav-menu a {{
      color:var(--text);
      display:block;
      font-size:14px;
      line-height:1.2;
      opacity:.94;
      padding:6px 20px;
      text-decoration:none;
    }}
    .nav-menu a:hover {{
      background:var(--table-hover);
      color:var(--link);
      text-decoration:none;
    }}
    .nav-row {{
      position:relative;
    }}
    .nav-row > a::after {{
      color:var(--muted);
      content:"›";
      position:absolute;
      right:12px;
    }}
    .nav-submenu {{
      background:var(--panel);
      border:1px solid var(--border);
      box-shadow:0 8px 24px rgba(0,0,0,.28);
      display:none;
      left:100%;
      min-width:235px;
      padding:7px 0;
      position:absolute;
      top:-7px;
      z-index:10000;
    }}
    .nav-row:hover > .nav-submenu {{
      display:block;
    }}
    .top-search {{
      align-items:center;
      margin-left:auto;
      padding-left:12px;
    }}
    .top-search input {{
      min-width:260px;
    }}
    .theme-toggle {{
      margin-left:6px;
      white-space:nowrap;
    }}

  </style>
</head>
<body>
<header class="topbar">
  <div class="brand">
    <a href="/"><span class="brand-m">M</span><span class="brand-rest">iddKiPS</span></a>
  </div>

  <nav class="main-nav">
    <div class="nav-item">
      <div class="nav-button">Dashboards</div>
      <div class="nav-menu">
        <div class="nav-section">Dashboards</div>
        <a href="/">Overview</a>
        <a href="/dashboard">Device</a>
        <a href="/reports/events">Events</a>
        <a href="/reports/interface-statistics">Interface</a>
        <a href="https://raccoon.middlebury.edu:8443/" target="_blank" rel="noopener noreferrer">NetFlow / Arkarado</a>
      </div>
    </div>

    <div class="nav-item">
      <div class="nav-button">Reports</div>
      <div class="nav-menu">
        <div class="nav-row">
          <a href="/reports/interface-statistics">Interface</a>
          <div class="nav-submenu">
            <div class="nav-section">Interface Reports</div>
            <a href="/reports/interface-configuration">Interface Configuration</a>
            <a href="/reports/interface-statistics">Interface Statistics</a>
            <a href="/reports/unused-interfaces">Unused Interfaces</a>
          </div>
        </div>

        <div class="nav-row">
          <a href="/devices">Device</a>
          <div class="nav-submenu">
            <div class="nav-section">Device Reports</div>
            <a href="/devices">Devices</a>
            <a href="/reports/vlans">VLANs</a>
            <a href="/reports/events">Events</a>
          </div>
        </div>

        <div class="nav-section">Network</div>
        <a href="/reports/mac-table">MAC Table</a>
        <a href="/reports/arp-ip">ARP / IP</a>
        <a href="/reports/vlans">VLANs</a>
      </div>
    </div>

    <div class="nav-item">
      <div class="nav-button">Topologies</div>
      <div class="nav-menu">
        <div class="nav-section">Recommended</div>
        <a href="/tools/topologies">Topology Maps Home</a>
        <a href="/tools/topology-vmap?site=vt&layout=mist_pods&show_core=1&show_services=1&show_distribution=1&show_access=1&show_nonmist_access=0&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0">Mist POD View</a>
        <a href="/tools/topology-vmap?site=vt&layout=mist_pods&show_core=1&show_services=1&show_distribution=1&show_access=1&show_nonmist_access=1&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0">Mist POD + Non-Mist Inventory</a>
        <a href="/tools/topology-vmap?site=vt&layout=mist_pods&show_core=1&show_services=1&show_distribution=1&show_access=1&show_nonmist_access=0&show_aps=0&show_ports=1&flow=1&redundant_only=1&tv=0">Mist POD + Ports</a>

        <div class="nav-section">Core / Full</div>
        <a href="/tools/topology-vmap?site=vt&show_core=1&show_services=1&show_distribution=1&show_access=0&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0">Core / Services / Distribution</a>
        <a href="/tools/topology-vmap?site=vt&show_core=1&show_services=1&show_distribution=1&show_access=1&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0">Full V-Map</a>
        <a href="/tools/topology-vmap?site=vt&show_core=1&show_services=1&show_distribution=1&show_access=1&show_aps=0&show_ports=1&flow=1&redundant_only=1&tv=0">Full V-Map + Ports</a>

        <div class="nav-section">Legacy / Other</div>
        <a href="/tools/topology-v2-split?q=core&limit=250&show_aps=0">Topology v2 Split</a>
        <a href="/tools/topology-v2?q=core&limit=250&show_aps=0">Standard v2</a>
        <a href="/tools/topology">Logical Topology</a>
        <a href="/tools/mist/topology">Legacy Mist Logical Topology</a>

        <div class="nav-section">Wallboard</div>
        <a href="/tv/topology?zoom=1.35">TV Topology</a>
        <a href="/tv/topology-wallboard">Topology Wallboard</a>
      </div>
    </div>

    <div class="nav-item">
      <div class="nav-button">Tools</div>
      <div class="nav-menu">
        <div class="nav-section">Lookup</div>
        <a href="/lookup">Universal Lookup</a>
        <a href="/tools/lookup">Lookup Hub</a>
        <a href="/tools/clearpass">ClearPass Lookup</a>
        <a href="/tools/lldp-lookup">LLDP Lookup</a>
        <a href="/tools/unmatched-lldp-switches">Unmatched LLDP Switches</a>
        <a href="/tools/mist">Mist Lookup</a>

        <div class="nav-section">Config / DDI</div>
        <a href="/config/">Config Lookup</a>
        <a href="/tools/solidserver">SolidServer</a>
      </div>
    </div>

    <div class="nav-item">
      <div class="nav-button">NetOps</div>
      <div class="nav-menu">
        <div class="nav-section">Middlebury Ops</div>
        <a href="/devices">LibreNMS Devices</a>
        <a href="/reports/events">LibreNMS Events</a>
        <a href="/tools/solidserver">SolidServer DDI</a>
        <a href="/reports/unused-interfaces">Unused Interfaces</a>
        <div class="nav-section">Switching</div>
        <a href="/reports/interface-configuration">Interface Config</a>
        <a href="/reports/interface-statistics">Interface Stats</a>
        <a href="/reports/mac-table">MAC Table</a>
        <a href="/reports/arp-ip">ARP / IP</a>
      </div>
    </div>

    <div class="nav-item">
      <div class="nav-button">Admin</div>
      <div class="nav-menu">
        <div class="nav-section">MiddKiPS</div>
        <a href="/dashboard">Dashboard</a>
        <a href="/reports/events">Events</a>
      </div>
    </div>
  </nav>

  <form class="top-search" method="get" action="/lookup">
    <input name="q" placeholder="Universal lookup: IP, MAC, hostname, FQDN, VLAN, interface">
    <button type="submit">Lookup</button>
    <button class="theme-toggle" type="button" onclick="toggleTheme()">Theme</button>
  </form>
</header>
<main>{body}</main>
<script>

function filterSwitches(value) {{
  var needle = String(value || "").toLowerCase();
  document.querySelectorAll("[data-switch-search]").forEach(function (row) {{
    var haystack = row.getAttribute("data-switch-search") || "";
    row.style.display = haystack.indexOf(needle) >= 0 ? "" : "none";
  }});
}}

function saveSwitchScroll() {{
  var el = document.getElementById("switchSidebar");
  if (el) sessionStorage.setItem("middkipsSwitchScroll", String(el.scrollTop));
}}

document.addEventListener("DOMContentLoaded", function () {{
  var el = document.getElementById("switchSidebar");
  var saved = sessionStorage.getItem("middkipsSwitchScroll");
  if (el && saved !== null) el.scrollTop = parseInt(saved, 10) || 0;
}});

function middkipsRootPath() {{
  return "{h(APP_ROOT_PATH)}";
}}

function stripMiddkipsRoot(path) {{
  var root = middkipsRootPath();
  if (!root) return path;
  if (path === root) return "/";
  if (path.indexOf(root + "/") === 0) return path.slice(root.length) || "/";
  return path;
}}

function addMiddkipsRoot(path) {{
  var root = middkipsRootPath();
  if (!root) return path;
  path = stripMiddkipsRoot(path);
  if (path === "/") return root + "/";
  return root + path;
}}

function isSelectionPath(path) {{
  path = stripMiddkipsRoot(path);
  return path === "/dashboard" || path === "/devices" || path.indexOf("/reports/") === 0;
}}

function clearSelectedSwitches() {{
  sessionStorage.removeItem("middkipsSelectedDeviceIds");
  saveSwitchScroll();
}}

function syncSelectedSwitches() {{
  var url = new URL(window.location.href);
  var path = url.pathname;
  var ids = url.searchParams.get("device_ids");

  if (ids && ids.trim() !== "") {{
    sessionStorage.setItem("middkipsSelectedDeviceIds", ids);
  }} else if (isSelectionPath(path)) {{
    var saved = sessionStorage.getItem("middkipsSelectedDeviceIds");
    if (saved && saved.trim() !== "") {{
      url.searchParams.set("device_ids", saved);
      window.location.replace(url.toString());
      return;
    }}
  }}

  var activeIds = url.searchParams.get("device_ids") || sessionStorage.getItem("middkipsSelectedDeviceIds") || "";

  document.querySelectorAll("a[href]").forEach(function (link) {{
    if (link.classList.contains("switch-clear")) return;
    if (link.closest && link.closest("#switchSidebar")) return;

    var href = link.getAttribute("href") || "";
    if (href.indexOf("/") !== 0) return;

    var linkUrl = new URL(href, window.location.origin);

    if (!isSelectionPath(linkUrl.pathname)) return;

    if (activeIds && activeIds.trim() !== "") {{
      linkUrl.searchParams.set("device_ids", activeIds);
    }} else {{
      linkUrl.searchParams.delete("device_ids");
    }}

    link.setAttribute("href", addMiddkipsRoot(linkUrl.pathname) + linkUrl.search + linkUrl.hash);
  }});
}}

document.addEventListener("DOMContentLoaded", syncSelectedSwitches);

function setThemeButtonLabel() {{
  var current = document.documentElement.getAttribute("data-theme") || "dark";
  var button = document.getElementById("themeToggle");
  if (button) button.textContent = current === "light" ? "Dark" : "Light";
}}
function toggleTheme() {{
  var current = document.documentElement.getAttribute("data-theme") || "dark";
  var next = current === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("itsPortalTheme", next);
  setThemeButtonLabel();
}}
document.addEventListener("DOMContentLoaded", setThemeButtonLabel);
document.addEventListener("click", function (event) {{
  const th = event.target.closest("table.report th");
  if (!th) return;
  const table = th.closest("table");
  const tbody = table.tBodies[0];
  if (!tbody) return;
  const headers = Array.from(th.parentElement.children);
  const index = headers.indexOf(th);
  const direction = th.dataset.sortDirection === "asc" ? "desc" : "asc";
  headers.forEach(h => {{
    h.dataset.sortDirection = "";
    h.textContent = h.textContent.replace(/\\s+[▲▼]$/, "");
  }});
  th.dataset.sortDirection = direction;
  th.textContent = th.textContent.replace(/\\s+[▲▼]$/, "") + (direction === "asc" ? " ▲" : " ▼");
  const collator = new Intl.Collator(undefined, {{ numeric:true, sensitivity:"base" }});
  Array.from(tbody.rows)
    .sort((a,b) => {{
      const av = (a.cells[index]?.innerText || "").trim();
      const bv = (b.cells[index]?.innerText || "").trim();
      const r = collator.compare(av,bv);
      return direction === "asc" ? r : -r;
    }})
    .forEach(row => tbody.appendChild(row));
}});
</script>
</body>
</html>"""



def selected_device_ids(device_ids: str = "") -> list[int]:
    ids: list[int] = []
    for part in str(device_ids or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except Exception:
            continue
        if value not in ids:
            ids.append(value)
    return ids


def ids_csv(ids: list[int]) -> str:
    return ",".join(str(x) for x in ids)


def qs(q: str = "", ids: list[int] | None = None) -> str:
    parts: list[str] = []
    if q:
        parts.append("q=" + quote_plus(q))
    if ids:
        parts.append("device_ids=" + quote_plus(ids_csv(ids)))
    return "?" + "&".join(parts) if parts else ""


def add_device_filter(where_parts: list[str], params: list[Any], ids: list[int], alias: str = "d") -> None:
    if ids:
        where_parts.append(f"{alias}.device_id IN (" + ",".join(["%s"] * len(ids)) + ")")
        params.extend(ids)


def device_catalog() -> list[dict[str, Any]]:
    return safe_query("""
        SELECT
            device_id,
            COALESCE(NULLIF(sysName,''), NULLIF(hostname,''), INET6_NTOA(ip)) AS device,
            hostname,
            sysName,
            INET6_NTOA(ip) AS ip_addr,
            os,
            hardware,
            status
        FROM devices
        ORDER BY device
        LIMIT 1000
    """)

def switch_selector(current_path: str, selected_ids: list[int], q: str = "") -> str:
    rows = device_catalog()

    selected_items = ""
    other_items = ""

    for row in rows:
        try:
            device_id = int(row.get("device_id") or 0)
        except Exception:
            continue

        next_ids = list(selected_ids)
        selected = device_id in next_ids

        if selected:
            next_ids.remove(device_id)
            cls = "switch-row selected"
        else:
            next_ids.append(device_id)
            cls = "switch-row"

        name = device_label(row)
        search_text = " ".join([
            str(name or ""),
            str(row.get("hostname") or ""),
            str(row.get("sysName") or ""),
            str(row.get("os") or ""),
            str(row.get("hardware") or ""),
            str(row.get("ip_addr") or ""),
        ]).lower()

        item = f"""
        <a class="{cls}" href="{current_path}{qs(q, next_ids)}" onclick="saveSwitchScroll()" data-switch-search="{h(search_text)}">
          <span>{h(name)}</span>
        </a>
        """

        if selected:
            selected_items += item
        else:
            other_items += item

    clear_url = current_path + qs(q, [])

    divider = ""
    if selected_items and other_items:
        divider = '<div class="switch-divider">available</div>'

    return f"""
    <aside class="switch-sidebar" id="switchSidebar">
      <div class="switch-top">
        <strong>Switches</strong>
        <span>{len(selected_ids)} selected</span>
      </div>
      <input class="switch-filter" id="switchFilter" placeholder="filter switches" oninput="filterSwitches(this.value)">
      <a class="switch-clear" href="{clear_url}" onclick="clearSelectedSwitches()">clear</a>
      <div class="switch-list">{selected_items}{divider}{other_items}</div>
    </aside>
    """

def two_col(current_path: str, selected_ids: list[int], q: str, body: str) -> str:
    return f"""
    <section class="switch-layout">
      {switch_selector(current_path, selected_ids, q)}
      <section class="switch-main">{body}</section>
    </section>
    """


def hidden_device_ids(selected_ids: list[int]) -> str:
    return f'<input type="hidden" name="device_ids" value="{h(ids_csv(selected_ids))}">'

app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def middkips_topology_hub_body():
    production_cards = [
        {
            "title": "Mist POD View",
            "desc": "Primary production topology view. Mist pod layout with core, services, distribution, and access layers.",
            "url": "/tools/topology-vmap?site=vt&layout=mist_pods&show_core=1&show_services=1&show_distribution=1&show_access=1&show_nonmist_access=0&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0",
        },
        {
            "title": "Mist POD + Non-Mist Inventory",
            "desc": "Production pod view with non-Mist inventory included for comparison and migration cleanup.",
            "url": "/tools/topology-vmap?site=vt&layout=mist_pods&show_core=1&show_services=1&show_distribution=1&show_access=1&show_nonmist_access=1&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0",
        },
        {
            "title": "Full V-Map",
            "desc": "Full campus V-map with core, services, distribution, and access layers.",
            "url": "/tools/topology-vmap?site=vt&show_core=1&show_services=1&show_distribution=1&show_access=1&show_aps=0&show_ports=0&flow=1&redundant_only=1&tv=0",
        },
        {
            "title": "Full V-Map + Ports",
            "desc": "Full V-map with ports enabled. Useful for validation, noisier for normal viewing.",
            "url": "/tools/topology-vmap?site=vt&show_core=1&show_services=1&show_distribution=1&show_access=1&show_aps=0&show_ports=1&flow=1&redundant_only=1&tv=0",
        },
    ]

    tv_cards = [
        {
            "title": "TV Topology",
            "desc": "Wallboard-friendly topology route for large display use.",
            "url": "/tv/topology?zoom=1.35",
        },
        {
            "title": "Topology Wallboard",
            "desc": "Alternate topology wallboard view.",
            "url": "/tv/topology-wallboard",
        },
    ]

    legacy_cards = [
        {
            "title": "Topology v2 Split",
            "desc": "Legacy split topology page. Kept for validation and comparison only.",
            "url": "/tools/topology-v2-split?q=core&limit=250&show_aps=0",
        },
        {
            "title": "Standard v2",
            "desc": "Legacy v2 topology page. Kept for comparison only.",
            "url": "/tools/topology-v2?q=core&limit=250&show_aps=0",
        },
        {
            "title": "Legacy Mist Logical Topology",
            "desc": "Disabled legacy Mist-derived topology placeholder. Kept so old links do not break.",
            "url": "/tools/mist/topology?q=core&limit=80",
        },
    ]

    def card_html(card):
        return (
            f'<a class="topology-card" href="{card["url"]}">'
            f'<h3>{card["title"]}</h3>'
            f'<p>{card["desc"]}</p>'
            f'</a>'
        )

    def section(title, desc, cards, legacy=False):
        note_class = "legacy-note" if legacy else "muted"
        return (
            f'<h2 class="section-title">{title}</h2>'
            f'<p class="{note_class}">{desc}</p>'
            '<div class="topology-grid">'
            + "".join(card_html(card) for card in cards)
            + '</div>'
        )

    return f"""
<h1>MiddKiPS</h1>
<p class="muted">
  Network visibility and operational tools for topology, lookup, LibreNMS/Oxidized workflows,
  Mist, ClearPass, and SolidServer.
</p>

<div class="actions" style="margin-bottom:16px">
  <a class="button" href="/lookup">Universal Lookup</a>
  <a class="button" href="/tools/mist">Mist Lookup</a>
  <a class="button" href="/tools/clearpass">ClearPass</a>
  <a class="button" href="/tools/solidserver">SolidServer</a>
</div>

{section(
    "Production Views",
    "Use these first. These are the cleanest topology views for normal operations.",
    production_cards,
)}

{section(
    "TV / Wallboard Views",
    "Large-screen views for NOC, kiosk, and wallboard display.",
    tv_cards,
)}

{section(
    "Legacy / Validation Views",
    "These routes still work, but they are kept for comparison, validation, or old bookmarks. Do not treat them as the primary operational view.",
    legacy_cards,
    legacy=True,
)}
"""

def middkips_home_body():
    return middkips_topology_hub_body()


@app.get("/", response_class=HTMLResponse)
def middkips_home():
    return globals()["layout"]("MiddKiPS", middkips_home_body())



@app.get("/tools/topology")
def compat_tools_topology():
    return RedirectResponse(url="/tools/topologies", status_code=302)

@app.get("/tools/topologies", response_class=HTMLResponse)
def topology_maps_menu():
    return globals()["layout"]("Topology Maps", middkips_topology_hub_body())


@app.get("/topologies", response_class=HTMLResponse)
def topology_maps_menu_shortcut():
    return globals()["layout"]("Topology Maps", middkips_topology_hub_body())


@app.get("/healthz")
def healthz():
    try:
        row = fetch_one("SELECT 1 AS ok")
        return {"status": "ok", "database": bool(row.get("ok"))}
    except Exception as exc:
        return {"status": "error", "database": False, "error": str(exc)}



@app.get("/devices", response_class=HTMLResponse)
def devices(q: str = "", device_ids: str = ""):
    q = (q or "").strip()
    selected_ids = selected_device_ids(device_ids)

    selected_rows: list[dict[str, Any]] = []

    if selected_ids:
        params: list[Any] = list(selected_ids)
        selected_rows = fetch_all(f"""
            SELECT
                d.device_id,
                COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device,
                d.hostname,
                INET6_NTOA(d.ip) AS ip_addr,
                d.os,
                d.hardware,
                d.location_id AS location,
                d.status,
                d.last_polled,
                COUNT(p.port_id) AS ports,
                SUM(CASE WHEN p.ifOperStatus = 'up' THEN 1 ELSE 0 END) AS ports_up,
                SUM(CASE WHEN p.ifOperStatus = 'down' THEN 1 ELSE 0 END) AS ports_down
            FROM devices d
            LEFT JOIN ports p ON p.device_id = d.device_id
            WHERE d.device_id IN ({",".join(["%s"] * len(selected_ids))})
            GROUP BY d.device_id, d.sysName, d.hostname, d.ip, d.os, d.hardware, d.location_id, d.status, d.last_polled
            ORDER BY device
        """, tuple(params))

    rows_html = ""
    for row in selected_rows:
        status = "up" if row.get("status") == 1 else "down"
        rows_html += f"""
        <tr>
          <td>{device_anchor(row, "device")}</td>
          <td>{h(fmt_ip(row.get('ip_addr') or row.get('ip')))}</td>
          <td>{h(row.get('os'))}</td>
          <td>{h(row.get('hardware'))}</td>
          <td>{h(row.get('ports') or 0)}</td>
          <td>{h(row.get('ports_up') or 0)}</td>
          <td>{h(row.get('ports_down') or 0)}</td>
          <td class="status-cell">{status_badge(status)}</td>
          <td>{h(row.get('last_polled'))}</td>
        </tr>"""

    if selected_rows:
        body = f"""
        <section class="panel">
          <div class="panel-head">
            <h1>Selected Switches</h1>
            <div class="actions">
              <a class="button" href="/reports/interface-configuration?device_ids={h(ids_csv(selected_ids))}">Interface Config</a>
              <a class="button" href="/reports/interface-statistics?device_ids={h(ids_csv(selected_ids))}">Interface Stats</a>
              <a class="button" href="/reports/mac-table?device_ids={h(ids_csv(selected_ids))}">MAC Table</a>
            </div>
          </div>

          <form class="toolbar" method="get">
            {hidden_device_ids(selected_ids)}
            <input name="q" value="{h(q)}" placeholder="Filter selected switches">
            <button>Search</button>
            <a class="button" href="/devices">Clear</a>
          </form>

          {table(["Device", "IPv4", "OS", "Hardware", "Ports", "Up", "Down", "Status", "Last Poll"], rows_html)}
        </section>
        """
    else:
        body = """
        <section class="panel">
          <h1>Devices</h1>
          <p class="muted">Select one or more switches from the left scroller.</p>
          <p>The selected switches will appear here, and the same selection carries into Interface Configuration, Interface Statistics, and MAC Table reports.</p>
        </section>
        """

    return layout("Devices", two_col("/dashboard", selected_ids, q, body))








def _device_diag_record(device_id: int) -> dict:
    return fetch_one("""
        SELECT
            d.*,
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device,
            INET6_NTOA(d.ip) AS ip_addr
        FROM devices d
        WHERE d.device_id = %s
    """, (device_id,))


def _diag_display_name(dev: dict) -> str:
    if not dev:
        return ""
    return device_label(dev) or dev.get("sysName") or dev.get("hostname") or dev.get("ip_addr") or str(dev.get("device_id"))


def _diag_layout(title: str, device_id: int, q: str, device_ids: str, body: str):
    selected_ids = selected_device_ids(device_ids)
    if device_id not in selected_ids:
        selected_ids = [device_id] + selected_ids
    return layout(title, two_col("/dashboard", selected_ids, q, body))


@app.get("/device/{device_id}/ping", response_class=HTMLResponse)
def device_ping(device_id: int, q: str = "", device_ids: str = "", count: int = 4):
    dev = _device_diag_record(device_id)
    if not dev:
        return layout("Device not found", "<section class='panel'><h1>Device not found</h1></section>")

    display_name = _diag_display_name(dev)
    target = dev.get("ip_addr") or dev.get("hostname") or dev.get("device")
    count = max(1, min(int(count or 4), 10))

    if not shutil.which("ping"):
        rc = 1
        output = "ping is not installed in the MiddKIPS container."
    else:
        cmd = ["ping", "-c", str(count), "-W", "2", str(target)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=(count * 3) + 5)
            rc = proc.returncode
            output = (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired as exc:
            rc = 124
            output = f"Ping timed out after {exc.timeout} seconds."

    body = f"""
<section class="panel">
  <div class="panel-head">
    <h1>Ping: {h(display_name)}</h1>
    <div class="actions">
      <a class="button" href="/device/{h(device_id)}">Dashboard</a>
      <a class="button" href="/device/{h(device_id)}/ping">Ping Again</a>
      <a class="button" href="/device/{h(device_id)}/snmpwalk">SNMP Walk</a>
    </div>
  </div>
  <p><strong>Target:</strong> {h(target)} &nbsp; <strong>Status:</strong> {h('OK' if rc == 0 else 'Exit ' + str(rc))}</p>
  <pre style="white-space:pre-wrap;background:#0d1117;border:1px solid var(--border);padding:10px;border-radius:6px;overflow:auto;max-height:650px;">{h(output)}</pre>
</section>
"""
    return _diag_layout(f"Ping {display_name}", device_id, q, device_ids, body)


def _snmp_v3_command(dev: dict, target: str, oid: str):
    authlevel = str(dev.get("authlevel") or "").strip()
    authname = dev.get("authname") or dev.get("security_name") or dev.get("secname")
    authalgo = str(dev.get("authalgo") or "SHA").upper()
    authpass = dev.get("authpass") or dev.get("auth_pass")
    cryptoalgo = str(dev.get("cryptoalgo") or "AES").upper()
    cryptopass = dev.get("cryptopass") or dev.get("crypto_pass")

    if not authlevel:
        if authpass and cryptopass:
            authlevel = "authPriv"
        elif authpass:
            authlevel = "authNoPriv"
        else:
            authlevel = "noAuthNoPriv"

    if not authname:
        return None, "SNMPv3 authname/security name is missing in LibreNMS for this device."

    cmd = ["snmpwalk", "-v3", "-l", authlevel, "-u", str(authname), "-On", "-t", "3", "-r", "1"]

    if authlevel.lower() in ("authnopriv", "authpriv"):
        if not authpass:
            return None, "SNMPv3 auth password is missing in LibreNMS for this device."
        cmd += ["-a", authalgo, "-A", str(authpass)]

    if authlevel.lower() == "authpriv":
        if not cryptopass:
            return None, "SNMPv3 privacy password is missing in LibreNMS for this device."
        cmd += ["-x", cryptoalgo, "-X", str(cryptopass)]

    cmd += [target, oid]
    return cmd, None


@app.get("/device/{device_id}/snmpwalk", response_class=HTMLResponse)
def device_snmpwalk(device_id: int, q: str = "", device_ids: str = "", oid: str = "1.3.6.1.2.1.1", max_lines: int = 300):
    dev = _device_diag_record(device_id)
    if not dev:
        return layout("Device not found", "<section class='panel'><h1>Device not found</h1></section>")

    display_name = _diag_display_name(dev)
    target_ip = dev.get("ip_addr") or dev.get("hostname") or dev.get("device")
    snmpver = str(dev.get("snmpver") or "v2c").lower().replace("v", "")
    community = dev.get("community")
    port = int(dev.get("port") or 161)
    transport = str(dev.get("transport") or "udp").lower()
    oid = oid.strip() or "1.3.6.1.2.1.1"
    max_lines = max(25, min(int(max_lines or 300), 1000))

    if not re.match(r"^[0-9.]+$", oid):
        oid = "1.3.6.1.2.1.1"

    target = f"{transport}:{target_ip}:{port}"

    if not shutil.which("snmpwalk"):
        rc = 1
        output = "snmpwalk is not installed in the MiddKIPS container."
    elif not target_ip:
        rc = 1
        output = "No IP or hostname found for this device."
    else:
        if snmpver in ("1", "2", "2c"):
            if not community:
                cmd = None
                err = "No SNMP community found in LibreNMS for this v1/v2c device."
            else:
                cmd = ["snmpwalk", "-v", "2c" if snmpver in ("2", "2c") else "1", "-c", str(community), "-On", "-t", "3", "-r", "1", target, oid]
                err = None
        elif snmpver == "3":
            cmd, err = _snmp_v3_command(dev, target, oid)
        else:
            cmd = None
            err = f"Unsupported SNMP version: {snmpver}"

        if not cmd:
            rc = 1
            output = err
        else:
            safe_cmd = []
            skip_next = False
            for i, part in enumerate(cmd):
                if skip_next:
                    safe_cmd.append("REDACTED")
                    skip_next = False
                    continue
                safe_cmd.append(part)
                if part in ("-c", "-A", "-X"):
                    skip_next = True

            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                rc = proc.returncode
                raw = (proc.stdout or "") + (proc.stderr or "")
                lines = raw.splitlines()
                if len(lines) > max_lines:
                    raw = "\n".join(lines[:max_lines]) + f"\n... truncated at {max_lines} lines ..."
                output = "$ " + " ".join(safe_cmd) + "\n\n" + raw
            except subprocess.TimeoutExpired as exc:
                rc = 124
                output = f"SNMP walk timed out after {exc.timeout} seconds."

    body = f"""
<section class="panel">
  <div class="panel-head">
    <h1>SNMP Walk: {h(display_name)}</h1>
    <div class="actions">
      <a class="button" href="/device/{h(device_id)}">Dashboard</a>
      <a class="button" href="/device/{h(device_id)}/ping">Ping</a>
      <a class="button" href="/device/{h(device_id)}/snmpwalk?oid=1.3.6.1.2.1.1">System</a>
      <a class="button" href="/device/{h(device_id)}/snmpwalk?oid=1.3.6.1.2.1.2">Interfaces</a>
    </div>
  </div>
  <p><strong>Target:</strong> {h(target_ip)} &nbsp; <strong>OID:</strong> {h(oid)} &nbsp; <strong>Status:</strong> {h('OK' if rc == 0 else 'Exit ' + str(rc))}</p>
  <form method="get" action="/device/{h(device_id)}/snmpwalk" style="margin-bottom:10px;">
    <input name="oid" value="{h(oid)}" style="width:320px;" />
    <button type="submit">Walk OID</button>
  </form>
  <pre style="white-space:pre-wrap;background:#0d1117;border:1px solid var(--border);padding:10px;border-radius:6px;overflow:auto;max-height:650px;">{h(output)}</pre>
</section>
"""
    return _diag_layout(f"SNMP Walk {display_name}", device_id, q, device_ids, body)

@app.get("/device/{device_id}", response_class=HTMLResponse)
def device(device_id: int, q: str = "", device_ids: str = ""):
    dev = fetch_one("""
        SELECT
            d.device_id,
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device,
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS friendly_name,
            d.hostname,
            d.sysName,
            INET6_NTOA(d.ip) AS ip_addr,
            d.os,
            d.hardware,
            d.version,
            d.location_id AS location,
            d.status,
            d.uptime,
            d.last_polled
        FROM devices d
        WHERE d.device_id = %s
    """, (device_id,))

    if not dev:
        return layout("Not found", "<section class='panel'><h1>Device not found</h1></section>")

    display_name = dev.get("friendly_name") or dev.get("sysName") or dev.get("hostname") or dev.get("ip_addr") or str(device_id)

    # Match the left switch selector label when possible.
    try:
        for catalog_row in device_catalog():
            if str(catalog_row.get("device_id")) == str(device_id):
                catalog_label = device_label(catalog_row)
                if catalog_label:
                    display_name = catalog_label
                break
    except Exception:
        pass

    dev["device"] = display_name

    ports = safe_query("""
        SELECT
            p.*,
            CASE
                WHEN p.ifLastChange IS NULL OR p.ifLastChange = 0 OR d.uptime IS NULL OR d.last_polled IS NULL THEN NULL
                ELSE DATE_SUB(d.last_polled, INTERVAL CAST(GREATEST(d.uptime - (p.ifLastChange / 100), 0) AS UNSIGNED) SECOND)
            END AS ifLastChange_at,
            COALESCE(f.mac_count, 0) AS mac_count
        FROM ports p
        JOIN devices d ON d.device_id = p.device_id
        LEFT JOIN (
            SELECT port_id, COUNT(*) AS mac_count
            FROM ports_fdb
            GROUP BY port_id
        ) f ON f.port_id = p.port_id
        WHERE p.device_id = %s
        ORDER BY
            CASE WHEN p.ifName REGEXP '^[a-zA-Z]+-[0-9]+/[0-9]+/[0-9]+' THEN 0 ELSE 1 END,
            p.ifName
    """, (device_id,))

    cpu = safe_query("""
        SELECT processor_descr AS descr, processor_usage AS pct
        FROM processors
        WHERE device_id = %s
        ORDER BY processor_usage DESC
        LIMIT 12
    """, (device_id,))

    mem = safe_query("""
        SELECT mempool_descr AS descr, mempool_perc AS pct
        FROM mempools
        WHERE device_id = %s
        ORDER BY mempool_perc DESC
        LIMIT 12
    """, (device_id,))

    storage = safe_query("""
        SELECT storage_descr AS descr, storage_perc AS pct
        FROM storage
        WHERE device_id = %s
        ORDER BY storage_perc DESC
        LIMIT 20
    """, (device_id,))

    events = safe_query("""
        SELECT datetime, type, severity, message
        FROM eventlog
        WHERE device_id = %s
        ORDER BY datetime DESC
        LIMIT 12
    """, (device_id,))

    summary = {
        "interfaces": len(ports),
        "up": sum(1 for row in ports if str(row.get("ifOperStatus") or "").lower() == "up"),
        "down": sum(1 for row in ports if str(row.get("ifOperStatus") or "").lower() == "down"),
        "admin_down": sum(1 for row in ports if str(row.get("ifAdminStatus") or "").lower() == "down"),
    }

    problem_ports = [
        row for row in ports
        if str(row.get("ifOperStatus") or "").lower() != "up"
        and str(row.get("ifAdminStatus") or "").lower() == "up"
    ][:12]

    busy_ports = sorted(
        ports,
        key=lambda row: safe_float(row.get("ifInOctets_rate")) + safe_float(row.get("ifOutOctets_rate")),
        reverse=True,
    )[:15]

    # If rates are mostly empty, still show useful ports rather than a blank table.
    if not busy_ports and ports:
        busy_ports = ports[:15]

    if ports and all((safe_float(row.get("ifInOctets_rate")) + safe_float(row.get("ifOutOctets_rate"))) == 0 for row in busy_ports):
        busy_ports = ports[:15]

    problem_rows = ""
    for row in problem_ports:
        problem_rows += f"""
        <tr>
          <td>{interface_anchor(row)}</td>
          <td class="status-cell">{status_badge(row.get('ifOperStatus'))}</td>
          <td>{h(row.get('ifAlias') or row.get('ifDescr'))}</td>
        </tr>"""

    vital_rows = ""
    for row in cpu:
        vital_rows += f"<tr><td>CPU</td><td>{h(row.get('pct'))}%</td><td>{h(row.get('descr'))}</td></tr>"
    for row in mem:
        vital_rows += f"<tr><td>Mem</td><td>{h(row.get('pct'))}%</td><td>{h(row.get('descr'))}</td></tr>"
    for row in storage:
        vital_rows += f"<tr><td>Disk</td><td>{h(row.get('pct'))}%</td><td>{h(row.get('descr'))}</td></tr>"

    event_rows = ""
    for row in events:
        sev = str(row.get("severity") or "")
        sev_class = "bad" if sev in ("4", "5", "critical", "error") else ""
        event_rows += f"""
        <tr>
          <td>{h(row.get('datetime'))}</td>
          <td>{h(row.get('type'))}</td>
          <td class="{sev_class}">{h(row.get('severity'))}</td>
          <td>{h(row.get('message'))}</td>
        </tr>"""

    activity_rows = ""
    for row in busy_ports:
        error_total = safe_float(row.get("ifInErrors_rate")) + safe_float(row.get("ifOutErrors_rate"))
        activity_rows += f"""
        <tr>
          <td>{interface_anchor(row)}</td>
          <td class="status-cell">{status_badge(row.get('ifOperStatus'))}</td>
          <td>{h(fmt_speed(row.get('ifSpeed')))}</td>
          <td>{h(fmt_rate(row.get('ifInOctets_rate')))}</td>
          <td>{h(fmt_rate(row.get('ifOutOctets_rate')))}</td>
          <td>{h(error_total)}</td>
          <td>{h(row.get('mac_count'))}</td>
          <td>{h(row.get('ifAlias') or row.get('ifDescr'))}</td>
        </tr>"""

    if not activity_rows and ports:
        for row in ports[:15]:
            activity_rows += f"""
            <tr>
              <td>{interface_anchor(row)}</td>
              <td class="status-cell">{status_badge(row.get('ifOperStatus'))}</td>
              <td>{h(fmt_speed(row.get('ifSpeed')))}</td>
              <td></td>
              <td></td>
              <td>0</td>
              <td>{h(row.get('mac_count'))}</td>
              <td>{h(row.get('ifAlias') or row.get('ifDescr'))}</td>
            </tr>"""

    port_title = f'{summary["interfaces"]} Interfaces: {summary["up"]} up, {summary["down"]} down'

    body = f"""
<style>
.akips-device-grid {{
  display:grid;
  grid-template-columns: 280px 330px minmax(620px, 1fr);
  gap:10px;
  align-items:start;
}}
.akips-mini-stack {{
  display:grid;
  grid-template-columns:1fr;
  gap:10px;
}}
.akips-center-stack {{
  display:grid;
  grid-template-columns:1fr;
  gap:10px;
}}
.akips-panel-title {{
  text-align:center;
  font-weight:700;
  margin:0 0 8px 0;
}}
.akips-tabbar {{
  display:flex;
  gap:6px;
  justify-content:center;
  margin:0 0 8px 0;
}}
.akips-tabbar a {{
  border:1px solid var(--border);
  border-radius:4px;
  padding:5px 9px;
  text-decoration:none;
  color:var(--text);
  background:var(--panel2);
}}
.akips-availability {{
  display:grid;
  grid-template-columns:1fr 90px;
  gap:8px;
  align-items:center;
}}
.akips-bar {{
  height:10px;
  border:1px solid var(--border);
  background:linear-gradient(90deg, rgba(46,160,67,.7), rgba(46,160,67,.2));
}}
.akips-events-box {{
  min-height:62px;
}}
@media (max-width: 1300px) {{
  .akips-device-grid {{
    grid-template-columns:1fr;
  }}
}}
</style>

<section class="panel">
  <div class="panel-head">
    <h1>{h(display_name)}</h1>
    <div class="actions">
      <a class="button" href="{LIBRENMS_BASE_URL}/device/device={h(dev.get('device_id'))}/">LibreNMS</a>
      <a class="button" href="/device/{h(device_id)}/config.txt">Download Config</a>
      <a class="button" href="/device/{h(device_id)}/ping">Ping</a>
      <a class="button" href="/device/{h(device_id)}/snmpwalk">SNMP Walk</a>
    </div>
  </div>

  <table class="identity">
    <tr>
      <th>Device</th>
      <th>IPv4</th>
      <th>Uptime</th>
      <th>Location ID</th>
      <th>Identifier</th>
      <th>Description</th>
    </tr>
    <tr>
      <td>{h(display_name)}</td>
      <td>{h(fmt_ip(dev.get('ip_addr') or dev.get('ip')))}</td>
      <td>{h(dev.get('uptime'))}</td>
      <td>{h(dev.get('location'))}</td>
      <td>{h(dev.get('hardware'))}</td>
      <td>{h(dev.get('os'))} {h(dev.get('version'))}</td>
    </tr>
  </table>

  <section class="cards">
    {card("Interfaces", summary["interfaces"])}
    {card("Up", summary["up"])}
    {card("Down", summary["down"])}
    {card("Admin Down", summary["admin_down"])}
  </section>
</section>

<section class="akips-device-grid">
  <section class="akips-mini-stack">
    <section class="panel akips-events-box">
      <h2 class="akips-panel-title">Events</h2>
      {table(["Date/Time", "Type", "Severity"], "".join(f"<tr><td>{h(r.get('datetime'))}</td><td>{h(r.get('type'))}</td><td>{h(r.get('severity'))}</td></tr>" for r in events[:5]))}
    </section>

    <section class="panel">
      <h2 class="akips-panel-title">Status Exceptions</h2>
      {table(["Interface", "Problem", "Title"], problem_rows)}
    </section>
  </section>

  <section class="akips-center-stack">
    <section class="panel">
      <h2 class="akips-panel-title">Availability</h2>
      <div class="akips-availability">
        <div>Ping / SNMP</div>
        <div class="akips-bar"></div>
      </div>
    </section>

    <section class="panel">
      <h2 class="akips-panel-title">Vitals</h2>
      {table(["Type", "Value", "Description"], vital_rows)}
    </section>
  </section>

  <section class="panel">
    <h2 class="akips-panel-title">{h(port_title)}</h2>
    <div class="akips-tabbar">
      <a href="/reports/interface-configuration?device_ids={h(device_id)}">Config</a>
      <a href="/reports/interface-statistics?device_ids={h(device_id)}">Statistics</a>
      <a href="/reports/mac-table?device_ids={h(device_id)}">MAC Table</a>
      <a href="/reports/events?device_ids={h(device_id)}">Events</a>
    </div>
    {table(["Interface", "Status", "Speed", "In", "Out", "Errors", "MACs", "Title"], activity_rows)}
  </section>
</section>

<section class="panel">
  <h2 class="center">Eventlog</h2>
  {table(["Date/Time", "Type", "Severity", "Message"], event_rows)}
</section>
"""

    selected_ids = selected_device_ids(device_ids)
    if device_id not in selected_ids:
        selected_ids = [device_id] + selected_ids

    return layout(str(display_name), two_col("/devices", selected_ids, q, body))


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(q: str = "", device_ids: str = ""):
    selected_ids = selected_device_ids(device_ids)

    if len(selected_ids) == 1:
        return device(int(selected_ids[0]), q, device_ids)

    if len(selected_ids) == 0:
        message = """
<section class="panel">
  <h1>Dashboard</h1>
  <p>Select one switch on the left to open its dashboard.</p>
</section>
"""
    else:
        message = f"""
<section class="panel">
  <h1>Dashboard</h1>
  <p>{h(len(selected_ids))} switches are selected. Select exactly one switch to open a device dashboard.</p>
  <div class="actions">
    <a class="button" href="/reports/interface-configuration?device_ids={h(",".join(str(x) for x in selected_ids))}">Interface Config</a>
    <a class="button" href="/reports/interface-statistics?device_ids={h(",".join(str(x) for x in selected_ids))}">Interface Stats</a>
    <a class="button" href="/reports/mac-table?device_ids={h(",".join(str(x) for x in selected_ids))}">MAC Table</a>
  </div>
</section>
"""

    return layout("Dashboard", two_col("/dashboard", selected_ids, q, message))


def _norm_akips_key_part(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _akips_unused_key(device, interface) -> str:
    return f"{_norm_akips_key_part(device)}|{_norm_akips_key_part(interface)}"


def _load_akips_unused_cache() -> dict:
    try:
        with open(AKIPS_UNUSED_CACHE_FILE, "r") as fh:
            return json.load(fh)
    except Exception:
        return {"ports": {}}


@app.get("/reports/interface-configuration", response_class=HTMLResponse)
def report_interface_configuration(q: str = "", device_ids: str = ""):
    selected_ids = selected_device_ids(device_ids)

    selected_clause = ""
    params = []

    if selected_ids:
        placeholders = ",".join(["%s"] * len(selected_ids))
        selected_clause = f" AND d.device_id IN ({placeholders}) "
        params.extend(selected_ids)

    search_clause = ""
    if q:
        search_clause = """
          AND (
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) LIKE %s
            OR d.hostname LIKE %s
            OR p.ifName LIKE %s
            OR p.ifDescr LIKE %s
            OR p.ifAlias LIKE %s
            OR p.ifType LIKE %s
          )
        """
        like = f"%{q}%"
        params.extend([like, like, like, like, like, like])

    last_change_expr = """
        CASE
            WHEN p.ifLastChange IS NULL OR p.ifLastChange = 0 OR d.uptime IS NULL OR d.last_polled IS NULL THEN NULL
            ELSE DATE_SUB(d.last_polled, INTERVAL CAST(GREATEST(d.uptime - (p.ifLastChange / 100), 0) AS UNSIGNED) SECOND)
        END
    """

    sql = f"""
        SELECT
            d.device_id,
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device,
            INET6_NTOA(d.ip) AS switch_ip,
            p.port_id,
            p.ifName,
            p.ifDescr,
            p.ifAlias,
            p.ifOperStatus,
            p.ifAdminStatus,
            p.ifSpeed,
            p.ifDuplex,
            p.ifPhysAddress,
            p.ifType,
            p.ifLastChange,
            {last_change_expr} AS snmp_last_change
        FROM ports p
        JOIN devices d ON d.device_id = p.device_id
        WHERE p.ifName IS NOT NULL
          AND p.ifName <> ''
          {selected_clause}
          {search_clause}
        ORDER BY d.device_id, p.port_id
    """

    rows = safe_query(sql, tuple(params))

    akips_cache = _load_akips_unused_cache()
    akips_ports = akips_cache.get("ports", {}) or {}
    akips_state_window = akips_cache.get("state_window") or ""

    for row in rows:
        akips_row = akips_ports.get(_akips_unused_key(row.get("device"), row.get("ifName")))
        row["akips_last_change"] = akips_row.get("last_change") if akips_row else ""
        row["akips_state"] = akips_row.get("state") if akips_row else ""
        row["akips_current"] = akips_row.get("current") if akips_row else ""

        if akips_row and akips_row.get("title") and not row.get("ifAlias"):
            row["ifAlias"] = akips_row.get("title")

    detail_limit = 2000
    body_rows = ""

    for row in rows[:detail_limit]:
        admin_badge = status_badge(row.get("ifAdminStatus") or "")
        oper_badge = status_badge(row.get("ifOperStatus") or "")
        akips_last = row.get("akips_last_change") or row.get("snmp_last_change") or ""

        body_rows += f"""
        <tr>
          <td><a href="/dashboard?device_ids={h(row.get('device_id'))}">{h(row.get('device'))}</a></td>
          <td>{interface_anchor(row)}</td>
          <td>{h(fmt_speed(row.get('ifSpeed')))}</td>
          <td>{admin_badge}</td>
          <td>{h(row.get('snmp_last_change') or '')}</td>
          <td>{oper_badge}</td>
          <td>{h(akips_last)}</td>
          <td>{h(row.get('ifDuplex') or '')}</td>
          <td>{h(row.get('ifPhysAddress') or '')}</td>
          <td></td>
          <td>{h(row.get('ifType') or '')}</td>
          <td>{h(row.get('ifDescr') or '')}</td>
          <td>{h(row.get('ifAlias') or '')}</td>
        </tr>"""

    if len(rows) > detail_limit:
        body_rows += f"""
        <tr>
          <td colspan="13">Showing first {h(detail_limit)} of {h(len(rows))} interfaces. Use a switch selection or filter to narrow results.</td>
        </tr>"""

    akips_note = f"AKIPS cache: {h(akips_state_window)}" if akips_state_window else "AKIPS cache loaded"

    body = f"""
<section class="panel">
  <h1 class="center">Interface Configuration</h1>
  <div class="unused-total">Top {h(min(len(rows), detail_limit))} of {h(len(rows))} — {akips_note}</div>

  <form class="unused-controls" method="get" action="/reports/interface-configuration">
    {hidden_device_ids(selected_ids)}
    <input name="q" value="{h(q)}" placeholder="Filter selected switches, interface, title, type" />
    <button type="submit">Search</button>
    <a class="button" href="/reports/interface-configuration?device_ids={h(','.join(str(x) for x in selected_ids))}">Clear</a>
  </form>

  {table(["Device", "Interface", "Speed", "Admin State", "Admin Last Change", "Status", "AKIPS Last Change", "Duplex", "MAC", "IPAddr", "Type", "Description", "Title"], body_rows)}
</section>
"""

    return layout("Interface Configuration", two_col("/reports/interface-configuration", selected_ids, q, body))

@app.get("/reports/interface-statistics", response_class=HTMLResponse)
def interface_statistics(q: str = "", device_ids: str = "", limit: int = 150):
    q = (q or "").strip()
    selected_ids = selected_device_ids(device_ids)

    params: list[Any] = []
    where_parts: list[str] = []

    add_device_filter(where_parts, params, selected_ids, "d")

    if q:
        where_parts.append("(d.hostname LIKE %s OR p.ifName LIKE %s OR p.ifAlias LIKE %s OR p.ifDescr LIKE %s)")
        params.extend([f"%{q}%"] * 4)

    where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    params.append(limit)

    rows = fetch_all(f"""
        SELECT COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device, d.device_id, p.*, CASE
            WHEN p.ifLastChange IS NULL OR p.ifLastChange = 0 OR d.uptime IS NULL OR d.last_polled IS NULL THEN NULL
            ELSE DATE_SUB(d.last_polled, INTERVAL CAST(GREATEST(d.uptime - (p.ifLastChange / 100), 0) AS UNSIGNED) SECOND)
        END AS ifLastChange_at, COALESCE(f.mac_count, 0) AS mac_count
        FROM ports p
        JOIN devices d ON d.device_id = p.device_id
        LEFT JOIN (
            SELECT port_id, COUNT(*) AS mac_count
            FROM ports_fdb
            GROUP BY port_id
        ) f ON f.port_id = p.port_id
        {where}
        ORDER BY COALESCE(p.ifInOctets_rate,0) + COALESCE(p.ifOutOctets_rate,0) DESC
        LIMIT %s
    """, tuple(params))

    trs = ""
    for row in rows:
        trs += f"""
        <tr>
          <td>{device_anchor(row)}</td>
          <td>{interface_anchor(row)}</td>
          <td class="status-cell">{status_badge(row.get('ifOperStatus'))}</td>
          <td>{h(fmt_speed(row.get('ifSpeed')))}</td>
          <td>{h(row.get('mac_count'))}</td>
          <td>{h(fmt_rate(row.get('ifOutOctets_rate')))}</td>
          <td>{h(fmt_rate(row.get('ifInOctets_rate')))}</td>
          <td>{h(row.get('ifOutErrors_rate') or 0)}</td>
          <td>{h(row.get('ifInErrors_rate') or 0)}</td>
          <td>{h(row.get('ifAlias'))}</td>
        </tr>"""

    body = f"""
<section class="panel">
  <h1 class="center">Interface Statistics</h1>
  <div class="subtitle">Top {len(rows)} of {len(rows)}</div>
  <form class="toolbar" method="get">
    {hidden_device_ids(selected_ids)}
    <input name="q" value="{h(q)}" placeholder="Filter selected switches, interface, title">
    <button>Search</button>
    <a class="button" href="/reports/interface-statistics">Clear</a>
  </form>
  {table(["Device", "Interface", "Status", "Speed", "MACs", "Tx Bits/Sec", "Rx Bits/Sec", "Tx Errors", "Rx Errors", "Title"], trs)}
</section>
"""
    return layout("Interface Statistics", two_col("/reports/interface-statistics", selected_ids, q, body))


@app.get("/reports/mac-table", response_class=HTMLResponse)
def mac_table(q: str = "", device_ids: str = "", limit: int = 250):
    q = (q or "").strip()
    selected_ids = selected_device_ids(device_ids)

    params: list[Any] = []
    where_parts: list[str] = []

    add_device_filter(where_parts, params, selected_ids, "d")

    if q:
        qmac = q.replace(":", "").replace("-", "").replace(".", "")
        where_parts.append("(d.hostname LIKE %s OR p.ifName LIKE %s OR f.mac_address LIKE %s OR f.vlan_id LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%", f"%{qmac}%", f"%{q}%"])

    where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    params.append(limit)

    rows = fetch_all(f"""
        SELECT COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device, p.device_id, f.mac_address, f.vlan_id, f.created_at AS first_seen, f.updated_at AS last_seen, p.ifName
        FROM ports_fdb f
        LEFT JOIN ports p ON p.port_id = f.port_id
        LEFT JOIN devices d ON d.device_id = p.device_id
        {where}
        ORDER BY f.updated_at DESC
        LIMIT %s
    """, tuple(params))

    trs = ""
    for row in rows:
        trs += f"<tr><td>{device_anchor(row)}</td><td>{h(fmt_mac(row.get('mac_address')))}</td><td>{interface_anchor(row)}</td><td>{h(row.get('vlan_id'))}</td><td>{h(row.get('first_seen'))}</td><td>{h(row.get('last_seen'))}</td></tr>"

    body = f"""
<section class="panel">
  <h1 class="center">MAC Table</h1>
  <form class="toolbar" method="get">
    {hidden_device_ids(selected_ids)}
    <input name="q" value="{h(q)}" placeholder="Filter selected switches, MAC, port, VLAN">
    <button>Search</button>
    <a class="button" href="/reports/mac-table">Clear</a>
  </form>
  {table(["Device", "MAC Address", "Port", "VLAN", "First Seen", "Last Seen"], trs)}
</section>
"""
    return layout("MAC Table", two_col("/reports/mac-table", selected_ids, q, body))



@app.get("/reports/arp-ip", response_class=HTMLResponse)
def arp_ip(q: str = "", device_ids: str = "", limit: int = 250):
    q = (q or "").strip()
    selected_ids = selected_device_ids(device_ids)

    params: list[Any] = []
    where_parts: list[str] = []

    add_device_filter(where_parts, params, selected_ids, "d")

    if q:
        qmac = q.replace(":", "").replace("-", "").replace(".", "")
        where_parts.append("""
            (
              COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) LIKE %s
              OR p.ifName LIKE %s
              OR m.mac_address LIKE %s
              OR m.ipv4_address LIKE %s
            )
        """)
        params.extend([f"%{q}%", f"%{q}%", f"%{qmac}%", f"%{q}%"])

    where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    params.append(limit)

    rows = safe_query(f"""
        SELECT
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device,
            COALESCE(m.device_id, p.device_id) AS device_id,
            p.port_id,
            p.ifName,
            m.mac_address,
            m.ipv4_address,
            m.context_name
        FROM ipv4_mac m
        LEFT JOIN ports p ON p.port_id = m.port_id
        LEFT JOIN devices d ON d.device_id = COALESCE(m.device_id, p.device_id)
        {where}
        ORDER BY d.hostname, p.ifName, m.ipv4_address
        LIMIT %s
    """, tuple(params))

    trs = ""
    for row in rows:
        trs += f"""
        <tr>
          <td>{device_anchor(row)}</td>
          <td>{h(row.get('ipv4_address'))}</td>
          <td>{h(fmt_mac(row.get('mac_address')))}</td>
          <td>{interface_anchor(row)}</td>
          <td>{h(row.get('context_name'))}</td>
        </tr>"""

    clear_href = "/reports/arp-ip"
    if selected_ids:
        clear_href += "?device_ids=" + h(ids_csv(selected_ids))

    body = f"""
<section class="panel">
  <h1 class="center">ARP/IP</h1>
  <div class="subtitle">Top {len(rows)} of {len(rows)}</div>

  <form class="toolbar" method="get">
    {hidden_device_ids(selected_ids)}
    <input name="q" value="{h(q)}" placeholder="Filter selected switches, IP, MAC, interface">
    <button>Search</button>
    <a class="button" href="{clear_href}">Clear</a>
  </form>

  {table(["Device", "IPv4 Address", "MAC Address", "Port", "Context"], trs)}
</section>
"""
    return layout("ARP/IP", two_col("/reports/arp-ip", selected_ids, q, body))



@app.get("/reports/vlans", response_class=HTMLResponse)
def vlans(q: str = "", device_ids: str = "", limit: int = 250):
    q = (q or "").strip()
    selected_ids = selected_device_ids(device_ids)

    params: list[Any] = []
    where_parts: list[str] = [
        "(p.ifName LIKE 'vlan%%' OR p.ifName LIKE 'br%%' OR p.ifDescr LIKE 'vlan%%' OR p.ifAlias LIKE 'vlan%%' OR p.ifVlan IS NOT NULL)"
    ]

    add_device_filter(where_parts, params, selected_ids, "d")

    if q:
        where_parts.append("""
            (
              COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) LIKE %s
              OR p.ifName LIKE %s
              OR p.ifAlias LIKE %s
              OR p.ifDescr LIKE %s
              OR p.ifVlan LIKE %s
            )
        """)
        params.extend([f"%{q}%"] * 5)

    where = "WHERE " + " AND ".join(where_parts)
    params.append(limit)

    rows = fetch_all(f"""
        SELECT
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device,
            d.device_id,
            p.port_id,
            p.ifName,
            p.ifVlan,
            p.ifOperStatus,
            p.ifAdminStatus,
            p.ifSpeed,
            p.ifType,
            p.ifAlias,
            p.ifDescr,
            COALESCE(f.mac_count, 0) AS mac_count
        FROM ports p
        JOIN devices d ON d.device_id = p.device_id
        LEFT JOIN (
            SELECT port_id, COUNT(*) AS mac_count
            FROM ports_fdb
            GROUP BY port_id
        ) f ON f.port_id = p.port_id
        {where}
        ORDER BY d.hostname, p.ifName
        LIMIT %s
    """, tuple(params))

    trs = ""
    for row in rows:
        trs += f"""
        <tr>
          <td>{device_anchor(row)}</td>
          <td>{interface_anchor(row)}</td>
          <td>{h(row.get('ifVlan'))}</td>
          <td class="status-cell">{status_badge(row.get('ifOperStatus'))}</td>
          <td class="status-cell">{status_badge(row.get('ifAdminStatus'))}</td>
          <td>{h(fmt_speed(row.get('ifSpeed')))}</td>
          <td>{h(row.get('mac_count'))}</td>
          <td>{h(row.get('ifType'))}</td>
          <td>{h(row.get('ifAlias') or row.get('ifDescr'))}</td>
        </tr>"""

    clear_href = "/reports/vlans"
    if selected_ids:
        clear_href += "?device_ids=" + h(ids_csv(selected_ids))

    body = f"""
<section class="panel">
  <h1 class="center">VLANs</h1>
  <div class="subtitle">Top {len(rows)} of {len(rows)}</div>

  <form class="toolbar" method="get">
    {hidden_device_ids(selected_ids)}
    <input name="q" value="{h(q)}" placeholder="Filter selected switches, VLAN, interface, title">
    <button>Search</button>
    <a class="button" href="{clear_href}">Clear</a>
  </form>

  {table(["Device", "Interface", "VLAN", "Status", "Admin", "Speed", "MACs", "Type", "Title"], trs)}
</section>
"""
    return layout("VLANs", two_col("/reports/vlans", selected_ids, q, body))



@app.get("/reports/changes", response_class=HTMLResponse)
def changes(q: str = "", device_ids: str = "", limit: int = 250):
    q = (q or "").strip()
    selected_ids = selected_device_ids(device_ids)

    params: list[Any] = []
    where_parts: list[str] = ["p.ifLastChange IS NOT NULL"]

    add_device_filter(where_parts, params, selected_ids, "d")

    if q:
        where_parts.append("""
            (
              COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) LIKE %s
              OR p.ifName LIKE %s
              OR p.ifAlias LIKE %s
              OR p.ifDescr LIKE %s
            )
        """)
        params.extend([f"%{q}%"] * 4)

    where = "WHERE " + " AND ".join(where_parts)
    params.append(limit)

    rows = fetch_all(f"""
        SELECT
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device,
            d.device_id,
            p.port_id,
            p.ifName,
            p.ifOperStatus,
            p.ifAdminStatus,
            CASE
                WHEN p.ifLastChange IS NULL OR p.ifLastChange = 0 OR d.uptime IS NULL OR d.last_polled IS NULL THEN NULL
                ELSE DATE_SUB(d.last_polled, INTERVAL CAST(GREATEST(d.uptime - (p.ifLastChange / 100), 0) AS UNSIGNED) SECOND)
            END AS ifLastChange_at,
            p.ifSpeed,
            p.ifAlias,
            p.ifDescr
        FROM ports p
        JOIN devices d ON d.device_id = p.device_id
        {where}
        ORDER BY ifLastChange_at DESC
        LIMIT %s
    """, tuple(params))

    trs = ""
    for row in rows:
        trs += f"""
        <tr>
          <td>{device_anchor(row)}</td>
          <td>{interface_anchor(row)}</td>
          <td class="status-cell">{status_badge(row.get('ifOperStatus'))}</td>
          <td class="status-cell">{status_badge(row.get('ifAdminStatus'))}</td>
          <td>{h(row.get('ifLastChange_at'))}</td>
          <td>{h(fmt_speed(row.get('ifSpeed')))}</td>
          <td>{h(row.get('ifAlias') or row.get('ifDescr'))}</td>
        </tr>"""

    clear_href = "/reports/changes"
    if selected_ids:
        clear_href += "?device_ids=" + h(ids_csv(selected_ids))

    body = f"""
<section class="panel">
  <h1 class="center">Changes</h1>
  <div class="subtitle">Top {len(rows)} of {len(rows)}</div>

  <form class="toolbar" method="get">
    {hidden_device_ids(selected_ids)}
    <input name="q" value="{h(q)}" placeholder="Filter selected switches, interface, title">
    <button>Search</button>
    <a class="button" href="{clear_href}">Clear</a>
  </form>

  {table(["Device", "Interface", "Status", "Admin", "Last Change", "Speed", "Title"], trs)}
</section>
"""
    return layout("Changes", two_col("/reports/changes", selected_ids, q, body))




def _load_unused_cache() -> dict:
    try:
        with open(UNUSED_CACHE_FILE, "r") as fh:
            return json.load(fh)
    except Exception:
        return {"ports": {}}


def _days_label(days: int) -> str:
    days = int(days)
    if days == 365:
        return "Last 1 year"
    if days == 730:
        return "Last 2 years"
    return f"Last {days} days"



def _middkips_load_json(path: str) -> dict:
    try:
        with open(path, "r") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _middkips_norm_key_part(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _middkips_akips_key(device, interface) -> str:
    return f"{_middkips_norm_key_part(device)}|{_middkips_norm_key_part(interface)}"


def _middkips_days_label(days: int) -> str:
    days = int(days)
    if days == 365:
        return "Last 12 Months"
    if days == 730:
        return "Last 2 Years"
    return f"Last {days} days"


@app.get("/reports/unused-interfaces", response_class=HTMLResponse)
def report_unused_interfaces(q: str = "", device_ids: str = "", days: int = 365, mode: str = "detailed"):
    selected_ids = selected_device_ids(device_ids)
    days = max(1, min(int(days or 365), 730))

    selected_clause = ""
    params = []

    if selected_ids:
        placeholders = ",".join(["%s"] * len(selected_ids))
        selected_clause = f" AND d.device_id IN ({placeholders}) "
        params.extend(selected_ids)

    search_clause = ""
    if q:
        search_clause = """
          AND (
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) LIKE %s
            OR d.hostname LIKE %s
            OR p.ifName LIKE %s
            OR p.ifDescr LIKE %s
            OR p.ifAlias LIKE %s
          )
        """
        like = f"%{q}%"
        params.extend([like, like, like, like, like])

    last_change_expr = """
        CASE
            WHEN p.ifLastChange IS NULL OR p.ifLastChange = 0 OR d.uptime IS NULL OR d.last_polled IS NULL THEN NULL
            ELSE DATE_SUB(d.last_polled, INTERVAL CAST(GREATEST(d.uptime - (p.ifLastChange / 100), 0) AS UNSIGNED) SECOND)
        END
    """

    sql = f"""
        SELECT
            d.device_id,
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device,
            d.hostname,
            d.sysName,
            INET6_NTOA(d.ip) AS ip_addr,
            p.port_id,
            p.ifName,
            p.ifDescr,
            p.ifAlias,
            p.ifOperStatus,
            p.ifAdminStatus,
            p.ifSpeed,
            p.ifType,
            {last_change_expr} AS snmp_last_change
        FROM ports p
        JOIN devices d ON d.device_id = p.device_id
        WHERE p.ifName IS NOT NULL
          AND p.ifName <> ''
          {selected_clause}
          {search_clause}
        ORDER BY d.device_id, p.port_id
    """

    rows = safe_query(sql, tuple(params))

    akips_cache = _middkips_load_json(AKIPS_UNUSED_CACHE_FILE)
    akips_ports = akips_cache.get("ports", {}) or {}
    akips_window = akips_cache.get("state_window") or ""
    state_label = akips_window or _middkips_days_label(days)

    rrd_cache = _middkips_load_json(UNUSED_CACHE_FILE)
    rrd_ports = rrd_cache.get("ports", {}) or {}
    cutoff_ts = int(time.time()) - (days * 86400)

    for row in rows:
        akips_row = akips_ports.get(_middkips_akips_key(row.get("device"), row.get("ifName")))
        rrd_row = rrd_ports.get(str(row.get("port_id")))

        row["display_state"] = ""
        row["display_current"] = ""
        row["display_last_change"] = ""
        row["display_title"] = ""

        if akips_row:
            row["display_state"] = akips_row.get("state") or ""
            row["display_current"] = akips_row.get("current") or ""
            row["display_last_change"] = akips_row.get("last_change") or ""
            row["display_title"] = akips_row.get("title") or ""
            if akips_row.get("speed"):
                try:
                    row["ifSpeed"] = int(akips_row.get("speed"))
                except Exception:
                    pass

        if not row["display_state"]:
            if rrd_row and rrd_row.get("rrd_found"):
                last_ts = rrd_row.get("last_traffic_ts")
                if last_ts and int(last_ts) >= cutoff_ts:
                    row["display_state"] = "used"
                else:
                    row["display_state"] = "free"
            else:
                row["display_state"] = "free" if str(row.get("ifOperStatus") or "").lower() == "down" else "used"

        if not row["display_current"]:
            row["display_current"] = row.get("ifOperStatus") or ""

        if not row["display_last_change"]:
            row["display_last_change"] = row.get("snmp_last_change") or ""

        if not row["display_title"]:
            row["display_title"] = row.get("ifAlias") or row.get("ifDescr") or ""

    summary = {}
    for row in rows:
        did = row.get("device_id")
        if did not in summary:
            summary[did] = {
                "device_id": did,
                "device": row.get("device"),
                "total": 0,
                "free": 0,
                "used": 0,
            }

        summary[did]["total"] += 1
        if str(row.get("display_state") or "").lower() == "free":
            summary[did]["free"] += 1
        else:
            summary[did]["used"] += 1

    summary_rows = ""
    for item in sorted(summary.values(), key=lambda x: (-x["free"], x["device"] or "")):
        did = item["device_id"]
        summary_rows += f"""
        <tr>
          <td><a href="/reports/unused-interfaces?device_ids={h(did)}&days={h(days)}&mode=detailed">{h(item["device"])}</a></td>
          <td>{h(item["total"])}</td>
          <td>{h(item["free"])}</td>
          <td>{h(item["used"])}</td>
        </tr>"""

    def sort_key(row):
        return (
            0 if str(row.get("display_state") or "").lower() == "free" else 1,
            row.get("device") or "",
            row.get("ifName") or "",
        )

    detail_rows = ""
    detail_limit = 3000

    for row in sorted(rows, key=sort_key)[:detail_limit]:
        state = row.get("display_state") or ""
        state_badge = f'<span class="badge {"bad" if str(state).lower() == "free" else "good"}">{h(state)}</span>'
        current_badge = status_badge(row.get("display_current") or "")
        row_class = ' class="unused-free"' if str(state).lower() == "free" else ""

        detail_rows += f"""
        <tr{row_class}>
          <td><a href="/dashboard?device_ids={h(row.get('device_id'))}">{h(row.get('device'))}</a></td>
          <td>{interface_anchor(row)}</td>
          <td>{h(fmt_speed(row.get('ifSpeed')))}</td>
          <td>{state_badge}</td>
          <td>{current_badge}</td>
          <td>{h(row.get('display_last_change'))}</td>
          <td>{h(row.get('display_title'))}</td>
        </tr>"""

    if len(rows) > detail_limit:
        detail_rows += f"""
        <tr>
          <td colspan="7">Showing first {h(detail_limit)} of {h(len(rows))} interfaces. Use a switch selection or filter to narrow results.</td>
        </tr>"""

    ids = ",".join(str(x) for x in selected_ids)

    days_options = ""
    for opt in (30, 90, 180, 365, 730):
        selected = "selected" if opt == days else ""
        days_options += f'<option value="{opt}" {selected}>{h(_middkips_days_label(opt))}</option>'

    akips_note = f"AKIPS cache {h(akips_window)} loaded, {h(len(akips_ports))} ports" if akips_ports else "No AKIPS cache loaded"
    rrd_note = f"RRD cache generated {h(rrd_cache.get('generated_at', 'unknown'))}" if rrd_ports else "No RRD cache"

    body = f"""
<style>
.unused-grid {{
  display:grid;
  grid-template-columns: 420px minmax(700px, 1fr);
  gap:12px;
  align-items:start;
}}
.unused-free td {{
  background: rgba(248, 81, 73, .10);
}}
.unused-controls {{
  display:flex;
  gap:8px;
  align-items:center;
  margin-bottom:10px;
  flex-wrap:wrap;
}}
.unused-controls input {{
  min-width:260px;
}}
.unused-total {{
  text-align:center;
  color:var(--muted);
  margin-top:-8px;
  margin-bottom:8px;
}}
@media (max-width: 1200px) {{
  .unused-grid {{
    grid-template-columns:1fr;
  }}
}}
</style>

<section class="panel">
  <div class="panel-head">
    <h1>Unused Interfaces</h1>
    <div class="actions">
      <a class="button" href="/reports/unused-interfaces?days={h(days)}&mode=summary&device_ids={h(ids)}">Summary</a>
      <a class="button" href="/reports/unused-interfaces?days={h(days)}&mode=detailed&device_ids={h(ids)}">Detailed</a>
    </div>
  </div>

  <form class="unused-controls" method="get" action="/reports/unused-interfaces">
    {hidden_device_ids(selected_ids)}
    <select name="days">{days_options}</select>
    <input name="q" value="{h(q)}" placeholder="Filter device, interface, title" />
    <button type="submit">Search</button>
    <a class="button" href="/reports/unused-interfaces">Clear</a>
  </form>

  <div class="unused-total">Interface usage for {h(state_label)} — {akips_note} — {rrd_note}</div>

  <section class="unused-grid">
    <section class="panel">
      <h2 class="center">Interface Usage</h2>
      <div class="unused-total">Total {h(len(summary))} devices</div>
      {table(["Device", "Total", "Free", "Used"], summary_rows)}
    </section>

    <section class="panel">
      <h2 class="center">Interface Detail</h2>
      <div class="unused-total">Total {h(len(rows))} interfaces</div>
      {table(["Device", "Interface", "Speed", f"State {h(state_label)}", "Current", "Last Change", "Title"], detail_rows)}
    </section>
  </section>
</section>
"""

    return layout("Unused Interfaces", two_col("/reports/unused-interfaces", selected_ids, q, body))

@app.get("/reports/events", response_class=HTMLResponse)
def events(q: str = "", device_ids: str = "", limit: int = 250):
    q = (q or "").strip()
    selected_ids = selected_device_ids(device_ids)

    params: list[Any] = []
    where_parts: list[str] = []

    add_device_filter(where_parts, params, selected_ids, "d")

    if q:
        where_parts.append("""
            (
              COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) LIKE %s
              OR e.type LIKE %s
              OR e.severity LIKE %s
              OR e.message LIKE %s
            )
        """)
        params.extend([f"%{q}%"] * 4)

    where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    params.append(limit)

    rows = safe_query(f"""
        SELECT
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device,
            d.device_id,
            e.datetime,
            e.type,
            e.severity,
            e.message
        FROM eventlog e
        LEFT JOIN devices d ON d.device_id = e.device_id
        {where}
        ORDER BY e.datetime DESC
        LIMIT %s
    """, tuple(params))

    trs = ""
    for row in rows:
        sev = str(row.get("severity") or "")
        sev_class = "bad" if sev in ("4", "5", "critical", "error") else ""
        trs += f"""
        <tr>
          <td>{device_anchor(row)}</td>
          <td>{h(row.get('datetime'))}</td>
          <td>{h(row.get('type'))}</td>
          <td class="{sev_class}">{h(row.get('severity'))}</td>
          <td>{h(row.get('message'))}</td>
        </tr>"""

    clear_href = "/reports/events"
    if selected_ids:
        clear_href += "?device_ids=" + h(ids_csv(selected_ids))

    body = f"""
<section class="panel">
  <h1 class="center">Events</h1>
  <div class="subtitle">Top {len(rows)} of {len(rows)}</div>

  <form class="toolbar" method="get">
    {hidden_device_ids(selected_ids)}
    <input name="q" value="{h(q)}" placeholder="Filter selected switches, type, severity, message">
    <button>Search</button>
    <a class="button" href="{clear_href}">Clear</a>
  </form>

  {table(["Device", "Date/Time", "Type", "Severity", "Message"], trs)}
</section>
"""
    return layout("Events", two_col("/reports/events", selected_ids, q, body))


@app.get("/lookup", response_class=HTMLResponse)
def lookup(q: str = "", limit: int = 50):
    from app.services.middkips_universal_page import render_universal_lookup_page
    body = render_universal_lookup_page(q=q, limit=limit)

    if q:
        try:
            from app.services.middkips_clearpass_page import render_clearpass_universal_correlation_card
            clearpass_card = render_clearpass_universal_correlation_card(q=q, limit=limit)
            if clearpass_card and "Primary Correlations" in body:
                section_start = body.find("Primary Correlations")
                section_end = body.find("</section>", section_start)
                if section_end != -1:
                    body = body[:section_end] + clearpass_card + body[section_end:]
                else:
                    body += clearpass_card
            elif clearpass_card:
                body += clearpass_card
        except Exception as exc:
            import html
            body += f"""
            <section class="panel lookup-section">
              <h2>ClearPass Correlation <span class="muted">(error)</span></h2>
              <p class="bad">ClearPass correlation card failed: {html.escape(str(exc))}</p>
            </section>
            """




    if q:
        try:
            from app.services.middkips_clearpass_page import render_clearpass_session_universal_section
            body += render_clearpass_session_universal_section(q=q, limit=limit)
        except Exception as exc:
            import html
            body += f"""
            <section class="panel lookup-section">
              <h2>ClearPass Session Matches <span class="muted">(error)</span></h2>
              <p class="bad">ClearPass session lookup failed inside Universal Lookup: {html.escape(str(exc))}</p>
            </section>
            """

    return layout("Universal Lookup", body)




@app.get("/config/", response_class=HTMLResponse)
def config_lookup_landing():
    body = """
    <section class="panel">
      <h1>Config Lookup</h1>
      <p class="muted">Enter a hostname or device name to open matching config tools.</p>
      <form method="get" action="/tools/lookup" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;">
        <input name="q" placeholder="hostname, FQDN, IP, MAC" style="min-width:360px;">
        <button type="submit">Lookup</button>
      </form>
      <p class="muted" style="margin-top:12px;">
        Direct config links use <code>/config/&lt;hostname&gt;</code>.
      </p>
    </section>
    """
    return globals()["layout"]("Config Lookup", body)


@app.get("/config/{hostname}", response_class=HTMLResponse)
def config_links(hostname: str):
    body = f"""
<section class="panel">
  <h1>Config Links: {h(hostname)}</h1>
  <div class="actions">
    <a class="button" href="{OXIDIZED_BASE_URL}/node/show/{h(hostname)}">Open Oxidized Config</a>
    <a class="button" href="{OXIDIZED_BASE_URL}/node/fetch/{h(hostname)}">Download Oxidized Config</a>
  </div>
</section>
"""
    return layout("Config Links", body)


@app.get("/device/{device_id}/config.txt")
def device_config_txt(device_id: int):
    import os
    import re
    import urllib.parse
    import requests
    from fastapi.responses import Response, PlainTextResponse

    rows = fetch_all("""
        SELECT device_id, hostname, sysName, ip, status
        FROM devices
        WHERE device_id = %s
        LIMIT 1
    """, (device_id,))

    if not rows:
        return PlainTextResponse(f"Device {device_id} not found\\n", status_code=404)

    dev = rows[0]
    hostname = (dev.get("hostname") or dev.get("sysName") or "").strip()
    ip = fmt_ip(dev.get("ip_addr") or dev.get("ip")).strip()

    display_name = hostname or ip or f"device-{device_id}"
    safe_filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", display_name).strip("_") or f"device-{device_id}"

    oxidized_api = os.environ.get("OXIDIZED_API_URL", "http://127.0.0.1:8888").rstrip("/")

    candidates = []
    for value in [ip, hostname, hostname.lower(), hostname.upper()]:
        if value and value not in candidates:
            candidates.append(value)

    errors = []
    fetch_paths = []

    try:
        r = requests.get(f"{oxidized_api}/nodes.json", timeout=10)
        r.raise_for_status()
        nodes = r.json()

        for node in nodes:
            name = str(node.get("name") or "")
            full_name = str(node.get("full_name") or "")
            group = str(node.get("group") or "")
            full_leaf = full_name.split("/", 1)[-1] if "/" in full_name else full_name

            if name in candidates or full_leaf in candidates or full_name in candidates:
                if full_name:
                    fetch_paths.append(full_name)
                if group and name:
                    fetch_paths.append(f"{group}/{name}")
                if name:
                    fetch_paths.append(name)
                break

    except Exception as exc:
        errors.append(f"nodes.json lookup failed: {exc}")

    for value in candidates:
        fetch_paths.append(value)
        fetch_paths.append(f"junos/{value}")
        fetch_paths.append(f"arubaos-cx/{value}")
        fetch_paths.append(f"panos/{value}")

    unique_fetch_paths = []
    seen = set()
    for path in fetch_paths:
        if path and path not in seen:
            unique_fetch_paths.append(path)
            seen.add(path)

    for fetch_path in unique_fetch_paths:
        try:
            encoded = urllib.parse.quote(fetch_path, safe="/")
            url = f"{oxidized_api}/node/fetch/{encoded}"
            r = requests.get(url, timeout=20)

            if r.status_code == 200 and r.text.strip():
                config_text = r.text
                detected_hostname = ""

                for line in config_text.splitlines()[:100]:
                    m = re.search(r"^\s*#\s*Hostname:\s*(\S+)", line, re.IGNORECASE)
                    if m:
                        detected_hostname = m.group(1)
                        break

                    m = re.search(r"^\s*set\s+system\s+host-name\s+(\S+)", line, re.IGNORECASE)
                    if m:
                        detected_hostname = m.group(1).strip(";")
                        break

                    m = re.search(r"^\s*hostname\s+(\S+)", line, re.IGNORECASE)
                    if m:
                        detected_hostname = m.group(1).strip(";")
                        break

                download_name = detected_hostname or display_name
                download_filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", download_name).strip("_") or safe_filename

                return Response(
                    content=config_text,
                    media_type="text/plain; charset=utf-8",
                    headers={
                        "Content-Disposition": f'attachment; filename="{download_filename}.txt"',
                        "X-MiddKiPS-Config-Source": fetch_path,
                    },
                )

            errors.append(f"{fetch_path}: HTTP {r.status_code}")

        except Exception as exc:
            errors.append(f"{fetch_path}: {exc}")

    body = (
        f"Unable to fetch Oxidized config for {display_name} / device_id {device_id}\\n\\n"
        f"Tried candidates: {', '.join(unique_fetch_paths)}\\n\\n"
        "Errors:\\n" + "\\n".join(errors[-20:]) + "\\n"
    )

    return PlainTextResponse(body, status_code=404)


@app.get("/interface/{port_id}", response_class=HTMLResponse)
def interface_detail(port_id: int):
    port = fetch_one("""
        SELECT
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device,
            d.device_id,
            d.hostname,
            INET6_NTOA(d.ip) AS ip_addr,
            d.os,
            d.hardware,
            d.uptime,
            d.last_polled,
            p.*,
            CASE
                WHEN p.ifLastChange IS NULL OR p.ifLastChange = 0 OR d.uptime IS NULL OR d.last_polled IS NULL THEN NULL
                ELSE DATE_SUB(d.last_polled, INTERVAL CAST(GREATEST(d.uptime - (p.ifLastChange / 100), 0) AS UNSIGNED) SECOND)
            END AS ifLastChange_at
        FROM ports p
        JOIN devices d ON d.device_id = p.device_id
        WHERE p.port_id = %s
    """, (port_id,))

    if not port:
        return layout("Interface not found", "<section class='panel'><h1>Interface not found</h1></section>")

    macs = safe_query("""
        SELECT mac_address, vlan_id, created_at AS first_seen, updated_at AS last_seen
        FROM ports_fdb
        WHERE port_id = %s
        ORDER BY updated_at DESC
        LIMIT 100
    """, (port_id,))

    arps = safe_query("""
        SELECT ipv4_address, mac_address, context_name
        FROM ipv4_mac
        WHERE port_id = %s
        ORDER BY ipv4_address
        LIMIT 100
    """, (port_id,))

    events = safe_query("""
        SELECT datetime, type, severity, message
        FROM eventlog
        WHERE device_id = %s
          AND message LIKE %s
        ORDER BY datetime DESC
        LIMIT 30
    """, (port.get("device_id"), f"%{port.get('ifName') or port.get('ifDescr') or ''}%"))

    mac_rows = ""
    for row in macs:
        mac_rows += f"""
        <tr>
          <td>{h(fmt_mac(row.get('mac_address')))}</td>
          <td>{h(row.get('vlan_id'))}</td>
          <td>{h(row.get('first_seen'))}</td>
          <td>{h(row.get('last_seen'))}</td>
        </tr>"""

    arp_rows = ""
    for row in arps:
        arp_rows += f"""
        <tr>
          <td>{h(row.get('ipv4_address'))}</td>
          <td>{h(fmt_mac(row.get('mac_address')))}</td>
          <td>{h(row.get('context_name'))}</td>
        </tr>"""

    event_rows = ""
    for row in events:
        event_rows += f"""
        <tr>
          <td>{h(row.get('datetime'))}</td>
          <td>{h(row.get('type'))}</td>
          <td>{h(row.get('severity'))}</td>
          <td>{h(row.get('message'))}</td>
        </tr>"""

    ident_rows = f"""
      <tr><td>Device</td><td>{device_anchor(port)}</td></tr>
      <tr><td>Interface</td><td>{h(port.get('ifName'))}</td></tr>
      <tr><td>Description</td><td>{h(port.get('ifDescr'))}</td></tr>
      <tr><td>Title</td><td>{h(port.get('ifAlias'))}</td></tr>
      <tr><td>Type</td><td>{h(port.get('ifType'))}</td></tr>
      <tr><td>Speed</td><td>{h(fmt_speed(port.get('ifSpeed')))}</td></tr>
      <tr><td>Duplex</td><td>{h(port.get('ifDuplex') or 'na')}</td></tr>
      <tr><td>MAC</td><td>{h(fmt_mac(port.get('ifPhysAddress')))}</td></tr>
      <tr><td>VLAN</td><td>{h(port.get('ifVlan'))}</td></tr>
      <tr><td>MTU</td><td>{h(port.get('ifMtu'))}</td></tr>
      <tr><td>Last Change</td><td>{h(port.get('ifLastChange_at') or port.get('ifLastChange'))}</td></tr>
    """

    stat_rows = f"""
      <tr><td>Admin Status</td><td class="status-cell">{status_badge(port.get('ifAdminStatus'))}</td></tr>
      <tr><td>Oper Status</td><td class="status-cell">{status_badge(port.get('ifOperStatus'))}</td></tr>
      <tr><td>Rx Bits/Sec</td><td>{h(fmt_rate(port.get('ifInOctets_rate')))}</td></tr>
      <tr><td>Tx Bits/Sec</td><td>{h(fmt_rate(port.get('ifOutOctets_rate')))}</td></tr>
      <tr><td>Rx Errors/Sec</td><td>{h(port.get('ifInErrors_rate') or 0)}</td></tr>
      <tr><td>Tx Errors/Sec</td><td>{h(port.get('ifOutErrors_rate') or 0)}</td></tr>
      <tr><td>Rx Discards/Sec</td><td>{h(port.get('ifInDiscards_rate') or 0)}</td></tr>
      <tr><td>Tx Discards/Sec</td><td>{h(port.get('ifOutDiscards_rate') or 0)}</td></tr>
      <tr><td>Known MACs</td><td>{h(len(macs))}</td></tr>
      <tr><td>Known ARP/IPs</td><td>{h(len(arps))}</td></tr>
    """

    iface_title = f"{port.get('device')} {port.get('ifName')}"

    body = f"""
<section class="panel">
  <div class="panel-head">
    <h1>{h(iface_title)}</h1>
    <div class="actions">
      <a class="button" href="/device/{h(port.get('device_id'))}">Device Dashboard</a>
      <a class="button" href="{LIBRENMS_BASE_URL}/device/device={h(port.get('device_id'))}/tab=ports/port={h(port_id)}/">LibreNMS Port</a>
      <a class="button" href="{LIBRENMS_BASE_URL}/graphs/type=port_bits/id={h(port_id)}/">Traffic Graph</a>
      <a class="button" href="{LIBRENMS_BASE_URL}/graphs/type=port_errors/id={h(port_id)}/">Error Graph</a>
      <a class="button" href="{OXIDIZED_BASE_URL}/node/show/{h(port.get('hostname'))}">Config</a>
    </div>
  </div>

  <section class="cards">
    {card("Admin", port.get("ifAdminStatus"))}
    {card("Status", port.get("ifOperStatus"))}
    {card("Speed", fmt_speed(port.get("ifSpeed")))}
    {card("Rx", fmt_rate(port.get("ifInOctets_rate")))}
    {card("Tx", fmt_rate(port.get("ifOutOctets_rate")))}
    {card("MACs", len(macs))}
  </section>
</section>

<section class="dashboard-grid">
  <section class="panel">
    <h2>Interface Identity</h2>
    {table(["Field", "Value"], ident_rows)}
  </section>

  <section class="panel">
    <h2>Current Statistics</h2>
    {table(["Metric", "Value"], stat_rows)}
  </section>

  <section class="panel">
    <h2>Recent Events</h2>
    {table(["Date/Time", "Type", "Severity", "Message"], event_rows)}
  </section>
</section>

<section class="dashboard-grid">
  <section class="panel">
    <h2>MACs on this Port</h2>
    {table(["MAC", "VLAN", "First Seen", "Last Seen"], mac_rows)}
  </section>

  <section class="panel">
    <h2>ARP/IP on this Port</h2>
    {table(["IPv4", "MAC", "Context"], arp_rows)}
  </section>

  <section class="panel">
    <h2>Notes</h2>
    <table class="report">
      <tbody>
        <tr><td>LibreNMS Port ID</td><td>{h(port_id)}</td></tr>
        <tr><td>Device ID</td><td>{h(port.get('device_id'))}</td></tr>
        <tr><td>Last Polled</td><td>{h(port.get('last_polled'))}</td></tr>
      </tbody>
    </table>
  </section>
</section>
"""
    return layout(iface_title, body)


def _middkips_reverse_dns(value: str) -> str:
    value = str(value or "").strip()
    try:
        return socket.gethostbyaddr(value)[0]
    except Exception:
        return ""


def _middkips_token_sets_for_lldp(value: str):
    value = str(value or "").strip()
    terms = []

    if value:
        terms.append(value)

    rdns = _middkips_reverse_dns(value)
    if rdns:
        terms.append(rdns)
        terms.append(rdns.split(".")[0])

    if "." in value and not re.match(r"^\d+\.\d+\.\d+\.\d+$", value):
        terms.append(value.split(".")[0])

    token_sets = []
    stop = {"middlebury", "edu", "www", "net", "org", "com"}

    for term in terms:
        cleaned = term.lower()
        cleaned = cleaned.replace(".middlebury.edu", "")
        tokens = [
            t for t in re.split(r"[^a-z0-9]+", cleaned)
            if len(t) > 1 and t not in stop and not t.isdigit()
        ]
        if tokens and tokens not in token_sets:
            token_sets.append(tokens)

    return terms, token_sets


@app.get("/tools/lldp-lookup", response_class=HTMLResponse)
def tools_lldp_lookup(target: str = ""):
    target = str(target or "").strip()
    rows = []
    terms = []
    token_sets = []

    if target:
        terms, token_sets = _middkips_token_sets_for_lldp(target)

        expr = """
        CONCAT_WS(' ',
          COALESCE(l.remote_hostname,''),
          COALESCE(l.remote_port,''),
          COALESCE(l.remote_platform,''),
          COALESCE(l.remote_version,''),
          COALESCE(NULLIF(rd.sysName,''), NULLIF(rd.hostname,''), INET6_NTOA(rd.ip)),
          INET6_NTOA(rd.ip)
        )
        """

        clauses = []
        params = []

        for term in terms:
            clauses.append(f"LOWER({expr}) LIKE LOWER(%s)")
            params.append(f"%{term}%")

        for tokens in token_sets:
            parts = []
            for tok in tokens:
                parts.append(f"LOWER({expr}) LIKE LOWER(%s)")
                params.append(f"%{tok}%")
            if parts:
                clauses.append("(" + " AND ".join(parts) + ")")

        where = " OR ".join(clauses) if clauses else "1=0"

        sql = f"""
        SELECT
          l.id,
          l.protocol,
          l.local_port_id,
          COALESCE(NULLIF(ld.sysName,''), NULLIF(ld.hostname,''), INET6_NTOA(ld.ip)) AS local_switch,
          INET6_NTOA(ld.ip) AS local_switch_ip,
          lp.ifName AS local_interface,
          lp.ifAlias AS local_title,
          l.remote_hostname,
          l.remote_port,
          l.remote_platform,
          l.remote_version,
          COALESCE(l.remote_device_id,0) AS remote_device_id,
          COALESCE(NULLIF(rd.sysName,''), NULLIF(rd.hostname,''), INET6_NTOA(rd.ip)) AS matched_remote_device,
          INET6_NTOA(rd.ip) AS matched_remote_ip
        FROM links l
        LEFT JOIN ports lp ON lp.port_id = l.local_port_id
        LEFT JOIN devices ld ON ld.device_id = lp.device_id
        LEFT JOIN devices rd ON rd.device_id = l.remote_device_id
        WHERE l.protocol = 'lldp'
          AND ({where})
        ORDER BY
          CASE WHEN COALESCE(l.remote_device_id,0) = 0 THEN 1 ELSE 0 END,
          l.remote_hostname,
          local_switch,
          local_interface
        LIMIT 200
        """

        rows = safe_query(sql, tuple(params))

    body_rows = ""
    for r in rows:
        matched = r.get("matched_remote_device") or ""
        if not matched and int(r.get("remote_device_id") or 0) == 0:
            matched = "Unmatched in LibreNMS"

        body_rows += f"""
        <tr>
          <td>{h(r.get('remote_hostname'))}</td>
          <td>{h(r.get('remote_port'))}</td>
          <td>{h(r.get('local_switch'))}</td>
          <td>{h(r.get('local_switch_ip'))}</td>
          <td>{h(r.get('local_interface'))}</td>
          <td>{h(r.get('local_title'))}</td>
          <td>{h(matched)}</td>
          <td>{h(r.get('matched_remote_ip') or '')}</td>
          <td>{h(r.get('remote_version') or r.get('remote_platform') or '')}</td>
        </tr>"""

    if target and not rows:
        body_rows = '<tr><td colspan="9">No LLDP matches found.</td></tr>'

    searched = ", ".join(terms) if terms else ""
    token_text = "; ".join([" + ".join(x) for x in token_sets]) if token_sets else ""

    body = f"""
<section class="panel">
  <h1>LLDP Lookup</h1>

  <form class="unused-controls" method="get" action="/tools/lldp-lookup">
    <input name="target" value="{h(target)}" placeholder="IP, DNS name, switch name" style="min-width:360px" />
    <button type="submit">Search</button>
  </form>

  <div class="unused-total">
    Search terms: {h(searched or "none")}<br />
    Token match: {h(token_text or "none")}
  </div>

  {table(["Remote Hostname", "Remote Port", "Local Switch", "Local IP", "Local Interface", "Local Title", "LibreNMS Match", "Matched IP", "Remote Version"], body_rows)}
</section>
"""
    return layout("LLDP Lookup", body)



@app.get("/tools/unmatched-lldp-switches", response_class=HTMLResponse)
def tools_unmatched_lldp_switches(q: str = "", min_links: int = 1, mode: str = "missing"):
    min_links = max(1, int(min_links or 1))
    mode = (mode or "missing").lower()
    if mode not in ("missing", "exists", "all"):
        mode = "missing"

    params = []
    q_clause = ""
    if q:
        q_clause = """
          AND CONCAT_WS(' ',
            COALESCE(l.remote_hostname,''),
            COALESCE(l.remote_port,''),
            COALESCE(l.remote_platform,''),
            COALESCE(l.remote_version,''),
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)),
            INET6_NTOA(d.ip),
            COALESCE(p.ifName,''),
            COALESCE(p.ifAlias,'')
          ) LIKE %s
        """
        params.append(f"%{q}%")

    sql = f"""
    SELECT
      l.remote_hostname,
      COUNT(*) AS links_seen,

      SUBSTRING_INDEX(
        GROUP_CONCAT(
          DISTINCT CONCAT(
            COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)),
            ' ',
            COALESCE(p.ifName,''),
            ' -> ',
            COALESCE(l.remote_port,'')
          )
          ORDER BY COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)), COALESCE(p.ifName,'')
          SEPARATOR '\\n'
        ),
        '\\n',
        8
      ) AS local_edges,

      SUBSTRING_INDEX(
        GROUP_CONCAT(DISTINCT COALESCE(l.remote_port,'') ORDER BY COALESCE(l.remote_port,'') SEPARATOR '\\n'),
        '\\n',
        8
      ) AS remote_ports,

      MAX(COALESCE(l.remote_platform,'')) AS remote_platform,
      MAX(COALESCE(l.remote_version,'')) AS remote_version,
      MAX(md.device_id) AS possible_device_id,
      MAX(COALESCE(NULLIF(md.sysName,''), NULLIF(md.hostname,''), INET6_NTOA(md.ip))) AS possible_device,
      MAX(INET6_NTOA(md.ip)) AS possible_device_ip
    FROM links l
    LEFT JOIN ports p ON p.port_id = l.local_port_id
    LEFT JOIN devices d ON d.device_id = p.device_id
    LEFT JOIN devices md ON (
         LOWER(md.hostname) = LOWER(l.remote_hostname)
      OR LOWER(md.sysName) = LOWER(l.remote_hostname)
      OR LOWER(SUBSTRING_INDEX(md.hostname, '.', 1)) = LOWER(l.remote_hostname)
      OR LOWER(SUBSTRING_INDEX(md.sysName, '.', 1)) = LOWER(l.remote_hostname)
    )
    WHERE l.protocol = 'lldp'
      AND COALESCE(l.remote_device_id, 0) = 0
      AND COALESCE(l.remote_hostname, '') <> ''
      AND (
           LOWER(COALESCE(l.remote_version,'')) REGEXP 'juniper|junos|ethernet switch|ex[0-9]|qfx|aruba.*switch|arubaos-cx|cx[0-9][0-9][0-9][0-9]|2930m|2930f|3810m|5400r|6300|6400|8320|8360'
        OR LOWER(COALESCE(l.remote_platform,'')) REGEXP 'juniper|junos|ethernet switch|ex[0-9]|qfx|aruba.*switch|arubaos-cx|cx[0-9][0-9][0-9][0-9]|2930m|2930f|3810m|5400r|6300|6400|8320|8360'
        OR LOWER(COALESCE(l.remote_hostname,'')) REGEXP '(^dist-|^agg|aggregation|core|qfx|-[cj]$|-cx$|-j$)'
      )
      AND LOWER(CONCAT_WS(' ',
        COALESCE(l.remote_hostname,''),
        COALESCE(l.remote_port,''),
        COALESCE(l.remote_platform,''),
        COALESCE(l.remote_version,'')
      )) NOT REGEXP 'aruba ap|model: [0-9]+h|axis|camera|phone|sip-|codec|poweredge|linux|printer|algo|yealink|polycom|in-touch|intouch|shure|microflex|loudspeaker|mxn5|mxn'
      {q_clause}
    GROUP BY l.remote_hostname
    HAVING links_seen >= %s
    ORDER BY links_seen DESC, l.remote_hostname
    LIMIT 500
    """
    params.append(min_links)

    rows = safe_query(sql, tuple(params))

    if mode == "missing":
        rows = [r for r in rows if not r.get("possible_device_id")]
    elif mode == "exists":
        rows = [r for r in rows if r.get("possible_device_id")]

    body_rows = ""
    for r in rows:
        possible_id = r.get("possible_device_id")

        if possible_id:
            action = "Exists in LibreNMS. Rediscover/poll LLDP on the local switches, or fix hostname/sysName normalization."
            match = f'<a href="/dashboard?device_ids={h(possible_id)}">{h(r.get("possible_device"))}</a><br>{h(r.get("possible_device_ip") or "")}'
            row_class = ""
        else:
            action = "Missing from LibreNMS. Add device or align Mist/Junos hostname with DNS, then rediscover local LLDP."
            match = "No matching device row"
            row_class = ' class="unused-free"'

        edges = h(r.get("local_edges") or "").replace("\n", "<br>")
        ports = h(r.get("remote_ports") or "").replace("\n", "<br>")
        version = h(r.get("remote_version") or r.get("remote_platform") or "")

        if int(r.get("links_seen") or 0) > 8:
            edges += f"<br><span class='muted'>... {h(int(r.get('links_seen')) - 8)} more links</span>"

        body_rows += f"""
        <tr{row_class}>
          <td>{h(r.get('remote_hostname'))}</td>
          <td>{h(r.get('links_seen'))}</td>
          <td>{edges}</td>
          <td>{ports}</td>
          <td>{version}</td>
          <td>{match}</td>
          <td>{action}</td>
        </tr>"""

    if not body_rows:
        body_rows = '<tr><td colspan="7">No unmatched switch-like LLDP neighbors found for this view.</td></tr>'

    body = f"""
<section class="panel">
  <h1>Unmatched LLDP Switches</h1>

  <form class="unused-controls" method="get" action="/tools/unmatched-lldp-switches">
    <input name="q" value="{h(q)}" placeholder="Filter remote host, local switch, port, platform" style="min-width:380px" />
    <label>Min links <input name="min_links" value="{h(min_links)}" style="width:70px" /></label>
    <select name="mode">
      <option value="missing" {'selected' if mode == 'missing' else ''}>Missing from LibreNMS</option>
      <option value="exists" {'selected' if mode == 'exists' else ''}>Exists but not correlated</option>
      <option value="all" {'selected' if mode == 'all' else ''}>All unmatched LLDP</option>
    </select>
    <button type="submit">Search</button>
    <a class="button" href="/tools/unmatched-lldp-switches">Clear</a>
  </form>

  <div class="actions" style="margin-bottom:8px">
    <a class="button" href="/tools/unmatched-lldp-switches?mode=missing&min_links={h(min_links)}">Missing from LibreNMS</a>
    <a class="button" href="/tools/unmatched-lldp-switches?mode=exists&min_links={h(min_links)}">Exists but not correlated</a>
    <a class="button" href="/tools/unmatched-lldp-switches?mode=all&min_links={h(min_links)}">All</a>
  </div>

  <div class="unused-total">
    Default view shows switch-like LLDP neighbors that do not have a matching LibreNMS device row. Endpoint devices are hidden.
  </div>

  {table(["Remote Switch", "Links", "Seen From", "Remote Ports", "Platform / Version", "LibreNMS Match", "Action"], body_rows)}
</section>
"""
    return layout("Unmatched LLDP Switches", body)



def _solidserver_plugin_settings() -> dict:
    import json
    import os

    settings = {}
    try:
        cols = fetch_all("SHOW COLUMNS FROM plugins")
        colnames = {str(c.get("Field")) for c in cols}
        name_col = next((c for c in ("plugin_name", "name", "plugin") if c in colnames), None)
        if name_col and "settings" in colnames:
            row = fetch_one(f"SELECT settings FROM plugins WHERE `{name_col}` = %s LIMIT 1", ("SolidServer",))
            if row and row.get("settings"):
                parsed = json.loads(str(row.get("settings")))
                if isinstance(parsed, dict):
                    settings.update(parsed)
    except Exception:
        pass

    return {
        "base_url": str(settings.get("base_url") or os.getenv("EIP_BASE_URL") or "https://juno-eip.middlebury.edu").rstrip("/"),
        "username": str(settings.get("username") or os.getenv("EIP_USER") or ""),
        "password": str(settings.get("password") or os.getenv("EIP_PASS") or ""),
        "verify_tls": bool(settings.get("verify_tls", False)),
    }


def _eip_get_rows(endpoint: str, where: str = "", max_rows: int = 100) -> list:
    import base64
    import json
    import ssl
    import urllib.parse
    import urllib.request

    cfg = _solidserver_plugin_settings()
    if not cfg["username"] or not cfg["password"]:
        raise RuntimeError("SolidServer credentials not found in LibreNMS plugin settings.")

    params = {"limit": max_rows, "offset": 0}
    if where:
        params["WHERE"] = where

    url = cfg["base_url"] + endpoint + "?" + urllib.parse.urlencode(params)
    auth = base64.b64encode((cfg["username"] + ":" + cfg["password"]).encode()).decode()
    req = urllib.request.Request(url, headers={
        "Authorization": "Basic " + auth,
        "Accept": "application/json",
    })

    ctx = ssl.create_default_context() if cfg["verify_tls"] else ssl._create_unverified_context()

    with urllib.request.urlopen(req, context=ctx, timeout=25) as resp:
        body = resp.read().decode("utf-8", "replace")

    data = json.loads(body) if body.strip() else []
    if isinstance(data, dict):
        if str(data.get("errno", "0")) != "0":
            raise RuntimeError(data.get("errmsg") or data.get("name") or "SolidServer API error")
        data = [data]

    return [r for r in data if isinstance(r, dict) and str(r.get("errno", "0")) == "0"]


def _eip_q(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _solid_table(rows: list, cols: list) -> str:
    if not rows:
        return "<div class='unused-total'>No matches.</div>"
    body = ""
    for r in rows:
        body += "<tr>" + "".join(f"<td>{h(r.get(k, ''))}</td>" for label, k in cols) + "</tr>"
    return table([label for label, k in cols], body)


def _solidserver_search(endpoint: str, where: str, max_rows: int = 100) -> list:
    try:
        return _eip_get_rows(endpoint, where, max_rows=max_rows)
    except Exception:
        return []




def _eip_get_rows_paged(endpoint: str, where: str = "", max_rows: int = 2000) -> list:
    import base64
    import json
    import ssl
    import urllib.parse
    import urllib.request

    cfg = _solidserver_plugin_settings()
    if not cfg["username"] or not cfg["password"]:
        raise RuntimeError("SolidServer credentials not found in LibreNMS plugin settings.")

    rows = []
    limit = 500
    offset = 0

    while len(rows) < max_rows:
        params = {"limit": limit, "offset": offset}
        if where:
            params["WHERE"] = where

        url = cfg["base_url"] + endpoint + "?" + urllib.parse.urlencode(params)
        auth = base64.b64encode((cfg["username"] + ":" + cfg["password"]).encode()).decode()
        req = urllib.request.Request(url, headers={
            "Authorization": "Basic " + auth,
            "Accept": "application/json",
        })

        ctx = ssl.create_default_context() if cfg["verify_tls"] else ssl._create_unverified_context()

        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")

        page = json.loads(body) if body.strip() else []
        if isinstance(page, dict):
            if str(page.get("errno", "0")) != "0":
                raise RuntimeError(page.get("errmsg") or page.get("name") or "SolidServer API error")
            page = [page]

        if not isinstance(page, list):
            raise RuntimeError("SolidServer API returned unexpected data")

        good = [r for r in page if isinstance(r, dict) and str(r.get("errno", "0")) == "0"]
        rows.extend(good)

        if len(page) < limit:
            break

        offset += limit

    return rows[:max_rows]


def _safe_int(v, default=0):
    try:
        if v is None or v == "":
            return default
        return int(float(str(v)))
    except Exception:
        return default


def _safe_float(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(str(v))
    except Exception:
        return default


def _solidserver_dashboard_html() -> str:
    errors = []

    def load(endpoint, max_rows):
        try:
            return _eip_get_rows_paged(endpoint, "", max_rows=max_rows)
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
            return []

    ranges = load("/rest/dhcp_range_list", 5000)
    scopes = load("/rest/dhcp_scope_list", 3000)
    statics = load("/rest/dhcp_static_list", 1000)
    ipam = load("/rest/ip_address_list", 1000)
    rrs = load("/rest/dns_rr_list", 3000)
    zones = load("/rest/dns_zone_list", 1000)

    by_network = {}
    for r in ranges:
        name = r.get("dhcpsn_name") or r.get("dhcpscope_name") or "unknown"
        item = by_network.setdefault(name, {
            "name": name,
            "ranges": 0,
            "used": 0,
            "size": 0,
            "lease_percent_max": 0.0,
            "servers": set(),
            "scopes": set(),
        })
        size = _safe_int(r.get("dhcprange_size"))
        used = _safe_int(r.get("dhcprange_lease_count"))
        pct = _safe_float(r.get("dhcprange_lease_percent"), 0.0) or 0.0
        item["ranges"] += 1
        item["size"] += size
        item["used"] += used
        item["lease_percent_max"] = max(item["lease_percent_max"], pct)
        if r.get("dhcp_name"):
            item["servers"].add(str(r.get("dhcp_name")))
        if r.get("dhcpscope_name"):
            item["scopes"].add(str(r.get("dhcpscope_name")))

    network_rows = sorted(by_network.values(), key=lambda x: x["lease_percent_max"], reverse=True)[:100]
    network_body = ""
    for n in network_rows:
        pct = (n["used"] / n["size"] * 100.0) if n["size"] else n["lease_percent_max"]
        network_body += f"""
        <tr>
          <td><a href="/tools/solidserver?q={h(n['name'])}">{h(n['name'])}</a></td>
          <td>{h(n['ranges'])}</td>
          <td>{h(n['size'])}</td>
          <td>{h(n['used'])}</td>
          <td>{h(f'{pct:.1f}%')}</td>
          <td>{h(', '.join(sorted(n['servers'])[:3]))}</td>
        </tr>
        """

    rr_by_type = {}
    rr_by_zone = {}
    for r in rrs:
        rr_type = r.get("rr_type") or "unknown"
        zone = r.get("dnszone_name") or "unknown"
        rr_by_type[rr_type] = rr_by_type.get(rr_type, 0) + 1
        rr_by_zone[zone] = rr_by_zone.get(zone, 0) + 1

    rr_type_body = ""
    for rr_type, count in sorted(rr_by_type.items(), key=lambda kv: kv[1], reverse=True):
        rr_type_body += f"<tr><td>{h(rr_type)}</td><td>{h(count)}</td></tr>"

    zone_body = ""
    for zone, count in sorted(rr_by_zone.items(), key=lambda kv: kv[1], reverse=True)[:100]:
        zone_body += f"""
        <tr>
          <td><a href="/tools/solidserver?q={h(zone)}">{h(zone)}</a></td>
          <td>{h(count)}</td>
        </tr>
        """

    static_body = ""
    for r in statics[:100]:
        static_body += f"""
        <tr>
          <td><a href="/tools/solidserver?q={h(r.get('dhcphost_name') or r.get('dhcphost_addr') or '')}">{h(r.get('dhcphost_name'))}</a></td>
          <td>{h(r.get('dhcphost_addr'))}</td>
          <td>{h(r.get('dhcphost_mac_addr'))}</td>
          <td>{h(r.get('dhcpsn_name'))}</td>
          <td>{h(r.get('dhcp_name'))}</td>
        </tr>
        """

    ipam_body = ""
    for r in ipam[:100]:
        ipam_body += f"""
        <tr>
          <td><a href="/tools/solidserver?q={h(r.get('hostaddr') or '')}">{h(r.get('hostaddr'))}</a></td>
          <td>{h(r.get('name'))}</td>
          <td>{h(r.get('mac_addr'))}</td>
          <td>{h(r.get('subnet_name'))}</td>
          <td>{h(r.get('pool_name'))}</td>
          <td>{h(r.get('last_seen'))}</td>
        </tr>
        """

    zone_inventory_body = ""
    for z in zones[:100]:
        zone_inventory_body += f"""
        <tr>
          <td><a href="/tools/solidserver?q={h(z.get('dnszone_name') or '')}">{h(z.get('dnszone_name'))}</a></td>
          <td>{h(z.get('dnsview_name'))}</td>
          <td>{h(z.get('dnszone_type'))}</td>
          <td>{h(z.get('dns_name'))}</td>
          <td>{h(z.get('dnszone_is_reverse'))}</td>
        </tr>
        """

    error_html = ""
    if errors:
        error_html = "<section class='panel'><h2>Load notes</h2>" + "".join(f"<div class='unused-total'>{h(e)}</div>" for e in errors) + "</section>"

    return f"""
    <section class="panel">
      <h1>SolidServer</h1>
      <p>Live dashboard from EIP/SolidServer DHCP, IPAM, and DNS REST data.</p>
      <form class="unused-controls" method="get" action="/tools/solidserver">
        <input name="q" placeholder="IP, hostname, FQDN, MAC, subnet, DNS value">
        <button class="button" type="submit">Search</button>
      </form>
    </section>

    <section class="dashboard-grid">
      {card("DHCP Ranges", len(ranges))}
      {card("DHCP Scopes", len(scopes))}
      {card("DHCP Static", len(statics))}
      {card("IPAM Records", len(ipam))}
      {card("DNS RRs", len(rrs))}
      {card("DNS Zones", len(zones))}
    </section>

    <section class="panel">
      <h2>DHCP Utilization by Shared Network</h2>
      <div class="unused-total">Top 100 by highest observed range utilization.</div>
      {table(["Shared Network", "Ranges", "Size", "Used", "Used %", "DHCP Servers"], network_body)}
    </section>

    <section class="dashboard-grid">
      <section class="panel">
        <h2>DNS RR Types</h2>
        {table(["Type", "Count"], rr_type_body)}
      </section>
      <section class="panel">
        <h2>Top DNS Zones by RR Count</h2>
        {table(["Zone", "RRs"], zone_body)}
      </section>
    </section>

    <section class="panel">
      <h2>DNS Zone Inventory</h2>
      {table(["Zone", "View", "Type", "DNS Server", "Reverse"], zone_inventory_body)}
    </section>

    <section class="panel">
      <h2>DHCP Static / Reservations Sample</h2>
      {table(["Name", "IP", "MAC", "Shared Network", "DHCP Server"], static_body)}
    </section>

    <section class="panel">
      <h2>IPAM Address Sample</h2>
      {table(["IP", "Name", "MAC", "Subnet", "Pool", "Last Seen"], ipam_body)}
    </section>

    {error_html}
    """




@app.get("/tools/clearpass", response_class=HTMLResponse)
def tools_clearpass(q: str = "", limit: int = 50):
    from app.services.middkips_clearpass_page import render_clearpass_lookup_page
    body = render_clearpass_lookup_page(q=q, limit=limit)
    return layout("ClearPass Lookup", body)


@app.get("/tools/lookup", response_class=HTMLResponse)
def tools_lookup(q: str = ""):
    from app.services.middkips_lookup_hub import render_lookup_hub_page
    body = render_lookup_hub_page(q=q)
    return layout("Lookup Tools", body)


@app.get("/lookup.csv")
def lookup_csv(q: str = "", limit: int = 50):
    import csv
    import io
    import json
    import re
    from fastapi.responses import Response
    from app.services.universal_tool import universal_context

    q = (q or "").strip()
    try:
        limit = int(limit or 50)
    except Exception:
        limit = 50

    def clean(value):
        if value is None:
            return ""
        if isinstance(value, (dict, list, tuple)):
            value = json.dumps(value, default=str)
        value = str(value)
        value = re.sub(r"<[^>]+>", "", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    ctx = universal_context(q=q, limit=limit)

    rows = []

    for row in ctx.get("entity_rows", []) or []:
        if isinstance(row, dict):
            out = {"section": "Correlated Summary"}
            out.update(row)
            rows.append(out)

    for sec in ctx.get("sections", []) or []:
        title = sec.get("title") or "Section"
        for row in sec.get("rows", []) or sec.get("preview_rows", []) or []:
            if isinstance(row, dict):
                out = {"section": title}
                out.update(row)
                rows.append(out)

    preferred = [
        "section", "kind", "name", "sources", "status", "ip", "mac", "site",
        "role", "model", "version", "serial", "device", "interface", "title",
        "type", "value", "zone", "view", "time", "message", "user", "id",
    ]

    all_keys = []
    for key in preferred:
        if any(key in r for r in rows):
            all_keys.append(key)

    for row in rows:
        for key in row.keys():
            if key not in all_keys:
                all_keys.append(key)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=all_keys, extrasaction="ignore")
    writer.writeheader()

    for row in rows:
        writer.writerow({k: clean(v) for k, v in row.items()})

    safe_q = re.sub(r"[^A-Za-z0-9_.-]+", "_", q or "lookup").strip("_")[:80]
    filename = f"middkips-lookup-{safe_q}.csv"

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )



@app.get("/tools/topology", response_class=HTMLResponse)
def tools_topology(q: str = "", limit: int = 50):
    from app.services.middkips_mist_topology import render_mist_topology_page
    body = render_mist_topology_page(q=q, limit=limit)
    return layout("Logical Topology", body)

@app.get("/tools/mist/topology", response_class=HTMLResponse)
def tools_mist_topology(q: str = "", limit: int = 250):
    return HTMLResponse("""
    <!doctype html>
    <html>
    <head>
      <title>Legacy Mist Topology Disabled</title>
      <style>
        body {
          font-family: system-ui, -apple-system, Segoe UI, sans-serif;
          margin: 2rem;
          background: #0f1115;
          color: #e8e8e8;
        }
        .panel {
          max-width: 900px;
          border: 1px solid #333;
          border-radius: 12px;
          padding: 1.5rem;
          background: #171a21;
        }
        a { color: #67b7ff; }
        code {
          background: #242936;
          padding: .15rem .35rem;
          border-radius: 4px;
        }
        ul { line-height: 1.7; }
      </style>
    </head>
    <body>
      <div class="panel">
        <h1>Legacy Mist Topology Disabled</h1>
        <p>
          The old all-in-one topology view has been disabled because it mixed sites,
          devices, switches, APs, and clients into one graph.
        </p>
        <p>Use these views for now:</p>
        <ul>
          <li><a href="/tools/mist">Mist Lookup</a></li>
          <li>Site views from the Mist lookup page</li>
          <li>Switch detail pages from site views</li>
        </ul>
        <p>
          Replacement plan:
          <code>site summary</code>,
          <code>single-site topology</code>,
          <code>switch/AP/client local view</code>.
        </p>
      </div>
    </body>
    </html>
    """)

@app.get("/tools/mist", response_class=HTMLResponse)
def tools_mist(q: str = "", limit: int = 50):
    from app.services.middkips_mist_page import render_mist_lookup_page
    body = render_mist_lookup_page(q=q, limit=limit)
    return layout("Mist Lookup", body)


@app.get("/tools/mist/site/{site_id}", response_class=HTMLResponse)
def tools_mist_site(site_id: str, limit: int = 100):
    try:
        from app.services.middkips_mist_page import render_mist_site_page
        body = render_mist_site_page(site_id=site_id, limit=limit)
    except Exception as exc:
        body = f"""
        <section class="panel">
          <h1>Mist Site Error</h1>
          <p class="muted">Could not load Mist site detail for {h(site_id)}.</p>
          <pre>{h(exc)}</pre>
          <a class="button" href="/tools/mist">Back to Mist Lookup</a>
        </section>
        """
    return layout("Mist Site", body)


@app.get("/tools/mist/switch/{site_id}/{mac}", response_class=HTMLResponse)
def tools_mist_switch(site_id: str, mac: str):
    try:
        from app.services.middkips_mist_page import render_mist_switch_page
        body = render_mist_switch_page(site_id=site_id, mac=mac)
    except Exception as exc:
        body = f"""
        <section class="panel">
          <h1>Mist Switch Error</h1>
          <p class="muted">Could not load Mist switch detail for site {h(site_id)} / MAC {h(mac)}.</p>
          <pre>{h(exc)}</pre>
          <a class="button" href="/tools/mist">Back to Mist Lookup</a>
        </section>
        """
    return layout("Mist Switch", body)

@app.get("/tools/solidserver", response_class=HTMLResponse)
def tools_solidserver(q: str = ""):
    q = (q or "").strip()

    if not q:
        try:
            body = _solidserver_dashboard_html()
        except Exception as exc:
            body = f"""
            <section class="panel">
              <h1>SolidServer</h1>
              <div class="unused-total">Dashboard load failed: {h(exc)}</div>
              <form class="unused-controls" method="get" action="/tools/solidserver">
                <input name="q" placeholder="IP, hostname, FQDN, MAC, subnet, DNS value">
                <button class="button" type="submit">Search</button>
              </form>
            </section>
            """
        return layout("SolidServer", body)

    like = "%" + q.replace("'", "''") + "%"
    exact = _eip_q(q)

    ipam_where = " OR ".join([
        f"hostaddr={exact}",
        f"name LIKE {_eip_q(like)}",
        f"ip_alias LIKE {_eip_q(like)}",
        f"mac_addr LIKE {_eip_q(like)}",
        f"subnet_name LIKE {_eip_q(like)}",
        f"pool_name LIKE {_eip_q(like)}",
    ])

    dhcp_static_where = " OR ".join([
        f"dhcphost_addr={exact}",
        f"dhcphost_name LIKE {_eip_q(like)}",
        f"db_hostname LIKE {_eip_q(like)}",
        f"dhcphost_mac_addr LIKE {_eip_q(like)}",
        f"dhcpsn_name LIKE {_eip_q(like)}",
        f"dhcpscope_name LIKE {_eip_q(like)}",
    ])

    dhcp_scope_where = " OR ".join([
        f"dhcpsn_name LIKE {_eip_q(like)}",
        f"dhcpscope_name LIKE {_eip_q(like)}",
        f"dhcpscope_net_addr LIKE {_eip_q(like)}",
        f"dhcp_name LIKE {_eip_q(like)}",
    ])

    dhcp_range_where = " OR ".join([
        f"dhcpsn_name LIKE {_eip_q(like)}",
        f"dhcpscope_name LIKE {_eip_q(like)}",
        f"dhcprange_name LIKE {_eip_q(like)}",
        f"dhcprange_start_addr LIKE {_eip_q(like)}",
        f"dhcprange_end_addr LIKE {_eip_q(like)}",
        f"dhcp_name LIKE {_eip_q(like)}",
    ])

    dns_rr_where = " OR ".join([
        f"rr_full_name LIKE {_eip_q(like)}",
        f"rr_all_value LIKE {_eip_q(like)}",
        f"value1 LIKE {_eip_q(like)}",
        f"value2 LIKE {_eip_q(like)}",
        f"target LIKE {_eip_q(like)}",
        f"dnszone_name LIKE {_eip_q(like)}",
    ])

    dns_zone_where = " OR ".join([
        f"dnszone_name LIKE {_eip_q(like)}",
        f"dns_name LIKE {_eip_q(like)}",
        f"dnsview_name LIKE {_eip_q(like)}",
    ])

    error = ""
    try:
        ipam = _solidserver_search("/rest/ip_address_list", ipam_where, 100)
        statics = _solidserver_search("/rest/dhcp_static_list", dhcp_static_where, 100)
        scopes = _solidserver_search("/rest/dhcp_scope_list", dhcp_scope_where, 100)
        ranges = _solidserver_search("/rest/dhcp_range_list", dhcp_range_where, 100)
        rrs = _solidserver_search("/rest/dns_rr_list", dns_rr_where, 150)
        zones = _solidserver_search("/rest/dns_zone_list", dns_zone_where, 50)
    except Exception as exc:
        ipam = statics = scopes = ranges = rrs = zones = []
        error = str(exc)

    arp_rows = safe_query("""
        SELECT m.ipv4_address, m.mac_address, m.context_name,
               COALESCE(NULLIF(d.sysName,''), NULLIF(d.hostname,''), INET6_NTOA(d.ip)) AS device,
               d.device_id, p.ifName, p.port_id, p.ifOperStatus
        FROM ipv4_mac m
        LEFT JOIN ports p ON p.port_id = m.port_id
        LEFT JOIN devices d ON d.device_id = COALESCE(m.device_id, p.device_id)
        WHERE m.ipv4_address LIKE %s OR m.mac_address LIKE %s OR d.hostname LIKE %s OR d.sysName LIKE %s OR p.ifName LIKE %s
        ORDER BY m.ipv4_address
        LIMIT 100
    """, (f"%{q}%", f"%{q.replace(':','').replace('-','').replace('.','')}%", f"%{q}%", f"%{q}%", f"%{q}%"))

    arp_body = ""
    for row in arp_rows:
        arp_body += f"""
        <tr>
          <td>{h(row.get('ipv4_address'))}</td>
          <td>{h(fmt_mac(row.get('mac_address')))}</td>
          <td><a href="/dashboard?device_ids={h(row.get('device_id'))}">{h(row.get('device'))}</a></td>
          <td><a href="/interface/{h(row.get('port_id'))}">{h(row.get('ifName'))}</a></td>
          <td>{h(row.get('ifOperStatus'))}</td>
          <td>{h(row.get('context_name'))}</td>
        </tr>
        """

    body = f"""
    <section class="panel">
      <h1>SolidServer Lookup</h1>
      <form class="unused-controls" method="get" action="/tools/solidserver">
        <input name="q" value="{h(q)}" placeholder="IP, hostname, FQDN, MAC, subnet, DNS value">
        <button class="button" type="submit">Search</button>
        <a class="button" href="/tools/solidserver">Clear</a>
      </form>
      {('<div class="unused-total">Lookup error: ' + h(error) + '</div>') if error else ''}
    </section>

    <section class="dashboard-grid">
      {card("IPAM", len(ipam))}
      {card("DHCP Static", len(statics))}
      {card("DHCP Scopes", len(scopes))}
      {card("DHCP Ranges", len(ranges))}
      {card("DNS RRs", len(rrs))}
      {card("DNS Zones", len(zones))}
    </section>

    <section class="panel"><h2>IPAM Address Records</h2>
      {_solid_table(ipam, [("IP", "hostaddr"), ("Name", "name"), ("Alias", "ip_alias"), ("MAC", "mac_addr"), ("Subnet", "subnet_name"), ("Pool", "pool_name"), ("Last Seen", "last_seen"), ("Created By", "trace_creation_usr_login"), ("Updated", "trace_last_update_date")])}
    </section>

    <section class="panel"><h2>DHCP Static / Reservations</h2>
      {_solid_table(statics, [("Name", "dhcphost_name"), ("IP", "dhcphost_addr"), ("MAC", "dhcphost_mac_addr"), ("DB Hostname", "db_hostname"), ("Scope", "dhcpscope_name"), ("Shared Network", "dhcpsn_name"), ("DHCP Server", "dhcp_name"), ("Last Seen", "dhcphost_last_seen"), ("Expire", "dhcphost_expire_time")])}
    </section>

    <section class="panel"><h2>DHCP Scopes</h2>
      {_solid_table(scopes, [("Shared Network", "dhcpsn_name"), ("Scope", "dhcpscope_name"), ("Network", "dhcpscope_net_addr"), ("Mask", "dhcpscope_net_mask"), ("Prefix", "dhcpscope_prefix"), ("Size", "dhcpscope_size"), ("DHCP Server", "dhcp_name"), ("Failover", "dhcpfailover_name")])}
    </section>

    <section class="panel"><h2>DHCP Ranges</h2>
      {_solid_table(ranges, [("Shared Network", "dhcpsn_name"), ("Scope", "dhcpscope_name"), ("Range", "dhcprange_name"), ("Start", "dhcprange_start_addr"), ("End", "dhcprange_end_addr"), ("Size", "dhcprange_size"), ("Lease Count", "dhcprange_lease_count"), ("Lease %", "dhcprange_lease_percent"), ("Server", "dhcp_name")])}
    </section>

    <section class="panel"><h2>DNS Resource Records</h2>
      {_solid_table(rrs, [("Name", "rr_full_name"), ("Type", "rr_type"), ("Value", "rr_all_value"), ("Value1", "value1"), ("Zone", "dnszone_name"), ("View", "dnsview_name"), ("DNS Server", "dns_name"), ("TTL", "ttl"), ("Last Update Days", "rr_last_update_days")])}
    </section>

    <section class="panel"><h2>DNS Zones</h2>
      {_solid_table(zones, [("Zone", "dnszone_name"), ("View", "dnsview_name"), ("Type", "dnszone_type"), ("DNS Server", "dns_name"), ("Reverse", "dnszone_is_reverse"), ("Masters", "dnszone_masters"), ("Also Notify", "dnszone_also_notify")])}
    </section>

    <section class="panel"><h2>LibreNMS ARP / IP / Port Correlation</h2>
      {table(["IP", "MAC", "Device", "Interface", "Status", "Context"], arp_body) if arp_body else "<div class='unused-total'>No LibreNMS ARP/IP matches.</div>"}
    </section>
    """

    return layout("SolidServer", body)


@app.get("/tools/topology-v2", response_class=HTMLResponse)
def tools_topology_v2(q: str = "", limit: int = 50, show_aps: str = "0"):
    from app.services.middkips_topology_v2_page import render_topology_v2_page
    body = render_topology_v2_page(q=q, limit=limit, show_aps=show_aps)
    return layout("Logical Topology v2", body)


@app.get("/tools/mist/clients-v2", response_class=HTMLResponse)
def tools_mist_clients_v2(q: str = "", limit: int = 50):
    from app.services.middkips_mist_clients_v2_page import render_mist_clients_v2_page
    body = render_mist_clients_v2_page(q=q, limit=limit)
    return layout("Mist Client Search v2", body)


@app.get("/tools/topology-v2-split", response_class=HTMLResponse)
def tools_topology_v2_split(q: str = "", limit: int = 50, show_aps: str = "0", focus: str = ""):
    from app.services.middkips_topology_v2_split_page import render_topology_v2_split_page
    body = render_topology_v2_split_page(q=q, limit=limit, show_aps=show_aps, focus=focus)
    return layout("Topology v2 Split", body)




@app.get("/tv/topology", response_class=HTMLResponse)
def tv_topology(zoom: str = "1.35",
    show_nonmist_access: str = "0"):
    from app.services.middkips_topology_vmap_page import render_topology_vmap_page

    try:
        zoom_value = float(zoom)
    except Exception:
        zoom_value = 1.35

    # Keep humans from entering 400x and accidentally summoning a graph kaiju.
    zoom_value = max(0.75, min(2.75, zoom_value))

    body = render_topology_vmap_page(
        site="vt",
        show_ports="0",
        show_aps="0",
        show_core="1",
        show_services="1",
        show_distribution="1",
        show_access="0",
        flow="1",
        redundant_only="1",
        tv="1",
        show_nonmist_access=show_nonmist_access)

    scaled_body = (
        f'<div class="tv-scale-spacer" style="--tv-scale:{zoom_value};">'
        f'<div class="tv-scale">{body}</div>'
        f'</div>'
    )

    extra_css = """
    <style>
      html, body {
        margin: 0 !important;
        padding: 0 !important;
        width: 100vw;
        min-height: 100vh;
        overflow: auto !important;
        background: #111827;
        color: #e5e7eb;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }

      main, .container, .content {
        margin: 0 !important;
        padding: 0 !important;
        max-width: none !important;
        width: 100vw !important;
      }

      h1 {
        font-size: 28px;
        margin: 10px 18px 4px 18px;
      }

      .toolbar {
        display: none !important;
      }

      .note {
        margin: 4px 18px 8px 18px !important;
        font-size: 14px;
      }

      /*
        Transform-based wallboard scaling.
        The spacer creates scrollable area. The inner element gets scaled.
        This is uglier than it should be, naturally, but signage wrappers
        tend to treat normal responsive CSS like a personal insult.
      */
      .tv-scale-spacer {
        width: calc(100vw * var(--tv-scale));
        min-height: calc(100vh * var(--tv-scale));
        overflow: visible;
      }

      .tv-scale {
        transform: scale(var(--tv-scale));
        transform-origin: top left;
        width: 100vw;
      }

      .vmap-wrap,
      .vmap-wrap.tv {
        width: 100vw !important;
        max-height: none !important;
        margin: 0 !important;
        padding: 8px !important;
        border: 0 !important;
        border-radius: 0 !important;
        overflow: visible !important;
        box-sizing: border-box;
      }

      svg.vmap {
        width: 100vw !important;
        height: auto !important;
        min-width: 0 !important;
        max-width: none !important;
        display: block;
      }

      .edge:hover path {
        stroke-width: 9 !important;
        opacity: 1 !important;
        filter: drop-shadow(0 0 10px #38bdf8);
      }
    </style>
    """

    html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="300">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MiddKiPS Topology TV</title>
  __EXTRA_CSS__
</head>
<body>
  __BODY__
</body>
</html>"""

    return HTMLResponse(
        html
        .replace("__EXTRA_CSS__", extra_css)
        .replace("__BODY__", scaled_body)
    )


@app.get("/tools/topology-vmap", response_class=HTMLResponse)
def tools_topology_vmap(
    site: str = "vt",
    show_ports: str = "1",
    show_aps: str = "0",
    show_core: str = "1",
    show_services: str = "1",
    show_distribution: str = "1",
    show_access: str = "1",
    flow: str = "0",
    redundant_only: str = "0",
    tv: str = "0",
    layout: str = "topdown",
    selected: str = "",
    focus: str = "",
    show_nonmist_access: str = "0"):
    from app.services.middkips_topology_vmap_page import render_topology_vmap_page
    body = render_topology_vmap_page(
        site=site,
        show_ports=show_ports,
        show_aps=show_aps,
        show_core=show_core,
        show_services=show_services,
        show_distribution=show_distribution,
        show_access=show_access,
        flow=flow,
        redundant_only=redundant_only,
        tv=tv,
        layout=layout,
        selected=selected,
        focus=focus)
    return globals()["layout"]("Topology V Map", body)


@app.get("/tv/topology-wallboard", response_class=HTMLResponse)
def tv_topology_wallboard():
    from app.services.middkips_topology_vmap_page import render_topology_vmap_page

    zoom_value = 1.35

    body = render_topology_vmap_page(
        site="vt",
        show_ports="0",
        show_aps="0",
        show_core="1",
        show_services="1",
        show_distribution="1",
        show_access="0",
        flow="1",
        redundant_only="1",
        tv="1",
    )

    scaled_body = (
        f'<div class="tv-scale-spacer" style="--tv-scale:{zoom_value};">'
        f'<div class="tv-scale">{body}</div>'
        f'</div>'
    )

    extra_css = """
    <style>
      html, body {
        margin: 0 !important;
        padding: 0 !important;
        width: 100vw;
        min-height: 100vh;
        overflow: auto !important;
        background: #111827;
        color: #e5e7eb;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }

      main, .container, .content {
        margin: 0 !important;
        padding: 0 !important;
        max-width: none !important;
        width: 100vw !important;
      }

      h1 {
        font-size: 28px;
        margin: 10px 18px 4px 18px;
      }

      .toolbar,
      .preset-dropdown,
      .layer-switches,
      .vmap-drag-controls {
        display: none !important;
      }

      .note {
        margin: 4px 18px 8px 18px !important;
        font-size: 14px;
      }

      .tv-scale-spacer {
        width: calc(100vw * var(--tv-scale));
        min-height: calc(100vh * var(--tv-scale));
        overflow: visible;
      }

      .tv-scale {
        transform: scale(var(--tv-scale));
        transform-origin: top left;
        width: 100vw;
      }

      .vmap-wrap,
      .vmap-wrap.tv {
        width: 100vw !important;
        max-height: none !important;
        margin: 0 !important;
        padding: 8px !important;
        border: 0 !important;
        border-radius: 0 !important;
        overflow: visible !important;
        box-sizing: border-box;
      }

      svg.vmap {
        width: 100vw !important;
        height: auto !important;
        min-width: 0 !important;
        max-width: none !important;
        display: block;
      }
    </style>
    """

    html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="300">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MiddKiPS Topology Wallboard</title>
  __EXTRA_CSS__
</head>
<body>
  __BODY__
</body>
</html>"""

    return HTMLResponse(
        html
        .replace("__EXTRA_CSS__", extra_css)
        .replace("__BODY__", scaled_body)
    )



