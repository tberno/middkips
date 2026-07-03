#!/usr/bin/env bash
set -u

BASE="${BASE:-http://127.0.0.1:8051}"
DEVICE_ID="${DEVICE_ID:-142}"
fail=0
tmp="/tmp/middkips-check.$$"

check_url() {
  name="$1"
  url="$2"
  max_time="${3:-20}"
  min_bytes="${4:-1000}"
  code=$(curl --max-time "$max_time" -s -o "$tmp" -w "%{http_code}" "$url")
  bytes=$(wc -c < "$tmp" 2>/dev/null || echo 0)
  if [ "$code" = "200" ] && [ "$bytes" -ge "$min_bytes" ]; then
    printf "OK     %-32s %s bytes=%s\n" "$name" "$code" "$bytes"
  else
    printf "FAILED %-32s %s bytes=%s min_bytes=%s url=%s\n" "$name" "$code" "$bytes" "$min_bytes" "$url"
    fail=1
  fi
}

echo "MiddKIPS healthcheck"
echo "BASE=$BASE"
echo "DEVICE_ID=$DEVICE_ID"
echo

check_url "root" "$BASE/" 20 1000
check_url "devices" "$BASE/devices" 20 1000
check_url "dashboard" "$BASE/dashboard?device_ids=$DEVICE_ID" 20 1000
check_url "device page" "$BASE/device/$DEVICE_ID" 20 1000
check_url "config download" "$BASE/device/$DEVICE_ID/config.txt" 20 1000
check_url "interface configuration" "$BASE/reports/interface-configuration?device_ids=$DEVICE_ID" 20 1000
check_url "interface statistics" "$BASE/reports/interface-statistics?device_ids=$DEVICE_ID" 20 1000
check_url "unused interfaces" "$BASE/reports/unused-interfaces?device_ids=$DEVICE_ID" 45 1000
check_url "mac table" "$BASE/reports/mac-table?device_ids=$DEVICE_ID" 20 1000
check_url "arp ip" "$BASE/reports/arp-ip?device_ids=$DEVICE_ID" 20 1000
check_url "vlans" "$BASE/reports/vlans?device_ids=$DEVICE_ID" 20 1000
check_url "events" "$BASE/reports/events?device_ids=$DEVICE_ID" 20 1000
check_url "solidserver tool" "$BASE/tools/solidserver" 20 1000

rm -f "$tmp"
exit "$fail"
