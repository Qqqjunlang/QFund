"""可离线打开的中文交互式分析图；计算与图表展示分开。"""
from html import escape
from importlib.resources import files
import json

from fundlab.analysis.premium import PremiumSeries
from fundlab.models import Fund
from fundlab.presentation import APP_NAME, FONT_FAMILY, FONT_SIZES, typography_css


DEFAULT_CHART_AVERAGE_PERIODS = (20,)


def render_premium_timeline(fund: Fund, series: PremiumSeries, *, embedded: bool = False) -> str:
    import plotly.graph_objects as go
    import plotly.io as pio

    points = series.points
    first, last = points[0].price_date, points[-1].price_date
    valid = series.latest_valid
    name = escape(fund.name)
    figure = go.Figure(go.Scatter(
        x=[point.price_date for point in points],
        y=[float(point.premium) if point.premium is not None else None for point in points],
        name="每日溢价率", mode="lines+markers", connectgaps=False,
        line={"color": "#2563eb", "width": 2.5}, marker={"size": 4},
        hovertemplate="%{x|%Y-%m-%d}<br>溢价率：%{y:.2f}%<extra></extra>",
    ))
    figure.update_layout(
        template="plotly_white", autosize=True, height=560,
        margin={"l": 75, "r": 30, "t": 80, "b": 60},
        font={"family": FONT_FAMILY, "size": FONT_SIZES["note"], "color": "#334155"},
        paper_bgcolor="white", plot_bgcolor="white", dragmode="zoom", hovermode="closest",
        xaxis={"title": "价格日期", "type": "date", "range": [first, last], "tickformat": "%Y-%m-%d",
               "automargin": True, "title_font": {"size": FONT_SIZES["body"]},
               "rangeslider": {"visible": True, "thickness": 0.14, "range": [first, last],
                               "autorange": False, "bgcolor": "#f8fafc"}, "showgrid": False},
        yaxis={"title": "溢价率（%）", "ticksuffix": "%", "fixedrange": False,
               "automargin": True, "title_font": {"size": FONT_SIZES["body"]},
               "gridcolor": "#edf2f7", "zerolinecolor": "#94a3b8"},
        showlegend=True, legend={"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0},
    )
    chart = pio.to_html(figure, full_html=False, include_plotlyjs=True, div_id="premium-chart",
                        config={"responsive": True, "scrollZoom": True, "displayModeBar": False, "showTips": False})
    source_data = json.dumps([{"date": p.price_date, "premium": p.premium} for p in points],
                             ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    client = files("fundlab.analysis").joinpath("premium_client.js").read_text(encoding="utf-8")
    current = f"{float(valid.premium):.2f}%" if valid else "暂无有效数据"
    current_date = valid.price_date if valid else "—"
    # 内嵌图的基金标题与页面操作共用父页同一行；离线文档仍自带标题。
    heading = "" if embedded else f"<h1>{fund.code} {name} · 溢价率时序</h1>"
    embedded_style = "body{background:white}main{max-width:none;margin:0;padding:0}.card{border-radius:9px;padding:22px}.summary{margin-top:0}" if embedded else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(APP_NAME)} · {fund.code} {name} · 溢价率时序分析</title>
<style>
{typography_css()}
*{{box-sizing:border-box}}body{{margin:0;background:#f3f6fb;color:#1e293b;font:var(--font-body)/1.6 var(--font-family)}}
main{{max-width:1280px;margin:28px auto;padding:0 24px}}h1{{font-size:var(--font-title);line-height:1.35;font-weight:600;margin:8px 0 18px}}p{{line-height:1.7}}
.muted{{color:#64748b}}.card{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:22px;margin-top:18px}}
.summary{{display:flex;gap:28px 40px;flex-wrap:wrap}}.number{{display:block;font-size:var(--font-number);font-weight:600;line-height:1.3;color:#2563eb;margin:10px 0 8px;font-variant-numeric:tabular-nums}}
.summary>div>.muted{{font-size:var(--font-note)}}
.rank-panel{{flex:1 1 360px;min-width:0}}.rank-panel .muted{{overflow-wrap:anywhere}}#rank-feedback{{color:#b91c1c;margin:6px 0 0}}
.controls{{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:14px 0}}
[hidden]{{display:none!important}}
.analysis-range{{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}}
.analysis-range .controls{{margin:0;gap:9px}}.analysis-range>button{{white-space:nowrap}}
.range-caption{{margin:14px 0 0;color:#64748b;font-size:var(--font-note)}}
.custom-settings{{margin-top:16px;padding:16px 18px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:9px}}
.custom-settings .controls{{margin:0;gap:12px}}.custom-settings .controls+.controls{{margin-top:16px;padding-top:16px;border-top:1px solid #e2e8f0}}
.custom-settings label{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.average-results{{display:flex;gap:16px;flex-wrap:wrap;margin:22px 0 18px}}
.average-item{{min-width:190px;padding:14px 18px;background:#f8faff;border-radius:8px}}
.average-label{{display:block;font-size:var(--font-body);color:#64748b}}.average-value{{display:flex;align-items:center;gap:8px;margin-top:9px}}
.average-value strong{{font-size:var(--font-metric);font-weight:600;color:#334155;font-variant-numeric:tabular-nums}}
.average-item .help-tooltip{{left:0;right:auto}}
.chart-tools{{display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap;padding:0 0 18px;margin-bottom:18px;border-bottom:1px solid #e2e8f0}}
@media(max-width:650px){{.average-results{{gap:12px}}.average-item{{flex:1 1 150px;min-width:0}}.custom-settings{{padding:14px}}.custom-settings .controls{{align-items:flex-end}}.chart-tools{{justify-content:flex-start}}}}
button,input{{font:inherit;min-height:44px;line-height:1.4;border:1px solid #cbd5e1;border-radius:7px;background:white;padding:10px 13px;color:#334155}}
input{{max-width:100%}}button:focus-visible,input:focus-visible{{outline:3px solid #93b7ff;outline-offset:2px}}
button{{cursor:pointer}}button:hover,button.active{{border-color:#2563eb;color:#1d4ed8;background:#eff6ff}}
.help-wrap{{position:relative;display:inline-flex;align-items:center}}
.help-trigger{{width:28px;height:28px;min-height:28px;padding:0;border-radius:50%;color:#64748b;font-size:var(--font-note);font-weight:600}}
.help-tooltip{{visibility:hidden;opacity:0;position:absolute;right:0;bottom:calc(100% + 9px);width:285px;max-width:75vw;
 background:#1e293b;color:#fff;padding:12px 15px;border-radius:8px;font-size:var(--font-note);line-height:1.7;z-index:20;
 box-shadow:0 4px 18px #0f172a26;pointer-events:none}}
.help-wrap:hover .help-tooltip,.help-wrap:focus-within .help-tooltip{{visibility:visible;opacity:1}}
.rank-heading{{position:relative;display:flex;align-items:center;gap:6px}}
.rank-heading .help-wrap{{position:static}}.rank-heading .help-tooltip{{left:0;right:auto}}
.notice{{color:#975b10;background:#fffbeb;border-radius:7px;padding:9px 12px;line-height:1.7}}
#premium-chart{{width:100%}}.caption{{font-size:var(--font-note)}}@media(max-width:650px){{main{{padding:0 10px}}.card{{padding:18px}}.summary{{gap:24px}}h1{{font-size:var(--font-title-compact)}}}}
{embedded_style}
</style></head><body><main>
{heading}
<section class="card summary">
<div>最新有效溢价率<strong class="number" id="latest-premium">{current}</strong><span class="muted">对应价格日期：{current_date}</span></div>
<div>本地已保存行情<strong class="number">{len(points)} 条</strong><span class="muted">{first} 至 {last}</span></div>
<div class="rank-panel"><div class="rank-heading"><span id="rank-title">当前溢价的区间历史分位</span>
<span class="help-wrap"><button type="button" class="help-trigger" aria-label="历史分位计算说明" aria-describedby="rank-helper">?</button>
<span class="help-tooltip" id="rank-helper" role="tooltip">按所选区间内“小于等于当前值”的比例计算</span></span></div>
<strong class="number" id="current-rank">—</strong>
<form id="rank-form" class="controls" style="margin-bottom:0">
<label>溢价率（%） <input id="rank-premium" type="text" inputmode="decimal" placeholder="例如 12.5" aria-label="指定溢价率" style="width:125px"></label>
<button type="submit">计算分位</button><button type="button" id="rank-reset">恢复当前溢价</button></form>
<p id="rank-feedback" role="status" aria-live="polite"></p></div>
</section>
<section class="card">
<div class="analysis-range"><div class="controls" id="period-controls">
<span>分析区间</span><button data-months="1">近1个月</button><button data-months="3">近3个月</button>
<button data-months="6">近6个月</button><button data-months="12">近1年</button><button id="all" class="active">全部</button>
</div><button type="button" id="customize" aria-expanded="false" aria-controls="custom-settings">自定义 ▾</button></div>
<p id="active-range" class="range-caption"></p>
<div id="custom-settings" class="custom-settings" hidden>
<div class="controls"><label>开始日期 <input id="start" type="date" value="{first}"></label>
<label>结束日期 <input id="end" type="date" value="{last}"></label><button id="apply">应用日期</button></div>
<div class="controls"><label>N 日平均溢价率周期 <input id="average-periods" value="{','.join(map(str, DEFAULT_CHART_AVERAGE_PERIODS))}" style="width:130px" aria-label="平均溢价率周期"></label>
<button id="apply-averages">应用周期</button><span class="help-wrap">
<button type="button" class="help-trigger" aria-label="平均周期填写说明" aria-describedby="average-help">?</button>
<span id="average-help" class="help-tooltip" role="tooltip">可填 5、10、20 等正整数，多个周期用逗号分隔。</span></span></div></div>
<div id="average-summary" class="average-results" role="status" aria-live="polite"></div>
<div class="chart-tools" role="group" aria-label="图表工具"><button id="reset">重置视图</button><button id="pan">切换为拖动</button><button id="download">保存图片</button></div>
<div id="window-message" class="notice" role="status" hidden></div>
{chart}
</section></main>
<script>{client}</script>
<script>
const observations={source_data};
const gd=document.getElementById('premium-chart');
const first=observations[0].date, latest=observations[observations.length-1].date;
const start=document.getElementById('start'),end=document.getElementById('end');
const message=document.getElementById('window-message');
function showMessage(text){{message.textContent=text;message.hidden=!text;}}
document.getElementById('customize').onclick=function(){{
 const panel=document.getElementById('custom-settings'),expanded=this.getAttribute('aria-expanded')!=='true';
 panel.hidden=!expanded;this.setAttribute('aria-expanded',String(expanded));this.textContent=expanded?'收起自定义 ▴':'自定义 ▾';
}};
let averagePeriods={json.dumps(DEFAULT_CHART_AVERAGE_PERIODS)};
let comparisonPremium=null;
const referenceColors={{'30':'#0891b2','50':'#7c3aed','70':'#d97706'}};
const averageColors=['#059669','#d97706','#db2777','#4f46e5'];
const graphConfig={{responsive:true,scrollZoom:true,displayModeBar:false,showTips:false}};
function setActive(button){{document.querySelectorAll('#period-controls button').forEach(b=>b.classList.remove('active'));if(button)button.classList.add('active');}}
function viewBounds(result,a,b){{
 const rows=result.series;
 let x0=rows.length?rows[0].price_date:a,x1=rows.length?rows[rows.length-1].price_date:b;
 if(x0===x1){{const day=Date.parse(x0);x0=new Date(day-43200000).toISOString();x1=new Date(day+43200000).toISOString();}}
 let low=Infinity,high=-Infinity;
 for(const row of rows){{
   const values=[row.premium,...Object.values(row.average_premiums)];
   for(const value of values){{if(value===null)continue;const n=Number(value);if(Number.isFinite(n)){{low=Math.min(low,n);high=Math.max(high,n);}}}}
 }}
 if(!Number.isFinite(low)){{low=-1;high=1;}}
 const padding=high>low?(high-low)*0.02:Math.max(Math.abs(low)*0.01,0.01);
 return {{x:[x0,x1],y:[low-padding,high+padding]}};
}}
function updateWindow(a,b){{
 if(!a||!b||a>b){{showMessage('请输入有效的起止日期，开始日期不能晚于结束日期。');return false;}}
 const result=FundlabPremiumStats.compute(observations,a,b,averagePeriods,comparisonPremium);
 let text=`所选区间：${{a}} 至 ${{b}}；区间内 ${{result.rows}} 条行情，${{result.valid_samples}} 条有效溢价记录。`;
 if(a<first||b>latest)text+=` 所选区间超出本地已保存范围（${{first}} 至 ${{latest}}），仅显示已有数据。`;
 if(!result.valid_samples)text+=' 该区间暂无有效溢价数据，分位不可计算。';
 if(result.missing_samples)text+=` ${{result.missing_samples}} 条缺失溢价未参与统计。`;
 showMessage(a<first||b>latest||!result.valid_samples||result.missing_samples?text:'');start.value=a;end.value=b;
 document.getElementById('active-range').textContent=`当前区间：${{a}} 至 ${{b}} · ${{result.valid_samples}} 条有效记录`;
 const comparison=result.comparison;
 document.getElementById('current-rank').textContent=comparison.percentile_rank===null?'暂无可比数据':comparison.percentile_rank.toFixed(2)+'%';
 document.getElementById('rank-title').textContent=comparison.mode==='custom'?'输入溢价率的区间历史分位':'当前溢价的区间历史分位';
 document.getElementById('rank-helper').textContent=comparison.mode==='custom'?`输入值：${{comparison.premium}}%，按当前所选区间计算`:'按所选区间内“小于等于当前值”的比例计算';
 const averageSummary=document.getElementById('average-summary');averageSummary.replaceChildren();
 averagePeriods.forEach(n=>{{
   const value=result.current?result.current.average_premiums[n]:null;
   const item=document.createElement('div'),label=document.createElement('span'),row=document.createElement('div'),number=document.createElement('strong');
   item.className='average-item';label.className='average-label';label.textContent=`近 ${{n}} 日平均溢价率`;row.className='average-value';
   number.textContent=value===null?'—':value.toFixed(2)+'%';row.append(number);
   if(value===null){{
     const help=document.createElement('span'),trigger=document.createElement('button'),tip=document.createElement('span');
     help.className='help-wrap';trigger.type='button';trigger.className='help-trigger';trigger.textContent='?';
     trigger.setAttribute('aria-label',`近 ${{n}} 日平均溢价率数据说明`);trigger.setAttribute('aria-describedby',`average-${{n}}-helper`);
     tip.id=`average-${{n}}-helper`;tip.className='help-tooltip';tip.setAttribute('role','tooltip');tip.textContent=`数据不足（共 ${{result.total_valid_samples}} 条有效记录），无法计算当前近 ${{n}} 日平均溢价率。`;
     help.append(trigger,tip);row.append(help);
   }}
   item.append(label,row);averageSummary.append(item);
 }});
 const visibility=new Map(gd.data.map(trace=>[trace.name,trace.visible]));
 const traces=[{{...gd.data[0],visible:visibility.get('每日溢价率')}}];
 ['30','50','70'].forEach(q=>{{
   const value=result.quantiles[q];
   const name=`第 ${{q}} 分位参考线`;
   traces.push({{x:[a,b],y:[value,value],name,mode:'lines',visible:visibility.get(name),
    line:{{color:referenceColors[q],width:1.4,dash:q==='50'?'dashdot':'dash'}},
    hovertemplate:`第 ${{q}} 分位：%{{y:.2f}}%<extra></extra>`}});
 }});
 averagePeriods.forEach((n,index)=>{{
   const name=`近 ${{n}} 日平均溢价率`;
   traces.push({{x:result.series.map(p=>p.price_date),y:result.series.map(p=>p.average_premiums[n]),name,
    mode:'lines',connectgaps:false,showlegend:result.series.some(p=>p.average_premiums[n]!==null),
    visible:visibility.get(name)??(n===20?'legendonly':true),line:{{color:averageColors[index%averageColors.length],width:2}},
    hovertemplate:`%{{x|%Y-%m-%d}}<br>近 ${{n}} 日平均溢价率：%{{y:.2f}}%<extra></extra>`}});
 }});
 const bounds=viewBounds(result,a,b);
 const layout={{...gd.layout,xaxis:{{...gd.layout.xaxis,range:bounds.x,autorange:false,
   minallowed:bounds.x[0],maxallowed:bounds.x[1],
   rangeslider:{{...gd.layout.xaxis.rangeslider,range:bounds.x,autorange:false}}}},
   yaxis:{{...gd.layout.yaxis,range:bounds.y,autorange:false,minallowed:bounds.y[0],maxallowed:bounds.y[1],fixedrange:false}}}};
 window.lastPremiumAnalysis=result;
 Plotly.react(gd,traces,layout,graphConfig);return true;
}}
function monthStart(months){{
 const [y,m,d]=latest.split('-').map(Number),target=new Date(Date.UTC(y,m-1-months,1));
 const lastDay=new Date(Date.UTC(target.getUTCFullYear(),target.getUTCMonth()+1,0)).getUTCDate();
 target.setUTCDate(Math.min(d,lastDay));return target.toISOString().slice(0,10);
}}
document.querySelectorAll('[data-months]').forEach(button=>button.onclick=()=>{{updateWindow(monthStart(Number(button.dataset.months)),latest);setActive(button);}});
function reset(){{updateWindow(first,latest);setActive(document.getElementById('all'));}}
document.getElementById('all').onclick=reset;document.getElementById('reset').onclick=reset;
document.getElementById('apply').onclick=()=>{{if(updateWindow(start.value,end.value))setActive(null);}};
document.getElementById('rank-form').onsubmit=event=>{{
 event.preventDefault();
 const value=document.getElementById('rank-premium').value.trim();
 try{{
   const preview=FundlabPremiumStats.compute(observations,start.value,end.value,averagePeriods,value);
   comparisonPremium=preview.comparison.premium;
   document.getElementById('rank-feedback').textContent='';
   updateWindow(start.value,end.value);
 }}catch(error){{document.getElementById('rank-feedback').textContent=error.message;}}
}};
document.getElementById('rank-reset').onclick=()=>{{
 comparisonPremium=null;document.getElementById('rank-premium').value='';
 document.getElementById('rank-feedback').textContent='';updateWindow(start.value,end.value);
}};
document.getElementById('apply-averages').onclick=()=>{{
 const tokens=document.getElementById('average-periods').value.trim().split(/[,，\\s]+/);
 const parsed=tokens.map(Number);
 if(!tokens.length||tokens.some(t=>!(/^[1-9][0-9]*$/).test(t))||parsed.some(n=>!Number.isSafeInteger(n))){{
   showMessage('N 日周期须为正整数，多个周期用逗号分隔，且每个周期不超过 9007199254740991。');return;
 }}
 averagePeriods=[...new Set(parsed)];updateWindow(start.value,end.value);
}};
document.getElementById('pan').onclick=function(){{const pan=gd.layout.dragmode!=='pan';Plotly.relayout(gd,{{dragmode:pan?'pan':'zoom'}});this.textContent=pan?'切换为框选缩放':'切换为拖动';}};
document.getElementById('download').onclick=()=>Plotly.downloadImage(gd,{{format:'png',filename:'{fund.code}_premium_timeline',scale:2}});
reset();
</script></body></html>"""
