#!/usr/bin/env bash
# Re-run every criterion with the final harness, teeing raw output.
cd "$(dirname "$0")"
export PYTHONPATH="../../../src:."
PY=/opt/homebrew/bin/python3.13
set -u
for s in c1_fail c2_missing c3_authz c4_artifacts c5_pages c6_values extra_probes; do
  echo "########## $s"
  $PY re_$s.py 2>${s}-stderr.txt | tee ${s}-checks.txt
  echo "EXIT_${s}=${PIPESTATUS[0]}"
done
echo "=== summaries"
for f in c1-07-criterion1-report.json c2-03-criterion2-report.json c3-06-criterion3-report.json c4-13-criterion4-report.json c5-09-criterion5-report.json c6-10-criterion6-report.json extra-01-probes.json; do
  $PY -c "import json,sys;d=json.load(open('$f'));print('$f', d['checks'], 'checks', d['failed'], 'failed')"
done
