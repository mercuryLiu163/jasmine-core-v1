#!/usr/bin/env bash
# FINAL validation of 0ae8041: run every criterion with the timed harness,
# tee raw stdout to *-checks.txt and raw stderr to *-stderr.txt.
cd "$(dirname "$0")"
export PYTHONPATH="../../../src:."
PY=/opt/homebrew/bin/python3.13
set -u

echo "########## analysis_real_records (read-only census)"
$PY final_analysis_real_records.py 2>analysis-real-records-stderr.txt \
  | tee analysis-real-records.txt
echo "EXIT_analysis=${PIPESTATUS[0]}"

for s in c1_fail c2_missing c3_authz c4_artifacts c5_pages c6_values extra_probes; do
  echo "########## $s"
  $PY final_$s.py 2>${s}-stderr.txt | tee ${s}-checks.txt
  echo "EXIT_${s}=${PIPESTATUS[0]}"
done

echo "=== summaries"
for f in c1-07-criterion1-report.json c2-03-criterion2-report.json \
         c3-06-criterion3-report.json c4-13-criterion4-report.json \
         c5-09-criterion5-report.json c6-10-criterion6-report.json \
         extra-01-probes.json; do
  $PY -c "import json;d=json.load(open('$f'));print('$f', d['checks'], 'checks', d['failed'], 'failed')"
done
