import csv
import json
import os
import re
from pathlib import Path

CSV_FILE = Path(os.getenv("AKIPS_UNUSED_CSV", "/data/akips-unused-interfaces.csv"))
OUT_FILE = Path(os.getenv("AKIPS_UNUSED_CACHE_FILE", "/data/akips_unused_cache.json"))

def norm(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

def key(device, interface):
    return f"{norm(device)}|{norm(interface)}"

def main():
    if not CSV_FILE.exists():
        raise SystemExit(f"Missing CSV file: {CSV_FILE}")

    with CSV_FILE.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []

        state_cols = [h for h in headers if h.startswith("State:") and h != "State:Current"]
        state_col = state_cols[0] if state_cols else ""
        current_col = "State:Current"

        rows = []
        ports = {}

        for row in reader:
            device = row.get("Device", "").strip()
            interface = row.get("Interface", "").strip()
            if not device or not interface:
                continue

            item = {
                "device": device,
                "interface": interface,
                "speed": row.get("Speed", "").strip(),
                "state": row.get(state_col, "").strip() if state_col else "",
                "current": row.get(current_col, "").strip(),
                "last_change": row.get("Last Change", "").strip(),
                "title": row.get("Title", "").strip(),
                "vlans": row.get("VLANs", "").strip(),
            }

            rows.append(item)
            ports[key(device, interface)] = item

    data = {
        "source": str(CSV_FILE),
        "state_window": state_col.replace("State:", "") if state_col else "",
        "headers": headers,
        "rows_total": len(rows),
        "ports": ports,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(OUT_FILE)

    print(f"source: {CSV_FILE}")
    print(f"output: {OUT_FILE}")
    print(f"state_window: {data['state_window']}")
    print(f"rows_total: {data['rows_total']}")
    print(f"cached_ports: {len(ports)}")

if __name__ == "__main__":
    main()
