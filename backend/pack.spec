# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：CustomTrader。

用法（在 backend/ 目录下执行）：
    pyinstaller pack.spec

跨平台：C 扩展二进制同时 glob ``.pyd``(Windows)与 ``.so``(Linux)。
"""

import os
from pathlib import Path

BASE = Path(os.getcwd()).resolve()
ROOT = BASE.parent
FRONTEND_DIST = ROOT / "frontend" / "dist"

# ========== 收集前端构建产物 ==========
frontend_tree = Tree(str(FRONTEND_DIST), prefix="frontend/dist")

# ========== 分析依赖 ==========
a = Analysis(
    ["run.py"],
    pathex=[str(BASE)],
    binaries=[],
    datas=[
        (str(BASE / ".env.example"), "."),
        (str(BASE / "strategies"), "strategies"),
        (str(BASE / "mcp_server" / "standalone_client.py"), "mcp_server"),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "sqlalchemy",
        "sqlalchemy.ext.asyncio",
        "sqlalchemy.orm",
        "app.api.routes",
        "app.api.websocket",
        "app.api.websocket.bridge",
        "app.api.websocket.endpoint",
        "app.api.websocket.manager",
        "app.api.deps",
        "app.api.schemas",
        "app.api.routes.options",
        "app.core.engine",
        "app.core.engine.base",
        "app.core.engine.log_engine",
        "app.core.engine.main_engine",
        "app.core.engine.oms_engine",
        "app.core.engine.risk_engine",
        "app.core.event",
        "app.core.event.engine",
        "app.core.event.type",
        "app.core.gateway",
        "app.core.gateway.base",
        "app.core.gateway.ctp",
        "app.core.gateway.ctp.ctp_gateway",
        "app.core.gateway.ctp.mapping",
        "app.core.gateway.stock",
        "app.core.gateway.stock.stock_gateway",
        "app.core.strategy",
        "app.core.strategy.array_manager",
        "app.core.strategy.backtesting",
        "app.core.strategy.bar_generator",
        "app.core.strategy.engine",
        "app.core.strategy.loader",
        "app.core.strategy.template",
        "app.core.strategy.option_backtesting",
        "app.core.strategy.option_template",
        "app.core.strategy.option_strategies",
        "app.core.strategy.validator",
        "app.core.strategy.strategies",
        "app.core.strategy.strategies.breakout_failure_short",
        "app.core.strategy.strategies.continuous_move",
        "app.core.strategy.strategies.donchian",
        "app.core.strategy.strategies.ma_break_retrace",
        "app.core.strategy.strategies.ma_cross",
        "app.core.strategy.strategies.rounded_bottom",
        "app.core.constant",
        "app.core.converter",
        "app.core.object",
        "app.core.utility",
        "app.db",
        "app.db.data_engine",
        "app.db.mappers",
        "app.db.models",
        "app.db.session",
        "app.ai",
        "app.ai.engine",
        "app.ai.presets",
        "app.ai.prompts",
        "app.ai.tools",
        "app.config",
        "app.conversations",
        "app.ctp_config",
        "app.sim_config",
        "app.stock_config",
        "app.watchlist",
        "app.state",
        "app.services",
        "app.services.backtest_manager",
        "app.services.bar_recorder",
        "app.services.option_pricing",
        "app.services.option_sync",
        "app.auth",
        "mcp",
        "anthropic",
        "openai",
        "openctp_ctp",
        "requests",
        "httpx",
        "tzdata",
        "bcrypt",
        "webview",
    ],
    hookspath=[],
    hooksconfig={},
    excludes=[
        # 注意：不能排除 http.server —— pywebview 本地服务后端依赖它
        "tkinter", "matplotlib", "scipy", "PIL",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "wx", "setuptools", "pip", "unittest",
        "turtle", "test", "pdb", "profile",
    ],
)

# ========== 收集 C 扩展二进制（跨平台）==========
import openctp_ctp
ctp_dir = Path(openctp_ctp.__file__).parent
for ext_file in list(ctp_dir.glob("*.pyd")) + list(ctp_dir.glob("*.so")):
    a.binaries.append((f"openctp_ctp/{ext_file.name}", str(ext_file), "BINARY"))

# ========== PYZ ==========
pyz = PYZ(a.pure)

# ========== EXE ==========
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    frontend_tree,
    name="CustomTrader",
    icon="../assets/icons/custom-trader.ico",
    debug=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory=".",
)
