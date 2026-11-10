"""Render the price series into a self-contained chart document."""

from datetime import datetime, timedelta
from html import escape

from fundlab.analysis.price import price_percentiles
from fundlab.presentation import APP_NAME, FONT_FAMILY, FONT_SIZES, browser_compat_script, typography_css


def _view_bounds(dates, traces):
    start, end = dates[0], dates[-1]
    if start == end:
        day = datetime.fromisoformat(start)
        half_day = timedelta(hours=12)
        start = (day - half_day if day > datetime.min else day).isoformat()
        end = (day + half_day).isoformat()

    values = [value for _, trace in traces for value in trace if value is not None]
    low, high = (float(min(values)), float(max(values))) if values else (-1, 1)
    padding = (high - low) * 0.02 if high > low else max(abs(low) * 0.01, 0.01)
    return (start, end), (low - padding, high + padding)


def render_price_timeline(fund, points, periods):
    import plotly.graph_objects as go
    import plotly.io as pio

    dates = [point.day for point in points]
    colors = ('#2563eb', '#c4621c', '#0e7b66', '#8254a1', '#5a687e', '#be5068')
    traces = [('收盘价', [point.close for point in points])]
    traces.extend((f'{period} 日均线', [point.averages[index] for point in points])
                  for index, period in enumerate(periods))
    x_range, y_range = _view_bounds(dates, traces)
    figure = go.Figure()
    for index, (label, values) in enumerate(traces):
        legend_rank = 1000
        if index:
            legend_rank = 1200 if periods[index - 1] == 5 else 1100
        figure.add_scatter(
            x=dates, y=[float(value) if value is not None else None for value in values],
            name=label, mode='lines+markers' if index == 0 else 'lines', connectgaps=False,
            visible=True if index == 0 else 'legendonly',
            legendrank=legend_rank,
            line={'color': colors[index], 'width': 2.5 if index == 0 else 2},
            marker={'size': 4},
            hovertemplate=f'%{{x|%Y-%m-%d}}<br>{label}：%{{y:.8~g}}<extra></extra>',
        )
    reference_colors = {30: '#0891b2', 50: '#7c3aed', 70: '#d97706'}
    for percentile, value in price_percentiles(points).items():
        if value is None:
            continue
        label = f'第 {percentile} 分位参考线'
        figure.add_scatter(
            x=x_range, y=[float(value)] * 2, name=label, mode='lines', visible=True,
            line={'color': reference_colors[percentile], 'width': 1.4,
                  'dash': 'dashdot' if percentile == 50 else 'solid'},
            hovertemplate=f'{label}：%{{y:.8~g}}<extra></extra>',
        )
    figure.update_layout(
        template='plotly_white', autosize=True, height=560,
        margin={'l': 65, 'r': 25, 't': 80, 'b': 60},
        font={'family': FONT_FAMILY, 'size': FONT_SIZES['note'], 'color': '#334155'},
        hovermode='closest', dragmode='zoom',
        xaxis={'title': '价格日期', 'type': 'date', 'tickformat': '%Y-%m-%d', 'automargin': True,
               'range': x_range, 'autorange': False, 'minallowed': x_range[0], 'maxallowed': x_range[1],
               'rangeslider': {'visible': True, 'thickness': 0.14, 'range': x_range,
                               'autorange': False, 'bgcolor': '#f8fafc'}},
        yaxis={'title': '收盘价（单位价格）', 'automargin': True, 'range': y_range,
               'autorange': False, 'minallowed': y_range[0], 'maxallowed': y_range[1], 'fixedrange': False},
        legend={'orientation': 'h', 'y': 1.02, 'yanchor': 'bottom', 'x': 0},
    )
    graph = pio.to_html(figure, full_html=False, include_plotlyjs=True, div_id='price-chart',
                        config={'responsive': True, 'scrollZoom': True, 'displayModeBar': False,
                                'showTips': False})
    valid = sum(point.close is not None for point in points)
    blank = [label for label, values in traces[1:] if all(value is None for value in values)]
    note = f'<p class="muted">区间内暂无可绘制的均线：{"、".join(blank)}。</p>' if blank else ''
    if not valid:
        note = '<p class="muted">所选区间的收盘价均为缺失。</p>'
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(APP_NAME)} · {escape(fund.name)} · 收盘价走势</title>
<script>{browser_compat_script()}</script>
<style>
{typography_css()}
*{{box-sizing:border-box}}body{{margin:0;color:#334155;font:var(--font-body)/1.6 var(--font-family)}}
main{{padding:0;min-width:0}}.chart-tools{{display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap;padding:0 0 18px;margin-bottom:18px;border-bottom:1px solid #e2e8f0}}
.muted{{margin:4px 0;color:#64748b}}button{{font:inherit;min-height:44px;padding:10px 13px;color:#334155;background:white;border:1px solid #cbd5e1;border-radius:7px;cursor:pointer}}
button:hover{{border-color:#2563eb;color:#1d4ed8;background:#eff6ff}}button:focus-visible{{outline:3px solid #93b7ff;outline-offset:2px}}
@media(max-width:650px){{.chart-tools{{justify-content:flex-start}}}}
</style></head><body><main>
<div class="chart-tools" role="group" aria-label="图表工具"><button type="button" id="reset">重置视图</button><button type="button" id="pan">切换为拖动</button><button type="button" id="download">保存图片</button></div>
{note}{graph}</main><script>
const graph=document.getElementById('price-chart');
const initialX=[...graph.layout.xaxis.range],initialY=[...graph.layout.yaxis.range];
document.getElementById('reset').onclick=()=>Plotly.relayout(graph,{{'xaxis.range':initialX,'yaxis.range':initialY}});
document.getElementById('pan').onclick=function(){{
 const pan=graph.layout.dragmode!=='pan';
 Plotly.relayout(graph,{{dragmode:pan?'pan':'zoom'}});
 this.textContent=pan?'切换为框选缩放':'切换为拖动';
}};
</script></body></html>'''
