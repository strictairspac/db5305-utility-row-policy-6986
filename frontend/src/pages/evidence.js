import {heading, panel} from '../components/common/heading.js';
import {records} from '../components/common/records.js';
import {topic} from '../constants/project.js';
import {date, el, esc} from '../utils/dom.js';
import {submit} from '../hooks/submit.js';

const EMPTY = {
  '每单采用记录': '本批没有可核对的任务。',
  '隔离记录': '本批没有隔离记录。',
  '未采用记录': '本批没有被舍弃的候选记录。',
  '区域汇总': '本批没有涉及作业区域。',
};

const TASK_COLUMNS = [
  ['任务编号', '任务编号'], ['计划日', '计划日'], ['作业区域', '作业区域'],
  ['状态', '状态'], ['采用回传流水号', '回传流水号'], ['采用依据', '采用依据'],
  ['抄表时间', '抄表时间'], ['回传时间', '回传时间'], ['表计读数', '表计读数'],
  ['已核验', '已核验'], ['候选记录数', '候选数'], ['隔离记录数', '隔离数'],
];

function stateBadge(status) {
  return `<span class="status" data-state="${status === '已采用' ? 'adopted' : 'pending'}">${esc(status)}</span>`;
}

function readableCell(value) {
  const timestamp = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$/.test(value);
  if (!timestamp) return esc(value);
  return `<time class="timestamp" datetime="${esc(value)}">${esc(value.slice(0, 10))}<small>${esc(value.slice(11))}</small></time>`;
}

function sectionRows(title, rows) {
  if (!rows.length) return `<p class="empty">${esc(EMPTY[title] || '当前没有记录。')}</p>`;
  if (title === '每单采用记录') {
    return records(TASK_COLUMNS.map(([, label]) => label), rows.map(row => TASK_COLUMNS.map(([key]) =>
      key === '状态' ? stateBadge(row[key]) : esc(row[key]))));
  }
  if (title === '隔离记录') {
    const keys = Object.keys(rows[0]);
    return records(keys, rows.map(row => keys.map(key => key === '隔离原因'
      ? `<span class="error-inline">${esc(row[key])}</span>` : readableCell(row[key]))));
  }
  const keys = Object.keys(rows[0]);
  return records(keys, rows.map(row => keys.map(key => readableCell(row[key]))));
}

export function evidencePage(state, identity) {
  const current = state.evidence.find(row=>row.id===identity) || state.evidence[0];
  if (!current) return heading(topic.modules[3]) + '<p class="empty">归并完成后，可查看、复核和导出结果。</p>';
  const {report} = current;
  const metrics = Object.entries(report.metrics).map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('');
  const sections = report.sections.map(x=>panel(x.title, sectionRows(x.title, x.rows))).join('');
  const review = current.reviewer ? `<p data-testid="reviewed">${esc(current.reviewer)} · ${esc(date(current.reviewed_at))}</p><p>${esc(current.review_note)}</p>`
    : state.session.role==='reviewer'?'<form id="review-form"><label>复核意见<textarea name="note" maxlength="500" required></textarea></label><button>签署复核</button></form>'
    : '<p class="note">待复核员签署</p>';
  const runCase = new Map(state.runs.map(row=>[row.id, row.case_id]));
  const caseId = runCase.get(current.run_id);
  const history = state.evidence
    .filter(row=>runCase.get(row.run_id)===caseId)
    .sort((a,b)=>b.id-a.id)
    .map(row=>[`<a href="#evidence/${row.id}">执行 ${row.run_id}</a>`, esc(row.report.summary), esc(row.reviewer || '待复核')]);
  return `${heading(topic.modules[3], report.summary, `<button id="download" data-id="${current.id}">导出 JSON</button><button class="secondary" id="export" data-id="${current.id}">导出 CSV</button>`)}
    <div class="spread evidence-top"><dl class="metrics">${metrics}</dl><aside class="brief"><h2>复核签署</h2>${review}<details><summary>结果校验码</summary><code class="digest">${esc(current.digest)}</code></details></aside></div>
    ${sections}<details><summary>本批次历史结果</summary>${records(['执行','结论','复核'], history)}</details>`;
}

function saveBlob(response, filename) {
  return response.blob().then(blob=>{
    const url = URL.createObjectURL(blob);
    const link = el('a');
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(()=>URL.revokeObjectURL(url), 1000);
  });
}

export function bindEvidence(api, update, fail) {
  const download = document.getElementById('download');
  const exportCsv = document.getElementById('export');
  if (!download && !exportCsv) return;
  const identity = (download || exportCsv).dataset.id;
  download?.addEventListener('click', async()=>{
    try {
      await saveBlob(await api(`/evidence/${identity}/download`, {response:true}), `evidence-${identity}.json`);
    } catch (error) { fail(error); }
  });
  exportCsv?.addEventListener('click', async()=>{
    try {
      await saveBlob(await api(`/evidence/${identity}/export`, {response:true}), `merge-result-${identity}.csv`);
    } catch (error) { fail(error); }
  });
  submit('review-form', async data=>{
    await api(`/evidence/${identity}/review`, {method:'POST', data:{note:data.get('note')}});
    await update();
  }, fail);
}
