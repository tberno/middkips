import datetime
import json
import math
import os
import subprocess
import time
from pathlib import Path

import pymysql


RRD_DIR = Path(os.getenv("LIBRENMS_RRD_DIR", "/opt/librenms/rrd"))
OUT_FILE = Path(os.getenv("UNUSED_CACHE_FILE", "/data/unused_interfaces_cache.json"))
MAX_DAYS = int(os.getenv("UNUSED_RRD_MAX_DAYS", "730"))
TRAFFIC_THRESHOLD = float(os.getenv("UNUSED_RRD_TRAFFIC_THRESHOLD", "0"))


def db():
    return pymysql.connect(
        host=os.getenv("LIBRENMS_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("LIBRENMS_DB_PORT", "3306")),
        user=os.getenv("LIBRENMS_DB_USER"),
        password=os.getenv("LIBRENMS_DB_PASSWORD"),
        database=os.getenv("LIBRENMS_DB_NAME", "librenms"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def fetch_ports():
    sql = """
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
            p.ifAdminStatus
        FROM ports p
        JOIN devices d ON d.device_id = p.device_id
        WHERE p.ifName IS NOT NULL
          AND p.ifName <> ''
        ORDER BY d.device_id, p.port_id
    """
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return list(cur.fetchall())


def candidate_rrd_paths(row):
    dirs = []
    for value in (row.get("ip_addr"), row.get("hostname"), row.get("sysName"), row.get("device")):
        if value:
            sval = str(value).strip()
            if sval and sval not in dirs:
                dirs.append(sval)

    for d in dirs:
        yield RRD_DIR / d / f"port-id{row['port_id']}.rrd"


def find_rrd(row):
    for path in candidate_rrd_paths(row):
        if path.exists():
            return path
    return None


def parse_float(value):
    value = value.strip().lower()
    if value in ("nan", "-nan", "u", ""):
        return None
    try:
        f = float(value)
        if math.isfinite(f):
            return f
    except Exception:
        return None
    return None


def last_traffic_ts(rrd_path):
    cmd = [
        "rrdtool",
        "fetch",
        str(rrd_path),
        "AVERAGE",
        "--start",
        f"end-{MAX_DAYS}d",
        "--end",
        "now",
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    if proc.returncode != 0:
        return None, proc.stderr.strip() or proc.stdout.strip()

    header = None
    in_idx = None
    out_idx = None
    last_seen = None

    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue

        if ":" not in line and "INOCTETS" in line and "OUTOCTETS" in line:
            header = line.split()
            in_idx = header.index("INOCTETS")
            out_idx = header.index("OUTOCTETS")
            continue

        if ":" not in line or in_idx is None or out_idx is None:
            continue

        ts_part, values_part = line.split(":", 1)
        try:
            ts = int(ts_part.strip())
        except Exception:
            continue

        values = values_part.split()
        if len(values) <= max(in_idx, out_idx):
            continue

        in_val = parse_float(values[in_idx])
        out_val = parse_float(values[out_idx])

        if (in_val is not None and in_val > TRAFFIC_THRESHOLD) or (out_val is not None and out_val > TRAFFIC_THRESHOLD):
            last_seen = ts

    return last_seen, ""


def fmt_ts(ts):
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")


def main():
    started = time.time()
    ports = fetch_ports()

    result = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "max_days": MAX_DAYS,
        "rrd_dir": str(RRD_DIR),
        "ports_total": len(ports),
        "ports": {},
    }

    print(f"Scanning {len(ports)} ports using RRD dir {RRD_DIR}")

    for idx, row in enumerate(ports, 1):
        if idx % 500 == 0:
            elapsed = int(time.time() - started)
            print(f"  scanned {idx}/{len(ports)} ports in {elapsed}s")

        port_id = str(row["port_id"])
        rrd_path = find_rrd(row)

        item = {
            "device_id": row.get("device_id"),
            "device": row.get("device"),
            "ifName": row.get("ifName"),
            "rrd_found": bool(rrd_path),
            "rrd_path": str(rrd_path) if rrd_path else "",
            "last_traffic_ts": None,
            "last_traffic": "",
            "error": "",
        }

        if rrd_path:
            try:
                ts, err = last_traffic_ts(rrd_path)
                item["last_traffic_ts"] = ts
                item["last_traffic"] = fmt_ts(ts)
                item["error"] = err
            except Exception as exc:
                item["error"] = str(exc)

        result["ports"][port_id] = item

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True))
    tmp.replace(OUT_FILE)

    elapsed = int(time.time() - started)
    print(f"Wrote {OUT_FILE}")
    print(f"Done in {elapsed}s")


if __name__ == "__main__":
    main()
