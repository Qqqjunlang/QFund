"""仅向用户输出明确的中文错误，不输出可能含凭据的第三方堆栈。"""


class FundlabError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SessionBusy(FundlabError):
    def __init__(self):
        super().__init__("SESSION_BUSY", "专用 Edge 正被另一项任务使用，请关闭该窗口或等待任务结束。")
