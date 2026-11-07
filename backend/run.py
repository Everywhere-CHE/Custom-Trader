"""CustomTrader 启动入口。

供 PyInstaller 打包使用，避免相对导入问题。
"""
import logging
import os
import sys
from pathlib import Path

# 确保当前目录在 sys.path 中，使 `app` 包可导入
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# 设置 CT_DATA_DIR 环境变量（默认指向 exe 同级的 data/ 目录）
# 兼容旧版 QT_DATA_DIR 变量名
exe_dir = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else backend_dir
data_dir = (
    os.environ.get("CT_DATA_DIR", "")
    or os.environ.get("QT_DATA_DIR", "")
    or str(exe_dir / "data")
)
os.environ.setdefault("CT_DATA_DIR", data_dir)
os.environ.setdefault("QT_DATA_DIR", data_dir)

# 数据库迁移：项目改名 quant_trader → custom_trader，旧库文件原地改名
_old_db = Path(data_dir) / "quant_trader.db"
if _old_db.is_file():
    _new_db = Path(data_dir) / "custom_trader.db"
    if not _new_db.exists():
        _old_db.replace(_new_db)
        for _suffix in ("-wal", "-shm"):
            _side = Path(str(_old_db) + _suffix)
            if _side.exists():
                _side.replace(Path(str(_new_db) + _suffix))

# 从 exe 同级的 .env 加载配置（CTP 账号、AI API key 等）
# 打包时 .env 不会被打进 exe 内部，需要用户自己放在 exe 旁边
env_file = exe_dir / ".env"
if env_file.exists():
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key not in os.environ:
                os.environ[key] = value

# 确保数据库路径指向 exe 同级的 data/ 目录，而不是 PyInstaller 临时目录
# 注意：必须覆盖 .env 中已有的 DATABASE_URL，不能用 setdefault
db_path = Path(data_dir) / "custom_trader.db"
os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"

# PyInstaller 打包为 GUI 模式（console=False）时 sys.stderr 为 None，
# uvicorn 的日志配置会因 stderr.isatty() 调用而崩溃。
# 此处将日志重定向到文件，同时确保 stderr 不为 None。
if sys.stderr is None:
    log_file = Path(data_dir) / "custom_trader.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    sys.stderr = open(str(log_file), "a", encoding="utf-8")

def _read_remote_url() -> str:
    """客户端模式配置：环境变量 CT_REMOTE_URL 或程序目录 remote.url 文件。

    设置后本程序不启动本地引擎，仅作为远程服务器的桌面客户端壳。
    """
    url = os.environ.get("CT_REMOTE_URL", "").strip()
    if not url:
        f = exe_dir / "remote.url"
        if f.exists():
            url = f.read_text(encoding="utf-8").strip().splitlines()[0].strip() if f.read_text(encoding="utf-8").strip() else ""
    return url.rstrip("/")


if __name__ == "__main__" and _read_remote_url():
    # ---------- 客户端模式：纯壳连远程服务器，数据在服务器 ----------
    remote_url = _read_remote_url()
    try:
        import webview

        webview.create_window(
            "CustomTrader 量化交易终端",
            remote_url,
            width=1440,
            height=900,
            min_size=(1100, 680),
        )
        webview.start()
    except Exception as gui_error:  # noqa: BLE001
        print(
            f"[CustomTrader] 原生窗口不可用（{gui_error}），"
            f"降级为浏览器打开远程服务器。若提示证书错误，"
            f"请先将服务器证书安装到本机『受信任的根证书颁发机构』。"
        )
        import webbrowser

        webbrowser.open(remote_url)
    sys.exit(0)

from app.main import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    import webbrowser

    # GUI 模式下 stderr 被重定向到文件，不是 TTY，
    # 显式关闭 uvicorn 日志颜色，避免 DefaultFormatter 内部调用 stderr.isatty()
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(message)s",
                "use_colors": False,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                "use_colors": False,
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO"},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
        },
    }

    # 绑定地址/端口读取 Settings（.env 的 HOST/PORT 可覆盖，默认仅本机）
    from app.config import get_settings

    settings = get_settings()
    url = f"http://127.0.0.1:{settings.port}"

    # 窗口模式选择：CT_GUI=window|browser|none
    # 默认：打包版 window（原生窗口），源码运行 browser
    gui_mode = os.environ.get("CT_GUI") or (
        "window" if getattr(sys, "frozen", False) else "browser"
    )

    if gui_mode == "none":
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            reload=False,
            log_level="info",
            log_config=log_config,
        )
        sys.exit(0)

    # ---- 先在后台线程启动服务，再打开界面 ----
    server_config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
        log_config=log_config,
    )
    server = uvicorn.Server(server_config)
    import threading

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # 等待服务就绪（最长 15 秒）
    import time as _time

    for _ in range(75):
        if server.started:
            break
        if not server_thread.is_alive():
            break
        _time.sleep(0.2)

    if gui_mode == "window":
        try:
            import webview

            webview.create_window(
                "CustomTrader 量化交易终端",
                url,
                width=1440,
                height=900,
                min_size=(1100, 680),
            )
            # 阻塞直到用户关闭窗口；随后优雅停服退出
            webview.start()
            server.should_exit = True
            server_thread.join(timeout=8)
            os._exit(0)
        except Exception as gui_error:  # noqa: BLE001
            # Linux 缺 WebKit/GTK、Windows 缺 WebView2 等场景：
            # 自动降级为浏览器模式
            print(
                f"[CustomTrader] 原生窗口不可用（{gui_error}），"
                f"降级为浏览器模式。可安装系统依赖后重试，"
                f"或设置 CT_GUI=browser 固定浏览器模式。"
            )

    # 浏览器模式（或窗口降级）：服务已在后台线程运行，
    # 打开默认浏览器标签页后主线程等待退出信号
    if server.started:
        webbrowser.open(url)

    try:
        server_thread.join()
    except KeyboardInterrupt:
        server.should_exit = True