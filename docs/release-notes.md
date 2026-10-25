# QFund v0.3.0 — Release草稿

本文件供首次GitHub Release审阅，尚未发布；发布时使用对应源码提交创建v0.3.0标签，并填写实际发布日期。

## 下载与安装

普通使用者下载QFund-Setup-0.3.0.exe；需要Windows 10/11 x64和Microsoft Edge，无需另装Python。双击按向导安装，从开始菜单或桌面图标打开。

默认程序在当前用户LOCALAPPDATA/Programs/QFund，数据在LOCALAPPDATA/QFund。第一次获取前由本人在专用Edge登录。软件仍通过浏览器操作，任务结束后可在运行窗口按Ctrl+C停止。

源码使用者下载QFund-0.3.0-source.zip或GitHub的Source code，按README中的Python 3.12源码路线运行。wheel供技术使用，不是Windows安装程序。

## 包含功能

- QDII历史页面真实获取、SQLite按日期累积、修订与运行记录。
- Excel/CSV及来源说明、日期查询下载、溢价率分析与PNG保存。
- 自动更新名单、北京时间与交易日日历、独立Windows后台任务。
- 安装向导与快捷方式；原位置覆盖安装保留任务启停，卸载保留用户数据并移除对应任务。

## 当前边界

- 安装包未签名；当前交易日日历覆盖2026年。
- 初次只取得源站可访问的近期窗口，不保证完整历史或所有类别。
- 本机基本使用已实测；第二台安装/启动由用户确认，其余跨电脑业务复验暂缓。
- 软件不提供源站账号、数据访问授权或投资建议；自动获取并非软件自动升级。

## 校验与许可

使用SHA256SUMS.txt核对下载附件。QFund源码为MIT，Copyright (c) 2026 Qqqjunlang；新构建安装包的_internal/qfund-licenses保留项目与第三方许可。

发布前将本草稿与实际构建文件、版本和检查记录逐项核对；源码ZIP、wheel、安装器及清单应来自同一份源码。
