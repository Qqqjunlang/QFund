# Third-party notices

QFund自己的源码采用[MIT许可证](LICENSE)，版权署名Qqqjunlang。第三方组件仍受各自许可证约束，QFund的MIT许可不替代它们的原始声明。

以下为当前锁定依赖及构建工具的归属摘要；具体版本以requirements.lock.txt、packaging/windows/requirements-build.txt和每个安装包的许可清单为准。

| 组件 | 用途 | 上游许可摘要 |
|---|---|---|
| Python | EXE内置运行时 | PSF及Python分发包附带的其他声明 |
| Playwright 1.56.0 | 专用Edge自动化 | Apache-2.0；驱动内Node及其他依赖另有声明 |
| openpyxl 3.1.5 | Excel导出 | MIT |
| et_xmlfile 2.0.0 | XML写入 | MIT，含Python许可代码 |
| Plotly 7.1.0 | 交互图表及随附Plotly.js | MIT及上游随附声明 |
| greenlet 3.5.6 | Playwright运行依赖 | MIT AND PSF-2.0 |
| pyee 13.0.1 | 事件支持 | MIT |
| narwhals 2.27.1 | Plotly运行依赖 | MIT |
| packaging 26.3 | 版本与包信息 | Apache-2.0 OR BSD-2-Clause |
| typing_extensions 4.16.0 | 类型兼容 | PSF-2.0 |
| setuptools | 打包及部分运行支持 | MIT及随附依赖声明；源码环境84.0.0，EXE构建环境80.9.0 |
| PyInstaller 6.12.0 | 构建EXE及bootloader | GPL-2.0-or-later，附允许分发构建产物的例外 |
| pyinstaller-hooks-contrib 2025.2 | 构建钩子 | 上游随附许可证，区分工具与钩子代码 |
| altgraph 0.17.4、pefile 2023.2.7 | 构建工具依赖 | MIT |
| pywin32-ctypes 0.2.3 | 构建工具依赖 | BSD-3-Clause |
| Inno Setup 6.7.3 | 安装向导引擎 | Inno Setup License |

新的EXE构建将项目LICENSE、本文件、Python许可及安装包中依赖的上游许可文本放在 `_internal/qfund-licenses/`，并生成来源相对路径与SHA256清单。Playwright驱动的LICENSE、NOTICE、ThirdPartyNotices和Node许可一并收集。构建工具本身不作为软件功能安装到用户系统。

收集入口为scripts/collect_licenses.py；更换依赖、Python或构建工具后，应重新构建并核对实际二进制组成、许可文本及清单，不将旧安装包自动视作已包含新声明。项目没有分发Microsoft Edge或源站数据授权；Edge由使用者自行安装，数据访问仍受源站规则与本人账号权限约束。
