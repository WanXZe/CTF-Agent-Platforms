"""Print a compact solve summary from the VM localhost API."""
import json
import sys
from urllib.request import urlopen

challenge_id = sys.argv[1]
with urlopen(f"http://127.0.0.1:12346/api/solve-log/local/{challenge_id}", timeout=5) as response:
    payload = json.load(response)
data = payload.get("data") or {}
logs = data.get("logs") or []
print("status:", data.get("status"), "logs:", len(logs))
for item in logs[-5:]:
    print(item.get("timestamp"), item.get("type"), str(item.get("content", ""))[:300])
