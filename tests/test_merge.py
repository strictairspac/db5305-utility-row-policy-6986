"""Rule tests for the field-return merge; stdlib + pytest, no database."""

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from src.services.domain import SPEC, merge, validate  # noqa: E402


def record(**overrides):
    base = {
        "record_id": "R-1", "task_id": "T-1", "plan_date": "2026-11-21", "zone": "南堤",
        "read_at": "2026-11-21T09:00:00+08:00", "received_at": "2026-11-21T09:05:00+08:00",
        "meter_value": "12.5", "verified": False, "verified_at": None,
    }
    base.update(overrides)
    return base


def run(records, **extra):
    return merge({"batch_name": "测试批次", "records": records, **extra})


def tasks(result):
    return {row["任务编号"]: row for row in result["sections"][0]["rows"]}


def isolated(result):
    return result["sections"][1]["rows"]


def test_sample_is_stable_and_reproducible():
    first = merge(copy.deepcopy(SPEC["sample"]))
    second = merge(copy.deepcopy(SPEC["sample"]))
    assert json.dumps(first, sort_keys=True, ensure_ascii=False) == \
           json.dumps(second, sort_keys=True, ensure_ascii=False)
    assert first["metrics"]["回传记录数"] == len(SPEC["sample"]["records"])
    assert first["metrics"]["归并任务数"] == 7
    assert first["metrics"]["已采用任务数"] == 4
    assert first["metrics"]["待补采任务数"] == 3
    assert first["metrics"]["隔离记录数"] == 8
    assert first["metrics"]["未采用记录数"] == 2


def test_every_record_is_accounted_for():
    result = run([
        record(record_id="A"), record(record_id="B", meter_value=None),
        record(record_id="A", meter_value="13.0"),
    ])
    accounted = (result["metrics"]["已采用任务数"] + len(isolated(result))
                 + len(result["sections"][2]["rows"]))
    assert accounted == 3


def test_verified_record_wins_even_when_late():
    result = run([
        record(record_id="A", read_at="2026-11-21T23:00:00+08:00",
               received_at="2026-11-22T00:40:00+08:00"),
        record(record_id="B", verified=True, verified_at="2026-11-21T23:30:00+08:00",
               read_at="2026-11-21T23:10:00+08:00",
               received_at="2026-11-21T23:20:00+08:00"),
    ])
    assert tasks(result)["T-1"]["采用回传流水号"] == "B"
    assert result["metrics"]["已核验采用数"] == 1


def test_latest_verification_wins_among_verified():
    result = run([
        record(record_id="A", verified=True, verified_at="2026-11-21T10:00:00+08:00"),
        record(record_id="B", verified=True, verified_at="2026-11-21T11:00:00+08:00"),
        record(record_id="C", verified=True, verified_at="2026-11-21T09:00:00+08:00"),
    ])
    assert tasks(result)["T-1"]["采用回传流水号"] == "B"


def test_cross_day_only_fills_when_no_in_plan_record():
    filled = run([record(record_id="C", read_at="2026-11-22T00:10:00+08:00",
                         received_at="2026-11-22T00:20:00+08:00")])
    assert tasks(filled)["T-1"]["采用依据"] == "未核验·跨日补齐"
    assert filled["metrics"]["跨日补齐数"] == 1

    kept = run([
        record(record_id="P"),
        record(record_id="C", read_at="2026-11-22T00:10:00+08:00",
               received_at="2026-11-22T00:20:00+08:00"),
    ])
    assert tasks(kept)["T-1"]["采用回传流水号"] == "P"
    assert kept["metrics"]["跨日补齐数"] == 0


def test_different_plan_days_never_merge():
    result = run([
        record(record_id="A"),
        record(record_id="B", plan_date="2026-11-22",
               read_at="2026-11-22T09:00:00+08:00", received_at="2026-11-22T09:05:00+08:00"),
    ])
    assert result["metrics"]["归并任务数"] == 2


def test_window_is_enforced():
    outside = run([record(record_id="A", read_at="2026-11-21T09:00:00+08:00",
                          received_at="2026-11-22T04:30:00+08:00")])
    assert outside["metrics"]["隔离记录数"] == 1
    assert "时间窗" in isolated(outside)[0]["隔离原因"]

    zero = run([record(record_id="B", read_at="2026-11-22T00:10:00+08:00",
                       received_at="2026-11-22T00:20:00+08:00")], window_hours=0)
    assert zero["metrics"]["待补采任务数"] == 1
    assert zero["metrics"]["隔离记录数"] == 1


# (overrides, expected reason, whether the record still identifies a task)
ISOLATION_CASES = [
    ({"task_id": None}, "缺少任务编号", False),
    ({"plan_date": None}, "缺少或无效的计划日", False),
    ({"plan_date": "2026/11/21"}, "缺少或无效的计划日", False),
    ({"zone": None}, "缺少作业区域", True),
    ({"read_at": None}, "抄表时间缺失或缺少时区", True),
    ({"read_at": "2026-11-21T09:00:00"}, "抄表时间缺失或缺少时区", True),
    ({"received_at": "2026-11-21T08:00:00+08:00"}, "时间倒退", True),
    ({"meter_value": None}, "表计读数缺失", True),
    ({"verified": True, "verified_at": None}, "缺少核验时间", True),
    ({"verified": True, "verified_at": "2026-11-21T08:00:00+08:00"}, "时间倒退", True),
]


def test_isolation_reasons():
    for overrides, reason, identifies_task in ISOLATION_CASES:
        result = run([record(**overrides)])
        assert result["metrics"]["隔离记录数"] == 1, overrides
        assert reason in isolated(result)[0]["隔离原因"], overrides
        expected_pending = 1 if identifies_task else 0
        assert result["metrics"]["待补采任务数"] == expected_pending, overrides


def test_conflicting_duplicate_ids_are_all_isolated():
    result = run([record(record_id="D", meter_value="1"), record(record_id="D", meter_value="2")])
    assert result["metrics"]["已采用任务数"] == 0
    assert result["metrics"]["隔离记录数"] == 2
    assert all("重复且内容不一致" in row["隔离原因"] for row in isolated(result))


def test_identical_duplicates_collapse_to_one():
    result = run([record(record_id="D"), record(record_id="D")])
    assert result["metrics"]["已采用任务数"] == 1
    assert result["metrics"]["隔离记录数"] == 0
    assert result["metrics"]["未采用记录数"] == 1


def test_input_aliases_and_integer_ids():
    result = merge({"batch_name": "别名批次", "returns": [{
        "record_no": 101, "task_no": 701, "planned_date": "2026-11-21", "region": "西港",
        "reading_at": "2026-11-21T09:00:00+08:00",
        "reported_at": "2026-11-21T09:05:00+08:00", "reading": 12.5,
        "checked": 1, "checked_at": "2026-11-21T09:06:00+08:00",
    }]})
    adopted = tasks(result)["701"]
    assert adopted["采用回传流水号"] == "101"
    assert adopted["表计读数"] == "12.5"
    assert adopted["已核验"] == "是"


def test_zone_summary_matches_task_rows():
    result = run([
        record(record_id="A", zone="南堤"),
        record(record_id="B", zone="西港", task_id="T-2", meter_value=None),
    ])
    summary = {row["作业区域"]: row for row in result["sections"][3]["rows"]}
    assert summary["南堤"]["已采用任务数"] == 1
    assert summary["西港"]["待补采任务数"] == 1
    assert summary["西港"]["隔离记录数"] == 1


def test_empty_batch_is_valid():
    result = merge({"batch_name": "空批次", "records": []})
    assert result["metrics"]["回传记录数"] == 0
    assert all(section["rows"] == [] for section in result["sections"])


def _raises(call):
    try:
        call()
    except ValueError:
        return True
    return False


def test_validation_rejects_malformed_input():
    assert _raises(lambda: validate({"records": []}))
    assert _raises(lambda: validate({"batch_name": "x", "records": "not-a-list"}))
    assert _raises(lambda: validate({"batch_name": "x", "records": [], "window_hours": 99}))


if __name__ == "__main__":
    failures = 0
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc!r}")
    raise SystemExit(1 if failures else 0)
