# 公开文件范围与整理记录

更新：2026-10-25。公开源码已上传至Qqqjunlang/QFund，首次公开版本确定为1.0.0，Release尚未正式发布；按明确白名单整理并扫描候选文件，不将模式检查称为穷尽所有敏感内容的保证。

## 候选公开范围

公开集合以packaging/public-files.json为准。根目录包含README、使用说明、AGENTS、MIT LICENSE、THIRD_PARTY_NOTICES、CONTRIBUTING、CODE_OF_CONDUCT、SECURITY、SUPPORT、CHANGELOG、pyproject、依赖锁、启动/安装CMD及Git忽略/换行规则。

目录：src/fundlab/的生产源码及包资源、tests/的Python测试、scripts/的Python/CMD安装、构建及辅助入口，packaging/windows/的spec、构建依赖、installer.iss、zh-CN.isl和assets/qfund.ico，以及docs/下本文件、installation.md、installation-acceptance.md、exe-prototype.md、exe-automatic-updates.md、windows-installer.md、development.md、maintenance-guide.md、trading-calendar-maintenance.md、extensibility.md。

生产资源包括.py、网页.html/.css/.js/.svg、图表.js、日历.json和任务.ps1。递归打包时排除__pycache__、字节码、egg-info和生成文件。不要因为src目录允许公开就把其中所有文件无条件收进发布包。

安装入口已实现并完成同机隔离验证，见[安装入口验收](installation-acceptance.md)。本轮新增.github中的Issue表单、PR模板、依赖更新、Windows公开检查和手动候选构建工作流；MIT署名Qqqjunlang，包元数据同步。版本以pyproject.toml的1.0.0为准，对应标签v1.0.0；标签与Release草稿状态分别核对，不代表正式发布。

EXE安装向导已另步完成，见[第三步记录](windows-installer.md)。新增installation.py、build_installer.py、安装维护测试及打包资源可纳入逐项公开审查；编译器、生成的程序/安装包、测试登记/日志、数据及构建staging仍是本地生成或私有资料，不提交仓库。公开分发前须核对第三方依赖和可分发内容/许可。

docs/installed-use-acceptance.md为后续安装版实机使用的通用记录，可纳入公开审查；证据、下载文件、账号会话和个人数据仍留在忽略目录，不随该说明发布。

## 本地私有范围

- data/：个人行情、归档、名单、时间、计划与尝试账本。
- .local/：专用浏览器资料、运行日志、任务、截图、证据、恢复演练与本机交接。
- backups/：私人一致性备份，保留原件。
- .venv/、build/、dist/、*.egg-info/、__pycache__/：本机环境与历史/生成产物。
- docs/archive/：本机历史设计、验收和旧交接，当前整目录忽略，不进入公开仓库。若以后公开其中某份资料，先逐项审阅、修复引用和去除本机信息，再移入明确公开位置。
- .env及凭据文件：不公开，不能用发布文档替代逐文件审查。

.gitignore只影响未跟踪文件；已有Git仓库时仍须核对跟踪集合与历史，既往提交不能靠新增忽略规则撤回。

## 前一步目录整理记录

1. 原README、使用说明、AGENTS、旧设计包、.gitignore及完整docs已留存于私有快照，35份源文件逐一核对SHA256一致后才开始修改。
2. 26份历史阶段/验收/本机进度及准备文档移入docs/archive/development-history/；根目录旧设计包一并归档。既有docs/archive旧交接保留。
3. 重写公共README、使用说明、AGENTS、维护和日历说明；新增安装说明、开发说明、公开范围清单；持续改进文档保留通用约定并去除本机阶段入口。
4. .gitignore新增docs/archive/，本地交接入口保存在.local/，公共首页不再指向私人历史包或原作者Python路径。

历史文档移动后不作为公共使用入口；完整原路径结构与原相对链接保存在私有快照。归档内部旧链接不是公共文档链接验收对象，不将旧历史链接解释为当前运行要求。

## 发布前剩余工作

- 用户确认第二台电脑安装及启动，剩余跨电脑业务复验暂缓，保留真实覆盖范围。
- 源码仓库为[Qqqjunlang/QFund](https://github.com/Qqqjunlang/QFund)；版本同步、Release草稿、分支规则、私人漏洞报告和实际GitHub工作流状态仍须分别核对，不能虚构已通过状态。
- 白名单候选已做高置信度凭据/个人路径检查；发布前继续人工审阅实际跟踪集合、历史及新二进制组成，新增资源重新扫描。
- 公开测试与私有录制模块分开，当前记录见[本轮整理](github-preparation.md)。
- 新源码、wheel与含许可材料的安装候选单独生成，文件清单及SHA256保留；Releases附件与源码仓库分开，不提交EXE或历史包。
- 当前备份脚本的源码后缀白名单漏SVG，需另步修复并验证；本轮没有改维护代码。

前一步目录整理未改src/、tests/或scripts/，未安装依赖、运行测试或启动服务。本次安装小步新增安装CMD、scripts/install_environment.py和tests/test_install_environment.py，在私有副本内安装并验证，结果见安装入口验收；没有改生产业务模块、查询/修改正式Windows任务或初始化/推送Git仓库。
