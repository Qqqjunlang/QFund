# GitHub首次发布整理

2026-10-24。用户要求按GitHub常见开源规范整理，并选择MIT、版权署名Qqqjunlang。当前是本地待发布材料，尚未创建或上传GitHub仓库。

| 位置 | 内容 |
|---|---|
| README.md | 用户入口、安装、使用、能力和边界 |
| LICENSE / THIRD_PARTY_NOTICES.md | 项目MIT与第三方归属 |
| CONTRIBUTING.md / CODE_OF_CONDUCT.md | 贡献步骤与社区行为 |
| SECURITY.md / SUPPORT.md | 安全报告与普通帮助 |
| CHANGELOG.md | 未发布改动记录 |
| .github/ISSUE_TEMPLATE/ | 中文问题和功能表单 |
| .github/PULL_REQUEST_TEMPLATE.md | 改动、验证及兼容说明 |
| .github/workflows/ci.yml | Windows公开离线检查及wheel构建 |
| .github/dependabot.yml | Actions与Python依赖的月度更新建议 |
| packaging/public-files.json | 公开源码白名单 |
| scripts/check_public_files.py | 白名单、高置信度泄漏与已跟踪文件检查 |
| scripts/build_source_archive.py | 经检查的源码ZIP与SHA256清单 |
| docs/releasing.md | 标签、草稿、附件和发布步骤 |

公开文件检查仅扫描候选公开集合，不读取本机登录资料。不存在Git仓库时会明确报告尚未检查跟踪集合；高置信度模式扫描也不等于穷尽所有形式的敏感内容。发布材料仍须人工审阅。

旧安装包和旧源码包原样保留，不把它们重命名为本轮新包。当前公开版版本以pyproject.toml为唯一入口；GitHub地址确定后再添加实际链接和状态徽章，不使用占位仓库或虚构CI通过标记。

具体构建与发布步骤见[发布流程](releasing.md)，能力及真实验收范围见[安装版实测](installed-use-acceptance.md)。

## 本轮检查结果

- 从白名单创建独立公开副本，并用缓存的锁定wheel新建项目环境，安装及pip check通过；没有复制个人数据库或登录会话。
- 在该副本运行19个公开测试模块，共252项通过、无跳过；私人test_stage_handoff模块明确排除。新增发布工具7项检查包含私有资料排除、疑似凭据不回显、禁止覆盖旧包及跟踪集合核对。
- 两个工作流使用actionlint 1.7.12检查通过；工具来自上游官方发布并核对SHA256。这里只是本地语法检查，未在GitHub运行CI。
- 新wheel核对版本0.3.0、MIT、作者Qqqjunlang及许可/静态/日历/任务资源。EXE收集50份许可文本，安装器另附Inno许可。
- 新许可安装候选在独立中文目录安装、无外部Python PATH诊断及空数据服务启动/正常退出、卸载保留测试文件通过；临时登记及快捷方式已移除，没有获取真实行情。

源码副本的早期保护测试发现测试样本在Windows写入CRLF，而断言写死LF；改为与原文件字节核对，归档逻辑未改。wheel首次构建遇工具沙箱全局pip缓存权限问题，使用--no-cache-dir后成功，没有修改缓存ACL。工作流同步采用该参数。

产物按新目录另存，旧安装包保留；本轮没有替用户升级已安装程序，没有创建远端仓库、提交、标签或发布Release。完整证据在忽略的.local/github-preparation-2026-10-24/中。


## 2026-10-25 版本统一

公开源码仓库已建立为[Qqqjunlang/QFund](https://github.com/Qqqjunlang/QFund)。按维护者决定，首次公开版本统一为1.0.0，标签v1.0.0；源码元数据、安装器与当前下载说明同步，重新构建对应附件并保留旧0.3.0产物。Release仍只准备草稿，未正式发布。上文0.3.0检查为历史结果，不代替新版本构建验证。
