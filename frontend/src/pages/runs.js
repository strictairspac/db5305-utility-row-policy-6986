import {heading, panel} from '../components/common/heading.js';
import {records} from '../components/common/records.js';
import {badge} from '../components/common/status.js';
import {statusLabels} from '../constants/workflow.js';
import {topic} from '../constants/project.js';
import {date, esc} from '../utils/dom.js';

export function runsPage(state, identity) {
  const selected = state.runs.find(row=>row.id===identity) || state.runs[0];
  const cases = new Map(state.cases.map(row=>[row.id, row.title]));
  const byCase = new Map();
  state.runs.forEach(row=>{ byCase.set(row.case_id, (byCase.get(row.case_id) || 0) + 1); });
  const rows = state.runs.map(row=>[`<a href="#runs/${row.id}">${esc(cases.get(row.case_id) || row.case_id)}</a>`, badge(row.status), esc(row.owner), esc(date(row.created_at))]);
  const result = state.evidence.find(row=>row.run_id===selected?.id);
  const active = selected && ['queued','running'].includes(selected.status);
  const reused = selected && byCase.get(selected.case_id) > 1 ? '<p class="note">该批次已有多次执行，本次复用最新成果，不新增批次。</p>' : '';
  const detail = selected ? `<h2>${esc(cases.get(selected.case_id) || selected.case_id)}</h2>
    <p data-testid="run-status">${badge(selected.status)}</p>
    <dl><dt>启动时间</dt><dd>${esc(date(selected.started_at))}</dd><dt>完成时间</dt><dd>${esc(date(selected.finished_at))}</dd></dl>
    ${selected.error ? `<p class="error">${esc(selected.error)}</p>` : ''}
    ${active?'<p class="note">后台处理中，进度会自动刷新。</p>':''}
    ${reused}
    ${result?`<a class="button" href="#evidence/${result.id}">查看归并结果</a>`:''}` : '<h2>执行记录</h2><p>从批次创建一条归并执行后，可在此查看进度。</p>';
  const audit = state.audit.filter(x=>selected && x.reference===String(selected.id) && ['enqueue', 'complete', 'failed'].includes(x.action));
  return `${heading(topic.modules[2], `${state.spec.entity_labels[1]}与后台处理进度`, '<button class="secondary" type="button" id="refresh">刷新记录</button>')}
    <div class="spread"><section>${records(['批次','状态','经办','提交时间'], rows, '暂无归并执行记录。')}</section><aside class="brief">${detail}</aside></div>
    ${audit.length?panel('操作记录', records(['操作者','操作','时间'], audit.map(x=>[esc(x.actor),esc(statusLabels[x.action]||'提交执行'),esc(date(x.created_at))]))):''}`;
}
