"""将现有独立交互图嵌入工作台；只做展示通信和文档安全边界。"""
import base64
import hashlib
from html import escape
from html.parser import HTMLParser
import json

from fundlab.config import AppConfig
from fundlab.presentation import APP_NAME
from fundlab.services.analysis import premium_chart_document


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts = []
        self.hashes = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = True
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            # 浏览器在执行前将HTML中的CRLF/CR转换为LF。
            source = "".join(self.parts).replace("\r\n", "\n").replace("\r", "\n")
            digest = base64.b64encode(hashlib.sha256(source.encode("utf-8")).digest()).decode("ascii")
            self.hashes.append(f"'sha256-{digest}'")
            self.active = False


def chart_policy(document: str) -> str:
    scripts = _Scripts()
    scripts.feed(document)
    return ("default-src 'none'; script-src " + " ".join(scripts.hashes) +
            "; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; "
            "frame-ancestors 'self'; base-uri 'none'; form-action 'none'")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False).replace("<", "\\u003c")


def chart_document(config: AppConfig, code: str) -> str:
    document = premium_chart_document(config, code, embedded=True)
    bridge = """<script>
(() => {
  const code = CODE_VALUE;
  let failed = false;
  function send(status, extra = {}) {
    parent.postMessage({type:'fundlab-premium', code, status, ...extra}, '*');
  }
  function failure() { if (!failed) {failed=true;send('error',{message:'分析图加载失败，请点击“重新加载”重试。'});} }
  window.addEventListener('error', failure);
  window.addEventListener('unhandledrejection', failure);
  window.addEventListener('load', () => {
    const main = document.querySelector('main');
    const graph = document.getElementById('premium-chart');
    if (!main || !graph || !window.lastPremiumAnalysis) {failure();return;}
    let previous = 0;
    const resize = () => {
      const height = Math.ceil(main.getBoundingClientRect().height + 12);
      if (height !== previous) {previous=height;send('resize',{height});}
    };
    if (!failed) send('ready');
    // 由外层工作台接收图片并下载，兼容浏览器对隔离子页面下载的限制。
    document.getElementById('download').onclick = () => {
      send('image-start');
      Plotly.toImage(graph,{format:'png',scale:2})
        .then(image => send('image',{image}))
        .catch(() => send('image-error'));
    };
    new ResizeObserver(resize).observe(main);
    window.addEventListener('resize',resize);
    resize();
  });
})();
</script>""".replace("CODE_VALUE", _json(code))
    return document.replace("</head>", bridge + "</head>", 1)


def error_document(code: str, message: str) -> str:
    event = _json({"type": "fundlab-premium", "code": code, "status": "error", "message": message})
    return (f"<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>{escape(APP_NAME)} · 分析暂不可用</title>"
            f"<body><p>{escape(message)}</p><script>parent.postMessage({event}, '*');</script></body></html>")
