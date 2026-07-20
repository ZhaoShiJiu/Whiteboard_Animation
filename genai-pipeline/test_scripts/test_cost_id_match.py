"""
Test: verify AiUsage.request_id == AiRequestLog.id after a Gateway call.

Usage:
    docker compose exec whiteboard-animation-ai python genai-pipeline/test_scripts/test_cost_id_match.py
"""

import sys
from pathlib import Path

# Ensure genai-pipeline is importable
_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))

import sqlite3

# 1. Init DB
from ai_gateway.db.connection import init_db
import yaml

gw_yaml = _here / "ai_gateway" / "gateway.yaml"
with open(gw_yaml) as f:
    config = yaml.safe_load(f)
init_db(config["database"])

# 2. Import generate (lazy singleton)
from ai_gateway import generate

# 3. Record current counts
db_path = _here / "ai_gateway" / "ai_gateway.db"
conn = sqlite3.connect(str(db_path))

def count_matches(cursor):
    cursor.execute(
        "SELECT COUNT(*) FROM ai_usage u "
        "INNER JOIN ai_request_logs r ON u.request_id = r.id"
    )
    return cursor.fetchone()[0]

cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM ai_request_logs")
logs_before = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM ai_usage")
usage_before = cur.fetchone()[0]
matches_before = count_matches(cur)

print(f"Before: AiRequestLog={logs_before}  AiUsage={usage_before}  matches={matches_before}")

# 4. Make a Gateway call
print("\nCalling generate()...")
try:
    resp = generate(
        task="story",
        prompt="Say 'hello world' in one sentence.",
        options={"max_tokens": 50},
    )
    print(f"  provider={resp.provider}  model={resp.model}")
    print(f"  request_id={resp.request_id}")
    print(f"  cost={resp.usage.cost}")
    print(f"  tokens: in={resp.usage.input_tokens} out={resp.usage.output_tokens}")
except Exception as e:
    print(f"  ERROR: {e}")
    conn.close()
    sys.exit(1)

# 5. Check results
cur.execute("SELECT COUNT(*) FROM ai_request_logs")
logs_after = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM ai_usage")
usage_after = cur.fetchone()[0]
matches_after = count_matches(cur)

print(f"\nAfter:  AiRequestLog={logs_after}  AiUsage={usage_after}  matches={matches_after}")
print(f"Delta:  AiRequestLog +{logs_after - logs_before}  AiUsage +{usage_after - usage_before}  matches +{matches_after - matches_before}")

# 6. Check the exact IDs
cur.execute("SELECT id FROM ai_request_logs ORDER BY created_at DESC LIMIT 1")
last_log_id = cur.fetchone()[0]
cur.execute("SELECT request_id FROM ai_usage ORDER BY rowid DESC LIMIT 1")
last_usage_id = cur.fetchone()[0]

print(f"\nLatest AiRequestLog.id  = {last_log_id}")
print(f"Latest AiUsage.request_id = {last_usage_id}")
print(f"MATCH: {last_log_id == last_usage_id}")

# 7. Verify the response.request_id matches the log
print(f"\nResponse.request_id     = {resp.request_id}")
print(f"Matches AiRequestLog.id = {resp.request_id == last_log_id}")
print(f"Matches AiUsage.request_id = {resp.request_id == last_usage_id}")

conn.close()

if matches_after > matches_before:
    print("\n✅ PASS — new records have matching IDs")
else:
    print("\n❌ FAIL — new records still don't match")
    print("   Check Docker logs for DIAGNOSTIC messages from gateway.py and cost_mw.py")
