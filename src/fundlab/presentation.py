"""工作台和独立/内嵌图表共用的展示规范；不包含业务规则。"""

APP_NAME = "QFund"
FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
FONT_SIZES = {
    "body": 16,
    "title": 30,
    "title-compact": 28,
    "section": 20,
    "note": 14,
    "small": 13,
    "number": 32,
    "metric": 24,
}


def typography_css() -> str:
    sizes = "".join(f"--font-{name}:{size}px;" for name, size in FONT_SIZES.items())
    return f":root{{--font-family:{FONT_FAMILY};{sizes}}}\n"
