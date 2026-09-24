#!/bin/bash
# One device batch under the track's protocol: a GPU utilization check first (wait 5 minutes while above 10 percent,
# at most three waits), then each process alone under the device lock, with a 30 s timeout, CAIRN_GPU_TESTS=1 in its
# own environment only, and a 12 s rest between processes; afterwards the Windows event logs for driver resets.
# Usage: batch.sh NAME OUTDIR "PAIR BUILD_DIR [REPS]" ...
set -u
name=$1; out=$2; shift 2
mkdir -p "$out"
log="$out/$name.log"
wt=/home/sam/cairn-wt/device-perf
py=/home/sam/cairn/.venv/bin/python
echo "batch $name start $(date -Is)" | tee -a "$log"
started=$(date +%s)
waits=0
while true; do
  util=$(nvidia-smi --query-gpu=utilization.gpu,memory.used,temperature.gpu --format=csv,noheader)
  echo "smi $(date -Is) $util" | tee -a "$log"
  pct=${util%% %*}
  if [ "$pct" -le 10 ]; then break; fi
  waits=$((waits + 1))
  if [ $waits -gt 3 ]; then echo "SKIPPED: GPU busy after three waits" | tee -a "$log"; exit 3; fi
  echo "busy: waiting 300 s (wait $waits)" | tee -a "$log"
  sleep 300
done
count=0
for spec in "$@"; do
  set -- $spec
  pair=$1; dir=$2; reps=${3:-}; shift 2; extra="$*"; shift 1 2>/dev/null; after="$*"
  count=$((count + 1))
  if [ $count -gt 12 ]; then echo "STOP: more than 12 processes" | tee -a "$log"; break; fi
  if [ $count -gt 1 ]; then sleep 12; fi
  tag=$(basename "$dir")
  echo "run $count $pair $tag args=${extra:-default} $(date -Is)" | tee -a "$log"
  t0=$(date +%s.%N)
  if [ "$pair" = "exec" ]; then  # "exec TAG BINARY": one program run as it is, under the same lock and timeout
    tag=$dir; pair=exec
    CAIRN_GPU_TESTS=1 flock /tmp/cairn-gpu.lock timeout 30 "$reps" $after > "$out/${pair}_${tag}.json" 2> "$out/${pair}_${tag}.err"
  else
    (cd "$wt" && CAIRN_GPU_TESTS=1 "$py" bench/device/device.py run "$pair" $extra --out "$dir") > "$out/${pair}_${tag}.json" 2> "$out/${pair}_${tag}.err"
  fi
  status=$?
  t1=$(date +%s.%N)
  echo "  status $status seconds $(awk "BEGIN{print $t1 - $t0}")" | tee -a "$log"
  if [ $status -ne 0 ]; then echo "  stderr: $(tail -3 "$out/${pair}_${tag}.err")" | tee -a "$log"; fi
  found=$(powershell.exe -NoProfile -Command "Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddMinutes(-3)} -ErrorAction SilentlyContinue | Where-Object { \$_.ProviderName -match 'nvlddmkm|Display' -or \$_.Id -in 4101,153,14,13 } | Measure-Object | Select-Object -ExpandProperty Count" 2>/dev/null | tr -d '\r')
  echo "  driver events in the last 3 minutes: ${found:-unknown}" | tee -a "$log"
  if [ "${found:-0}" != "0" ]; then echo "STOP: a driver event after process $count" | tee -a "$log"; break; fi
done
minutes=$(( ( $(date +%s) - started ) / 60 + 2 ))
echo "resets check $(date -Is) over the last $minutes minutes" | tee -a "$log"
powershell.exe -NoProfile -Command "Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddMinutes(-$minutes)} -ErrorAction SilentlyContinue | Where-Object { \$_.ProviderName -match 'nvlddmkm|Display' -or \$_.Id -in 4101,153,14,13 } | Format-List TimeCreated,Id,ProviderName,Message" > "$out/$name.system_events.txt" 2>&1
powershell.exe -NoProfile -Command "Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddMinutes(-$minutes)} -ErrorAction SilentlyContinue | Where-Object { \$_.Message -match 'LiveKernelEvent' -or \$_.ProviderName -match 'LiveKernelEvent' } | Format-List TimeCreated,Id,ProviderName,Message" > "$out/$name.application_events.txt" 2>&1
echo "system events: $(grep -c TimeCreated "$out/$name.system_events.txt"), application LiveKernelEvent: $(grep -c TimeCreated "$out/$name.application_events.txt")" | tee -a "$log"
nvidia-smi --query-gpu=utilization.gpu,memory.used,temperature.gpu --format=csv,noheader | sed 's/^/smi after /' | tee -a "$log"
echo "batch $name end $(date -Is)" | tee -a "$log"
