"""本机服务识别，不返回项目路径或登录状态。"""
import hashlib
import os


def service_identity(config):
    location = os.path.normcase(str(config.root.resolve()))
    return {"app": "fundlab-local", "protocol": 1,
            "project_id": hashlib.sha256(location.encode("utf-8")).hexdigest()}
