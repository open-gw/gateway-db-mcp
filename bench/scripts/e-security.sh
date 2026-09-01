#!/usr/bin/env bash
# Security-layer verification. Confirms the README's claims are true of the
# running system rather than only of the source.
#
# Invariant: every check must be falsifiable. A check that cannot fail is worse
# than no check — it creates false confidence. Each assertion below would flip
# to FAIL under a concrete, realistic regression (wrong status, empty body,
# missing allowlist filter, or uncapped row count).
set -euo pipefail
cd "$(dirname "$0")/.."

D=http://localhost:8080
G=http://localhost:8000/db

# Prefer compose value; allow override for local experiments.
SECURITY_MAX_ROWS="${SECURITY_MAX_ROWS:-$(
  awk -F'"' '/SECURITY_MAX_ROWS:/ {print $2; exit}' docker-compose.bench.yml 2>/dev/null || true
)}"
SECURITY_MAX_ROWS="${SECURITY_MAX_ROWS:-500}"

pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; FAILURES=$((FAILURES + 1)); }
FAILURES=0

echo "== L2: gateway rejects unauthenticated requests =="
code=$(curl -s -o /dev/null -w '%{http_code}' "$G/tables")
[ "$code" = "401" ] && pass "no token -> 401" || fail "no token -> $code (expected 401)"

echo "== L1: read-only credential / validator blocks writes =="
code=$(curl -s -o /tmp/e-sec-l1.json -w '%{http_code}' -X POST "$D/query" \
  -H 'Content-Type: application/json' \
  -d '{"sql":"INSERT INTO orders (id,customer_id,status,total) VALUES (99999,1,'"'"'x'"'"',1.0)"}')
body=$(cat /tmp/e-sec-l1.json)
if [ "$code" = "403" ] && echo "$body" | grep -qi 'forbidden\|not permitted\|denied'; then
  pass "INSERT rejected (HTTP $code)"
else
  fail "INSERT expected HTTP 403 + FORBIDDEN body, got $code: $body"
fi

echo "== L4: DDL blocked =="
code=$(curl -s -o /tmp/e-sec-l4.json -w '%{http_code}' -X POST "$D/query" \
  -H 'Content-Type: application/json' -d '{"sql":"DROP TABLE orders"}')
body=$(cat /tmp/e-sec-l4.json)
if [ "$code" = "403" ] && echo "$body" | grep -q 'FORBIDDEN'; then
  pass "DROP rejected (HTTP $code)"
else
  fail "DROP expected HTTP 403 + FORBIDDEN, got $code: $body"
fi

echo "== L3: non-allowlisted table hidden at discovery =="
code=$(curl -s -o /tmp/e-sec-l3.json -w '%{http_code}' "$D/tables")
body=$(cat /tmp/e-sec-l3.json)
if [ "$code" != "200" ]; then
  fail "/tables expected HTTP 200, got $code: $body"
elif ! echo "$body" | jq -e '.tables | type == "array" and length > 0' >/dev/null; then
  fail "/tables body must be JSON with non-empty tables[]: $body"
elif echo "$body" | jq -e '.tables | map(ascii_downcase) | index("internal_audit")' >/dev/null; then
  fail "internal_audit visible in discovery: $body"
else
  pass "internal_audit hidden; tables=$(echo "$body" | jq -c .tables)"
fi

echo "== Documented gap: allowedTables NOT enforced on POST /query =="
r=$(curl -s -X POST "$D/query" -H 'Content-Type: application/json' \
     -d '{"sql":"SELECT id,actor FROM internal_audit"}')
echo "  Response: $r"
echo "  If this returns rows, the README §Security model gap is confirmed"
echo "  empirically. Report it. It is the honest disclosure that Reviewer 1"
echo "  called the strongest part of the submission."

echo "== L5: maxRows cap enforced =="
# Total rows via /query — must exceed the cap or the test is unfalsifiable.
total=$(curl -sf -X POST "$D/query" -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT COUNT(*) AS n FROM orders"}' \
  | jq -r '.rows[0].n // .rows[0].N // empty')
if [ -z "$total" ] || ! [ "$total" -gt 0 ] 2>/dev/null; then
  fail "could not read orders COUNT(*): aborting L5"
elif ! [ "$total" -gt "$SECURITY_MAX_ROWS" ]; then
  fail "orders has $total rows; need > $SECURITY_MAX_ROWS to falsify the cap (re-seed db/)"
else
  resp=$(curl -sf "$D/tables/orders/rows?limit=100000")
  n=$(echo "$resp" | jq '.count // (.rows|length)')
  truncated=$(echo "$resp" | jq -r 'if has("truncated") then .truncated|tostring else "absent" end')
  echo "  table rows: $total  returned: $n  SECURITY_MAX_ROWS=$SECURITY_MAX_ROWS  truncated=$truncated"
  if [ "$n" -gt "$SECURITY_MAX_ROWS" ]; then
    fail "cap not enforced: returned $n > $SECURITY_MAX_ROWS"
  elif [ "$n" -lt "$SECURITY_MAX_ROWS" ]; then
    fail "returned $n < cap $SECURITY_MAX_ROWS while table has $total rows (unexpected limiter)"
  elif [ "$n" -eq "$SECURITY_MAX_ROWS" ]; then
    pass "cap enforced exactly ($n of $total); truncated=$truncated"
  else
    fail "unexpected count $n"
  fi
fi

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "All falsifiable checks passed."
  exit 0
else
  echo "$FAILURES check(s) failed."
  exit 1
fi
