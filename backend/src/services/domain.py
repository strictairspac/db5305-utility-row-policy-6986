"""Merge split field meter-reading returns into reviewable task results.

Field crews often send one planned meter task back as several records.  This
module groups a batch of returns by ``task_id + plan_date`` and adopts exactly
one record per task with a deterministic, audit-friendly rule set:

* a verified record always wins over an unverified record, and the latest
  verification wins among verified records, so the same input always yields the
  same conclusion;
* inside the same verification tier an in-plan record wins over a late record,
  which wins over a cross-day record;
* late or cross-day records may only fill a task on the same plan day and only
  while they stay inside the plan-day time window;
* records with a backwards timestamp, missing key fields, an out-of-window
  timestamp or a duplicated return id are isolated with a reason instead of
  being silently dropped.

Every input record is accounted for exactly once in the report: adopted, not
adopted, or isolated.  The module is intentionally pure (stdlib only) so the
merge rules can be tested without a database.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone

SHANGHAI = timezone(timedelta(hours=8))
DEFAULT_WINDOW_HOURS = 4
MAX_WINDOW_HOURS = 48
MAX_RECORDS = 5000
UNKNOWN_ZONE = "未标注"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SPEC = {
    "name": "埠灯公服·抄表回传归并",
    "summary": "按任务编号与计划日归并现场回传，已核验记录优先，异常记录隔离并注明原因。",
    "customer": "埠灯公共服务有限公司 · 表务调度与内控组",
    "entity_labels": ["回传批次", "归并执行", "归并结果"],
    "field_labels": {
        "batch_name": "批次名称",
        "window_hours": "跨日补齐时间窗（小时）",
        "records": "现场回传记录",
        "record_id": "回传流水号",
        "task_id": "任务编号",
        "plan_date": "计划日",
        "zone": "作业区域",
        "read_at": "抄表时间（含时区）",
        "received_at": "回传时间（含时区）",
        "meter_value": "表计读数",
        "verified": "已核验",
        "verified_at": "核验时间（含时区）",
        "adopt_basis": "采用依据",
        "isolation_reason": "隔离原因",
        "status": "状态",
    },
    "guidance": (
        "录入一批现场回传记录，按任务编号和计划日归并。已核验记录优先采用；"
        "迟到或跨日记录只在同一计划日的时间窗内补齐；时间倒退或关键字段缺失的记录会被隔离。"
    ),
    "sample": {
        "batch_name": "2026-11-21 早班现场回传",
        "window_hours": 4,
        "records": [
            {
                "record_id": "R-1001", "task_id": "MR-701", "plan_date": "2026-11-21",
                "zone": "南堤", "read_at": "2026-11-21T08:10:00+08:00",
                "received_at": "2026-11-21T08:15:00+08:00", "meter_value": "120.5",
                "verified": False, "verified_at": None,
            },
            {
                "record_id": "R-1002", "task_id": "MR-701", "plan_date": "2026-11-21",
                "zone": "南堤", "read_at": "2026-11-21T08:12:00+08:00",
                "received_at": "2026-11-21T08:20:00+08:00", "meter_value": "120.6",
                "verified": True, "verified_at": "2026-11-21T08:30:00+08:00",
            },
            {
                "record_id": "R-1003", "task_id": "MR-701", "plan_date": "2026-11-21",
                "zone": "南堤", "read_at": "2026-11-21T09:40:00+08:00",
                "received_at": "2026-11-21T09:50:00+08:00", "meter_value": "121.0",
                "verified": False, "verified_at": None,
            },
            {
                "record_id": "R-1004", "plan_date": "2026-11-21", "zone": "南堤",
                "read_at": "2026-11-21T08:40:00+08:00",
                "received_at": "2026-11-21T08:45:00+08:00", "meter_value": "88.0",
                "verified": False, "verified_at": None,
            },
            {
                "record_id": "R-1005", "task_id": "MR-702", "plan_date": "2026-11-21",
                "zone": "北闸", "read_at": "2026-11-21T22:00:00+08:00",
                "received_at": "2026-11-22T01:10:00+08:00", "meter_value": "33.2",
                "verified": False, "verified_at": None,
            },
            {
                "record_id": "R-1006", "task_id": "MR-703", "plan_date": "2026-11-21",
                "zone": "南堤", "read_at": "2026-11-22T00:30:00+08:00",
                "received_at": "2026-11-22T01:00:00+08:00", "meter_value": "301.7",
                "verified": False, "verified_at": None,
            },
            {
                "record_id": "R-1007", "task_id": "MR-703", "plan_date": "2026-11-21",
                "zone": "南堤", "read_at": "2026-11-22T05:10:00+08:00",
                "received_at": "2026-11-22T05:20:00+08:00", "meter_value": "302.0",
                "verified": False, "verified_at": None,
            },
            {
                "record_id": "R-1008", "task_id": "MR-704", "plan_date": "2026-11-21",
                "zone": "西港", "read_at": "2026-11-21T10:00:00+08:00",
                "received_at": "2026-11-21T09:55:00+08:00", "meter_value": "45.0",
                "verified": False, "verified_at": None,
            },
            {
                "record_id": "R-1009", "task_id": "MR-704", "plan_date": "2026-11-21",
                "zone": "西港", "read_at": "2026-11-21T10:20:00+08:00",
                "received_at": "2026-11-21T10:25:00+08:00", "meter_value": None,
                "verified": False, "verified_at": None,
            },
            {
                "record_id": "R-1010", "task_id": "MR-705", "plan_date": "2026-11-21",
                "zone": "南堤", "read_at": "2026-11-21T11:00:00+08:00",
                "received_at": "2026-11-21T11:10:00+08:00", "meter_value": "66.8",
                "verified": False, "verified_at": None,
            },
            {
                "record_id": "R-1011", "task_id": "MR-705", "plan_date": "2026-11-21",
                "zone": "南堤", "read_at": "2026-11-21T11:05:00+08:00",
                "received_at": "2026-11-22T04:30:00+08:00", "meter_value": "66.9",
                "verified": False, "verified_at": None,
            },
            {
                "record_id": "R-1012", "task_id": "MR-706", "plan_date": "2026-11-21",
                "zone": "北闸", "read_at": "2026-11-21T12:00:00+08:00",
                "received_at": "2026-11-21T12:10:00+08:00", "meter_value": "91.0",
                "verified": True, "verified_at": "2026-11-21T12:05:00+08:00",
            },
            {
                "record_id": "R-1012", "task_id": "MR-706", "plan_date": "2026-11-21",
                "zone": "北闸", "read_at": "2026-11-21T12:02:00+08:00",
                "received_at": "2026-11-21T12:12:00+08:00", "meter_value": "91.1",
                "verified": False, "verified_at": None,
            },
            {
                "record_id": "R-1013", "task_id": "MR-707", "plan_date": "2026-11-22",
                "zone": "西港", "read_at": "2026-11-21T23:00:00+08:00",
                "received_at": "2026-11-21T23:10:00+08:00", "meter_value": "18.4",
                "verified": False, "verified_at": None,
            },
        ],
    },
}


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

def _is_text(value):
    return isinstance(value, str) and value.strip() != "" and "\x00" not in value


def _identifier(value):
    """Identifiers may arrive as text or as an integer from the source system."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    return value.strip() if _is_text(value) else None


def _parse_day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_type):
        return value
    if not _is_text(value):
        return None
    raw = value.strip()
    if not DATE_PATTERN.match(raw):
        return None
    try:
        return date_type.fromisoformat(raw)
    except ValueError:
        return None


def _parse_time(value):
    """Return an aware datetime, or None when missing, naive or malformed."""
    if not _is_text(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed.utcoffset() is not None else None


def _parse_verified(value):
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "是", "yes"}:
            return True
        if lowered in {"false", "0", "否", "no"}:
            return False
    raise ValueError("已核验标记须为布尔值。")


def _parse_meter(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _day_bounds(plan_day):
    start = datetime.combine(plan_day, time.min, tzinfo=SHANGHAI)
    return start, start + timedelta(days=1)


def window_hours(payload):
    value = payload.get("window_hours", DEFAULT_WINDOW_HOURS)
    if value is None or value == "":
        return DEFAULT_WINDOW_HOURS
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("跨日补齐时间窗须为 0 到 48 之间的数字（小时）。")
    if not 0 <= value <= MAX_WINDOW_HOURS:
        raise ValueError("跨日补齐时间窗须为 0 到 48 之间的数字（小时）。")
    return value


def _pick(record, *names):
    """Read the first present alias; unknown keys are ignored."""
    for name in names:
        if name in record:
            return record[name]
    return None


def records_of(payload):
    if not isinstance(payload, dict):
        raise ValueError("归并输入须为对象。")
    records = None
    for name in ("records", "returns", "items", "data", "rows"):
        if name in payload:
            records = payload[name]
            break
    if records is None:
        raise ValueError("现场回传记录须为数组。")
    if not isinstance(records, list):
        raise ValueError("现场回传记录须为数组。")
    if len(records) > MAX_RECORDS:
        raise ValueError(f"单批现场回传记录不能超过 {MAX_RECORDS} 条。")
    return records


def _fingerprint(record):
    try:
        return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(record)


class _Row:
    """One input record after parsing; either usable or isolated with a reason."""

    __slots__ = ("index", "record_id", "task_id", "plan_day", "zone", "read_at",
                 "received_at", "meter_value", "verified", "verified_at",
                 "tier", "reason", "adopted", "duplicate")

    def __init__(self, index):
        self.index = index
        self.record_id = None
        self.task_id = None
        self.plan_day = None
        self.zone = UNKNOWN_ZONE
        self.read_at = None
        self.received_at = None
        self.meter_value = None
        self.verified = False
        self.verified_at = None
        self.tier = None
        self.reason = None
        self.adopted = False
        self.duplicate = False

    @property
    def usable(self):
        return self.reason is None and not self.duplicate


def _read_row(record, index, hours):
    row = _Row(index)
    if not isinstance(record, dict):
        row.reason = "记录不是对象"
        return row

    row.record_id = _identifier(_pick(record, "record_id", "return_id", "record_no", "return_no", "id"))
    row.task_id = _identifier(_pick(record, "task_id", "task_no", "work_order_id", "order_id", "job_id"))
    row.plan_day = _parse_day(_pick(record, "plan_date", "plan_day", "planned_date", "scheduled_date"))
    raw_zone = _pick(record, "zone", "area", "region")
    row.zone = raw_zone.strip() if _is_text(raw_zone) else UNKNOWN_ZONE
    row.read_at = _parse_time(_pick(record, "read_at", "meter_read_at", "reading_at", "read_time"))
    row.received_at = _parse_time(_pick(record, "received_at", "returned_at", "return_at", "reported_at", "submitted_at"))
    row.meter_value = _parse_meter(_pick(record, "meter_value", "reading", "reading_value", "value"))

    try:
        row.verified = _parse_verified(_pick(record, "verified", "is_verified", "checked", "reviewed"))
    except ValueError as exc:
        row.reason = str(exc)
        return row
    if row.verified:
        row.verified_at = _parse_time(_pick(record, "verified_at", "checked_at", "reviewed_at"))

    # Required key fields, in a fixed order so the reported reason is stable.
    if row.task_id is None:
        row.reason = "缺少任务编号"
    elif row.plan_day is None:
        row.reason = "缺少或无效的计划日"
    elif not _is_text(raw_zone):
        row.reason = "缺少作业区域"
    elif row.read_at is None:
        row.reason = "抄表时间缺失或缺少时区"
    elif row.received_at is None:
        row.reason = "回传时间缺失或缺少时区"
    elif row.meter_value is None:
        row.reason = "表计读数缺失"
    elif row.verified and row.verified_at is None:
        row.reason = "标记已核验但缺少核验时间"
    elif row.received_at < row.read_at:
        row.reason = "回传时间早于抄表时间（时间倒退）"
    elif row.verified and row.verified_at < row.read_at:
        row.reason = "核验时间早于抄表时间（时间倒退）"
    else:
        plan_start, plan_end = _day_bounds(row.plan_day)
        cutoff = plan_end + timedelta(hours=hours)
        if row.read_at < plan_start:
            row.reason = "抄表时间早于计划日（时间倒退）"
        elif row.read_at >= cutoff:
            row.reason = "抄表时间超出计划日时间窗"
        elif row.received_at >= cutoff:
            row.reason = "回传超出补齐时间窗"
        elif row.read_at < plan_end:
            row.tier = "plan" if row.received_at < plan_end else "late"
        else:
            row.tier = "cross"
    return row


# --------------------------------------------------------------------------
# Merge rules
# --------------------------------------------------------------------------

TIER_ORDER = {"plan": 0, "late": 1, "cross": 2}
TIER_LABELS = {"plan": "计划日内", "late": "迟到补齐", "cross": "跨日补齐"}


def _selection_key(row):
    anchor = row.verified_at if row.verified else row.read_at
    return (
        0 if row.verified else 1,
        TIER_ORDER[row.tier],
        -anchor.timestamp(),
        -row.read_at.timestamp(),
        -row.received_at.timestamp(),
        row.record_id or "",
    )


def _adopt_basis(row):
    return ("已核验" if row.verified else "未核验") + "·" + TIER_LABELS[row.tier]


def _dominant_zone(rows):
    counts = Counter(row.zone for row in rows)
    return sorted(counts, key=lambda zone: (-counts[zone], zone))[0]


def _stamp(value):
    return value.isoformat() if value is not None else "—"


def _record_view(row):
    return {
        "序号": row.index,
        "回传流水号": row.record_id or "—",
        "任务编号": row.task_id or "—",
        "计划日": row.plan_day.isoformat() if row.plan_day else "—",
        "作业区域": row.zone,
        "抄表时间": _stamp(row.read_at),
        "回传时间": _stamp(row.received_at),
        "表计读数": row.meter_value if row.meter_value is not None else "—",
        "已核验": "是" if row.verified else "否",
    }


def merge(payload, batch_name=None):
    hours = window_hours(payload)
    raw_records = records_of(payload)

    rows = [_read_row(record, index, hours) for index, record in enumerate(raw_records, start=1)]

    # A repeated return id is only a duplicate when the content is identical;
    # then the first copy represents the group.  Conflicting copies make
    # adoption ambiguous, so every copy is isolated rather than guessed.
    by_id = defaultdict(list)
    for row, record in zip(rows, raw_records):
        if row.record_id is not None:
            by_id[row.record_id].append((row, record))
    for record_id, copies in by_id.items():
        if len(copies) < 2:
            continue
        fingerprints = {_fingerprint(record) for _, record in copies}
        if len(fingerprints) == 1:
            # Identical copies are one physical return; keep the first.
            for row, _ in copies[1:]:
                if row.usable:
                    row.duplicate = True
                    row.tier = None
        else:
            for row, _ in copies:
                if row.usable:
                    row.reason = f"回传流水号重复且内容不一致（{record_id}）"
                    row.tier = None

    groups = defaultdict(list)
    for row in rows:
        if row.usable:
            groups[(row.plan_day, row.task_id)].append(row)

    adopted = {}
    for key, candidates in groups.items():
        candidates.sort(key=_selection_key)
        adopted[key] = candidates[0]
        candidates[0].adopted = True

    # Isolated records still identify a task; report it as 待补采 when no
    # usable record exists for the same task and plan day.
    pending = defaultdict(list)
    for row in rows:
        if row.usable or row.task_id is None or row.plan_day is None:
            continue
        key = (row.plan_day, row.task_id)
        if key not in groups:
            pending[key].append(row)

    task_rows = []
    isolation_per_task = Counter()
    for row in rows:
        if row.usable:
            continue
        if row.task_id is not None and row.plan_day is not None:
            isolation_per_task[(row.plan_day, row.task_id)] += 1

    for key in sorted(groups, key=lambda item: (item[0], item[1])):
        plan_day, task_id = key
        winner = adopted[key]
        task_rows.append({
            "任务编号": task_id,
            "计划日": plan_day.isoformat(),
            "作业区域": _dominant_zone(groups[key]),
            "状态": "已采用",
            "采用回传流水号": winner.record_id or "—",
            "采用依据": _adopt_basis(winner),
            "抄表时间": _stamp(winner.read_at),
            "回传时间": _stamp(winner.received_at),
            "表计读数": winner.meter_value,
            "已核验": "是" if winner.verified else "否",
            "候选记录数": len(groups[key]),
            "隔离记录数": isolation_per_task[key],
        })
    for key in sorted(pending, key=lambda item: (item[0], item[1])):
        plan_day, task_id = key
        task_rows.append({
            "任务编号": task_id,
            "计划日": plan_day.isoformat(),
            "作业区域": _dominant_zone(pending[key]),
            "状态": "待补采",
            "采用回传流水号": "—",
            "采用依据": "无可用记录",
            "抄表时间": "—",
            "回传时间": "—",
            "表计读数": "—",
            "已核验": "—",
            "候选记录数": 0,
            "隔离记录数": isolation_per_task[key],
        })
    task_rows.sort(key=lambda item: (item["计划日"], item["任务编号"]))

    isolated_rows, unused_rows = [], []
    for row in rows:
        if row.duplicate:
            view = _record_view(row)
            view["未采用原因"] = "重复回传且内容相同，已合并为一条"
            unused_rows.append(view)
        elif not row.usable:
            view = _record_view(row)
            view["隔离原因"] = row.reason
            isolated_rows.append(view)
        elif not row.adopted:
            winner = adopted[(row.plan_day, row.task_id)]
            view = _record_view(row)
            view["未采用原因"] = f"同一任务已采用更优记录（{winner.record_id or '—'}）"
            unused_rows.append(view)
    isolated_rows.sort(key=lambda item: item["序号"])
    unused_rows.sort(key=lambda item: item["序号"])

    zones = sorted({row["作业区域"] for row in task_rows}
                   | {row["作业区域"] for row in isolated_rows}
                   | {row["作业区域"] for row in unused_rows})
    zone_rows = []
    for zone in zones:
        zone_rows.append({
            "作业区域": zone,
            "归并任务数": sum(1 for row in task_rows if row["作业区域"] == zone),
            "已采用任务数": sum(1 for row in task_rows if row["作业区域"] == zone and row["状态"] == "已采用"),
            "待补采任务数": sum(1 for row in task_rows if row["作业区域"] == zone and row["状态"] == "待补采"),
            "隔离记录数": sum(1 for row in isolated_rows if row["作业区域"] == zone),
            "未采用记录数": sum(1 for row in unused_rows if row["作业区域"] == zone),
        })

    used_tiers = Counter(row.tier for row in adopted.values())
    metrics = {
        "回传记录数": len(rows),
        "归并任务数": len(task_rows),
        "已采用任务数": len(groups),
        "待补采任务数": len(pending),
        "已核验采用数": sum(1 for row in adopted.values() if row.verified),
        "迟到补齐数": used_tiers["late"],
        "跨日补齐数": used_tiers["cross"],
        "隔离记录数": len(isolated_rows),
        "未采用记录数": len(unused_rows),
        "涉及区域数": len({row["作业区域"] for row in task_rows}),
    }

    if not rows:
        summary = "本批没有现场回传记录，归并结果为空。"
    elif not task_rows:
        summary = f"本批 {len(rows)} 条回传均被隔离，没有可核对的任务，请补采后重传。"
    elif metrics["隔离记录数"]:
        summary = (
            f"本批 {len(rows)} 条回传归并为 {len(task_rows)} 个任务："
            f"{metrics['已采用任务数']} 个已采用、{metrics['待补采任务数']} 个待补采，"
            f"{metrics['隔离记录数']} 条记录被隔离。"
        )
    else:
        summary = (
            f"本批 {len(rows)} 条回传归并为 {len(task_rows)} 个任务："
            f"{metrics['已采用任务数']} 个已采用、{metrics['待补采任务数']} 个待补采。"
        )

    title = batch_name
    if not _is_text(title):
        candidate = payload.get("batch_name")
        title = candidate.strip() if _is_text(candidate) else "未命名批次"
    return {
        "batch_name": title,
        "summary": summary,
        "metrics": metrics,
        "sections": [
            {"title": "每单采用记录", "rows": task_rows},
            {"title": "隔离记录", "rows": isolated_rows},
            {"title": "未采用记录", "rows": unused_rows},
            {"title": "区域汇总", "rows": zone_rows},
        ],
    }


def batch_identity(payload):
    """Return the normalized identity of a batch input.

    The merge ignores the display name and is order-insensitive, so the batch
    identity is the window plus the multiset of raw records.  Re-submitting the
    same returns in a different order or under a different batch name therefore
    resolves to the same batch instead of creating a duplicate.
    """
    hours = window_hours(payload)
    rows = []
    for record in records_of(payload):
        try:
            rows.append(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False))
        except (TypeError, ValueError):
            rows.append(repr(record))
    return {"window_hours": hours, "records": sorted(rows)}


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError("归并输入须为对象。")
    if not _is_text(payload.get("batch_name")):
        raise ValueError("批次名称须为非空文本。")
    window_hours(payload)
    records_of(payload)


def solve(payload, engine=None, batch_name=None):
    """Entry point used by the worker; merging needs no database access."""
    validate(payload)
    return merge(payload, batch_name=batch_name)
