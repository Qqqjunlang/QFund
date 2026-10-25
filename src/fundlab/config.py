"""集中管理项目路径，不依赖命令行的当前工作目录。"""
from dataclasses import dataclass
import os
from pathlib import Path
import sys

from fundlab.errors import FundlabError


@dataclass(frozen=True)
class AppConfig:
    root: Path

    @property
    def local_dir(self) -> Path:
        return self.root / ".local"

    @property
    def profile_dir(self) -> Path:
        return self.local_dir / "edge-profile"

    @property
    def evidence_dir(self) -> Path:
        return self.local_dir / "evidence"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "fundlab.sqlite3"

    @property
    def watchlist_path(self) -> Path:
        return self.data_dir / "watchlist.sqlite3"

    @property
    def schedule_path(self) -> Path:
        return self.data_dir / "schedule.sqlite3"

    @property
    def funds_dir(self) -> Path:
        return self.data_dir / "funds"

    @classmethod
    def default(cls) -> "AppConfig":
        # 程序资源仍随包读取；安装版的可写数据不放在程序目录或解包临时目录。
        # 源码版不改变路径，安装版也不自动迁移或读取原项目数据。
        if getattr(sys, "frozen", False):
            local = os.environ.get("LOCALAPPDATA")
            if not local or not Path(local).is_absolute():
                raise FundlabError("DATA_DIRECTORY_UNAVAILABLE", "无法确定当前用户的数据目录，请检查Windows用户环境后重试。")
            return cls(Path(local) / "QFund")
        return cls(Path(__file__).resolve().parents[2])
