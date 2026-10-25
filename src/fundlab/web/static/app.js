/* 本地工作台：展示与交互；行情读写规则交给Python服务。 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  function syncDateAppearance(){
    for(const id of ['history-start','history-end']){
      const field=$(id);
      field.classList.toggle('date-empty',!field.value);
    }
  }
  const pages = {home:'首页',collect:'数据获取',data:'数据查询',analysis:'数据分析'};
  const store = {get(key,fallback){try{return localStorage.getItem('fundlab.'+key)||fallback;}catch{return fallback;}},set(key,value){try{localStorage.setItem('fundlab.'+key,value);}catch{/* 存储不可用时仍允许使用页面 */}}};
  let data = {funds:[],latest:null};
  let selected = store.get('fund','');
  let analysisOpened=false, activeFrame=null, analysisTimer=null, loadedAnalysisCode='';
  function dateTime(value){if(!value)return '暂无记录';const d=new Date(value);return Number.isNaN(d.valueOf())?'日期不可用':new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d);}
  function showPage(page){if(!pages[page])page='home';document.querySelectorAll('.page').forEach(el=>el.hidden=el.id!=='page-'+page);document.querySelectorAll('[data-page]').forEach(b=>{b.classList.toggle('active',b.dataset.page===page);if(b.dataset.page===page)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});$('page-name').textContent=pages[page];store.set('page',page);window.scrollTo(0,0);}
  document.querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',()=>showPage(b.dataset.page)));
  document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',()=>showPage(b.dataset.go)));
  function button(label,action){const b=document.createElement('button');b.className='text-button';b.textContent=label;b.addEventListener('click',action);return b;}
  function selectFund(code){const changed=selected!==code;selected=code;store.set('fund',code);$('analysis-fund').value=code;const fund=data.funds.find(f=>f.code===code);$('selected-data').textContent=fund?`${fund.code} · ${fund.name}`:'请选择基金';$('analyze-selected').disabled=!fund;$('open-premium').disabled=!fund;renderRows('home-funds',data.funds.slice(0,5),false);renderData();if(changed){$('history-start').value='';$('history-end').value='';applied={start:'',end:'',page:1};}syncDateAppearance();if(fund)void loadHistory(applied);else{++requestId;currentHistory=null;$('history-head').replaceChildren();$('history-body').replaceChildren();$('history-meta').textContent='';$('history-summary').textContent='暂无本地基金，请先获取数据。';$('prev-page').disabled=true;$('next-page').disabled=true;downloadButtons();}if(analysisOpened&&(changed||!fund))loadPremium();}
  function renderRows(id,funds,full){
    const body=$(id),collection=id==='collect-funds';body.replaceChildren();
    for(const fund of funds){
      const row=document.createElement('tr');row.classList.toggle('selected-row',full&&fund.code===selected);
      const name=document.createElement('td'),title=document.createElement('span'),code=document.createElement('span');
      title.className='fund-name';title.textContent=fund.name;code.className='fund-code';code.textContent=fund.code;name.append(title,code);row.append(name);
      const values=full?[fund.category,fund.records+' 条',`${fund.start||'—'} 至 ${fund.end||'—'}`,dateTime(fund.fetched_at)]:[fund.records+' 条',`${fund.start||'—'} 至 ${fund.end||'—'}`];
      for(const value of values){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}
      const actions=document.createElement('td');actions.className='actions';
      actions.append(button(full?'查看明细':'查看数据',()=>{selectFund(fund.code);showPage('data');if(full||collection)$('history-panel').scrollIntoView({block:'start'});}));
      if(collection){
        const update=button('选择更新',()=>{
          if(collectSubmitting||collectUncertain||activeTask(collectTask))return;
          ++collectRevision;$('fund-query').value=fund.code;$('collect-error').hidden=true;
          renderCandidates({matches:[fund],partial:false,message:'已选择本地基金；点击“获取所选基金数据”后读取源站当前数据。'});
          $('collect-candidates').scrollIntoView({block:'start'});
        });
        update.classList.add('collect-select-fund');update.disabled=!!(collectSubmitting||collectUncertain||activeTask(collectTask));actions.append(update);
      }else actions.append(button('分析',()=>{selectFund(fund.code);openPremium();}));
      row.append(actions);body.append(row);
    }
    if(!funds.length){const cell=body.insertRow().insertCell();cell.colSpan=full?6:4;cell.textContent=data.error?'本地数据读取失败，请刷新重试。':collection?'还没有本地基金数据，可在上方查找并获取第一只基金。':'暂无匹配基金，可以从“数据获取”开始。';}
  }
  function renderData(){const q=$('filter').value.trim().toLowerCase();renderRows('data-funds',data.funds.filter(f=>(f.code+' '+f.name).toLowerCase().includes(q)),true);}
  function renderOverview(){
  $('analysis-fund').replaceChildren();$('analysis-fund').disabled=false;
  for(const fund of data.funds){const option=document.createElement('option');option.value=fund.code;option.textContent=`${fund.code} · ${fund.name}`;$('analysis-fund').append(option);}
  if(!data.funds.length){const option=document.createElement('option');option.textContent='暂无本地基金，请先获取数据';$('analysis-fund').append(option);$('analysis-fund').disabled=true;}
  $('fund-count').textContent=data.error?'—':data.funds.length;$('list-count').textContent=data.funds.length;
  $('collect-local-count').textContent=data.error?'—':data.funds.length+' 只';renderRows('collect-funds',data.funds,false);
  $('collect-local-status').textContent=data.error?'本地数据读取失败，请刷新重试。':'已保存到本地的基金；选择更新后，确认再开始获取。';
  const statusNames={succeeded:'更新成功',failed:'更新失败',export_failed:'已保存，导出失败',saved_pending_export:'已保存，待导出',running:'更新中'};
  $('latest-status').className='latest-status';
  if(data.latest){const run=data.latest;const result=run.result||{};const fund=data.funds.find(f=>f.code===run.code);$('latest-status').textContent=statusNames[run.status]||'状态待确认';$('latest-status').classList.add(run.status==='succeeded'?'status-success':'status-other');$('latest-brief').textContent=`${run.code} · ${fund?.name||result.name||'基金'} · ${dateTime(run.completed_at||run.started_at)}`;$('latest-detail').textContent=`${run.code} ${fund?.name||result.name||''} · ${statusNames[run.status]||run.status} · ${dateTime(run.completed_at||run.started_at)}${result.inserted!==undefined?` · 新增 ${result.inserted} 条，修订 ${result.updated||0} 条`:''}`;}else{$('latest-status').textContent=data.error?'读取失败':'暂无更新';$('latest-brief').textContent='获取基金数据后，更新结果将显示在这里。';}
  $('snapshot-time').textContent=dateTime(data.generated_at);
  }
  $('analysis-fund').addEventListener('change',e=>selectFund(e.target.value));$('filter').addEventListener('input',renderData);$('analyze-selected').addEventListener('click',openPremium);
  function clearPremium(){clearTimeout(analysisTimer);analysisTimer=null;activeFrame=null;loadedAnalysisCode='';$('analysis-frame-host').replaceChildren();}
  function analysisFailure(message){clearTimeout(analysisTimer);$('analysis-view').setAttribute('aria-busy','false');$('analysis-status').textContent='未能加载分析';$('analysis-error').textContent=message;$('analysis-error').hidden=false;if(activeFrame)activeFrame.hidden=true;}
  function loadPremium(){
    clearPremium();$('analysis-error').hidden=true;$('analysis-view').setAttribute('aria-busy','true');
    const fund=data.funds.find(f=>f.code===selected);
    $('analysis-chart-title').textContent=fund?`${fund.code} ${fund.name} · 溢价率时序`:'溢价率时序';
    if(!fund){analysisFailure('暂无可分析的本地基金，请先获取数据。');return;}
    $('analysis-status').textContent=`正在读取 ${fund.code} · ${fund.name} 的本地数据…`;
    const frame=document.createElement('iframe');frame.id='premium-frame';frame.className='analysis-frame is-loading';frame.title=`${fund.code} ${fund.name} 溢价率分析`;frame.setAttribute('sandbox','allow-scripts allow-downloads allow-forms');
    loadedAnalysisCode=fund.code;activeFrame=frame;frame.src='/analysis/premium?'+new URLSearchParams({code:fund.code});
    frame.addEventListener('error',()=>{if(activeFrame===frame)analysisFailure('无法加载分析图，请确认本地服务已启动后重新加载。');});
    $('analysis-frame-host').append(frame);
    analysisTimer=setTimeout(()=>{if(activeFrame===frame)analysisFailure('分析图未能完成加载，请确认本地服务正常后点击“重新加载”。');},20000);
  }
  function openPremium(){showPage('analysis');$('analysis-tools').hidden=true;$('analysis-view').hidden=false;analysisOpened=true;if(loadedAnalysisCode!==selected||!activeFrame||!$('analysis-error').hidden)loadPremium();}
  $('open-premium').addEventListener('click',openPremium);$('reload-premium').addEventListener('click',loadPremium);
  $('analysis-back').addEventListener('click',()=>{analysisOpened=false;clearPremium();$('analysis-view').hidden=true;$('analysis-tools').hidden=false;});
  window.addEventListener('message',event=>{
    const payload=event.data;if(!activeFrame||event.source!==activeFrame.contentWindow||event.origin!=='null'||!payload||payload.type!=='fundlab-premium'||payload.code!==selected)return;
    if(payload.status==='resize'&&Number.isFinite(payload.height)){activeFrame.style.height=Math.max(400,Math.min(5000,payload.height))+'px';}
    else if(payload.status==='ready'){clearTimeout(analysisTimer);activeFrame.hidden=false;activeFrame.classList.remove('is-loading');$('analysis-view').setAttribute('aria-busy','false');$('analysis-error').hidden=true;$('analysis-status').textContent='';}
    else if(payload.status==='image-start'){$('analysis-status').textContent=`${selected} · 正在生成 PNG 图片…`;}
    else if(payload.status==='image'){
      try{if(typeof payload.image!=='string'||!payload.image.startsWith('data:image/png;base64,')||payload.image.length>40000000)throw new Error();
        const binary=atob(payload.image.split(',')[1]);const bytes=Uint8Array.from(binary,c=>c.charCodeAt(0));const url=URL.createObjectURL(new Blob([bytes],{type:'image/png'}));const link=document.createElement('a');link.href=url;link.download=selected+'_premium_timeline.png';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);$('analysis-status').textContent=`${selected} · 图片已交给浏览器下载`;
      }catch{$('analysis-status').textContent='图片保存失败，请重试。';}
    }
    else if(payload.status==='image-error'){$('analysis-status').textContent='图片生成失败，请重试。';}
    else if(payload.status==='error'){analysisFailure(typeof payload.message==='string'?payload.message:'分析图加载失败，请重新加载。');}
  });
  let applied={start:'',end:'',page:1}, currentHistory=null, requestId=0, downloadBusy=false;
  async function api(url,options={}){let response;try{response=await fetch(url,{cache:'no-store',...options});}catch{throw new Error('无法连接本地工作台，请确认服务已启动后重试。');}if(!response.ok){let message='请求未完成，请重试。',code;try{const detail=await response.json();message=detail.message||message;code=detail.code;}catch{}const error=new Error(message);error.status=response.status;error.code=code;throw error;}return response;}
  function downloadButtons(){for(const id of ['download-xlsx','download-csv'])$(id).disabled=downloadBusy||!currentHistory?.matched;}
  async function loadOverview(){
    $('refresh-data').disabled=true;$('collect-refresh-data').disabled=true;$('load-error').hidden=true;
    try{data=await (await api('/api/overview')).json();if(!data.funds.some(f=>f.code===selected))selected=data.funds[0]?.code||'';renderOverview();selectFund(selected);}
    catch(error){$('load-error').textContent=error.message;$('load-error').hidden=false;if(!data.generated_at){data.error=error.message;renderOverview();renderData();}$('collect-local-status').textContent='刷新失败：'+error.message+(data.generated_at?' 当前保留上次读取的列表。':'');}
    finally{$('refresh-data').disabled=false;$('collect-refresh-data').disabled=false;void loadWatchlist();}
  }
  async function loadHistory(query){
    const ticket=++requestId,code=selected;currentHistory=null;downloadButtons();$('history-error').hidden=true;$('download-status').hidden=true;$('history-panel').setAttribute('aria-busy','true');$('history-summary').textContent='正在读取本地历史数据…';$('history-body').replaceChildren();$('prev-page').disabled=true;$('next-page').disabled=true;
    try{const params=new URLSearchParams({code,...query});const result=await (await api('/api/history?'+params)).json();if(ticket!==requestId||code!==selected)return;
      currentHistory=result;applied={start:result.start,end:result.end,page:result.page};$('history-meta').textContent=`本地 ${result.total} 条 · ${result.local_start||'—'} 至 ${result.local_end||'—'} · 最近更新 ${dateTime(result.fetched_at)}`;$('history-summary').textContent=`当前区间共 ${result.matched} 条记录`;
      const head=document.createElement('tr');for(const column of result.columns){const th=document.createElement('th');th.textContent=column.label;th.scope='col';head.append(th);}$('history-head').replaceChildren(head);
      const body=$('history-body');for(const row of result.rows){const tr=document.createElement('tr');for(const column of result.columns){const td=document.createElement('td');td.textContent=row[column.key]??'—';tr.append(td);}body.append(tr);}if(!result.rows.length){const cell=body.insertRow().insertCell();cell.colSpan=result.columns.length;cell.textContent='该日期区间没有记录，请调整区间或点击“全部日期”。';}
      $('page-info').textContent=`第 ${result.page} / ${result.pages} 页 · 每页 50 条 · 价格日期从新到旧`;$('prev-page').disabled=result.page<=1;$('next-page').disabled=result.page>=result.pages;
    }catch(error){if(ticket!==requestId)return;$('history-error').textContent=error.message;$('history-error').hidden=false;$('history-summary').textContent='未能读取，请调整条件后重试。';$('history-meta').textContent='';$('page-info').textContent='每页 50 条 · 价格日期从新到旧';}
    finally{if(ticket===requestId){$('history-panel').setAttribute('aria-busy','false');downloadButtons();}}
  }
  $('refresh-data').addEventListener('click',loadOverview);
  $('collect-refresh-data').addEventListener('click',loadOverview);
  $('date-form').addEventListener('submit',e=>{e.preventDefault();if(selected)void loadHistory({start:$('history-start').value,end:$('history-end').value,page:1});});
  $('reset-dates').addEventListener('click',()=>{$('history-start').value='';$('history-end').value='';syncDateAppearance();if(selected)void loadHistory({start:'',end:'',page:1});});
  for(const id of ['history-start','history-end']){
    $(id).addEventListener('input',syncDateAppearance);
    $(id).addEventListener('change',syncDateAppearance);
  }
  syncDateAppearance();
  $('prev-page').addEventListener('click',()=>void loadHistory({...applied,page:applied.page-1}));$('next-page').addEventListener('click',()=>void loadHistory({...applied,page:applied.page+1}));
  async function download(format){if(!currentHistory?.matched||downloadBusy)return;const code=selected,ticket=requestId;downloadBusy=true;downloadButtons();$('download-status').hidden=false;$('download-status').textContent='正在生成当前区间文件…';
    try{const response=await api('/api/download?'+new URLSearchParams({code,format,start:applied.start,end:applied.end}));const blob=await response.blob();const name=response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1]||`${code}_history.${format}`;const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);if(ticket===requestId)$('download-status').textContent=`${code} 的${format==='csv'?' CSV':' Excel'}文件已生成，已交给浏览器下载。`;
    }catch(error){if(ticket===requestId)$('download-status').textContent=error.message;}
    finally{downloadBusy=false;downloadButtons();}
  }
  $('download-xlsx').addEventListener('click',()=>void download('xlsx'));$('download-csv').addEventListener('click',()=>void download('csv'));
  let csrfToken='',collectTask=null,collectSubmitting=false,collectPoll=null,collectChoice='',collectMatches=[],collectResultCode='',handledTask='',collectRevision=0,collectUncertain=false;
  const activeTask=job=>job&&['queued','running','waiting_login'].includes(job.status);
  function collectButtons(){const busy=collectSubmitting||collectUncertain||activeTask(collectTask);for(const id of ['collect-search','collect-login','collect-export','collect-again','fund-query'])$(id).disabled=!!busy;$('collect-start').disabled=!!busy||!collectChoice;document.querySelectorAll('.candidate-radio,.collect-select-fund').forEach(r=>r.disabled=!!busy);$('collect-cancel').hidden=!(activeTask(collectTask)&&collectTask.kind==='login');}
  async function postTask(path,body,retry=true){if(!csrfToken)csrfToken=(await (await api('/api/session')).json()).csrf_token;try{return await (await api(path,{method:'POST',headers:{'Content-Type':'application/json','X-Fundlab-CSRF':csrfToken},body:JSON.stringify(body)})).json();}catch(error){if(error.status===403&&retry){csrfToken='';return postTask(path,body,false);}throw error;}}
  let watchlistState=null,watchlistBusy=false,watchlistReady=false,scheduleSaving=false,scheduleTimeDirty=false,systemTaskState=null;
  function watchlistButtons(){
    const busy=watchlistBusy||scheduleSaving;
    $('watchlist-refresh').disabled=busy;
    $('watchlist-fund').disabled=busy||!watchlistReady||!$('watchlist-fund').value;
    $('watchlist-add').disabled=busy||!watchlistReady||!$('watchlist-fund').value;
    document.querySelectorAll('.watchlist-action').forEach(b=>b.disabled=busy||!watchlistReady||b.dataset.unavailable==='true');
    $('schedule-time').disabled=busy||!watchlistReady;
    $('schedule-save').disabled=busy||!watchlistReady||!scheduleTimeDirty;
    $('scheduler-install').disabled=busy||!watchlistReady||!!(systemTaskState?.enabled&&systemTaskState?.healthy);
    $('scheduler-disable').disabled=busy||!watchlistReady||!systemTaskState?.enabled;
    $('scheduler-remove').disabled=busy||!watchlistReady||!systemTaskState?.installed;
    $('watchlist-form').setAttribute('aria-busy',String(busy));
  }
  function renderWatchlist(state){
    watchlistState=state;
    $('watchlist-count').textContent=`${state.items.length} 只 · 启用 ${state.enabled_count} 只`;
    const choice=$('watchlist-fund').value,joined=new Set(state.items.map(item=>item.code));
    const options=state.available_funds.filter(fund=>!joined.has(fund.code));
    $('watchlist-fund').replaceChildren();
    for(const fund of options){const option=document.createElement('option');option.value=fund.code;option.textContent=`${fund.code} · ${fund.name}`;$('watchlist-fund').append(option);}
    if(options.some(fund=>fund.code===choice))$('watchlist-fund').value=choice;
    if(!options.length){const option=document.createElement('option');option.value='';option.textContent=state.available_funds.length?'已有基金均在名单中':'请先获取基金的本地数据';$('watchlist-fund').append(option);}
    const body=$('watchlist-body');body.replaceChildren();
    for(const item of state.items){
      const row=document.createElement('tr'),name=row.insertCell(),title=document.createElement('span'),code=document.createElement('span');
      title.className='fund-name';title.textContent=item.name;code.className='fund-code';code.textContent=item.code;name.append(title,code);
      const badge=document.createElement('span');badge.className='job-badge'+(item.enabled?' queued':'');badge.textContent=item.enabled?'已启用':'已停用';row.insertCell().append(badge);
      const result=row.insertCell(),last=item.last_auto_result;
      if(last){const labels={planned:'等待执行',running:'执行中或待恢复',succeeded:'更新成功',failed:last.phase==='export'?'行情已保存，待导出':'更新未完成',interrupted:'上次中断，待恢复',skipped:'已跳过',superseded:'已合并至新时段'};const title=document.createElement('span'),detail=document.createElement('span');title.textContent=labels[last.status]||'状态待核对';detail.className='fund-code schedule-result';detail.textContent=`${dateTime(last.updated_at)} · ${last.message}`;result.append(title,detail);}else result.textContent='尚未执行';
      const actions=row.insertCell();actions.className='actions';
      const toggle=button(item.enabled?'停用':'启用',()=>void changeWatchlist('set-enabled',item.code,!item.enabled));
      toggle.classList.add('watchlist-action');toggle.dataset.unavailable=String(!item.available&&!item.enabled);toggle.setAttribute('aria-label',`${item.enabled?'停用':'启用'} ${item.code} ${item.name}`);
      const remove=button('移出名单',()=>void changeWatchlist('remove',item.code));remove.classList.add('watchlist-action');remove.setAttribute('aria-label',`移出名单 ${item.code} ${item.name}`);actions.append(toggle,remove);body.append(row);
    }
    if(!state.items.length){const cell=body.insertRow().insertCell();cell.colSpan=4;cell.textContent='名单为空。选择上方基金加入，今后只自动更新名单中启用的基金。';}
    $('watchlist-status').textContent='';
  }
  function renderSchedule(plan){
    const jobs=plan.pending,blocked=jobs.filter(job=>job.status!=='planned');
    systemTaskState=plan.system_task;
    $('scheduler-status').textContent=systemTaskState.message;
    const processLabels={267011:'尚未执行',0:'上次进程正常结束',1:'上次进程未完成',2:'上次仍有待处理项目'};
    $('scheduler-detail').textContent=systemTaskState.installed?`任务：${systemTaskState.name} · ${processLabels[systemTaskState.last_result]||'进程状态待核对'}。行情结果请看下方“上次自动更新”。`:'';
    $('schedule-rule').textContent=`每个交易日 · ${plan.update_time}`;
    if(!scheduleTimeDirty)$('schedule-time').value=plan.update_time;
    $('schedule-summary').textContent=plan.calendar_error?plan.calendar_error:jobs.length?`当前 ${jobs.length} 只基金有到期计划${blocked.length?'（含等待重试或本人处理的项目）':''}。${plan.run_allowed?'':plan.message}`:`当前没有待执行的到期计划。${plan.run_allowed?'':plan.message}`;
    $('schedule-next').textContent=plan.next_slot?`下次计划：${dateTime(plan.next_slot)}`:'下次计划待核对，请更新交易日历。';
    const notice=plan.calendar_error||plan.calendar_warning||(blocked.length?`${blocked.length} 只基金等待重试或本人处理，请查看下方名单结果。`:jobs.length?`${jobs.length} 只基金有到期计划。`:'');
    $('schedule-alert').textContent=notice;$('schedule-alert').hidden=!notice;
    $('calendar-status').textContent=plan.calendar?`交易日历覆盖 ${plan.calendar.coverage_start} 至 ${plan.calendar.coverage_end}，核对日期 ${plan.calendar.verified_on}。${plan.calendar_warning||''}`:'交易日历暂不可用，到期采集已暂停；手动获取仍可使用。';
  }
  async function loadWatchlist(){
    if(watchlistBusy||scheduleSaving)return;
    watchlistBusy=true;watchlistButtons();$('watchlist-error').hidden=true;
    try{const state=await (await api('/api/watchlist')).json();const plan=await (await api('/api/schedule')).json();renderWatchlist(state);renderSchedule(plan);watchlistReady=true;}
    catch(error){watchlistReady=false;$('watchlist-error').textContent=error.message;$('watchlist-error').hidden=false;$('watchlist-status').textContent=watchlistState?'刷新失败，当前为上次读取的名单；请刷新后再操作。':'名单读取失败，请刷新重试。';$('schedule-summary').textContent='执行计划读取失败，请刷新后核对。';$('schedule-next').textContent='';$('schedule-alert').textContent='执行计划读取失败，请刷新后核对。';$('schedule-alert').hidden=false;}
    finally{watchlistBusy=false;watchlistButtons();}
  }
  async function changeWatchlist(action,code,enabled){
    if(watchlistBusy||scheduleSaving||!watchlistReady)return;
    watchlistBusy=true;watchlistButtons();$('watchlist-error').hidden=true;$('watchlist-status').textContent='正在保存名单…';
    try{const state=await postTask('/api/watchlist/'+action,{code,...(action==='set-enabled'?{enabled}:{})});const plan=await (await api('/api/schedule')).json();renderWatchlist(state);renderSchedule(plan);$('watchlist-status').textContent=action==='add'?'已加入名单。':action==='remove'?'已移出名单，本地行情和文件均保留。':enabled?'已启用名单项目。':'已停用名单项目，仍保留在名单中。';}
    catch(error){watchlistReady=false;$('watchlist-error').textContent=error.message;$('watchlist-error').hidden=false;$('watchlist-status').textContent='保存结果需核对，请点击“刷新名单”后继续操作。';}
    finally{watchlistBusy=false;watchlistButtons();}
  }
  $('watchlist-form').addEventListener('submit',event=>{event.preventDefault();if($('watchlist-fund').value)void changeWatchlist('add',$('watchlist-fund').value);});
  $('watchlist-refresh').addEventListener('click',()=>void loadWatchlist());
  $('schedule-time').addEventListener('input',()=>{scheduleTimeDirty=true;$('schedule-save-status').textContent='时间尚未保存。';watchlistButtons();});
  $('schedule-time-form').addEventListener('submit',async event=>{
    event.preventDefault();if(watchlistBusy||scheduleSaving||!watchlistReady||!scheduleTimeDirty)return;
    scheduleSaving=true;watchlistButtons();$('schedule-save-status').textContent='正在保存时间…';
    try{const plan=await postTask('/api/schedule/settings',{update_time:$('schedule-time').value});scheduleTimeDirty=false;renderSchedule(plan);$('schedule-save-status').textContent=`已保存：每个交易日 ${plan.update_time}。${plan.system_task.message}`;}
    catch(error){scheduleTimeDirty=false;watchlistReady=false;$('schedule-save-status').textContent=error.message+' 请点击“刷新名单与计划”核对保存结果。';}
    finally{scheduleSaving=false;watchlistButtons();}
  });
  for(const action of ['install','disable','remove'])$('scheduler-'+action).addEventListener('click',async()=>{
    if(watchlistBusy||scheduleSaving||!watchlistReady)return;
    scheduleSaving=true;watchlistButtons();$('schedule-save-status').textContent='正在处理Windows任务…';
    try{const plan=await postTask('/api/schedule/'+action,{});renderSchedule(plan);$('schedule-save-status').textContent=action==='remove'?'定时任务已移除，名单、时间设置和行情均保留。':plan.system_task.message;}
    catch(error){watchlistReady=false;$('schedule-save-status').textContent=error.message+' 请刷新名单与计划核对。';}
    finally{scheduleSaving=false;watchlistButtons();}
  });
  function renderCandidates(result){collectMatches=result.matches;collectChoice=collectMatches.length===1&&!result.partial?collectMatches[0].code:'';$('collect-candidates').hidden=false;$('candidate-note').textContent=result.message;$('candidate-body').replaceChildren();for(const [index,fund]of collectMatches.entries()){const row=document.createElement('tr');const choice=row.insertCell();const radio=document.createElement('input');radio.type='radio';radio.name='fund-candidate';radio.className='candidate-radio';radio.value=fund.code;radio.setAttribute('aria-label',`选择 ${fund.code} ${fund.name} ${fund.category}`);radio.checked=collectChoice===fund.code&&index===0;radio.addEventListener('change',()=>{collectChoice=fund.code;collectButtons();});choice.append(radio);for(const value of [fund.code,fund.name,fund.category])row.insertCell().textContent=value;$('candidate-body').append(row);}if(!collectMatches.length){const cell=$('candidate-body').insertRow().insertCell();cell.colSpan=4;cell.textContent='没有匹配候选，请修改输入或使用准确的六位代码。';}collectButtons();}
  function renderCollectTask(job){
    collectTask=job;collectButtons();if(!job)return;
    const labels={queued:'已提交',running:'进行中',waiting_login:'等待本人登录',succeeded:'已完成',failed:'未完成',export_failed:'数据已保存，导出失败',cancelled:'已取消',interrupted:'状态待确认'};
    const kindNames={search:'基金查找',update:'数据获取',login:'专用 Edge 登录',export:'重试导出'};
    $('collect-task-badge').textContent=labels[job.status]||job.status;$('collect-task-badge').className='job-badge '+job.status;
    $('collect-task-message').textContent=job.status==='succeeded'&&['update','export'].includes(job.kind)?'':job.message;
    const detail=$('collect-task-detail');detail.replaceChildren();const info=document.createElement('p');info.textContent=`${kindNames[job.kind]||'任务'} · 开始 ${dateTime(job.started_at)}${job.completed_at?' · 结束 '+dateTime(job.completed_at):''}`;detail.append(info);
    const result=job.result;collectResultCode=result?.code||job.payload?.code||'';
    if(result&&['update','export'].includes(job.kind)){const fund=document.createElement('p');fund.textContent=`${result.code} · ${result.name||'名称未确认'}`;detail.append(fund);if(['succeeded','export_failed'].includes(job.status)){const counts=document.createElement('div');counts.className='result-counts';for(const [key,label]of [['inserted','新增'],['updated','修订'],['unchanged','未变化'],['total','本地累计']]){const part=document.createElement('span');const number=document.createElement('strong');number.textContent=result[key];part.append(number,document.createTextNode(label+'条'));counts.append(part);}detail.append(counts);}}
    if(job.status==='interrupted'){const note=document.createElement('p');note.textContent='任务最终结果尚未确认；数据可能已保存，请先查看本地记录，不会自动重新采集。';detail.append(note);}
    if(job.log_warning){const warning=document.createElement('p');warning.textContent=job.log_warning;detail.append(warning);}
    const canView=!!collectResultCode&&data.funds.some(f=>f.code===collectResultCode)&&!activeTask(job);$('collect-result-actions').hidden=!(canView||(['update','export'].includes(job.kind)&&!activeTask(job)));$('collect-view').hidden=!canView;$('collect-analyze').hidden=!canView;$('collect-export').hidden=job.status!=='export_failed';$('collect-again').hidden=job.kind!=='update'||job.status!=='failed';
  }
  async function receiveTask(job){renderCollectTask(job);if(!job)return;if(!activeTask(job)&&handledTask!==job.id){handledTask=job.id;if(job.kind==='search'&&job.status==='succeeded'){renderCandidates(job.result);if(!$('fund-query').value)$('fund-query').value=job.payload.query;}
      if(['update','export'].includes(job.kind)){await loadOverview();if(collectTask?.id!==job.id)return;if(analysisOpened&&selected===job.payload.code)loadPremium();renderCollectTask(job);}
    }clearTimeout(collectPoll);if(activeTask(job))collectPoll=setTimeout(()=>void refreshCollect(),1000);
  }
  async function refreshCollect(){clearTimeout(collectPoll);const revision=++collectRevision;try{const state=await (await api('/api/tasks')).json();if(revision!==collectRevision)return;collectUncertain=false;$('collect-error').hidden=true;await receiveTask(state.active||state.latest);}catch(error){if(revision!==collectRevision)return;$('collect-error').textContent=error.message+' 如已提交，请先刷新任务状态，不要重复采集。';$('collect-error').hidden=false;if(activeTask(collectTask)||collectUncertain)collectPoll=setTimeout(()=>void refreshCollect(),3000);}finally{collectButtons();}}
  async function startCollect(kind,payload={}){if(collectSubmitting||collectUncertain||activeTask(collectTask))return;const revision=++collectRevision;collectSubmitting=true;collectButtons();$('collect-error').hidden=true;try{const job=await postTask('/api/tasks/'+kind,{...payload,request_id:crypto.randomUUID()});if(revision===collectRevision)await receiveTask(job);}catch(error){$('collect-error').textContent=error.message;$('collect-error').hidden=false;collectUncertain=!error.status||error.status>=500;try{const state=await (await api('/api/tasks')).json();if(revision===collectRevision){collectUncertain=false;await receiveTask(state.active||state.latest);}}catch{if(collectUncertain)collectPoll=setTimeout(()=>void refreshCollect(),3000);}}finally{collectSubmitting=false;collectButtons();}}
  $('collect-search-form').addEventListener('submit',e=>{e.preventDefault();void startCollect('search',{query:$('fund-query').value.trim()});});
  $('fund-query').addEventListener('input',()=>{collectChoice='';$('collect-candidates').hidden=true;collectButtons();});
  $('collect-start').addEventListener('click',()=>{if(collectChoice)void startCollect('update',{code:collectChoice});});
  $('collect-login').addEventListener('click',()=>void startCollect('login'));
  $('collect-export').addEventListener('click',()=>{if(collectResultCode)void startCollect('export',{code:collectResultCode});});
  $('collect-again').addEventListener('click',()=>{if(collectResultCode)void startCollect('update',{code:collectResultCode});});
  $('collect-refresh').addEventListener('click',refreshCollect);
  $('collect-cancel').addEventListener('click',async()=>{if(!collectTask)return;try{await receiveTask(await postTask('/api/tasks/cancel',{job_id:collectTask.id}));}catch(error){$('collect-error').textContent=error.message;$('collect-error').hidden=false;}});
  $('collect-view').addEventListener('click',async()=>{const code=collectResultCode;await loadOverview();selectFund(code);showPage('data');$('history-panel').scrollIntoView({block:'start'});});
  $('collect-analyze').addEventListener('click',async()=>{const code=collectResultCode;await loadOverview();selectFund(code);clearPremium();openPremium();});
  function clock(){const now=new Date();$('today').textContent=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'long',day:'numeric',weekday:'long'}).format(now);$('date-short').textContent=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit'}).format(now);$('weekday').textContent=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',weekday:'long'}).format(now);}
  clock();setInterval(clock,60000);
  const splitter=$('splitter');let dragging=false;
  function setWidth(value){const max=Math.max(176,Math.min(340,window.innerWidth-240));const width=Math.round(Math.max(176,Math.min(max,value)));document.documentElement.style.setProperty('--sidebar-width',width+'px');splitter.setAttribute('aria-valuenow',width);splitter.setAttribute('aria-valuemax',max);return width;}
  const savedWidth=Number(store.get('sidebar','220'));setWidth(Number.isFinite(savedWidth)?savedWidth:220);
  splitter.addEventListener('pointerdown',e=>{if(e.button!==0)return;dragging=true;splitter.setPointerCapture(e.pointerId);document.body.classList.add('resizing');});
  splitter.addEventListener('pointermove',e=>{if(dragging)setWidth(e.clientX);});
  function stopDrag(){if(dragging){dragging=false;document.body.classList.remove('resizing');store.set('sidebar',splitter.getAttribute('aria-valuenow'));}}
  splitter.addEventListener('pointerup',stopDrag);splitter.addEventListener('pointercancel',stopDrag);splitter.addEventListener('lostpointercapture',stopDrag);
  splitter.addEventListener('keydown',e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();store.set('sidebar',setWidth(Number(splitter.getAttribute('aria-valuenow'))+(e.key==='ArrowRight'?10:-10)));}if(e.key==='Home'){e.preventDefault();store.set('sidebar',setWidth(220));}});
  window.addEventListener('resize',()=>setWidth(Number(splitter.getAttribute('aria-valuenow'))));
  showPage(store.get('page','home'));void loadOverview().then(refreshCollect);
})();
