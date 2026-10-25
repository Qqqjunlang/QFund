# 贡献指南

欢迎提交问题、改进建议和Pull Request。请先阅读[README](README.md)、[开发说明](docs/development.md)及[持续改进约定](docs/extensibility.md)。

## 开始开发

使用Windows、Microsoft Edge和Python 3.12，在完整源码目录执行“安装环境.cmd”。已有环境仅检查，不自动覆盖；手动安装见[安装说明](docs/installation.md)。

```powershell
.\.venv\Scripts\python.exe scripts\run_public_tests.py
.\.venv\Scripts\python.exe scripts\check_public_files.py
```

公开测试使用临时数据和测试替身，不访问源站或安装真实系统任务。依赖本机录制和个人行情的test_stage_handoff.py不纳入公开检查；不要上传这些私人资料补齐样本。GitHub Actions的Windows检查配置见[CI工作流](.github/workflows/ci.yml)。

## 提交改动

1. 为具体问题建立分支，保持改动集中，避免顺手大重构。
2. 网页、CLI和自动任务复用现有业务服务；采集只使用真实源数据，测试样本不得进入生产。
3. 数据结构变更须说明版本、迁移和失败处理；升级保留行情、名单、时间和调度去重记录。
4. 按变更运行相关测试，更新对应说明。在PR中写明问题、行为变化、实际检查与尚未检查的部分。
5. 不提交data/、.local/、backups/、虚拟环境、旧发布包、登录资料或凭据。

提交前查看差异；新文件尤其需要审阅。一般问题使用Issue模板，安全问题按[安全政策](SECURITY.md)私下报告。参与讨论请遵守[行为准则](CODE_OF_CONDUCT.md)。

贡献按仓库[MIT许可证](LICENSE)提交；第三方代码、素材和许可证必须保留原始归属。不要提交权利来源不清的内容。
