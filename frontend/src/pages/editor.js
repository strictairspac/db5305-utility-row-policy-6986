import {heading} from '../components/common/heading.js';
import {fields, readFields} from '../components/fields.js';
import {topic} from '../constants/project.js';
import {esc} from '../utils/dom.js';
import {submit} from '../hooks/submit.js';
import {go} from '../router/hash.js';
import {notice} from '../stores/notice.js';

export function editorPage(state, identity) {
  if (state.session.role!=='operator') return heading(topic.modules[1], '经办员可以录入并提交现场回传批次。');
  const original = state.cases.find(row=>row.id===identity);
  const payload = original?.payload || state.spec.sample;
  state.draft = payload;
  return `${heading(topic.modules[1], state.spec.guidance)}
    <form id="case-form"><label class="field title-field"><span>批次名称</span><input name="title" required maxlength="120" value="${esc((original?.title || state.spec.sample.batch_name) + ' · 复核')}" /></label>
    <div class="field-grid">${fields(payload, state.spec.field_labels)}</div><div class="actions form-actions"><button>保存并提交归并</button><a class="secondary" href="#cases">返回批次</a></div></form>`;
}

export function bindEditor(state, api, fail) {
  submit('case-form', async data=>{
    const payload = readFields(state.draft, state.spec.field_labels);
    const draft = await api('/cases', {method:'POST', data:{title:data.get('title'), payload}});
    try {
      const run = await api(`/cases/${draft.id}/runs`, {method:'POST'});
      notice(run.reused ? '相同批次输入已存在，已复用原批次结果。' : (draft.reused ? '相同输入已存在，已复用原批次并提交归并。' : '批次已提交归并。'));
      go(`runs/${run.id}`);
    } catch (error) {
      notice(`批次已保存。提交归并失败：${error.message}`);
      go(`cases/${draft.id}`);
    }
  }, fail);
}
