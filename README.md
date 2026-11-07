# CustomTrader 量化交易终端

<p align="center">
  <img src="assets/icons/icon-256.png" width="96" alt="CustomTrader" />
</p>

跨平台（Windows / Linux）**量化交易桌面终端**：FastAPI + React 19 构建，
数据 100% 保存在本机。内置 A股/期货/期权行情、多标的组合回测、
原生桌面窗口，开箱即用。

> ⚠️ 免责声明：本软件仅供学习研究，不构成投资建议。据此交易，风险自负。

## 功能特性

- **行情终端**：A股/ETF 实时行情、K 线（多周期 + 关键价位标注）、五档盘口
- **期权**：期权链 T 型报价（IV / Delta）、ATM 隐波期限结构、多腿组合回测
  （牛市/熊市价差、铁鹰、跨式、备兑等 8 种模板 + Walk-Forward 参数优化）
- **策略引擎**：vn.py 风格 CtaTemplate，事件驱动，实盘与回测同构
- **回测**：单标的 bar 回放 + Walk-Forward / K 段稳健参数优化
- **监控守护**：条件告警（自选分组）、模拟盘跟单、AI 盘后复盘
- **AI 助手**：接入 Anthropic / OpenAI 协议模型，工具化查询行情与持仓
- **数据全部本机**：SQLite 数据库 + JSON 配置，程序目录即完整备份

## 安装（桌面版）

### Windows

1. 下载 `CustomTrader-windows-x64.zip` 并解压到任意目录
2. 双击 `install.bat`（创建桌面/开始菜单快捷方式）
3. 双击「CustomTrader 量化交易终端」图标启动
4. 首次登录：`admin / admin`（登录后请立即在设置页修改密码）

数据保存在程序目录的 `data\` 下——拷贝该目录即完整备份，删除目录即卸载。

### Linux

```bash
tar xzf CustomTrader-linux-x64.tar.gz
cd CustomTrader-linux-x64
./install.sh          # 装入 ~/.local/share/custom-trader 并创建菜单/桌面图标
custom-trader         # 或从应用菜单启动
```

数据保存在 `~/.local/share/custom-trader/data`。

首次登录：`admin / admin`（登录后请立即在设置页修改密码）。

> Linux 原生窗口需要 WebKit（缺省自动降级为浏览器打开）：
> `sudo apt install python3-gi python3-gi-cairo gir1.2webkit2gtk-4.1`

## 数据源配置（应用内「设置」页）

| 数据 | 网关 | 说明 |
|---|---|---|
| A股/ETF 行情 | STOCK | 无需配置，真实公开行情 |
| 期货/期权分时 | FUYAO（同花顺数据） | 需填入 fuyao API Key（[fuyao.aicubes.cn](https://fuyao.aicubes.cn) 申请），分钟级真实行情 |
| 期货交易 | CTP | 填期货公司前置地址与账号；模拟环境可用 [openctp](https://github.com/openctp/openctp) |

## 从源码构建

```bash
# 1. 安装依赖（Python 3.11+ / Node.js 18+）
install.bat        # Windows
./install.sh       # Linux / macOS

# 2. 开发模式（前端热更新）
start.bat          # Windows
./start.sh         # Linux / macOS

# 3. 构建发行包
build.bat          # Windows → backend\dist\CustomTrader.exe（配 packaging\windows\ 打包分发）
./build.sh         # Linux   → dist/CustomTrader-linux-x64.tar.gz
```

应用窗口形态由环境变量 `CT_GUI` 控制：`window`（原生窗口，默认）/
`browser`（自动打开浏览器）/ `none`（仅后台服务）。

### 客户端模式（连接远程服务器）

如果只想把本程序当作某台服务器上 CustomTrader 的**桌面客户端**（数据在
服务器，本机不另存数据）：在程序目录放置 `remote.url` 文件（内容一行写
服务器地址，如 `https://your-server.com`），启动后原生窗口直接加载该服务
器页面，不启动本地引擎。参见 `packaging/remote.url.example`。

## 技术栈

FastAPI · React 19 · antd 6 · ECharts · SQLite · vn.py 风格事件引擎 ·
PyInstaller · pywebview

## 目录结构

```
backend/     FastAPI 服务 + 量化引擎（策略/回测/网关/数据）
frontend/    React 前端
packaging/   桌面版安装脚本（Windows / Linux）
assets/      应用图标
research/    研究脚本（非平台代码）
```

## License

[MIT](LICENSE)
