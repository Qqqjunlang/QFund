"""读取本地行情并生成分析产物，不触发采集或改写历史导出。"""
import os
from pathlib import Path
import tempfile

from fundlab.analysis.charts import render_premium_timeline
from fundlab.analysis.calculations import AnalysisRequest, analyze_premium
from fundlab.analysis.premium import premium_series
from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.exports.service import fund_folder
from fundlab.storage.repository import read_history_snapshot
from fundlab.validation import validate_code


def analyze_one(config: AppConfig, code: str, request: AnalysisRequest = AnalysisRequest()) -> dict:
    code = validate_code(code)
    snapshot = read_history_snapshot(config.database_path, code)
    result = analyze_premium(premium_series(snapshot.rows), request)
    result["fund"] = {"code": snapshot.fund.code, "name": snapshot.fund.name, "source": snapshot.fund.source}
    result["source_coverage"] = snapshot.coverage
    return result


def premium_chart_document(config: AppConfig, code: str, *, embedded: bool = False) -> str:
    """只读生成当前行情的交互图文档，不保存文件或创建运行记录。"""
    code = validate_code(code)
    snapshot = read_history_snapshot(config.database_path, code)
    return render_premium_timeline(snapshot.fund, premium_series(snapshot.rows), embedded=embedded)


def create_premium_chart(config: AppConfig, code: str) -> dict:
    code = validate_code(code)
    snapshot = read_history_snapshot(config.database_path, code)
    series = premium_series(snapshot.rows)
    html = render_premium_timeline(snapshot.fund, series)
    directory = config.data_dir / "analysis" / fund_folder(snapshot.fund)
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tmp", prefix=".chart-", dir=directory,
                                         encoding="utf-8", delete=False) as output:
            temporary = Path(output.name)
            output.write(html)
        destination = directory / "premium_timeline.html"
        os.replace(temporary, destination)
    except OSError:
        raise FundlabError("CHART_WRITE_FAILED", "交互图保存失败，请检查目录或文件是否被占用；已有行情未改动。") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"code": code, "name": snapshot.fund.name, "chart": str(destination),
            "rows": len(series.points), "valid_samples": sum(p.premium is not None for p in series.points),
            "start": series.points[0].price_date, "end": series.points[-1].price_date,
            "offline": True, "message": "交互式溢价率时序图已保存，双击 HTML 文件即可打开。"}
