# 开发说明

## 架构与入口

QFund使用Python标准库HTTP服务、HTML/CSS/JS网页、SQLite、Playwright、openpyxl和Plotly；未采用Streamlit或pandas。

| 目录/模块 | 职责 |
|---|---|
| src/fundlab/config.py | 集中路径配置，源码按项目位置、EXE按用户目录推导数据根 |
| installation.py | 安装独占/运行共享锁、任务暂停恢复与维护日志，不迁移或删除用户数据 |
| collectors/、parsers/ | 专用Edge、真实源窗口、字段和单位解析 |
| storage/、exports/ | 事务累积、运行与修订记录、Excel/CSV/元数据发布 |
| services/ | 查询、名称解析、本人登录、更新/重试导出、分析、名单服务 |
| analysis/ | 溢价序列、计算、共享图表模板及浏览器交互 |
| scheduling/ | 日历资源、北京时间、计划/账本/恢复、Windows任务与worker |
| web/ | 回环HTTP、服务识别、任务状态、iframe通信与静态界面 |
| launcher.py | 环境检查、就绪判断、服务复用、浏览器启动 |
| scripts/ | 标准库安装入口、辅助登录/更新、维护备份、开发兼容入口 |
| tests/ | 离线行为检查及可选私有录制衔接检查 |

网页、CLI和worker调用同一业务服务。不要为新入口复制采集或分析算法。默认监听127.0.0.1，保持Host/来源校验、静态白名单、图iframe隔离与单项目单服务保护。

## 环境与常用命令

先按[安装说明](installation.md)准备可编辑环境，在项目根目录使用项目解释器：

```powershell
.\.venv\Scripts\python.exe -m fundlab.launcher --no-browser
.\.venv\Scripts\python.exe -m fundlab.cli --help
.\.venv\Scripts\python.exe -m fundlab.cli status --fund 513100
.\.venv\Scripts\python.exe -m fundlab.cli analyze --fund 513100 --ma 20
.\.venv\Scripts\python.exe -m fundlab.cli schedule status
```

status/analyze/schedule status读取本地状态，缺少数据时如实提示。fundlab.cli的search、login、update会访问源站；export写本地导出；schedule install/disable/remove修改任务，run-due可能采集。不能把这些命令列为无副作用的安装检查。

辅助 `scripts/update.cmd` 调用CLI后会询问代码或名称；`scripts/preview_layout.py`仅是兼容转发入口。Python服务需要空闲时正常停止再启动才能加载模块变更。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
```

这些是可按需执行的检查命令，不是本文编写时的执行结果。按改动选取相关测试，测试与浏览器预览串行；使用临时目录，不能写入真实数据。

安装入口的保护检查在test_install_environment.py，验证不覆盖既有环境、失败停止、路径及安装互斥。安装CMD和scripts/install_environment.py只创建/检查项目环境，不调用业务更新、登录或Windows任务管理。真实新建环境与HTTP启动验收和模拟安装失败分别记录，详见[安装入口验收](installation-acceptance.md)。

`test_stage_handoff.py`读取本机 `.local/evidence/` 的录制文件和只读行情库。公开源码不包含这些私有资料，缺失时按设计跳过；分别报告公开测试通过数和私有衔接跳过数。不要上传个人数据库、改成伪造生产数据，或用历史通过数冒充新环境结果。

其他测试中构造的样本仅验证逻辑，不等于真实源站或本人账号已成功。计划任务模拟、真实系统任务验收与真实采集分别表述。公开CI配置为.github/workflows/ci.yml，使用scripts/run_public_tests.py显式排除私人衔接模块；是否已在GitHub执行以真实运行记录为准。

## 构建与资源

Windows目录式EXE通过scripts/build_exe.py和packaging/windows/qfund.spec构建；scripts/frozen_entry.py复用原工作台，scripts/frozen_worker_entry.py生成无控制台QFundWorker.exe复用调度执行器。两入口共享程序资源，数据与程序分开。见[第一步记录](exe-prototype.md)及[当前自动获取接入与验收](exe-automatic-updates.md)。构建工具仅安装到独立环境，不加入运行依赖。

安装向导由scripts/build_installer.py及packaging/windows/installer.iss、zh-CN.isl、assets/qfund.ico生成。必须先准备完整目录式程序，维护助手在临时副本执行并跨越替换/删除持锁；不能把任务归属或数据保留逻辑移入网页重复实现。构建方法、测试模式与实测边界见[Windows安装版](windows-installer.md)。

包配置在pyproject.toml，生产资源包括图表JS、网页HTML/CSS/JS/SVG、日历JSON和任务PS1。后续打包应检查这些文件确实进入产物，并验证新包与本次源码版本一致。

当前分发名为fundlab-local，首次公开版本为1.0.0，对应Git标签v1.0.0，尚未正式发布。原有dist包含历史0.3.0不同批次产物，保留原件，不纳入仓库；1.0.0须重新构建并核对内容清单及SHA256。wheel不包含根目录CMD和虚拟环境，不是Windows安装器。

维护备份脚本有自己的资料白名单，目录或资源变化时要检查它的覆盖情况。目前源码备份后缀集合尚未包含SVG，根文件清单也未包含新增的安装环境.cmd；新增packaging资源也需核对。本轮未修复备份代码，不宣称备份包含全部新增构建材料。该脚本按源码任务入口设计，不将其直接作为安装版维护入口；适配另行验证。

## 持续维护

修改模块边界、数据结构或可变规则前阅读[持续改进约定](extensibility.md)。公共文件审查见[公开文件清单](public-files.md)。项目MIT署名Qqqjunlang，源码仓库为[Qqqjunlang/QFund](https://github.com/Qqqjunlang/QFund)，Release仍在准备中；CI是否通过以实际运行记录为准。发布材料见[发布流程](releasing.md)。
