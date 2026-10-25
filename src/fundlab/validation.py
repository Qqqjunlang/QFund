"""公共输入校验；不接触网络或数据库。"""
import re
from fundlab.errors import FundlabError


def validate_code(code: str) -> str:
    value = str(code).strip()
    if not re.fullmatch(r"[0-9]{6}", value):
        raise FundlabError("VALIDATION_ERROR", "请输入六位基金代码，并保留开头的零。")
    return value


def validate_query(query: str) -> str:
    value = query.strip()
    if not value or len(value) > 100 or any(ord(char) < 32 for char in value):
        raise FundlabError("VALIDATION_ERROR", "请输入 1—100 个字符的基金代码或名称。")
    return value
