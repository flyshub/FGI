#!/usr/bin/env bash
# Dev guard: refuse external access to Database._conn / Database._connection.
# Contract (issue #41): all DB access must go through Database public methods.
# Allowed callers: fgi/storage/database.py itself, and this script.
#
# Run via: bash scripts/check_no_external_conn.sh
# Exit non-zero on any external hit.
set -euo pipefail
cd "$(dirname "$0")/.."

HITS=$(grep -rnE '\._conn(ection)?\b' --include="*.py" \
    | grep -v '^[^:]*fgi/storage/database\.py:' \
    | grep -v '^[^:]*scripts/check_no_external_conn\.' \
    | grep -v '_pycache_' \
    | grep -v '^[^:]*tests/' \
    || true)

if [ -n "$HITS" ]; then
    echo "FAIL: external access to Database._conn / _connection found:"
    echo "$HITS"
    echo "Use Database public methods (count_rows / clear_table / update_score_field /"
    echo "get_indicator_status / get_latest_raw_date / get_raw_date_range / etc.) instead."
    exit 1
fi

echo "OK: no external access to Database._conn / _connection."
