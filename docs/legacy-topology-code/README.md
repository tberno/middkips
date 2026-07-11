# Legacy Topology Code

These files were removed from the active MiddKiPS service layer during the map reset.

The old topology implementation mixed Mist inventory, LLDP, AP/client data, legacy network devices, datacenter views, and wallboard rendering into overlapping routes. That made the maps noisy and hard to reason about.

The replacement model separates maps by purpose:

- Mist Pod Map
- Service Block Map
- Legacy Datacenter Map
- Edge Map
- TV Map Dashboard

The `.legacy` files are retained only for reference while the new map data model is built.
