# 开发说明

## 架构与入口

QFund使用Python标准库HTTP服务、HTML/CSS/JS网页、SQLite、Playwright、openpyxl和Plotly；未采用Streamlit或pandas。

| 目录/模块 | 职责 |
|---|---|
| src/fundlab/config.py | 集中路径配置，源码按项目位置、EXE按用户目录推导数据根 |
| installation.py | 安装独占/运行共享锁、任务暂停恢复与维护日志，不迁移或删除用户数据 |
| collectors/、parsers/ | 专用Edge、真实源窗口、字段和单位解析 |
| storage/、exports/ | 事务累积、运行与修订记录、Excel/CSV/元数据发布 |
| services/ | 查询、名称解析、本人登录、更新/重试导出、分析、名单、维护、导入与分组服务 |
| analysis/ | 溢价率与价格序列、分位/均线计算、共享图表模板及浏览器交互 |
| scheduling/ | 日历资源、北京时间、计划/账本/恢复、Windows任务与worker |
| web/ | 回环HTTP、服务识别、任务状态、iframe通信与静态界面 |
| launcher.py | 环境检查、就绪判断、服务复用、浏览器启动 |
| scripts/ | 标准库安装入口、辅助登录/更新、维护备份、开发兼容入口 |
| tests/ | 离线行为检查及可选私有录制衔接检查 |

网页、CLI和worker调用同一业务服务。不要为新入口复制采集或分析算法。默认监听127.0.0.1，保持Host/来源校验、静态白名单、图iframe隔离与单项目单服务保护。

### 本次更新中的行情存储版本

开发中的行情库版本为3，版本2新增字段覆盖、单日删除限制、数据修订号和维护审计，版本3新增基金回收状态、可恢复维护操作及多对多分组；名单库和调度库版本未改变。只读快照兼容行情版本1、2和3，不执行升级；Repository写入口首次接触旧版时，在写事务内保留SQLite一致性备份（数据库旁的`.schema1-*.bak`或`.schema2-*.bak`，包含已提交WAL），再事务升级，失败回滚。版本不明或高于支持范围时拒绝写入。新建空库直接完成初始化，不生成无数据备份。

基础行情保留源值，手动覆盖可保护具体字段及显式缺失值；单日删除仅保留限制而不清除基础记录。查询、分析、导出统一消费有效快照：手动溢价优先，其次在价格/净值受手动覆盖时使用重算值，否则保持旧版源站溢价口径；不能把缺失当零，也不能把净值日期当作价格日期。字段事务接口接收内部比例小数，页面百分数换算应在服务输入边界完成。

本地维护服务支持单日新增、修改、删除限制/解除及字段保护解除；服务输入比例使用百分数（12.5表示12.5%），存储事务接口使用内部比例小数。新增单日以明确标记的local运行记录保存身份，输入字段默认保护，原始来源不伪装采集记录。

采集、导出、维护和分组共用项目写锁，业务服务在最外层获取一次，不递归获取。名单修改另用名单锁，保留采集期间停用/移除尚未开始基金的既有行为；回收按项目锁→名单锁顺序执行。回收/恢复先持久化过渡状态，幂等移出名单后再完成行情库事务；若中断，基金保持不可读写状态，后续维护、采集或导出写入口先恢复未完成操作，不在普通读取中隐式写入。恢复不恢复原分组和定时名单。正常查询/分析/导出不包含回收基金，已加载的后台名单也不能绕过提交前状态校验。

网页“数据管理”已接入维护、文件导入、基金回收站与分组服务；查询页集中只读查看、筛选和下载。导入先解析预览，确认时重新核对目标状态并在写锁内提交；已有日期和删除限制日期跳过，不能绕过字段保护。不要将开发源码用于正式数据写入验收，也不要用旧版程序写入升级后的库。1.1.0候选已验证同机隔离的首版升级及未来版本清单拒绝，旧安装器仍不能追溯修补。正式数据维护与本地合成验证分开处理。

### 图表与浏览器能力

价格概览和当前/指定价格分位集中在analysis/price.py，分位以所选区间内小于等于目标价格的有效收盘价占比计算，最新有效价格取全部历史。services/analysis.py提供一致快照，网页不重复计算公式，异步结果须校验当前基金与分析状态。

工作台、两类内嵌图表及独立离线图共享browser_compat.js：缺replaceChildren时补实际使用能力，请求ID优先randomUUID，否则使用getRandomValues；没有安全随机源时明确停止提交。静态资源白名单、图表脚本哈希CSP与iframe边界保持。Edge能力缺失模拟不等于原旧浏览器实测，未承诺缺少现代JS语法、dialog或ResizeObserver的浏览器可用。

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

安装入口的保护检查在test_install_environment.py，验证不覆盖既有环境、失败停止、路径及安装互斥。安装CMD和scripts/install_environment.py只创建/检查项目环境，不调用业务更新、登录或Windows任务管理。验证时分别记录真实新建环境与HTTP启动结果，以及使用替身的安装失败检查；详细本机证据不纳入公开源码。

`test_stage_handoff.py`读取本机 `.local/evidence/` 的录制文件和只读行情库。公开源码不包含这些私有资料，缺失时按设计跳过；分别报告公开测试通过数和私有衔接跳过数。不要上传个人数据库、改成伪造生产数据，或用历史通过数冒充新环境结果。

其他测试中构造的样本仅验证逻辑，不等于真实源站或本人账号已成功。计划任务模拟、真实系统任务验收与真实采集分别表述。公开CI配置为.github/workflows/ci.yml，使用scripts/run_public_tests.py显式排除私人衔接模块；是否已在GitHub执行以真实运行记录为准。

## 构建与资源

Windows目录式EXE通过scripts/build_exe.py和packaging/windows/qfund.spec构建；scripts/frozen_entry.py复用原工作台，scripts/frozen_worker_entry.py生成无控制台QFundWorker.exe复用调度执行器。两入口共享程序资源，数据与程序分开。构建步骤见[发布流程](releasing.md)。构建工具仅安装到独立环境，不加入运行依赖。

安装向导由scripts/build_installer.py及packaging/windows/installer.iss、zh-CN.isl、assets/qfund.ico生成。必须先准备完整目录式程序，维护助手在临时副本执行并跨越替换/删除持锁；不能把任务归属或数据保留逻辑移入网页重复实现。构建方法、测试模式与实测边界见[Windows安装版](windows-installer.md)。

包配置在pyproject.toml，生产资源包括图表JS、网页HTML/CSS/JS/SVG、日历JSON和任务PS1。后续打包应检查这些文件确实进入产物，并验证新包与本次源码版本一致。

当前分发名为fundlab-local，源码版本字段为1.1.0；首版1.0.0已发布，本次1.1.0候选已通过同机隔离安装验收，尚未发布。现有环境元数据与源码字段可能不一致，正式构建前须同步并核对实际包版本、内容清单及SHA256。原有dist包含历史不同批次产物，保留原件，不纳入仓库；历史包不能代替当前源码候选。wheel不包含根目录CMD和虚拟环境，不是Windows安装器。

维护备份脚本有自己的资料白名单，目录或资源变化时要检查它的覆盖情况。目前源码备份后缀集合尚未包含SVG，根文件清单也未包含新增的安装环境.cmd；新增packaging资源也需核对。本轮未修复备份代码，不宣称备份包含全部新增构建材料。该脚本按源码任务入口设计，不将其直接作为安装版维护入口；适配另行验证。

## 持续维护

修改模块边界、数据结构或可变规则前阅读[持续改进约定](extensibility.md)。公共文件审查见[公开文件清单](public-files.md)。项目MIT署名Qqqjunlang，源码仓库为[Qqqjunlang/QFund](https://github.com/Qqqjunlang/QFund)，本次更新的Release仍在准备中；CI是否通过以实际运行记录为准。发布材料见[发布流程](releasing.md)。
