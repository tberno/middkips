# NetOps v2 Application Update

## Summary

NetOps v2 is a FastAPI-based network operations portal running alongside the existing MiddKIPS application.

- Existing MiddKIPS path: `/middkips/`
- NetOps v2 path: `/netops/`
- Host: `raccoon.middlebury.edu`
- Backend: `127.0.0.1:8052`
- Docker service/container: `netops`
- Source path: `/opt/docker-stacks/middkips/netops-app/app/main.py`

## Current Status

NetOps v2 is functional at:

`https://raccoon.middlebury.edu/netops/`

Confirmed working routes:

| Feature | URL |
|---|---|
| Hub | `/netops/` |
| Device Inventory | `/netops/devices` |
| Universal Lookup | `/netops/tools/object-lookup` |
| SolidServer DDI | `/netops/tools/solidserver` |
| SolidServer Audit placeholder | `/netops/tools/solidserver-audit` |
| Interface Statistics | `/netops/reports/interface-statistics` |
| Interface Configuration | `/netops/reports/interface-configuration` |
| Unused Interfaces | `/netops/reports/unused-interfaces` |
| Events | `/netops/reports/events` |
| Changes | `/netops/reports/changes` |
| MAC Table | `/netops/reports/mac-table` |
| ARP / IP | `/netops/reports/arp-ip` |
| VLANs | `/netops/reports/vlans` |
| LLDP Lookup | `/netops/tools/lldp-lookup` |
| Unmatched LLDP Switches | `/netops/tools/unmatched-lldp-switches` |

## Hub Page

The NetOps Hub has been reworked into an operational launchpad instead of a raw-count dashboard.

The old cards for device and port totals were removed because they were not useful as first-click operational tasks.

The hub now focuses on workflows such as:

- Universal Lookup
- SolidServer DDI
- Device Inventory
- Interface Statistics
- Unused Interfaces
- Events / Changes
- MAC Table
- ARP / IP
- Unmatched LLDP Switches

## Report Exports

NetOps pages include a compact export toolbar.

Current options:

- Download CSV
- Print / Save PDF
- Copy Report Link

Current behavior is client-side and exports visible tables from the current page.

Future improvement:

- Add server-side exports such as `?format=csv` and `?format=pdf`.

## SolidServer DDI

SolidServer DDI lookup is working for regular DDI data including DNS, DHCP, IPAM, ranges, scopes, static records, and zones.

SolidServer User Tracking / Audit integration is still incomplete.

Known audit findings:

- `/frontapi/index.php/___section/get` returns page metadata only.
- `/frontapi/index.php/___report/get` returns notification data, not user-tracking table rows.
- `/frontapi/index.php/___list/template` appears related to the list UI but requires the exact UI-generated payload.
- The true user-tracking row-data request still needs to be captured.

## Known Open Items

- Build a dedicated endpoint-to-switch-port workflow.
- Build a Graylog helper route.
- Finish SolidServer User Tracking / Audit integration.
- Add server-side CSV/PDF export endpoints.
- Add durable long-term interface history tables for cleanup reporting.

## Operations

Rebuild only NetOps:

    cd /opt/docker-stacks/middkips
    python3 -m py_compile netops-app/app/main.py
    sudo docker compose up -d --build --force-recreate netops

Health checks:

    curl -s -o /dev/null -w "backend: %{http_code}\n" http://127.0.0.1:8052/
    curl -k -s -o /dev/null -w "nginx: %{http_code}\n" https://raccoon.middlebury.edu/netops/

Logs:

    sudo docker logs --tail=120 netops

Rollback pattern:

    cd /opt/docker-stacks/middkips
    cp netops-app/app/main.py.bak.DESIRED_BACKUP netops-app/app/main.py
    python3 -m py_compile netops-app/app/main.py
    sudo docker compose up -d --build --force-recreate netops
