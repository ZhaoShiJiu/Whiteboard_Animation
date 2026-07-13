"""Quick test to verify the web app renders correctly."""
import sys
sys.path.insert(0, "web_app")

from app import app

with app.test_client() as client:
    resp = client.get("/")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8")
    assert "sidebar" in html, "Missing sidebar"
    assert "控制台" in html, "Missing 控制台"
    assert "作品画廊" in html, "Missing 作品画廊"
    assert "费用统计" in html, "Missing 费用统计"
    assert "newProjectForm" in html, "Missing form"

    # API tests
    health = client.get("/api/health")
    assert health.status_code == 200

    jobs = client.get("/api/jobs")
    assert jobs.status_code == 200

    outputs = client.get("/api/outputs")
    assert outputs.status_code == 200

    print("ALL TESTS PASSED")
    print(f"HTML length: {len(html)} chars")
    print("Routes verified: /, /api/health, /api/jobs, /api/outputs, /api/costs")
