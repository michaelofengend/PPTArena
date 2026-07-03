#!/usr/bin/env bash
D=/root/PPTArena/agent_bench/diag2.txt
: > "$D"
echo '{"security": {"auth": {"selectedType": "gemini-api-key"}}}' > /root/.gemini/settings.json
echo "settings restored:" >> "$D"; cat /root/.gemini/settings.json >> "$D"
python3 - << 'PY' >> "$D" 2>&1
import json, pathlib
p = pathlib.Path("/root/.gemini/trustedFolders.json")
d = {}
if p.exists():
    try: d = json.load(open(p))
    except Exception: d = {}
d["/root/PPTArena/agent_bench/workdirs"] = "TRUST_FOLDER"
d["/root/PPTArena"] = "TRUST_FOLDER"
json.dump(d, open(p, "w"), indent=1)
print("trustedFolders:", d)
PY
mkdir -p /tmp/gtrust_test && cd /tmp/gtrust_test
set -a; . /root/PPTArena/credentials.env; set +a
echo "--- probe WITHOUT env override (trust file only):" >> "$D"
timeout 90 gemini -m gemini-3.5-flash --yolo -p "Reply with exactly: OK" < /dev/null >> "$D" 2>&1
echo "--- probe WITH GEMINI_CLI_TRUST_WORKSPACE=true:" >> "$D"
timeout 90 env GEMINI_CLI_TRUST_WORKSPACE=true gemini -m gemini-3.5-flash --yolo -p "Reply with exactly: OK" < /dev/null >> "$D" 2>&1
echo "VMCTL_FIX_DONE" >> "$D"
