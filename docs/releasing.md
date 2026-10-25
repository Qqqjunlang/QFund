# GitHub发布流程

GitHub没有唯一强制的目录格式。本项目采用其社区文件位置、Git标签和Releases附件约定；版本从pyproject.toml读取，当前为0.3.0，计划对应标签v0.3.0。尚未创建远端仓库、标签或Release。

## 仓库和下载附件

源码仓库包含README、MIT LICENSE、贡献/行为/安全/支持指南、CHANGELOG、src、tests、scripts、packaging、公共docs和.github配置。完整清单由packaging/public-files.json定义，发布前执行scripts/check_public_files.py；扫描只输出疑似问题的位置，不打印凭据值。

data、.local、backups、虚拟环境、构建目录和历史资料均不进入仓库。EXE不提交到Git历史，作为Releases附件；GitHub自动提供标签对应的源码ZIP/tar.gz，本项目也提供经过白名单检查的源码ZIP及清单。

## 本地准备

```powershell
.\.venv\Scripts\python.exe scripts\check_public_files.py
.\.venv\Scripts\python.exe scripts\run_public_tests.py
.\.venv\Scripts\python.exe scripts\build_source_archive.py --output-dir dist\新的源码输出目录
```

公开测试不加载需要私人录制的test_stage_handoff模块。不要把未执行的私有/联网/第二电脑检查算作通过。公共CI使用Windows 2022运行机、Python 3.12、锁定依赖和项目虚拟环境；任务仅有contents:read，第三方Actions固定完整提交。配置存在不代表已在GitHub成功运行。

.github/workflows/build-release.yml仅手动运行：独立构建环境生成源码、wheel、带许可的程序与安装器，验证Inno编译器的固定SHA256和签名后使用，最后通过prepare_release_assets.py汇总匹配版本的候选附件。工作流仅上传可审阅的Actions产物，不创建Release或推送标签。

目录式EXE在独立构建环境安装运行锁定依赖及packaging/windows/requirements-build.txt后，以scripts/build_exe.py生成；再用完整Inno Setup 6.7.3目录运行scripts/build_installer.py。两个入口都要求新的输出目录。新包附带项目/第三方许可，详见[第三方声明](../THIRD_PARTY_NOTICES.md)。不覆盖已有安装目录或历史候选。

## 发布检查

1. 确认MIT署名Qqqjunlang、第三方许可、公开清单和敏感内容检查结果。检查Git实际跟踪集合与历史；.gitignore不能清除既有提交。
2. 确认源码、wheel、EXE与安装器来自同一份源码；版本号、清单、SHA256一致。更新CHANGELOG和Release说明中的实际结果。
3. 在仓库设置启用私人漏洞报告；维护者配置分支保护或规则，要求通过Windows public checks。不要虚构仓库URL、徽章、维护邮箱或已启用状态。
4. 在最终提交创建v版本标签，以该标签建立Release草稿。附件建议为QFund-Setup-版本.exe、QFund-版本-source.zip、wheel、SHA256SUMS.txt和文件清单。
5. 检查草稿里的安装条件、数据位置、升级/卸载、当前日历覆盖、未签名状态及实际验证范围，再由维护者发布。首次可按实际成熟度标记Pre-release。

创建仓库、上传代码、创建标签与发布Release另行执行；本地整理不自动向外发送文件。CI不携带登录资料，不自动登录源站或真实采集。自动依赖更新只提交建议，不自动合并或发布。

依据：[GitHub社区文件](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/about-community-profiles-for-public-repositories)、[Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)、[Actions安全](https://docs.github.com/en/actions/reference/security/secure-use)。
