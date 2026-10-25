# 安装说明

普通使用者可使用新的[Windows EXE安装版候选](windows-installer.md)，无需另装Python。EXE的同机隔离安装、覆盖安装和卸载保留数据已验证，尚未公开发布或完成第二台电脑检查。下文是继续保留的源码安装路线。

源码入口状态：安装环境.cmd已实现，同机两个中文/空格隔离目录的Python 3.12离线安装、重复检查及空数据服务启动/复用/正常退出通过。依赖来自本机原始wheel缓存；联网下载曾遇TLS失败，第二台电脑或联网源码安装尚待验收。

## 准备条件

- Windows，已安装 Microsoft Edge，可联网安装依赖和访问数据源。
- Python 3.11或更新版本；现有开发环境为3.12，新用户路线先以3.12作为验证目标。
- 将完整源码放在当前用户可写、可以长期保留的位置。不要只复制src文件夹，或在系统受保护目录中安装。

不需要Codex、AI账号或API Key。每位用户在本机由本人完成集思录登录，源站可访问范围由实际账号和站点状态决定。

## 双击安装源码环境

安装Python后，完整解压项目，双击根目录“安装环境.cmd”。程序优先检查已有项目环境；没有时依次查找Python启动器的3.12、3.11、默认Python 3及PATH中的python，并由安装程序拒绝低于3.11的版本。

安装仅创建本项目.venv，安装requirements.lock.txt及可编辑源码，然后检查依赖版本、Edge、数据目录和资源。完成后双击“启动工作台.cmd”。首次安装联网从官方PyPI下载，网络或证书失败不会显示完成，不应关闭证书校验解决问题。

如果Python未被自动发现，可在PowerShell中使用本人实际解释器路径直接执行：

```powershell
& '你的Python安装目录\python.exe' -X utf8 scripts\install_environment.py
```

也可临时设置 `QFUND_PYTHON` 为完整python.exe路径，再执行安装CMD；它仅用于首次新建环境，不改变系统默认Python。不要把占位路径原样复制使用。

已有.venv时只检查，不联网修复或覆盖。环境不完整、锁定版本不匹配或安装中断时，原环境保留并返回失败；先保留原目录，再在新的完整源码目录安装验证。若原目录已有自动任务，搬迁须按下文先处理旧任务。首次安装失败残留的.venv也不会被下一次双击擅自重建。

## 手动准备项目环境（技术入口）

在项目文件夹打开PowerShell。先确认Python解释器；如果已安装Windows的Python启动器，可执行：

```powershell
py -3.12 --version
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\.venv\Scripts\python.exe -m pip check
```

这些命令用于全新目录。若没有py启动器，应使用本人已核对的Python 3.12完整路径替代前两行的 `py -3.12`，不能盲用可能指向旧版本的系统python。手动命令不具有安装脚本的全部检查和互斥保护，普通使用者优先双击安装。

已有 `.venv` 时不要直接覆盖：可先执行其python的版本检查及 `-m pip check`，无法运行时先保留旧环境，再制定修复方案。现有数据与设置一律保留。依赖安装失败时先解决报错，不继续认定安装成功。

程序通过Playwright控制已安装的Edge，当前实现不要求另外下载Chromium。以上命令不启用自动任务，也不替用户登录。

维护人员可用 `scripts/install_environment.py --wheelhouse 本地wheel目录` 完全离线安装；目录须包含锁定依赖及当前Python/Windows架构所需原始wheel，本项目不随源码附带这些依赖。无此参数时通过官方PyPI安装，安装脚本使用pip隔离配置，企业代理或专用源需另行处理。`QFUND_SETUP_NO_PAUSE=1`仅供自动化验证CMD退出码。

## 启动

双击根目录 `启动工作台.cmd`，或执行：

```powershell
.\.venv\Scripts\python.exe -m fundlab.launcher
```

没有自动打开浏览器时访问运行窗口显示的本地地址，默认 `http://127.0.0.1:8765/`。保留运行窗口，停止前等待活动任务完成，再按Ctrl+C。

首次打开没有基金数据是正常状态，按[使用说明](../使用说明.md)本人登录并获取。自动任务需以后主动启用。

## 安装边界

当前默认数据根目录根据源码位置推导。以上采用可编辑源码安装，让程序仍使用项目内data/和.local/；不要把它等同于任意位置安装wheel已完成适配。

虚拟环境依赖基础Python位置，不能直接搬到新电脑。目录或解释器改变前须处理旧Windows任务，新路径重建环境并由本人重新登录；自动任务不会跟随文件夹自动迁移。

项目许可证已采用MIT、署名Qqqjunlang，当前版本入口为pyproject.toml。远端仓库和正式发布尚未建立，不提供虚构下载链接或PyPI安装命令。历史dist包各为当时快照，不能当作本轮最新发布材料，具体流程见[发布说明](releasing.md)。
