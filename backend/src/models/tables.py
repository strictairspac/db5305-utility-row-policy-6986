from sqlalchemy import (JSON, Column, ForeignKey, Index, Integer, MetaData,
                        String, Table, Text)
from sqlalchemy import text as sa_text

schema = MetaData()
users = Table('accounts', schema,
    Column('id', Integer, primary_key=True),
    Column('username', String(40), nullable=False, unique=True),
    Column('password_hash', String(200), nullable=False),
    Column('role', String(20), nullable=False))
cases = Table('business_cases', schema,
    Column('id', Integer, primary_key=True),
    Column('slug', String(64), nullable=False, unique=True),
    Column('title', String(120), nullable=False),
    Column('payload', JSON, nullable=False),
    Column('input_digest', String(64)),
    Column('author', String(40), nullable=False),
    Column('created_at', String(32), nullable=False))
Index('ux_business_cases_input_digest', cases.c.input_digest, unique=True)
runs = Table('business_runs', schema,
    Column('id', Integer, primary_key=True),
    Column('case_id', ForeignKey('business_cases.id'), nullable=False, index=True),
    Column('status', String(20), nullable=False, index=True),
    Column('owner', String(40), nullable=False),
    Column('attempt_token', String(36)),
    Column('started_at', String(32)),
    Column('finished_at', String(32)),
    Column('created_at', String(32), nullable=False),
    Column('error', Text))
evidence = Table('business_evidence', schema,
    Column('id', Integer, primary_key=True),
    Column('run_id', ForeignKey('business_runs.id'), nullable=False, unique=True),
    Column('report', JSON, nullable=False),
    Column('digest', String(64), nullable=False),
    Column('reviewer', String(40)),
    Column('review_note', String(500)),
    Column('reviewed_at', String(32)))
audit = Table('audit_events', schema,
    Column('id', Integer, primary_key=True),
    Column('actor', String(40), nullable=False),
    Column('action', String(32), nullable=False),
    Column('reference', String(50), nullable=False),
    Column('created_at', String(32), nullable=False))


def upgrade(engine):
    """Idempotently add columns introduced after the initial schema snapshot.

    The demo deployment keeps its volume across rebuilds, and ``create_all``
    never alters an existing table, so the batch-input digest needs an explicit
    upgrade for databases created before this version.
    """
    with engine.begin() as conn:
        if engine.dialect.name == 'postgresql':
            conn.execute(text('ALTER TABLE business_cases ADD COLUMN IF NOT EXISTS input_digest VARCHAR(64)'))
            conn.execute(text(
                'CREATE UNIQUE INDEX IF NOT EXISTS ux_business_cases_input_digest ON business_cases (input_digest)'))
        elif engine.dialect.name == 'mysql':
            column = conn.execute(text("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = 'business_cases' AND column_name = 'input_digest'
            """)).scalar_one()
            if not column:
                conn.execute(text('ALTER TABLE business_cases ADD COLUMN input_digest VARCHAR(64)'))
            index = conn.execute(text("""
                SELECT COUNT(*) FROM information_schema.statistics
                WHERE table_schema = DATABASE() AND table_name = 'business_cases' AND index_name = 'ux_business_cases_input_digest'
            """)).scalar_one()
            if not index:
                conn.execute(text('CREATE UNIQUE INDEX ux_business_cases_input_digest ON business_cases (input_digest)'))
        _backfill_input_digests(conn)


def _backfill_input_digests(conn):
    """Fill the digest for batches created before the column existed.

    Older rows keep a NULL digest and would otherwise never be recognized as the
    same batch.  Identical historical payloads are left to the first row so the
    unique index is never violated.
    """
    import hashlib
    import json

    seen = {row[0] for row in conn.execute(sa_text(
        'SELECT input_digest FROM business_cases WHERE input_digest IS NOT NULL'))}
    rows = conn.execute(sa_text(
        'SELECT id, payload FROM business_cases WHERE input_digest IS NULL ORDER BY id')).mappings().all()
    for row in rows:
        payload = row['payload']
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        try:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True,
                                 allow_nan=False).encode('utf-8')
        except (TypeError, ValueError):
            continue
        digest = hashlib.sha256(encoded).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        conn.execute(sa_text(
            'UPDATE business_cases SET input_digest = :digest WHERE id = :id AND input_digest IS NULL'),
            {'digest': digest, 'id': row['id']})
