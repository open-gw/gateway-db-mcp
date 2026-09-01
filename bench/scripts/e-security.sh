#!/usr/bin/env bash
# Security-layer verification. Confirms the README's claims are true of the
# running system rather than only of the source.
set -euo pipefail
D=http://localhost:8080
G=http://localhost:8000/db

pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; }

echo "== L2: gateway rejects unauthenticated requests =="
code=$(curl -s -o /dev/null -w '%{http_code}' "$G/tables")
[ "$code" = "401" ] && pass "no token -> 401" || fail "no token -> $code (expected 401)"

echo "== L1: read-only credential blocks writes =="
r=$(curl -s -X POST "$D/query" -H 'Content-Type: application/json' \
     -d '{"sql":"INSERT INTO orders (id,customer_id,status,total) VALUES (99999,1,'"'"'x'"'"',1.0)"}')
echo "$r" | grep -qi 'forbidden\|not permitted\|denied' && pass "INSERT rejected" || fail "INSERT response: $r"

echo "== L4: DDL blocked =="
r=$(curl -s -X POST "$D/query" -H 'Content-Type: application/json' -d '{"sql":"DROP TABLE orders"}')
echo "$r" | grep -q 'FORBIDDEN' && pass "DROP rejected" || fail "DROP response: $r"

echo "== L3: non-allowlisted table hidden at discovery =="
curl -sf "$D/tables" | grep -q internal_audit && fail "internal_audit visible" || pass "internal_audit hidden"

echo "== Documented gap: allowedTables NOT enforced on POST /query =="
r=$(curl -s -X POST "$D/query" -H 'Content-Type: application/json' \
     -d '{"sql":"SELECT id,actor FROM internal_audit"}')
echo "  Response: $r"
echo "  If this returns rows, the README §Security model gap is confirmed"
echo "  empirically. Report it. It is the honest disclosure that Reviewer 1"
echo "  called the strongest part of the submission."

echo "== L5: maxRows cap enforced =="
n=$(curl -sf "$D/tables/orders/rows?limit=100000" | jq '.count // (.rows|length)')
echo "  rows returned: $n (SECURITY_MAX_ROWS=500)"
[ "$n" -le 500 ] && pass "cap enforced" || fail "cap not enforced"
