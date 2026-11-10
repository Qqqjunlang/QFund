/* 数据管理界面。写入、冲突判断和比例换算由服务端负责。 */
'use strict';
function setupFundManagement({get, post, funds, refresh, filterChanged}) {
  const byId = id => document.getElementById(id);
  const dialog = byId('management-dialog');
  const body = byId('management-body');
  const message = byId('management-message');
  const groupFilter = byId('group-filter');
  const groupFilters = [groupFilter, byId('collect-group-filter'), byId('analysis-group-filter')];
  const origins = {jisilu: '集思录', manual: '手动', import: '文件导入', derived: '按有效字段重算'};
  let groups = [];
  let busy = false;
  let returnFocus;
  let fieldId = 0;

  function element(tag, text) {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function button(text, click, primary = false) {
    const item = element('button', text);
    item.type = 'button';
    item.className = primary ? 'primary' : 'secondary';
    item.addEventListener('click', click);
    return item;
  }
  function row(...items) {
    const container = element('div');
    container.className = 'button-row';
    container.append(...items);
    return container;
  }
  function input(type = 'text', value = '') {
    const item = element('input');
    item.type = type;
    item.value = value;
    return item;
  }
  function select(options) {
    const item = element('select');
    for (const [value, label] of options) item.add(new Option(label, value));
    return item;
  }
  function field(label, control) {
    const item = element('div');
    item.className = 'management-field';
    const caption = element('label', label);
    control.id = 'management-field-' + (++fieldId);
    caption.htmlFor = control.id;
    item.append(caption, control);
    return item;
  }
  function fields(...items) {
    const item = element('div');
    item.className = 'management-fields';
    item.append(...items);
    return item;
  }
  function setMessage(text = '', error = false) {
    message.textContent = text;
    message.hidden = !text;
    message.className = error ? 'history-error' : 'helper';
  }
  function report(text) {
    const item = byId('management-feedback');
    item.textContent = text;
    item.hidden = false;
  }
  function open(title, wide = false) {
    if (busy) return false;
    dialog.classList.toggle('management-records-view', wide);
    body.replaceChildren();
    setMessage();
    byId('management-title').textContent = title;
    if (!dialog.open) {
      returnFocus = document.activeElement;
      dialog.showModal();
    }
    return true;
  }
  async function run(operation) {
    if (busy) return;
    busy = true;
    const controls = [...dialog.querySelectorAll('button,input,select')].filter(item => !item.disabled);
    for (const control of controls) control.disabled = true;
    dialog.setAttribute('aria-busy', 'true');
    setMessage('正在处理…');
    try {
      await operation();
      if (message.textContent === '正在处理…') setMessage();
    } catch (error) {
      setMessage(error.message, true);
    } finally {
      for (const control of controls) control.disabled = false;
      dialog.removeAttribute('aria-busy');
      busy = false;
    }
  }
  byId('close-management').addEventListener('click', () => { if (!busy) dialog.close(); });
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', () => {
    body.replaceChildren();
    if (returnFocus?.isConnected && !returnFocus.disabled) returnFocus.focus();
    else byId('open-trash').focus();
  });

  async function refreshGroups() {
    groups = await get('/api/groups');
    for (const filter of groupFilters) {
      const current = filter.value;
      filter.replaceChildren(new Option('全部分组', ''));
      for (const group of groups) filter.add(new Option(group.name, group.id));
      filter.value = groups.some(group => String(group.id) === current) ? current : '';
    }
    filterChanged();
  }
  for (const filter of groupFilters) filter.addEventListener('change', filterChanged);
  async function finished(text, code) {
    dialog.close();
    report(text);
    await refresh(code);
  }

  function openImport() {
    if (!open('导入 Excel／CSV')) return;
    void run(async () => {
      const schema = await get('/api/import/fields');
      const code = input();
      code.maxLength = 6;
      code.inputMode = 'numeric';
      const name = input();
      name.maxLength = 80;
      const file = input('file');
      file.accept = '.xlsx,.csv';
      const header = input('number', '1');
      header.min = 0;
      header.max = 100;
      const encoding = select([['utf-8-sig', 'UTF-8'], ['gb18030', 'GB18030']]);
      const ratio = select([['percent', '百分数，例如 12.5 表示 12.5%'], ['fraction', '小数，例如 0.125 表示 12.5%']]);
      const sheet = select([['', '第一张工作表']]);
      const mappingArea = element('div');
      const previewArea = element('div');
      const commitArea = element('div');
      let mappings = null;
      let lastPreview = null;
      let activeToken = null;
      function invalidate(resetMapping = false) {
        activeToken = null;
        previewArea.replaceChildren();
        commitArea.replaceChildren();
        if (resetMapping) {
          mappings = null;
          lastPreview = null;
          mappingArea.replaceChildren();
        }
      }
      function showMapping(result, initial) {
        const controls = fields();
        mappings = new Map();
        for (const definition of schema.fields) {
          const control = select([['', '不导入'], ...result.headers.map((title, index) =>
            [String(index + 1), title ? `${index + 1} · ${title}` : `第 ${index + 1} 列`])]);
          control.value = String(initial[definition.key] ?? '');
          control.addEventListener('change', () => invalidate());
          mappings.set(definition.key, control);
          controls.append(field(definition.label, control));
        }
        const preset = button('套用集思录 QDII 十列', () => {
          const columns = lastPreview.presets.jisilu_qdii;
          for (const [key, control] of mappings) control.value = String(columns[key] ?? '');
          invalidate();
        });
        preset.disabled = result.headers.length < 10;
        mappingArea.replaceChildren(element('h3', '列映射'), controls, preset);
      }
      for (const control of [code, name, ratio]) control.addEventListener('input', () => invalidate());
      for (const control of [header, sheet, encoding]) control.addEventListener('input', () => invalidate(true));
      file.addEventListener('change', () => {
        sheet.replaceChildren(new Option('第一张工作表', ''));
        invalidate(true);
      });
      const preview = button('预览导入', () => void run(async () => {
        invalidate();
        if (!/^[0-9]{6}$/.test(code.value.trim()) || !name.value.trim()) throw new Error('请填写六位基金代码和基金名称。');
        const upload = file.files[0];
        if (!upload || upload.size > schema.max_bytes || upload.size === 0) throw new Error('请选择 10 MiB 以内的非空表格。');
        if (!header.value) throw new Error('请填写标题行。无标题时填 0。');
        const content = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onerror = () => reject(new Error('无法读取所选文件。'));
          reader.onload = () => resolve(reader.result.split(',')[1]);
          reader.readAsDataURL(upload);
        });
        const mapping = mappings ? Object.fromEntries([...mappings].filter(([, control]) => control.value)
          .map(([key, control]) => [key, Number(control.value)])) : null;
        const result = await post('/api/import/preview', {
          code: code.value.trim(), name: name.value.trim(), filename: upload.name, content,
          header_row: Number(header.value), sheet: sheet.value || null, encoding: encoding.value, ratio_unit: ratio.value, mapping
        });
        lastPreview = result;
        if (result.options.sheets) {
          sheet.replaceChildren(...result.options.sheets.map(title => new Option(title, title)));
          sheet.value = result.options.sheet;
        }
        showMapping(result, result.mapping);
        previewArea.append(element('p', `${result.code} · ${result.name}。新增 ${result.inserted} 条，已有日期跳过 ${result.skipped_existing} 条，受删除限制跳过 ${result.skipped_deleted} 条。`));
        if (result.sample.length) {
          const wrap = element('div');
          wrap.className = 'table-scroll';
          const table = element('table');
          const columns = schema.fields.filter(({key}) => key in result.mapping);
          const head = table.createTHead().insertRow();
          for (const title of ['文件行', ...columns.map(({label}) => label), '处理']) {
            const th = element('th', title);
            th.scope = 'col';
            head.append(th);
          }
          const samples = table.createTBody();
          const actions = {insert: '新增', existing: '已有日期', deleted: '受删除限制'};
          for (const item of result.sample) {
            const line = samples.insertRow();
            for (const value of [item.row, ...columns.map(({key}) => item.values[key]), actions[item.action]]) {
              line.insertCell().textContent = value ?? '—';
            }
          }
          wrap.append(table);
          previewArea.append(element('p', '比例样例以小数显示，例如 0.125 表示 12.5%。'), wrap);
        }
        if (result.error_count) {
          const errors = element('ul');
          for (const error of result.errors) {
            const label = schema.fields.find(({key}) => key === error.field)?.label;
            errors.append(element('li', `${error.row ? '第 ' + error.row + ' 行 ' : ''}${label ? label + ' ' : ''}${error.message}`));
          }
          previewArea.append(element('p', `发现 ${result.error_count} 处错误，修正后重新预览。`), errors);
          return;
        }
        if (!result.token) {
          previewArea.append(element('p', '基金正在进行回收或恢复，请先在回收站处理。'));
          return;
        }
        activeToken = result.token;
        if (result.requires_restore) commitArea.append(element('p', '基金在回收站。恢复后可重新分组或加入自动更新名单。'));
        commitArea.append(button(result.requires_restore ? '确认恢复并导入' : '确认导入', () => void run(async () => {
          const saved = await post('/api/import/commit', {token: activeToken, restore: result.requires_restore});
          invalidate();
          await finished(`导入完成。新增 ${saved.inserted} 条，跳过 ${saved.skipped_existing + saved.skipped_deleted} 条。`, saved.code);
        }), true));
      }), true);
      body.append(element('p', '每次导入一只基金。选好文件后预览，可调整列映射再预览。'),
        fields(field('基金代码', code), field('基金名称', name), field('Excel 或 CSV 文件', file),
          field('标题行（无标题填 0）', header), field('Excel 工作表', sheet), field('CSV 编码', encoding), field('比例单位', ratio)),
        preview, mappingArea, previewArea, commitArea);
      code.focus();
    });
  }

  function openManual(code, record, metadata, definitions) {
    if (!code || !open(record ? '编辑单日记录' : '手动维护')) return;
    void run(async () => {
      const state = await get('/api/maintenance?'+new URLSearchParams({code}));
      if (state.state !== 'active' || (record && state.revision !== metadata.revision)) {
        throw new Error('基金数据已变化，请刷新列表后重试。');
      }
      const schema = definitions || (await get('/api/import/fields')).fields;
      const day = input('date', record?.price_date || '');
      if (record) day.readOnly = true;
      const editable = schema.filter(column => column.key !== 'price_date');
      const controls = fields(field('价格日期', day));
      const inputs = new Map();
      const locked = new Set(metadata?.protected_fields);
      for (const {key, label} of editable) {
        const value = record?.[key] ?? '';
        const control = input(key === 'nav_date' ? 'date' : 'text', value);
        inputs.set(key, control);
        controls.append(field(locked.has(key) ? `${label}（已保护）` : label, control));
      }
      async function mutate(action, targetDay, changes, status) {
        await post('/api/maintenance/row', {code, day: targetDay, action, changes, expected_revision: state.revision});
        await finished(status, code);
      }
      const save = button(record ? '保存修改' : '新增单日记录', () => void run(async () => {
        if (!day.value) throw new Error('请选择价格日期。');
        const changes = {};
        for (const [key, control] of inputs) {
          const value = control.value.trim();
          if (value !== (record?.[key] ?? '')) changes[key] = value || null;
        }
        if (!Object.keys(changes).length) throw new Error('请填写或修改至少一个字段。');
        await mutate(record ? 'edit' : 'add', day.value, changes, record ? '已保存手动修改。' : '已新增单日记录。');
      }), true);
      body.append(element('p', `基金 ${code}。比例按百分数输入，例如 12.5 表示 12.5%。修改的字段会受到保护，清空后按缺失保存。`), controls, row(save));
      if (record) {
        body.append(element('p', `记录来源 ${origins[metadata.record_origin] || metadata.record_origin}。溢价来源 ${origins[metadata.premium_origin] || metadata.premium_origin}。`));
        const actions = row(button('删除这一天', () => {
          if (confirm(`删除 ${day.value} 的本地记录？解除日期限制后才可恢复或补回。`)) {
            void run(() => mutate('delete', day.value, {}, '已删除单日记录并保留日期限制。'));
          }
        }));
        if (locked.size) {
          const protectedField = select(editable.filter(({key}) => locked.has(key)).map(({key, label}) => [key, label]));
          body.append(fields(field('要解除保护的字段', protectedField)));
          actions.append(button('解除所选字段保护', () => {
            if (confirm('解除所选字段的保护？将显示当前可用的基础值。')) {
              void run(() => mutate('release', day.value, {[protectedField.value]: null}, '已解除所选字段保护。'));
            }
          }));
        }
        body.append(actions);
      } else {
        const deleted = select([['', '选择日期'], ...state.deleted_dates.map(value => [value, value])]);
        const unblock = button('解除日期限制', () => {
          if (deleted.value && confirm(`解除 ${deleted.value} 的删除限制？`)) {
            void run(() => mutate('restore', deleted.value, {}, '已解除该日期的删除限制。'));
          }
        });
        if (state.deleted_dates.length) body.append(fields(field('已删除日期', deleted)), unblock);
      }
    });
  }

  function moveToTrash(code, revision) {
    if (!confirm(`将 ${code} 移入回收站？它将移出自选分组和自动更新名单，已有导出文件会保留。`)) return;
    void run(async () => {
      await post('/api/maintenance/fund', {code, action: 'trash', expected_revision: revision});
      await finished(`已将 ${code} 移入回收站。`);
    });
  }

  function showFundRecords(code) {
    const fund = funds().find(item => item.code === code);
    if (!fund || !open('维护基金数据', true)) return;
    const start = input('date');
    const end = input('date');
    async function render(page = 1) {
      const query = new URLSearchParams({code, start: start.value, end: end.value, page});
      const result = await get('/api/history?' + query);
      const state = await get('/api/maintenance?' + new URLSearchParams({code}));
      body.replaceChildren(element('h3', `${fund.code} · ${fund.name}`),
        row(button('新增单日记录', () => openManual(code), true),
          button('将基金移入回收站', () => moveToTrash(code, state.revision))),
        fields(field('开始日期', start), field('结束日期', end)),
        row(button('筛选日期', () => void run(() => render())),
          button('全部日期', () => {
            start.value = ''; end.value = '';
            void run(() => render());
          })));
      const table = element('table');
      const heading = element('tr');
      for (const title of [...result.columns.map(column => column.label), '来源与保护', '操作']) {
        heading.append(element('th', title));
      }
      const head = element('thead'); head.append(heading); table.append(head);
      const rows = element('tbody');
      for (const [index, record] of result.rows.entries()) {
        const meta = result.row_meta[index];
        const line = element('tr');
        for (const column of result.columns) {
          line.append(element('td', record[column.key] ?? '—'));
        }
        line.append(element('td', describe(meta)));
        const actions = element('td');
        actions.append(button('编辑', () => openManual(code, record, meta, result.columns)));
        line.append(actions); rows.append(line);
      }
      if (!result.rows.length) {
        const line = element('tr'), cell = element('td', '该区间没有记录。');
        cell.colSpan = result.columns.length + 2; line.append(cell); rows.append(line);
      }
      table.append(rows);
      const scroll = element('div'); scroll.className = 'table-scroll';
      scroll.tabIndex = 0; scroll.setAttribute('aria-label', '待维护的基金记录');
      scroll.append(table);
      body.append(element('p', '可横向滚动查看全部字段，点击“编辑”维护该日记录。'), scroll);
      const previous = button('上一页', () => void run(() => render(result.page - 1)));
      const next = button('下一页', () => void run(() => render(result.page + 1)));
      previous.disabled = result.page <= 1; next.disabled = result.page >= result.pages;
      body.append(row(element('span', `第 ${result.page} / ${result.pages} 页 · 共 ${result.matched} 条`), previous, next));
    }
    void run(() => render());
  }

  function describe(meta) {
    return `${origins[meta.record_origin] || meta.record_origin}${meta.protected_fields.length ? ' · ' + meta.protected_fields.length + ' 项保护' : ''}`;
  }

  function showTrash() {
    if (!open('基金回收站')) return;
    void run(async () => {
      async function render() {
        const entries = await get('/api/trash');
        body.replaceChildren(element('p', '恢复会保留行情、字段保护和删除限制。你可以重新分组或加入自动更新名单。'));
        if (!entries.length) body.append(element('p', '回收站为空。'));
        for (const entry of entries) {
          const item = element('div');
          item.className = 'management-item';
          item.append(element('p', `${entry.code} · ${entry.name}`));
          if (entry.state === 'trashed') {
            item.append(button('恢复基金', () => {
              if (!confirm(`恢复 ${entry.code} · ${entry.name}？`)) return;
              void run(async () => {
                await post('/api/maintenance/fund', {code: entry.code, action: 'restore', expected_revision: entry.revision});
                await finished(`已恢复 ${entry.code}。`, entry.code);
              });
            }, true));
          } else {
            item.append(element('p', entry.state === 'trashing' ? '回收尚未完成。' : '恢复尚未完成。'),
              button('重试未完成操作', () => void run(async () => {
                await post('/api/maintenance/recover', {});
                await refresh();
                await render();
              })));
          }
          body.append(item);
        }
      }
      await render();
    });
  }

  function showGroups() {
    if (!open('自选分组')) return;
    void run(async () => {
      async function render() {
        await refreshGroups();
        const name = input();
        name.maxLength = 80;
        body.replaceChildren(element('p', '一只基金可加入多个分组。创建分组后，勾选基金并保存成员。'),
          fields(field('新分组名称', name)),
          button('创建分组', () => void run(async () => {
            await post('/api/groups/change', {action: 'create', name: name.value});
            await render();
            setMessage('已创建分组。');
          }), true));
        for (const group of groups) {
          const section = element('details');
          section.className = 'management-item';
          section.append(element('summary', `${group.name} · ${group.codes.length} 只基金`));
          const groupName = input('text', group.name);
          groupName.maxLength = 80;
          const base = {group_id: group.id, expected_revision: group.revision};
          async function change(action, options, label) {
            await post('/api/groups/change', {action, ...base, ...options});
            await render();
            setMessage(label);
          }
          const memberField = element('fieldset');
          memberField.append(element('legend', '分组成员'));
          const members = element('div');
          members.className = 'management-members';
          const localFunds = funds();
          const choices = new Map();
          for (const fund of localFunds) {
            const check = input('checkbox');
            check.checked = group.codes.includes(fund.code);
            choices.set(fund.code, check);
            const label = element('label');
            label.append(check, document.createTextNode(`${fund.code} · ${fund.name}`));
            members.append(label);
          }
          if (!localFunds.length) members.append(element('p', '暂无可加入的本地基金。'));
          memberField.append(members);
          section.append(fields(field('分组名称', groupName)), memberField, row(
            button('保存成员', () => void run(() => {
              const codes = [...group.codes.filter(code => !choices.has(code)),
                ...[...choices].filter(([, check]) => check.checked).map(([code]) => code)];
              return change('members', {codes}, '已保存分组成员。');
            }), true),
            button('改名', () => void run(() => change('rename', {name: groupName.value}, '已修改分组名称。'))),
            button('删除分组', () => {
              if (confirm(`删除分组“${group.name}”？基金行情和自动更新设置会保留。`)) {
                void run(() => change('delete', {}, '已删除分组。'));
              }
            })
          ));
          body.append(section);
        }
      }
      await render();
    });
  }

  byId('open-import').addEventListener('click', openImport);
  byId('open-groups').addEventListener('click', showGroups);
  byId('open-trash').addEventListener('click', showTrash);
  return {
    refreshGroups,
    groupNames: code => groups.filter(group => group.codes.includes(code)).map(group => group.name),
    records: showFundRecords,
    inFilter: (code, groupId = groupFilter.value) => !groupId || groups.some(group => String(group.id) === groupId && group.codes.includes(code)),
    edit: openManual,
    describe
  };
}
