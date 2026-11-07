"""通知渠道：飞书 / 企业微信 / 邮件 / 系统通知。

设计纪律（借鉴 ARL/TSP）：
- **失败静默降级**：任何渠道失败只记日志，绝不阻断告警主流程，
  绝不抛出到调用方；
- **专用线程**：dispatch 只把事件丢进内存队列，网络 IO 在
  后台 worker 线程执行，不阻塞事件分发线程/行情轮询；
- 去重不在本层做（调用方用 cooldown 控制）；
- 渠道选择与事件开关由 ``notify_config.json`` 决定，设置页热改。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import queue
import smtplib
import threading
import time
from email.header import Header
from email.mime.text import MIMEText
from typing import Any

import httpx

from ..notify_config import load_notify_config

logger = logging.getLogger("custom_trader.notify")

# 事件类别 → 配置开关字段
EVENT_SWITCHES = {
    "trade": "notify_trades",
    "risk": "notify_risk",
    "strategy": "notify_strategy",
    "guardian": "notify_guardian",
}

_queue: "queue.Queue[dict | None]" = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker, name="NotifyWorker", daemon=True)
        thread.start()
        _worker_started = True


def _worker() -> None:
    while True:
        item = _queue.get()
        if item is None:
            return
        try:
            _deliver(item)
        except Exception:  # noqa: BLE001 —— 静默降级，绝不崩溃 worker
            logger.exception("通知投递异常")


# ----------------------------------------------------------------------
# 各渠道发送（同步阻塞，只在 worker 线程调用）
# ----------------------------------------------------------------------

def _feishu_sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_feishu(webhook: str, secret: str, title: str, body: str, timeout: float = 8.0) -> tuple[bool, str]:
    """飞书自定义机器人。4xx/业务错误不重试（调用方冷却已打戳）。"""
    if not webhook:
        return False, "未配置 webhook"
    payload: dict[str, Any] = {
        "msg_type": "text",
        "content": {"text": f"{title}\n{body}"[:4000]},
    }
    if secret:
        ts = int(time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = _feishu_sign(secret, ts)
    resp = httpx.post(webhook, json=payload, timeout=timeout)
    data = resp.json() if resp.status_code == 200 else {}
    if resp.status_code == 200 and (data.get("code") in (0, None) or data.get("StatusCode") == 0):
        return True, "ok"
    return False, f"HTTP {resp.status_code} {data}"


def send_wecom(webhook: str, title: str, body: str, timeout: float = 8.0) -> tuple[bool, str]:
    """企业微信群机器人，按 UTF-8 字节截断到 4096 以内。"""
    if not webhook:
        return False, "未配置 webhook"
    content = f"**{title}**\n{body}"
    while len(content.encode("utf-8")) > 4000:
        content = content[: len(content) - 200]
    resp = httpx.post(
        webhook,
        json={"msgtype": "markdown", "markdown": {"content": content}},
        timeout=timeout,
    )
    data = resp.json() if resp.status_code == 200 else {}
    if resp.status_code == 200 and data.get("errcode") == 0:
        return True, "ok"
    return False, f"HTTP {resp.status_code} {data}"


def send_email(
    host: str,
    port: int,
    use_ssl: bool,
    username: str,
    password: str,
    to_list: list[str],
    title: str,
    body: str,
    timeout: float = 12.0,
) -> tuple[bool, str]:
    """SMTP 邮件（SSL 465 / STARTTLS 587）。"""
    if not host or not to_list:
        return False, "未配置 SMTP 或收件人"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = username
    msg["To"] = ", ".join(to_list)
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
            server.starttls()
        with server:
            if username and password:
                server.login(username, password)
            server.sendmail(username, to_list, msg.as_string())
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def send_system(title: str, body: str) -> tuple[bool, str]:
    """系统通知（Windows winotify / Linux notify-send / macOS osascript），尽力而为。"""
    import platform
    import subprocess

    text = f"{title}\n{body}"[:200]
    system = platform.system()
    try:
        if system == "Windows":
            try:
                from winotify import Notification, audio  # type: ignore

                Notification(app_id="CustomTrader", title=title, msg=body[:200]).show()
                return True, "ok"
            except ImportError:
                return False, "未安装 winotify（pip install winotify）"
        if system == "Darwin":
            subprocess.Popen(
                ["osascript", "-e", f'display notification "{body[:120]}" with title "{title}"'],
            )
            return True, "ok"
        if system == "Linux":
            subprocess.Popen(["notify-send", title, body[:120]])
            return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return False, f"不支持的平台 {system}"


def _deliver(item: dict) -> None:
    """按配置把一条通知投递到所有已配置渠道。"""
    from ..config import get_settings
    from ..notify_config import load_notify_config as _load

    config = _load(get_settings())
    title: str = item["title"]
    body: str = item["body"]

    if config.get("feishu_webhook"):
        ok, msg = send_feishu(
            config["feishu_webhook"], config.get("feishu_secret", ""), title, body
        )
        if not ok:
            logger.warning("飞书通知失败: %s", msg)
    if config.get("wecom_webhook"):
        ok, msg = send_wecom(config["wecom_webhook"], title, body)
        if not ok:
            logger.warning("企微通知失败: %s", msg)
    if config.get("email_smtp_host") and config.get("email_to"):
        ok, msg = send_email(
            config["email_smtp_host"],
            int(config.get("email_smtp_port") or 465),
            bool(config.get("email_smtp_ssl", True)),
            config.get("email_username", ""),
            config.get("email_password", ""),
            list(config.get("email_to") or []),
            title,
            body,
        )
        if not ok:
            logger.warning("邮件通知失败: %s", msg)
    if config.get("system_notify"):
        ok, msg = send_system(title, body)
        if not ok:
            logger.info("系统通知未发送: %s", msg)


# ----------------------------------------------------------------------
# 对外入口
# ----------------------------------------------------------------------

def dispatch(title: str, body: str, event_kind: str = "") -> None:
    """投递一条通知（非阻塞）。

    Args:
        event_kind: trade / risk / strategy / guardian；空串 = 不做开关过滤直接发。
    """
    if event_kind:
        switch = EVENT_SWITCHES.get(event_kind)
        if not switch:
            return
        from ..config import get_settings

        config = load_notify_config(get_settings())
        if not config.get(switch):
            return
        # 未配置任何渠道时直接跳过，避免无谓入队
        if not (
            config.get("feishu_webhook")
            or config.get("wecom_webhook")
            or config.get("email_smtp_host")
            or config.get("system_notify")
        ):
            return
    _ensure_worker()
    _queue.put({"title": title, "body": body})


def test_all() -> dict:
    """向所有已配置渠道发一条测试消息，返回各渠道结果（同步）。"""
    from ..config import get_settings

    config = load_notify_config(get_settings())
    title = "CustomTrader 测试通知"
    body = f"这是一条测试消息（{time.strftime('%H:%M:%S')}）。收到即表示该渠道配置正确。"
    results: dict[str, str] = {}

    if config.get("feishu_webhook"):
        ok, msg = send_feishu(config["feishu_webhook"], config.get("feishu_secret", ""), title, body)
        results["feishu"] = "ok" if ok else f"失败: {msg}"
    else:
        results["feishu"] = "未配置"
    if config.get("wecom_webhook"):
        ok, msg = send_wecom(config["wecom_webhook"], title, body)
        results["wecom"] = "ok" if ok else f"失败: {msg}"
    else:
        results["wecom"] = "未配置"
    if config.get("email_smtp_host") and config.get("email_to"):
        ok, msg = send_email(
            config["email_smtp_host"],
            int(config.get("email_smtp_port") or 465),
            bool(config.get("email_smtp_ssl", True)),
            config.get("email_username", ""),
            config.get("email_password", ""),
            list(config.get("email_to") or []),
            title,
            body,
        )
        results["email"] = "ok" if ok else f"失败: {msg}"
    else:
        results["email"] = "未配置"
    if config.get("system_notify"):
        ok, msg = send_system(title, body)
        results["system"] = "ok" if ok else f"失败: {msg}"
    else:
        results["system"] = "未启用"
    return results


# ----------------------------------------------------------------------
# 事件挂接：交易成交 / 风控告警
# ----------------------------------------------------------------------

class NotifyEngine:
    """轻量事件监听器（不走 BaseEngine，避免占用引擎名）。

    注册 EVENT_TRADE 与 EVENT_LOG，把成交回报与 RiskEngine 告警
    转发到通知队列。所有处理都只是入队，不阻塞事件线程。
    """

    def __init__(self, main_engine, event_engine) -> None:
        self.main_engine = main_engine
        self.event_engine = event_engine
        from ..core.event import EVENT_LOG, EVENT_TRADE

        event_engine.register(EVENT_TRADE, self.on_trade)
        event_engine.register(EVENT_LOG, self.on_log)
        main_engine.write_log("通知服务已挂接（成交/风控事件）", source="Notify")

    def on_trade(self, event) -> None:
        try:
            trade = event.data
            direction = "买入" if str(trade.direction).endswith("LONG") else "卖出"
            offset = getattr(trade, "offset", "")
            offset_text = "平" if "CLOSE" in str(offset) else "开"
            dispatch(
                "成交通知",
                f"{trade.vt_symbol} {direction}{offset_text} {trade.volume} @ {trade.price}"
                f"（{trade.gateway_name}）",
                event_kind="trade",
            )
        except Exception:  # noqa: BLE001
            logger.exception("成交通知构造失败")

    def on_log(self, event) -> None:
        try:
            log = event.data
            if log.gateway_name != "RiskEngine":
                return
            # 只转发告警级日志（≥WARNING：熔断触发/移动止损/强平失败），
            # 跳过配置加载等 INFO 噪音
            if log.level < 30:  # logging.WARNING
                return
            dispatch("风控告警", log.msg, event_kind="risk")
        except Exception:  # noqa: BLE001
            logger.exception("风控通知构造失败")

    def close(self) -> None:
        pass
