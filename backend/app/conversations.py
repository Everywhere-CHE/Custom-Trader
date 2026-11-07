"""AI 对话管理：持久化、列出、删除、创建对话。

每个对话以 JSON 文件形式存储在 data/ai_conversations/ 目录。
一个对话包含：
  - id: str（UUID）
  - title: str（根据首条用户消息自动生成）
  - messages: list[dict]（原始的 Anthropic messages 数组）
  - display_messages: list[dict]（用于展示的 user/assistant 文本）
  - created_at: str
  - updated_at: str
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR
from typing import Any

CONVERSATIONS_DIR = DATA_DIR / "ai_conversations"


def _ensure_dir() -> None:
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)


def _conversation_path(conv_id: str) -> Path:
    return CONVERSATIONS_DIR / f"{conv_id}.json"


def _auto_title(messages: list[dict]) -> str:
    """根据首条用户消息生成简短标题。"""
    for msg in messages:
        if msg.get("role") == "user":
            text = msg.get("content", "")
            if isinstance(text, list):
                for block in text:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block["text"]
                        break
                else:
                    text = ""
            if isinstance(text, str):
                text = text.strip()[:50]
                if text:
                    return text + ("..." if len(text) >= 50 else "")
    return "新对话"


def list_conversations() -> list[dict]:
    """返回所有对话，按最新在前排序，仅包含摘要字段。"""
    _ensure_dir()
    results = []
    for path in sorted(CONVERSATIONS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append({
                "id": data["id"],
                "title": data.get("title", "未命名对话"),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "message_count": len(data.get("display_messages", [])),
            })
        except Exception:
            continue
    return results


def get_conversation(conv_id: str) -> dict | None:
    """返回完整的对话数据。"""
    path = _conversation_path(conv_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def create_conversation(messages: list[dict] | None = None) -> dict:
    """创建一个新对话，可选携带初始消息。"""
    _ensure_dir()
    now = datetime.now(timezone.utc).isoformat()
    conv_id = str(uuid.uuid4())[:8]
    title = _auto_title(messages or [])
    conv = {
        "id": conv_id,
        "title": title,
        "messages": messages or [],
        "display_messages": _extract_display(messages or []),
        "created_at": now,
        "updated_at": now,
    }
    _conversation_path(conv_id).write_text(
        json.dumps(conv, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return conv


def update_conversation(conv_id: str, messages: list[dict], display_messages: list[dict] | None = None) -> dict | None:
    """更新对话的消息内容。"""
    conv = get_conversation(conv_id)
    if not conv:
        return None
    conv["messages"] = messages
    conv["display_messages"] = display_messages or _extract_display(messages)
    conv["updated_at"] = datetime.now(timezone.utc).isoformat()
    # 若标题仍为默认值，则根据首条用户消息更新标题
    if conv["title"] == "新对话" or conv["title"] == "未命名对话":
        conv["title"] = _auto_title(messages)
    _conversation_path(conv_id).write_text(
        json.dumps(conv, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return conv


def delete_conversation(conv_id: str) -> bool:
    """删除一个对话。"""
    path = _conversation_path(conv_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def _extract_display(messages: list[dict]) -> list[dict]:
    """提取用于展示的 user/assistant 文本对。"""
    display = []
    for msg in messages:
        role = msg.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
            text = "\n".join(parts)
        if text:
            display.append({"role": role, "text": text})
    return display