import hashlib
import uuid

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from src.config.settings import cache, engine
from src.constants.workflow import Status
from src.models.tables import audit, cases, evidence, runs
from src.services import domain
from src.utils.encoding import canonical
from src.utils.time import utc_time


def log(conn, actor, action, reference):
    conn.execute(audit.insert().values(actor=actor, action=action, reference=str(reference), created_at=utc_time()))


def recent(table):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(table).order_by(table.c.id.desc()).limit(100)).mappings()]


def found(conn, table, identity):
    row = conn.execute(select(table).where(table.c.id == identity)).mappings().first()
    if row is None:
        raise HTTPException(404, '找不到该记录')
    return dict(row)


def input_digest(payload):
    """Identity of a batch: the normalized business input, never the title."""
    try:
        identity = domain.batch_identity(payload)
    except ValueError:
        identity = payload
    return hashlib.sha256(canonical(identity)).hexdigest()


def make_case(title, payload, actor):
    title = title.strip()
    if not title:
        raise HTTPException(422, '档案名称不能为空')
    try:
        if len(canonical(payload)) > 512 * 1024:
            raise ValueError('业务输入超过 512 KiB')
        if callable(getattr(domain, 'validate', None)):
            domain.validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None
    digest = input_digest(payload)
    with engine.begin() as conn:
        existing = conn.execute(select(cases).where(cases.c.input_digest == digest)).mappings().first()
        if existing is not None:
            # The same batch input is a single batch; reruns reuse it.
            return {**dict(existing), 'reused': True}
        try:
            with conn.begin_nested():
                case_id = conn.execute(cases.insert().values(
                    slug=uuid.uuid4().hex, title=title, payload=payload, input_digest=digest,
                    author=actor, created_at=utc_time())).inserted_primary_key[0]
        except IntegrityError:
            # A concurrent submit of the same input won the insert race; the
            # savepoint above keeps this transaction usable for the lookup.
            existing = conn.execute(select(cases).where(cases.c.input_digest == digest)).mappings().first()
            if existing is None:
                raise
            return {**dict(existing), 'reused': True}
        log(conn, actor, 'create_case', case_id)
        return {**found(conn, cases, case_id), 'reused': False}


def _reusable_run(conn, case_id):
    """A run whose result can be reused: an active attempt, else the newest complete one."""
    with_result = select(runs.c.id).where(evidence.c.run_id == runs.c.id).exists()
    active = conn.execute(select(runs).where(
        runs.c.case_id == case_id, runs.c.status.in_([Status.QUEUED, Status.RUNNING]))
        .order_by(runs.c.id.desc()).limit(1)).mappings().first()
    if active is not None:
        return active, 'active'
    complete = conn.execute(select(runs).where(
        runs.c.case_id == case_id, runs.c.status == Status.COMPLETE, with_result)
        .order_by(runs.c.id.desc()).limit(1)).mappings().first()
    if complete is not None:
        return complete, 'complete'
    return None, None


def enqueue(case_id, actor):
    with engine.begin() as conn:
        # Lock the batch row so two concurrent reruns cannot both enqueue.
        conn.execute(select(cases.c.id).where(cases.c.id == case_id).with_for_update()).first()
        found(conn, cases, case_id)
        existing, reason = _reusable_run(conn, case_id)
        if existing is not None:
            # Rerunning the same batch reuses its original result instead of
            # creating a new batch or a duplicate run.
            return {**dict(existing), 'reused': True, 'reuse_reason': reason}
        run_id = conn.execute(runs.insert().values(case_id=case_id, status=Status.QUEUED,
                              owner=actor, created_at=utc_time())).inserted_primary_key[0]
        log(conn, actor, 'enqueue', run_id)
        result = found(conn, runs, run_id)
    try:
        cache.lpush('worker:wake', 'queued')
        cache.ltrim('worker:wake', 0, 99)
    except Exception:
        # The durable database queue is polled even if Redis is temporarily unavailable.
        pass
    return {**result, 'reused': False}


def claim():
    ready = or_(runs.c.status == Status.QUEUED,
                and_(runs.c.status == Status.RUNNING, runs.c.started_at < utc_time(-600)))
    with engine.begin() as conn:
        run = conn.execute(select(runs.c.id, runs.c.case_id).where(ready).order_by(runs.c.id)
                           .limit(1).with_for_update(skip_locked=True)).first()
        if not run:
            return None
        token = str(uuid.uuid4())
        conn.execute(runs.update().where(runs.c.id == run.id).values(
            started_at=utc_time(), status=Status.RUNNING, attempt_token=token))
        case = found(conn, cases, run.case_id)
        return run.id, token, case['payload'], case['title']


def finish(run_id, token, result=None, error=None):
    digest = hashlib.sha256(canonical(result)).hexdigest() if result is not None else None
    status = Status.FAILED if error else Status.COMPLETE
    with engine.begin() as conn:
        count = conn.execute(runs.update().where(runs.c.id == run_id,
                        runs.c.attempt_token == token, runs.c.status == Status.RUNNING)
                        .values(status=status, finished_at=utc_time(), error=error)).rowcount
        if not count:
            return
        if result is not None:
            # Evidence rows are append-only per run, so a failed retry never
            # overwrites the last good version of the batch.
            conn.execute(evidence.insert().values(run_id=run_id, report=result, digest=digest))
        log(conn, 'worker', status.value, run_id)


def review(identity, note, actor):
    note = note.strip()
    if not note:
        raise HTTPException(422, '复核意见不能为空')
    with engine.begin() as conn:
        found(conn, evidence, identity)
        count = conn.execute(evidence.update().where(evidence.c.id == identity, evidence.c.reviewed_at.is_(None))
                            .values(review_note=note, reviewer=actor, reviewed_at=utc_time())).rowcount
        if count != 1:
            raise HTTPException(409, '该证据已经复核')
        log(conn, actor, 'review', identity)
        return found(conn, evidence, identity)
