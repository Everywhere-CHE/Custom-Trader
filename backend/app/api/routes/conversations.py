"""AI 对话管理端点。"""

from fastapi import APIRouter, HTTPException

from ...conversations import (
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    update_conversation,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
def api_list_conversations() -> list[dict]:
    """列出所有对话（仅摘要）。"""
    return list_conversations()


@router.get("/{conv_id}")
def api_get_conversation(conv_id: str) -> dict:
    """获取单个对话及其完整消息。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    return conv


@router.post("", status_code=201)
def api_create_conversation() -> dict:
    """创建一个新的空对话。"""
    return create_conversation()


@router.delete("/{conv_id}")
def api_delete_conversation(conv_id: str) -> dict:
    """删除一个对话。"""
    ok = delete_conversation(conv_id)
    if not ok:
        raise HTTPException(404, "对话不存在")
    return {"deleted": True, "id": conv_id}