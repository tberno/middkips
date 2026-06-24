import csv
import json
import os
import re
from pathlib import Path

SRC_DIR = Path(os.getenv("AKIPS_UNUSED_DIR", "data/akips_exports"))
OUT_FILE = Path(os.getenv("AKIPS_UNUSED_CACHE_FILE", "data/akips_unused_cache.json"))

def norm(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

def key(device, interface):
    return f"{norm(device)}|{norm(interface)}"

def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        state_cols = [h for h in headers if h.startswith("State:") and h != "State:Current"]
        state_col = state_cols[0] if state_cols else ""
        state_window = state_col.replace("State:", "") if state_col else ""

        rows = []
        for row in reader:
            device = row.get("Device", "").strip()
            interface = row.get("Interface", "").strip()
            if not device or not interface:
                continue

            rows.append({
                "device": device,
                "interface": interface,
                "speed": row.get("Speed", "").strip(),
                "state": row.get(state_col, "").strip() if state_col else "",
                "current": row.get("State:Current", "").strip(),
                "last_change": row.get("Last Change", "").strip(),
                "title": row.get("Title", "").strip(),
                "vlans": row.get("VLANs", "").strip(),
                "source_file": path.name,
            })
        return headers, state_window, rows

def main():
    files = sorted(SRC_DIR.glob("*.csv"))
    if not files:
        raise SystemExit(f"No CSV files found in {SRC_DIR}")

    ports = {}
    all_headers = []
    windows = {}
    total_rows = 0

    for path in files:
        headers, state_window, rows = read_csv(path)
        all_headers = headers or all_headers
        windows[state_window] = windows.get(state_window, 0) + len(rows)
        total_rows += len(rows)

        for item in rows:
            ports[key(item["device"], item["interface"])] = item

    data = {
        "source_dir": str(SRC_DIR),
        "files": [p.name for p in files],
        "state_window": max(windows, key=windows.get) if windows else "",
        "state_windows": windows,
        "headers": all_headers,
        "rows_total": total_rows,
        "cached_ports": len(ports),
        "ports": ports,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(OUT_FILE)

    print("source_dir:", SRC_DIR)
    print("files:", len(files))
    print("rows_total:", total_rows)
    print("cached_ports:", len(ports))
    print("state_windows:", windows)
    print("output:", OUT_FILE)

if __name__ == "__main__":
    main()
