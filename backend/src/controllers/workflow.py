import csv
import io

from starlette.responses import Response

from src.config.settings import engine
from src.models.tables import evidence, cases, runs
from src.services.workflow import found
from src.utils.encoding import canonical


def download(identity):
    with engine.connect() as conn:
        data = found(conn, evidence, identity)
    return Response(canonical(data), media_type='application/json', headers={
        'Content-Disposition': f'attachment; filename="evidence-{identity}.json"'})


def _cell(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return '是' if value else '否'
    return str(value)


def export_csv(identity):
    """Flatten the adopted records, isolation reasons and zone summary to CSV."""
    with engine.connect() as conn:
        data = found(conn, evidence, identity)
        run = found(conn, runs, data['run_id'])
        case = found(conn, cases, run['case_id'])
    report = data['report']
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['批次', case['title']])
    writer.writerow(['结论', report.get('summary', '')])
    writer.writerow(['指标', '数值'])
    for label, value in report.get('metrics', {}).items():
        writer.writerow([label, _cell(value)])
    for section in report.get('sections', []):
        writer.writerow([])
        writer.writerow([section.get('title', '')])
        rows = section.get('rows') or []
        headers = list(dict.fromkeys(key for row in rows for key in row))
        writer.writerow(headers)
        for row in rows:
            writer.writerow([_cell(row.get(key)) for key in headers])
    content = '﻿' + buffer.getvalue()
    return Response(content, media_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="merge-result-{identity}.csv"'})
