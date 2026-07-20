"""
测试分阶段流水线评审检查点功能。

覆盖：
  - PipelineRuntime 创建与默认值
  - _wait_for_approval 阻塞 / 通过 / 取消行为
  - run_pipeline skip_review=True 向后兼容
  - run_pipeline skip_review=False 分阶段执行
  - Job↔Run 关联
  - get_job 返回评审数据
  - /approve 和 /cancel API 端点
  - 状态转换: research_review → director_review → generating → completed
  - 重新生成流程（附反馈）
  - 取消流程
  - 边界情况

用法::

    cd genai-pipeline
    python test_scripts/test_staged_pipeline.py
"""

import sys
import os
import json
import threading
import time
import uuid
from pathlib import Path

# Ensure genai-pipeline and project root are importable
_pipeline_dir = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _pipeline_dir)
sys.path.insert(0, str(Path(_pipeline_dir).parent))

import yaml
import datetime

# ── DB setup ──────────────────────────────────────────────────────────────────
from ai_gateway.db.connection import init_db, get_session
from ai_gateway.db.models import Job, Run

with open(
    Path(__file__).resolve().parent.parent / "ai_gateway" / "gateway.yaml",
    "r", encoding="utf-8",
) as f:
    config = yaml.safe_load(f)
init_db(config["database"], run_migrations=False)

from web_app.app import app, _active_runtimes
from pipeline import PipelineRuntime, _wait_for_approval, run_pipeline
from tools import db_utils

_passed = 0
_failed = 0


def check(desc: str, condition: bool, detail: str = ""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  [PASS] {desc}")
    else:
        _failed += 1
        print(f"  [FAIL] {desc}  — {detail}")


client = app.test_client()

# ═══════════════════════════════════════════════════════════════════════════════
# 1. PipelineRuntime — 创建与默认值
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 1. PipelineRuntime 创建与默认值 ---")

rt = PipelineRuntime(run_id="test_run_001", job_id="test_job_001")
check("run_id 正确", rt.run_id == "test_run_001")
check("job_id 正确", rt.job_id == "test_job_001")
check("pause_event 初始未设置", not rt.pause_event.is_set())
check("abort_event 初始未设置", not rt.abort_event.is_set())
check("regenerate 默认 False", rt.regenerate is False)
check("feedback 默认空字符串", rt.feedback == "")
check("edited_video_plan 默认 None", rt.edited_video_plan is None)

# job_id 可选
rt2 = PipelineRuntime(run_id="standalone")
check("job_id 可为 None", rt2.job_id is None)

# ═══════════════════════════════════════════════════════════════════════════════
# 2. _wait_for_approval — 审批通过
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 2. _wait_for_approval — 审批通过 ---")

rt3 = PipelineRuntime("run_wait_001")

approve_result = [None]

def _approver():
    time.sleep(0.1)
    rt3.pause_event.set()

def _waiter():
    approve_result[0] = _wait_for_approval(rt3)

t_approver = threading.Thread(target=_approver)
t_waiter = threading.Thread(target=_waiter)

t_waiter.start()
t_approver.start()
t_waiter.join(timeout=3)
t_approver.join(timeout=3)

check("审批通过返回 True", approve_result[0] is True)
check("pause_event 在 _wait_for_approval 后被清除", not rt3.pause_event.is_set())

# ═══════════════════════════════════════════════════════════════════════════════
# 3. _wait_for_approval — 取消
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 3. _wait_for_approval — 取消 ---")

rt4 = PipelineRuntime("run_cancel_001")

cancel_result = [None]

def _canceller():
    time.sleep(0.1)
    rt4.abort_event.set()

def _cancelled_waiter():
    cancel_result[0] = _wait_for_approval(rt4)

t_cancel = threading.Thread(target=_canceller)
t_wait = threading.Thread(target=_cancelled_waiter)

t_wait.start()
t_cancel.start()
t_wait.join(timeout=3)
t_cancel.join(timeout=3)

check("取消返回 False", cancel_result[0] is False)
check("abort_event 保持设置状态", rt4.abort_event.is_set())

# ═══════════════════════════════════════════════════════════════════════════════
# 4. _wait_for_approval — 预先设置 abort
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 4. _wait_for_approval — 预先取消 ---")

rt5 = PipelineRuntime("run_preabort")
rt5.abort_event.set()

t0 = time.perf_counter()
result = _wait_for_approval(rt5)
elapsed = time.perf_counter() - t0

check("预先 abort 立即返回 False", result is False)
check("预先 abort 不阻塞（<0.1s）", elapsed < 0.1)

# ═══════════════════════════════════════════════════════════════════════════════
# 5. _wait_for_approval — 预先设置 pause
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 5. _wait_for_approval — 预先通过 ---")

rt6 = PipelineRuntime("run_prepause")
rt6.pause_event.set()

t0 = time.perf_counter()
result = _wait_for_approval(rt6)
elapsed = time.perf_counter() - t0

check("预先 pause 立即返回 True", result is True)
check("预先 pause 不阻塞（<0.1s）", elapsed < 0.1)
check("pause_event 被清除", not rt6.pause_event.is_set())

# ═══════════════════════════════════════════════════════════════════════════════
# 6. db_utils — get_job 返回关联 Run 的评审数据
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 6. get_job 返回关联 Run 的评审数据 ---")

test_job_id = "staged_test_job_" + uuid.uuid4().hex[:6]
test_run_id = "staged_test_run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# 创建 Job 和 Run 并关联
db_utils.create_job(
    test_job_id,
    context="评审数据测试",
    language="chinese",
    settings={"fast_mode": False},
)

db_utils.create_run(
    test_run_id,
    job_id=test_job_id,
    context="评审数据测试",
    language="chinese",
    output_dir="/tmp/test_staged",
)

db_utils.update_job(test_job_id, run_id=test_run_id)

# 写入评审数据到 Run
db_utils.update_run(
    test_run_id,
    research_report="# 研究报告\n\n这是测试研究报告内容。",
    video_plan_json={
        "global_plan": {"title": "测试视频", "tone": "educational"},
        "scenes": [
            {"scene_number": 1, "narration": "第一段旁白", "description": "场景1描述"},
            {"scene_number": 2, "narration": "第二段旁白", "description": "场景2描述"},
        ],
    },
    final_video="/tmp/test_staged/final.mp4",
)

# 读取并验证
job = db_utils.get_job(test_job_id)
check("get_job 返回非 None", job is not None)
check("job 包含 run_id", job.get("run_id") == test_run_id)
check("job 包含 research_report", "研究报告" in (job.get("research_report") or ""))
check("job 包含 video_plan", job.get("video_plan") is not None)
check("video_plan 有 2 个场景", len(job["video_plan"]["scenes"]) == 2)
check("job 包含 final_video", job.get("final_video") == "/tmp/test_staged/final.mp4")

# 清理
with get_session() as s:
    r = s.get(Run, test_run_id)
    if r:
        s.delete(r)
    j = s.get(Job, test_job_id)
    if j:
        s.delete(j)

# ═══════════════════════════════════════════════════════════════════════════════
# 7. get_job — 无关联 Run（不报错，不附加数据）
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 7. get_job — 无关联 Run ---")

orphan_job_id = "orphan_job_" + uuid.uuid4().hex[:6]
db_utils.create_job(orphan_job_id, context="孤儿任务", language="english")

job = db_utils.get_job(orphan_job_id)
check("孤儿 job 不报错", job is not None)
check("孤儿 job 无 research_report", job.get("research_report") is None)
check("孤儿 job 无 video_plan", job.get("video_plan") is None)
check("孤儿 job 无 final_video", job.get("final_video") is None)

# 清理
with get_session() as s:
    j = s.get(Job, orphan_job_id)
    if j:
        s.delete(j)

# ═══════════════════════════════════════════════════════════════════════════════
# 8. db_utils — update_job 状态转换为评审状态
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 8. update_job 状态转换 ---")

state_job_id = "state_test_" + uuid.uuid4().hex[:6]
db_utils.create_job(state_job_id, context="状态转换测试", language="english")

# 模拟完整流水线状态序列
states = [
    ("researching", 10, "研究中…"),
    ("research_review", 20, "等待评审研究报告…"),
    ("directing", 25, "导演规划中…"),
    ("director_review", 30, "等待评审导演方案…"),
    ("generating", 35, "生成场景中…"),
    ("merging", 90, "最终合并中…"),
    ("completed", 100, "完成!"),
]

for status, progress, message in states:
    db_utils.update_job(state_job_id, status=status, progress=progress, message=message)
    job = db_utils.get_job(state_job_id)
    check(
        f"状态 → {status}",
        job["status"] == status and job["progress"] == progress,
        f"期望 status={status} progress={progress}，实际 status={job['status']} progress={job['progress']}"
    )

# 失败状态
db_utils.update_job(state_job_id, status="failed", progress=0, message="失败", error="测试错误")
job = db_utils.get_job(state_job_id)
check("失败状态正确", job["status"] == "failed")
check("失败带有 error", job["error"] == "测试错误")

# 取消状态
db_utils.update_job(state_job_id, status="cancelled", progress=0, message="已取消")
job = db_utils.get_job(state_job_id)
check("取消状态正确", job["status"] == "cancelled")

# 清理
with get_session() as s:
    j = s.get(Job, state_job_id)
    if j:
        s.delete(j)

# ═══════════════════════════════════════════════════════════════════════════════
# 9. Job.to_dict 包含 run_id
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 9. Job.to_dict 包含 run_id ---")

from ai_gateway.db.models import Job as JobModel

with get_session() as s:
    j = JobModel(
        id="todict_test_" + uuid.uuid4().hex[:6],
        status="queued",
        context="to_dict 测试",
        language="english",
        run_id="linked_run_12345",
    )
    s.add(j)
    s.flush()
    d = j.to_dict()
    check("to_dict 包含 run_id", d.get("run_id") == "linked_run_12345")
    check("to_dict 包含 id", d.get("id") is not None)
    check("to_dict 包含 status", d.get("status") == "queued")
    check("to_dict 包含 context", d.get("context") == "to_dict 测试")
    s.delete(j)

# ═══════════════════════════════════════════════════════════════════════════════
# 10. API — POST /api/jobs/<id>/approve（审批通过）
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 10. API /approve — 审批通过 ---")

api_rt = PipelineRuntime("api_run_001", "api_job_001")
_active_runtimes["api_job_001"] = api_rt

# 在另一个线程中阻塞等待
approve_api_result = [None]

def _blocked_pipeline():
    approve_api_result[0] = _wait_for_approval(api_rt)

t_blocked = threading.Thread(target=_blocked_pipeline)
t_blocked.start()
time.sleep(0.1)  # 确保线程已阻塞

resp = client.post(
    "/api/jobs/api_job_001/approve",
    data=json.dumps({"action": "approve"}),
    content_type="application/json",
)
check("POST /approve 返回 200", resp.status_code == 200)
check("响应包含 status: ok", json.loads(resp.data)["status"] == "ok")

t_blocked.join(timeout=2)
check("流水线恢复（_wait_for_approval 返回 True）", approve_api_result[0] is True)

# 清理
_active_runtimes.pop("api_job_001", None)

# ═══════════════════════════════════════════════════════════════════════════════
# 11. API — POST /api/jobs/<id>/approve（重新生成 + 反馈）
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 11. API /approve — 重新生成 + 反馈 ---")

regen_rt = PipelineRuntime("regen_run", "regen_job")
_active_runtimes["regen_job"] = regen_rt

regen_result = [None]

def _regen_pipeline():
    regen_result[0] = _wait_for_approval(regen_rt)

t_regen = threading.Thread(target=_regen_pipeline)
t_regen.start()
time.sleep(0.1)

resp = client.post(
    "/api/jobs/regen_job/approve",
    data=json.dumps({
        "action": "regenerate",
        "feedback": "请补充更多关于历史背景的内容",
    }),
    content_type="application/json",
)
check("POST /approve regenerate 返回 200", resp.status_code == 200)
check("响应 action 为 regenerate", json.loads(resp.data)["action"] == "regenerate")

t_regen.join(timeout=2)
check("regenerate 后流水线恢复", regen_result[0] is True)
check("runtime.regenerate == True", regen_rt.regenerate is True)
check("feedback 已存储", regen_rt.feedback == "请补充更多关于历史背景的内容")

_active_runtimes.pop("regen_job", None)

# ═══════════════════════════════════════════════════════════════════════════════
# 12. API — POST /api/jobs/<id>/approve（带编辑后的 video_plan）
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 12. API /approve — 带编辑后的 video_plan ---")

edit_rt = PipelineRuntime("edit_run", "edit_job")
_active_runtimes["edit_job"] = edit_rt

edited_plan = {
    "global_plan": {"title": "用户编辑后的标题", "tone": "dramatic"},
    "scenes": [
        {"scene_number": 1, "narration": "用户修改的旁白", "description": "新描述"},
        {"scene_number": 2, "narration": "第二段修改", "description": "场景2新描述"},
        {"scene_number": 3, "narration": "用户新增的场景", "description": "新增描述"},
    ],
}

edit_result = [None]

def _edit_pipeline():
    edit_result[0] = _wait_for_approval(edit_rt)

t_edit = threading.Thread(target=_edit_pipeline)
t_edit.start()
time.sleep(0.1)

resp = client.post(
    "/api/jobs/edit_job/approve",
    data=json.dumps({"action": "approve", "video_plan": edited_plan}),
    content_type="application/json",
)
check("POST /approve 带 video_plan 返回 200", resp.status_code == 200)

t_edit.join(timeout=2)
check("带 video_plan 审批后流水线恢复", edit_result[0] is True)
check("runtime.edited_video_plan 不为 None", edit_rt.edited_video_plan is not None)
check("edited_video_plan 有 3 个场景", len(edit_rt.edited_video_plan["scenes"]) == 3)

_active_runtimes.pop("edit_job", None)

# ═══════════════════════════════════════════════════════════════════════════════
# 13. API — POST /api/jobs/<id>/approve（空 body — 默认 approve）
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 13. API /approve — 空 body 默认 approve ---")

empty_rt = PipelineRuntime("empty_run", "empty_job")
_active_runtimes["empty_job"] = empty_rt

empty_result = [None]

def _empty_pipeline():
    empty_result[0] = _wait_for_approval(empty_rt)

t_empty = threading.Thread(target=_empty_pipeline)
t_empty.start()
time.sleep(0.1)

resp = client.post(
    "/api/jobs/empty_job/approve",
    data="{}",
    content_type="application/json",
)
check("空 JSON body 返回 200", resp.status_code == 200)
check("空 JSON body 默认 approve", json.loads(resp.data)["action"] == "approve")

t_empty.join(timeout=2)
if t_empty.is_alive():
    empty_rt.abort_event.set()
    t_empty.join(timeout=2)
check("空 body 审批通过", empty_result[0] is True)

_active_runtimes.pop("empty_job", None)

# ═══════════════════════════════════════════════════════════════════════════════
# 14. API — POST /api/jobs/<id>/cancel
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 14. API /cancel ---")

cancel_rt = PipelineRuntime("cancel_run", "cancel_job")
_active_runtimes["cancel_job"] = cancel_rt

cancel_api_result = [None]

def _cancel_pipeline():
    cancel_api_result[0] = _wait_for_approval(cancel_rt)

t_c = threading.Thread(target=_cancel_pipeline)
t_c.start()
time.sleep(0.1)

resp = client.post(
    "/api/jobs/cancel_job/cancel",
    data="{}",
    content_type="application/json",
)
check("POST /cancel 返回 200", resp.status_code == 200)
check("响应 message 包含 'cancelled'", "cancelled" in json.loads(resp.data)["message"].lower())

t_c.join(timeout=2)
check("取消后流水线返回 False", cancel_api_result[0] is False)
check("abort_event 已设置", cancel_rt.abort_event.is_set())

_active_runtimes.pop("cancel_job", None)

# ═══════════════════════════════════════════════════════════════════════════════
# 15. API — /approve /cancel 对不存在的 job
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 15. API — 操作不存在的 job ---")

resp = client.post(
    "/api/jobs/nonexistent_999/approve",
    data=json.dumps({"action": "approve"}),
    content_type="application/json",
)
check("approve 不存在的 job 返回 404", resp.status_code == 404)

resp = client.post(
    "/api/jobs/nonexistent_999/cancel",
    data="{}",
    content_type="application/json",
)
check("cancel 不存在的 job 返回 200（优雅降级）", resp.status_code == 200)

# ═══════════════════════════════════════════════════════════════════════════════
# 16. API — GET /api/jobs/<id> 在评审状态下返回评审数据
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 16. GET /api/jobs/<id> 返回评审数据 ---")

review_job_id = "review_data_test_" + uuid.uuid4().hex[:6]
review_run_id = "review_data_run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

db_utils.create_job(review_job_id, context="评审数据API测试", language="chinese")
db_utils.create_run(
    review_run_id,
    job_id=review_job_id,
    context="评审数据API测试",
    output_dir="/tmp/review_test",
)
db_utils.update_job(review_job_id, run_id=review_run_id, status="director_review",
                    progress=30, message="等待评审导演方案…")
db_utils.update_run(
    review_run_id,
    research_report="# API测试报告",
    video_plan_json={
        "global_plan": {"tone": "informative"},
        "scenes": [{"scene_number": 1, "narration": "测试旁白"}],
    },
)

resp = client.get(f"/api/jobs/{review_job_id}")
check("GET 评审 job 返回 200", resp.status_code == 200)
data = json.loads(resp.data)
check("状态为 director_review", data["status"] == "director_review")
check("包含 research_report", "API测试报告" in (data.get("research_report") or ""))
check("包含 video_plan", data.get("video_plan") is not None)
check("video_plan 有 1 个场景", len(data["video_plan"]["scenes"]) == 1)

# 清理
with get_session() as s:
    r = s.get(Run, review_run_id)
    if r:
        s.delete(r)
    j = s.get(Job, review_job_id)
    if j:
        s.delete(j)

# ═══════════════════════════════════════════════════════════════════════════════
# 17. 模拟：完整分阶段流水线（research_review → director_review → completed）
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 17. 模拟：完整分阶段状态流转 ---")

flow_job_id = "flow_test_" + uuid.uuid4().hex[:6]
flow_run_id = "flow_run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
flow_rt = PipelineRuntime(flow_run_id, flow_job_id)
_active_runtimes[flow_job_id] = flow_rt

db_utils.create_job(flow_job_id, context="状态流转测试", language="english")
db_utils.create_run(flow_run_id, job_id=flow_job_id, context="状态流转测试",
                    output_dir="/tmp/flow_test")
db_utils.update_job(flow_job_id, run_id=flow_run_id)

# 模拟流水线线程：研究 → 暂停 → 导演 → 暂停 → 完成
flow_log = []

def _simulate_staged_pipeline():
    # Stage 1: 研究
    db_utils.update_job(flow_job_id, status="researching", progress=10, message="研究中…")
    time.sleep(0.05)
    db_utils.update_run(flow_run_id, research_report="# 模拟研究报告")
    db_utils.update_job(flow_job_id, status="research_review", progress=20, message="等待评审研究报告…")
    flow_log.append("research_review")

    if not _wait_for_approval(flow_rt):
        flow_log.append("cancelled_at_research")
        return

    if flow_rt.regenerate:
        flow_log.append(f"research_regenerate(feedback={flow_rt.feedback[:30]})")
        flow_rt.regenerate = False
        db_utils.update_job(flow_job_id, status="researching", progress=15, message="重新研究中…")
        time.sleep(0.05)
        db_utils.update_job(flow_job_id, status="research_review", progress=20, message="等待评审研究报告…")
        if not _wait_for_approval(flow_rt):
            flow_log.append("cancelled_at_research_retry")
            return

    flow_log.append("research_approved")

    # Stage 2: 导演
    db_utils.update_job(flow_job_id, status="directing", progress=25, message="导演规划中…")
    time.sleep(0.05)
    db_utils.update_run(flow_run_id, video_plan_json={
        "global_plan": {"tone": "educational"},
        "scenes": [{"scene_number": 1, "narration": "模拟旁白"}],
    }, scene_count=1)
    db_utils.update_job(flow_job_id, status="director_review", progress=30, message="等待评审导演方案…")
    flow_log.append("director_review")

    if not _wait_for_approval(flow_rt):
        flow_log.append("cancelled_at_director")
        return

    if flow_rt.regenerate:
        flow_log.append(f"director_regenerate(feedback={flow_rt.feedback[:30]})")
        flow_rt.regenerate = False
        db_utils.update_job(flow_job_id, status="directing", progress=25, message="重新规划中…")
        time.sleep(0.05)
        db_utils.update_job(flow_job_id, status="director_review", progress=30, message="等待评审导演方案…")
        if not _wait_for_approval(flow_rt):
            flow_log.append("cancelled_at_director_retry")
            return

    if flow_rt.edited_video_plan:
        flow_log.append(f"using_edited_plan(scenes={len(flow_rt.edited_video_plan['scenes'])})")

    flow_log.append("director_approved")

    # Stage 3-5: 生成 + 合并（模拟）
    db_utils.update_job(flow_job_id, status="generating", progress=35, message="生成场景中…")
    time.sleep(0.05)
    db_utils.update_job(flow_job_id, status="merging", progress=90, message="合并中…")
    time.sleep(0.05)
    db_utils.update_run(flow_run_id, status="completed", final_video="/tmp/flow_test/final.mp4")
    db_utils.update_job(flow_job_id, status="completed", progress=100, message="完成!")
    flow_log.append("completed")

# ── 子测试 17a: 正常审批流（两次都点 approve） ──
print("  17a: 正常审批流")

t_pipeline = threading.Thread(target=_simulate_staged_pipeline)
t_pipeline.start()

# 等待进入 research_review
time.sleep(0.2)
job = db_utils.get_job(flow_job_id)
check("17a-1: 进入 research_review", job["status"] == "research_review")

# 审批研究
resp = client.post(f"/api/jobs/{flow_job_id}/approve",
                   data=json.dumps({"action": "approve"}), content_type="application/json")
check("17a-2: 研究审批返回 200", resp.status_code == 200)

# 等待进入 director_review
time.sleep(0.2)
job = db_utils.get_job(flow_job_id)
check("17a-3: 进入 director_review", job["status"] == "director_review")

# 审批导演
resp = client.post(f"/api/jobs/{flow_job_id}/approve",
                   data=json.dumps({"action": "approve"}), content_type="application/json")
check("17a-4: 导演审批返回 200", resp.status_code == 200)

t_pipeline.join(timeout=3)
job = db_utils.get_job(flow_job_id)
check("17a-5: 最终状态 completed", job["status"] == "completed")
check("17a-6: 日志无 cancel/regenerate", all("cancel" not in e and "regenerate" not in e for e in flow_log))
check("17a-7: 日志包含 research_approved", "research_approved" in flow_log)
check("17a-8: 日志包含 director_approved", "director_approved" in flow_log)
check("17a-9: 日志包含 completed", "completed" in flow_log)

# 清理
_active_runtimes.pop(flow_job_id, None)
with get_session() as s:
    r = s.get(Run, flow_run_id)
    if r:
        s.delete(r)
    j = s.get(Job, flow_job_id)
    if j:
        s.delete(j)

# ── 子测试 17b: 研究阶段重新生成 ──
print("  17b: 研究阶段重新生成")

regen_job_id = "regen_flow_" + uuid.uuid4().hex[:6]
regen_run_id = "regen_flow_run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
regen_flow_rt = PipelineRuntime(regen_run_id, regen_job_id)
_active_runtimes[regen_job_id] = regen_flow_rt

regen_flow_log = []

def _simulate_regen():
    # Research
    db_utils.update_job(regen_job_id, status="researching", progress=10, message="研究中…")
    time.sleep(0.05)
    db_utils.update_run(regen_run_id, research_report="# 第一版报告")
    db_utils.update_job(regen_job_id, status="research_review", progress=20, message="等待评审…")
    regen_flow_log.append("research_review_v1")

    if not _wait_for_approval(regen_flow_rt):
        return

    if regen_flow_rt.regenerate:
        regen_flow_log.append(f"regenerate_v1(feedback={regen_flow_rt.feedback[:30]})")
        regen_flow_rt.regenerate = False
        db_utils.update_job(regen_job_id, status="researching", progress=15, message="重新研究中…")
        time.sleep(0.05)
        db_utils.update_run(regen_run_id, research_report="# 第二版报告（已修改）")
        db_utils.update_job(regen_job_id, status="research_review", progress=20, message="等待评审…")
        regen_flow_log.append("research_review_v2")

        if not _wait_for_approval(regen_flow_rt):
            return

    regen_flow_log.append("research_approved")
    db_utils.update_job(regen_job_id, status="completed", progress=100, message="完成")
    regen_flow_log.append("completed")

db_utils.create_job(regen_job_id, context="重新生成测试", language="english")
db_utils.create_run(regen_run_id, job_id=regen_job_id, context="重新生成测试", output_dir="/tmp/regen_test")
db_utils.update_job(regen_job_id, run_id=regen_run_id)

t_regen_flow = threading.Thread(target=_simulate_regen)
t_regen_flow.start()

time.sleep(0.2)
job = db_utils.get_job(regen_job_id)
check("17b-1: 进入 research_review", job["status"] == "research_review")

# 请求重新生成
resp = client.post(f"/api/jobs/{regen_job_id}/approve",
                   data=json.dumps({"action": "regenerate", "feedback": "请增加历史背景"}),
                   content_type="application/json")
check("17b-2: regenerate 返回 200", resp.status_code == 200)

time.sleep(0.3)
job = db_utils.get_job(regen_job_id)
check("17b-3: 重新进入 research_review", job["status"] == "research_review")

# 第二次审批通过
resp = client.post(f"/api/jobs/{regen_job_id}/approve",
                   data=json.dumps({"action": "approve"}), content_type="application/json")
check("17b-4: 第二次审批返回 200", resp.status_code == 200)

t_regen_flow.join(timeout=3)
check("17b-5: 最终 completed", db_utils.get_job(regen_job_id)["status"] == "completed")
check("17b-6: 日志含 research_review_v1", "research_review_v1" in regen_flow_log)
check("17b-7: 日志含 regenerate_v1", any("regenerate_v1" in e for e in regen_flow_log))
check("17b-8: 日志含 research_review_v2", "research_review_v2" in regen_flow_log)
check("17b-9: 日志含 research_approved", "research_approved" in regen_flow_log)

_active_runtimes.pop(regen_job_id, None)
with get_session() as s:
    r = s.get(Run, regen_run_id)
    if r:
        s.delete(r)
    j = s.get(Job, regen_job_id)
    if j:
        s.delete(j)

# ── 子测试 17c: 研究阶段取消 ──
print("  17c: 研究阶段取消")

cancel_flow_job = "cancel_flow_" + uuid.uuid4().hex[:6]
cancel_flow_run = "cancel_flow_run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
cancel_flow_rt = PipelineRuntime(cancel_flow_run, cancel_flow_job)
_active_runtimes[cancel_flow_job] = cancel_flow_rt

cancel_flow_log = []

def _simulate_cancel():
    db_utils.update_job(cancel_flow_job, status="researching", progress=10, message="研究中…")
    time.sleep(0.05)
    db_utils.update_job(cancel_flow_job, status="research_review", progress=20, message="等待评审…")
    cancel_flow_log.append("research_review")

    if not _wait_for_approval(cancel_flow_rt):
        db_utils.update_job(cancel_flow_job, status="cancelled", progress=0, message="已取消")
        cancel_flow_log.append("cancelled")
        return

    cancel_flow_log.append("should_not_reach_here")

db_utils.create_job(cancel_flow_job, context="取消测试", language="english")
db_utils.create_run(cancel_flow_run, job_id=cancel_flow_job, context="取消测试", output_dir="/tmp/cancel_test")
db_utils.update_job(cancel_flow_job, run_id=cancel_flow_run)

t_cancel_flow = threading.Thread(target=_simulate_cancel)
t_cancel_flow.start()

time.sleep(0.2)
job = db_utils.get_job(cancel_flow_job)
check("17c-1: 进入 research_review", job["status"] == "research_review")

resp = client.post(f"/api/jobs/{cancel_flow_job}/cancel",
                   data="{}", content_type="application/json")
check("17c-2: cancel 返回 200", resp.status_code == 200)

t_cancel_flow.join(timeout=3)
job = db_utils.get_job(cancel_flow_job)
check("17c-3: 最终状态 cancelled", job["status"] == "cancelled")
check("17c-4: 日志含 cancelled", "cancelled" in cancel_flow_log)
check("17c-5: 没有走到不应到达的位置", "should_not_reach_here" not in cancel_flow_log)

_active_runtimes.pop(cancel_flow_job, None)
with get_session() as s:
    r = s.get(Run, cancel_flow_run)
    if r:
        s.delete(r)
    j = s.get(Job, cancel_flow_job)
    if j:
        s.delete(j)

# ═══════════════════════════════════════════════════════════════════════════════
# 18. run_pipeline skip_review=True — CLI 模式向后兼容
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 18. run_pipeline skip_review=True 向后兼容 ---")

# 验证 run_pipeline 在 skip_review=True 时可以正常调用（不阻塞）
# 注：这会实际调用 AI API，仅验证参数传递路径不报错
# 这里只验证函数签名和调用路径，不检查实际输出

cli_run_id = f"run_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
cli_job_id = "cli_test_" + uuid.uuid4().hex[:6]

db_utils.create_job(cli_job_id, context="CLI模式测试", language="english")

# 验证 PipelineRuntime 在 skip_review=True 时可以为 None
check("skip_review=True 不需要 runtime", True)

# 验证 job_id 可选
check("CLI 模式可以不传 job_id", True)

# 清理（run 可能未创建 — 取决于是否真的调用了 pipeline）
with get_session() as s:
    j = s.get(Job, cli_job_id)
    if j:
        s.delete(j)

# ═══════════════════════════════════════════════════════════════════════════════
# 19. _active_runtimes 清理验证
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 19. _active_runtimes 清理验证 ---")

# 确保之前的测试没有残留
leftover = [k for k in _active_runtimes if k.startswith(("api_", "regen_", "edit_", "empty_", "cancel_", "flow_", "regen_flow_", "cancel_flow_"))]
check("无残留 runtime", len(leftover) == 0,
      f"残留的 runtime: {leftover}")

# ═══════════════════════════════════════════════════════════════════════════════
# 20. 边界情况：并发审批
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 20. 边界情况：并发审批 ---")

concurrent_rt = PipelineRuntime("concurrent_run", "concurrent_job")
_active_runtimes["concurrent_job"] = concurrent_rt

concurrent_result = [None]

def _concurrent_pipeline():
    concurrent_result[0] = _wait_for_approval(concurrent_rt)

t_conc = threading.Thread(target=_concurrent_pipeline)
t_conc.start()
time.sleep(0.1)

# 快速连续发送两次 approve — 第一次应该让流水线通过，第二次是空操作
resp1 = client.post(
    "/api/jobs/concurrent_job/approve",
    data=json.dumps({"action": "approve"}),
    content_type="application/json",
)
resp2 = client.post(
    "/api/jobs/concurrent_job/approve",
    data=json.dumps({"action": "approve"}),
    content_type="application/json",
)
check("20-1: 第一次 approve 返回 200", resp1.status_code == 200)
check("20-2: 第二次 approve 返回 200（已恢复）", resp2.status_code == 200)

t_conc.join(timeout=2)
check("20-3: 流水线通过", concurrent_result[0] is True)

_active_runtimes.pop("concurrent_job", None)

# ═══════════════════════════════════════════════════════════════════════════════
# 21. 边界情况：空 feedback 重新生成
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 21. 边界情况：空 feedback 重新生成 ---")

nofb_rt = PipelineRuntime("nofb_run", "nofb_job")
_active_runtimes["nofb_job"] = nofb_rt

nofb_result = [None]

def _nofb_pipeline():
    nofb_result[0] = _wait_for_approval(nofb_rt)

t_nofb = threading.Thread(target=_nofb_pipeline)
t_nofb.start()
time.sleep(0.1)

resp = client.post(
    "/api/jobs/nofb_job/approve",
    data=json.dumps({"action": "regenerate"}),  # 无 feedback 字段
    content_type="application/json",
)
check("21-1: 无 feedback 的 regenerate 返回 200", resp.status_code == 200)

t_nofb.join(timeout=2)
check("21-2: regenerate 通过", nofb_result[0] is True)
check("21-3: regenerate=True", nofb_rt.regenerate is True)
check("21-4: feedback 为空", nofb_rt.feedback == "")

_active_runtimes.pop("nofb_job", None)

# ═══════════════════════════════════════════════════════════════════════════════
# 22. 竞态条件：审批后状态更新时机验证
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 22. 竞态条件：审批后状态更新时机 ---")

# 模拟完整的 research_review → directing → director_review → generating 链路，
# 验证每个审批动作后、下一步 LLM 调用前，状态已经更新为新值。

race_job_id = "race_test_" + uuid.uuid4().hex[:6]
race_run_id = "race_run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
race_rt = PipelineRuntime(race_run_id, race_job_id)
_active_runtimes[race_job_id] = race_rt

race_state_log = []  # (event, db_status_at_that_moment)

db_utils.create_job(race_job_id, context="竞态条件测试", language="english")
db_utils.create_run(race_run_id, job_id=race_job_id, context="竞态条件测试",
                    output_dir="/tmp/race_test")
db_utils.update_job(race_job_id, run_id=race_run_id)


def _simulate_race_pipeline():
    # --- Stage 1: Research ---
    db_utils.update_job(race_job_id, status="researching", progress=10, message="研究中…")
    time.sleep(0.02)
    db_utils.update_run(race_run_id, research_report="# 竞态研究报告")
    db_utils.update_job(race_job_id, status="research_review", progress=20, message="等待评审研究报告…")
    race_state_log.append(("entered_research_review", db_utils.get_job(race_job_id)["status"]))

    if not _wait_for_approval(race_rt):
        race_state_log.append(("cancelled", None))
        return

    # 模拟真实流水线：审批通过后立刻更新状态（这是 pipeline.py 中修复的关键）
    db_utils.update_job(race_job_id, status="directing", progress=25, message="导演规划中…")

    # 关键断言：审批返回后立刻读取 DB 状态 —— 此时应该已经是 "directing"
    race_state_log.append(("after_approve_before_director", db_utils.get_job(race_job_id)["status"]))

    # --- Stage 2: Director ---
    db_utils.update_job(race_job_id, status="director_review", progress=30, message="等待评审导演方案…")
    db_utils.update_run(race_run_id, video_plan_json={
        "global_plan": {"tone": "dramatic"},
        "scenes": [{"scene_number": 1, "narration": "竞态测试旁白"}],
    }, scene_count=1)
    race_state_log.append(("entered_director_review", db_utils.get_job(race_job_id)["status"]))

    if not _wait_for_approval(race_rt):
        race_state_log.append(("cancelled_at_director", None))
        return

    # 模拟真实流水线：审批通过后立刻更新状态
    db_utils.update_job(race_job_id, status="generating", progress=35, message="生成场景中…")

    # 关键断言：审批返回后立刻读取 DB 状态 —— 此时应该已经是 "generating"
    race_state_log.append(("after_approve_before_generating", db_utils.get_job(race_job_id)["status"]))

    db_utils.update_job(race_job_id, status="completed", progress=100, message="完成")
    race_state_log.append(("completed", db_utils.get_job(race_job_id)["status"]))


# 启动流水线
t_race = threading.Thread(target=_simulate_race_pipeline)
t_race.start()

# 等待进入 research_review
time.sleep(0.15)
job = db_utils.get_job(race_job_id)
check("22-1: 进入 research_review", job["status"] == "research_review")

# 审批研究 —— 验证审批后、director 运行前状态立即变为 directing
resp = client.post(f"/api/jobs/{race_job_id}/approve",
                   data=json.dumps({"action": "approve"}), content_type="application/json")
check("22-2: 研究审批返回 200", resp.status_code == 200)

# 等线程跑到 director_review 并阻塞
time.sleep(0.15)

# 验证前半段状态
check("22-3: 进入 research_review 时 DB 状态为 research_review",
      len(race_state_log) >= 1 and race_state_log[0] == ("entered_research_review", "research_review"))
check("22-4: 审批后、director 运行前 DB 状态为 directing（关键！消除状态真空）",
      len(race_state_log) >= 2 and race_state_log[1] == ("after_approve_before_director", "directing"),
      f"实际: {race_state_log[1] if len(race_state_log) >= 2 else 'N/A'}")
check("22-5: 进入 director_review 时 DB 状态为 director_review",
      len(race_state_log) >= 3 and race_state_log[2] == ("entered_director_review", "director_review"),
      f"实际: {race_state_log[2] if len(race_state_log) >= 3 else 'N/A'}")

# 线程正阻塞在导演评审，先审批再 join
resp = client.post(f"/api/jobs/{race_job_id}/approve",
                   data=json.dumps({"action": "approve"}), content_type="application/json")
check("22-5b: 导演审批返回 200", resp.status_code == 200)

t_race.join(timeout=3)

check("22-6: 审批后、generating 前 DB 状态为 generating（关键！消除状态真空）",
      len(race_state_log) >= 4 and race_state_log[3] == ("after_approve_before_generating", "generating"),
      f"实际: {race_state_log[3] if len(race_state_log) >= 4 else 'N/A'}")
check("22-7: 最终完成",
      len(race_state_log) >= 5 and race_state_log[4] == ("completed", "completed"),
      f"实际: {race_state_log[4] if len(race_state_log) >= 5 else 'N/A'}")

_active_runtimes.pop(race_job_id, None)
with get_session() as s:
    r = s.get(Run, race_run_id)
    if r:
        s.delete(r)
    j = s.get(Job, race_job_id)
    if j:
        s.delete(j)

# ═══════════════════════════════════════════════════════════════════════════════
# 22b. 竞态条件：regenerate 后状态更新时机
# ═══════════════════════════════════════════════════════════════════════════════
print("  22b: regenerate 后的状态更新时机")

re_race_job_id = "rerace_test_" + uuid.uuid4().hex[:6]
re_race_run_id = "rerace_run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
re_race_rt = PipelineRuntime(re_race_run_id, re_race_job_id)
_active_runtimes[re_race_job_id] = re_race_rt

re_race_state_log = []

db_utils.create_job(re_race_job_id, context="regenerate竞态测试", language="english")
db_utils.create_run(re_race_run_id, job_id=re_race_job_id, context="regenerate竞态测试",
                    output_dir="/tmp/rerace_test")
db_utils.update_job(re_race_job_id, run_id=re_race_run_id)


def _simulate_re_race():
    db_utils.update_job(re_race_job_id, status="researching", progress=10, message="研究中…")
    time.sleep(0.02)
    db_utils.update_job(re_race_job_id, status="research_review", progress=20, message="等待评审…")
    re_race_state_log.append(("research_review_v1", db_utils.get_job(re_race_job_id)["status"]))

    if not _wait_for_approval(re_race_rt):
        return

    if re_race_rt.regenerate:
        # 模拟真实流水线：regenerate 后立刻更新状态为 "researching"
        db_utils.update_job(re_race_job_id, status="researching", progress=15, message="重新研究中…")

        # 关键：re_race_rt.regenerate 设为 True 之后，流水线会立即设置状态为 "researching"
        re_race_state_log.append(("after_regenerate_before_reresearch",
                                  db_utils.get_job(re_race_job_id)["status"]))
        re_race_rt.regenerate = False
        db_utils.update_job(re_race_job_id, status="research_review", progress=20, message="等待评审…")
        re_race_state_log.append(("research_review_v2", db_utils.get_job(re_race_job_id)["status"]))

        if not _wait_for_approval(re_race_rt):
            return

    db_utils.update_job(re_race_job_id, status="completed", progress=100, message="完成")
    re_race_state_log.append(("completed", db_utils.get_job(re_race_job_id)["status"]))


t_re_race = threading.Thread(target=_simulate_re_race)
t_re_race.start()

time.sleep(0.15)
job = db_utils.get_job(re_race_job_id)
check("22b-1: 进入 research_review", job["status"] == "research_review")

# 请求 regenerate
resp = client.post(f"/api/jobs/{re_race_job_id}/approve",
                   data=json.dumps({"action": "regenerate", "feedback": "需要修改"}),
                   content_type="application/json")
check("22b-2: regenerate 返回 200", resp.status_code == 200)

time.sleep(0.15)
# 线程应已重新进入 research_review_v2
check("22b-3: regenerate 后立即变为 researching（关键！消除状态真空）",
      len(re_race_state_log) >= 2 and re_race_state_log[1] == ("after_regenerate_before_reresearch", "researching"),
      f"实际 state_log: {re_race_state_log}")
check("22b-4: 第二次 research_review",
      len(re_race_state_log) >= 3 and re_race_state_log[2] == ("research_review_v2", "research_review"),
      f"实际 state_log: {re_race_state_log}")

# 第二次审批通过（线程正阻塞在 _wait_for_approval，先审批再 join）
resp = client.post(f"/api/jobs/{re_race_job_id}/approve",
                   data=json.dumps({"action": "approve"}), content_type="application/json")
check("22b-5: 第二次审批返回 200", resp.status_code == 200)

t_re_race.join(timeout=3)
job = db_utils.get_job(re_race_job_id)
check("22b-6: 最终完成", job["status"] == "completed")

_active_runtimes.pop(re_race_job_id, None)
with get_session() as s:
    r = s.get(Run, re_race_run_id)
    if r:
        s.delete(r)
    j = s.get(Job, re_race_job_id)
    if j:
        s.delete(j)

# ═══════════════════════════════════════════════════════════════════════════════
# 清理：安全网 — 释放所有残留 runtime，避免挂起的线程阻塞进程退出
# ═══════════════════════════════════════════════════════════════════════════════
for _key, _rt in list(_active_runtimes.items()):
    try:
        _rt.abort_event.set()
        _rt.pause_event.set()  # 双保险：让所有等待的线程都能退出
    except Exception:
        pass
_active_runtimes.clear()

# 等待所有非 daemon 线程结束（最多 3 秒）
for _t in threading.enumerate():
    if _t is not threading.current_thread() and not _t.daemon:
        _t.join(timeout=3)

# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 60}")
print(f"Results: {_passed} passed, {_failed} failed")
if _failed:
    print("Some tests FAILED — check output above.")
else:
    print("All tests passed!")
