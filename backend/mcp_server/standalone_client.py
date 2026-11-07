#!/usr/bin/env python3
"""
CustomTrader Zero-Dependency MCP Client (JSON-RPC 2.0 over Stdio)
- 零第三方依赖：纯 Python 3 标准库（sys, json, os, urllib），无需 pip 安装任何包
- 极致冷启动：启动耗时 < 20ms，跨平台（Mac/Linux/Windows）即插即用
- 动态工具集：tools/list 实时拉取服务端 /api/mcp/tools，与服务端工具自动同步
- 广泛客户端兼容：NDJSON 与 LSP Content-Length 双帧协议，原生支持
  Claude Code, Cursor, Windsurf, Cline, OpenAI Codex, Antigravity, ZCode 等
- 凭据优先级：环境变量 QT_HOST/QT_TOKEN > 导出时预置 DEFAULT_HOST/DEFAULT_TOKEN
"""

import os
import sys
import json
import ssl
import urllib.request
import urllib.error

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "custom-trader"
SERVER_VERSION = "1.0.0"

# 自签名证书环境（如 nginx + self-signed）下保证开箱即用：跳过证书校验
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# 默认预置凭据占位符（从 Web 端导出单文件客户端时动态预填充；优先级低于环境变量）
DEFAULT_HOST = ""
DEFAULT_TOKEN = ""

# 工具清单缓存（服务端不可达时保留上次成功结果）
_TOOLS_CACHE = None


def _get_auth():
    """获取服务端地址与 Token，优先级：1. 环境变量 > 2. 预置静态凭据。"""
    host = os.environ.get("QT_HOST", "").strip()
    token = os.environ.get("QT_TOKEN", "").strip()
    if not host and DEFAULT_HOST:
        host = DEFAULT_HOST.strip()
    if not token and DEFAULT_TOKEN:
        token = DEFAULT_TOKEN.strip()
    if not host:
        host = "http://127.0.0.1:8000"
    return host.rstrip("/"), token


def _request_api(method: str, endpoint: str, body: dict = None) -> tuple:
    """向 CustomTrader 后端发送 HTTP 请求，返回 (status_code, data)。"""
    host, token = _get_auth()
    url = f"{host}/api{endpoint}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "CustomTrader-MCP/1.0",
    }
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    try:
        # 8 秒快速超时，避免 Agent 终端长时间挂起
        with urllib.request.urlopen(req, timeout=8, context=_SSL_CTX) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"message": str(e)}
    except Exception as e:
        return 500, {"message": str(e)}


def _error_result(message: str, auth_error: bool = False) -> dict:
    """构造 isError 的 MCP 工具结果。"""
    payload = {"status": "auth_error" if auth_error else "error", "message": message}
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
        ],
        "isError": True,
    }


def _fetch_tools() -> list:
    """拉取服务端工具清单，优先使用缓存（服务端不可达时降级）。"""
    global _TOOLS_CACHE
    code, data = _request_api("GET", "/mcp/tools")
    if code == 200 and isinstance(data, dict) and isinstance(data.get("tools"), list):
        _TOOLS_CACHE = data["tools"]
        return _TOOLS_CACHE
    if _TOOLS_CACHE is not None:
        return _TOOLS_CACHE
    if code in (401, 403):
        raise RuntimeError("鉴权失败：QT_TOKEN 无效或已刷新，请重新导出客户端脚本")
    raise RuntimeError(f"获取工具清单失败 (HTTP {code}): {data.get('message', data)}")


def handle_request(req: dict) -> dict:
    """处理 JSON-RPC 2.0 协议请求。"""
    req_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {}) or {}

    # 1. 协议初始化握手
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    # 2. 客户端完成初始化通知（Notification，无返回值）
    if method in ("notifications/initialized", "initialized"):
        return None

    # 3. 探活 ping
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    # 4. 列出可用工具集（动态拉取服务端注册表）
    if method == "tools/list":
        try:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": _fetch_tools()},
            }
        except Exception as e:  # noqa: BLE001 — 客户端必须始终得到回复
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(e)},
            }

    # 5. 调用工具（转发到服务端执行）
    if method == "tools/call":
        tool_name = str(params.get("name", ""))
        args = params.get("arguments", {}) or {}
        code, data = _request_api("POST", "/mcp/call", {"name": tool_name, "arguments": args})

        if code in (401, 403):
            return _error_result("鉴权失败：Token 无效或已过期，请重新导出客户端脚本或配置 QT_TOKEN", auth_error=True)
        if code == 404:
            return _error_result(f"服务端不支持工具调用接口 (HTTP 404)，请升级 CustomTrader 后端后重试")
        if code != 200 or not isinstance(data, dict) or "text" not in data:
            msg = data.get("message", data) if isinstance(data, dict) else data
            return _error_result(f"服务端响应异常 (HTTP {code}): {msg}")

        # 服务端已做精简 JSON 序列化，直接透传以最大化节省 Token
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": data["text"]}],
                "isError": bool(data.get("is_error")),
            },
        }

    # 未知方法
    if req_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not recognized"},
        }
    return None


def main():
    """标准输入输出事件主循环，支持原生换行符 (NDJSON) 与 LSP Content-Length 两种协议帧。"""
    in_stream = sys.stdin.buffer if hasattr(sys.stdin, "buffer") else sys.stdin
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    while True:
        try:
            line = in_stream.readline()
            if not line:
                break
        except Exception:
            break

        line_bytes = line if isinstance(line, bytes) else line.encode("utf-8")
        # 处理可能附带 Content-Length: 的 LSP 帧协议
        if line_bytes.lower().startswith(b"content-length:"):
            try:
                length_str = line_bytes.split(b":", 1)[1].strip()
                content_length = int(length_str)
                # 循环丢弃后续所有附带的 Header，直到真正的空行
                while True:
                    h_line = in_stream.readline()
                    if not h_line or not h_line.strip():
                        break
                raw_body = in_stream.read(content_length)
                body = raw_body.decode("utf-8", errors="replace") if isinstance(raw_body, bytes) else raw_body
                req = json.loads(body)
                try:
                    resp = handle_request(req)
                except Exception as e:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req.get("id") if isinstance(req, dict) else None,
                        "error": {"code": -32603, "message": f"Internal server error: {str(e)}"},
                    }
                if resp is not None:
                    out_body = json.dumps(resp, ensure_ascii=False)
                    out_bytes = out_body.encode("utf-8")
                    sys.stdout.write(f"Content-Length: {len(out_bytes)}\r\n\r\n{out_body}\r\n")
                    sys.stdout.flush()
            except Exception:
                pass
            continue

        # 标准换行符分隔的 JSON-RPC 消息 (NDJSON)
        line_str = line.decode("utf-8", errors="replace").strip() if isinstance(line, bytes) else line.strip()
        if not line_str:
            continue
        try:
            req = json.loads(line_str)
        except Exception:
            continue
        try:
            resp = handle_request(req)
        except Exception as e:
            resp = {
                "jsonrpc": "2.0",
                "id": req.get("id") if isinstance(req, dict) else None,
                "error": {"code": -32603, "message": f"Internal server error: {str(e)}"},
            }
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
