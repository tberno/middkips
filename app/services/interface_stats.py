import html


def h(value):
    return html.escape("" if value is None else str(value), quote=True)


def fmt_speed(value):
    try:
        v = float(value or 0)
    except Exception:
        return ""
    units = ["bps", "Kbps", "Mbps", "Gbps", "Tbps"]
    i = 0
    while v >= 1000 and i < len(units) - 1:
        v /= 1000
        i += 1
    if i == 0:
        return f"{int(v)} {units[i]}"
    return f"{v:.1f} {units[i]}"


def status_badge(value):
    v = "" if value is None else str(value)
    cls = "good" if v.lower() in ("up", "ok", "1", "true") else "bad"
    return f'<span class="status-pill {cls}">{h(v)}</span>'
