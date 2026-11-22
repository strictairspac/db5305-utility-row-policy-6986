import {heading, panel} from '../components/common/heading.js';
import {entries, records} from '../components/common/records.js';
import {date, esc} from '../utils/dom.js';
import {badge} from '../components/common/status.js';
import {topic} from '../constants/project.js';

function latestFor(state, caseId) {
  const runs = state.runs.filter(row=>row.case_id===caseId).sort((a,b)=>b.id-a.id);
  // Prefer the newest run that produced evidence, so a later failed retry
  // never hides the last good version of the batch.
  const run = runs.find(row=>state.evidence.some(item=>item.run_id===row.id)) || runs[0];
  const result = run ? state.evidence.find(row=>row.run_id===run.id) : null;
  return {run, result};
}

export function casesPage(state, identity) {
  const {spec, session, cases, runs} = state;
  const selected = cases.find(row=>row.id===identity) || cases[0];
  const operator = session.role==='operator';
  const rows = cases.map(row=>{
    const {run, result} = latestFor(state, row.id);
    const link = `<a href="#cases/${row.id}" ${row.id===selected?.id?'aria-current="true"':''}>${esc(row.title)}</a>`;
    return [link, esc(row.input_digest ? row.input_digest.slice(0,12) : '—'),
      result ? `<a href="#evidence/${result.id}">${esc(result.report.summary)}</a>` : (run ? badge(run.status) : '未执行'),
      esc(row.author), esc(date(row.created_at))];
  });
  const instruction = selected ? `<h2>${esc(selected.title)}</h2><p>${esc(spec.guidance)}</p>` : '';
  const input = selected ? Object.entries(selected.payload).map(([key, value])=>{
    const label = spec.field_labels[key] || key;
    const rows = Array.isArray(value) && value.every(x=>x && typeof x === 'object' && !Array.isArray(x));
    return panel(label, rows ? entries(value.slice(0,10), spec.field_labels) + (value.length>10?`<p class="note">显示前 10 条，共 ${value.length} 条。</p>`:'') : `<pre>${esc(value)}</pre>`);
  }).join('') : '';
  return `${heading(topic.modules[0], spec.summary, operator?'<a class="button" href="#editor">录入新批次</a>':'')}
    <div class="spread"><section aria-label="批次目录">${records([spec.entity_labels[0], '输入指纹', '最新结果', '经办', '建立时间'], rows, '尚未建立批次。经办员可以录入第一批现场回传。')}</section>
    <aside class="brief">${instruction || `<h2>${esc(spec.customer)}</h2>`}${selected?`<p class="note">该批次记录 ${runs.filter(row=>row.case_id===selected.id).length} 次执行。相同输入再次提交会复用原批次。</p>`:''}${selected&&operator?`<a href="#editor/${selected.id}">基于该批次新建</a>`:''}</aside></div>
    ${input ? `<div class="inspection">${input}</div>`:''}`;
}
