import pytest; pytest.skip("requires real CTP — SIM fixture removed", allow_module_level=True)  # noqa: E501

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.ai.engine import CONFIG_PATH
from app.main import app


@pytest.fixture(scope="module")
def client():
    # 确保本模块开始时持久化配置处于干净状态
    existed_before = CONFIG_PATH.exists()
    if existed_before:
        CONFIG_PATH.rename(CONFIG_PATH.with_suffix(".bak"))
    with TestClient(app) as test_client:
        time.sleep(0.3)
        yield test_client
    # 清理：删除测试写入的配置，恢复原始配置
    backup = CONFIG_PATH.with_suffix(".bak")
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
    if backup.exists():
        backup.replace(CONFIG_PATH)


def test_status_disabled_without_key(client):
    resp = client.get("/api/ai/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["has_api_key"] is False
    assert data["provider"] in ("anthropic", "openai")
    assert data["allow_trading"] is False


def test_presets_endpoint(client):
    resp = client.get("/api/ai/presets")
    assert resp.status_code == 200
    presets = resp.json()
    assert len(presets) >= 10
    groups = {p["group"] for p in presets}
    # 主流服务商全部覆盖
    assert {"Anthropic", "OpenAI", "DeepSeek", "通义千问", "中转/聚合"} <= groups
    for p in presets:
        assert p["provider"] in ("anthropic", "openai")
        assert p["model"]


def test_chat_returns_503_without_key(client):
    resp = client.post(
        "/api/ai/chat",
        json={"messages": [{"role": "user", "content": "你好"}]},
    )
    assert resp.status_code == 503
    assert "API Key" in resp.json()["detail"]


def test_update_config_and_runtime_switch(client):
    engine = client.app.state.ai_engine

    resp = client.put(
        "/api/ai/config",
        json={
            "provider": "openai",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-test-123",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "openai"
    assert data["model"] == "deepseek-chat"
    assert data["has_api_key"] is True
    assert data["enabled"] is True
    assert engine.client is not None

    # 已持久化到磁盘
    assert CONFIG_PATH.exists()

    # api_key 为空时保留已有的 key
    resp = client.put("/api/ai/config", json={"model": "deepseek-reasoner"})
    assert resp.json()["model"] == "deepseek-reasoner"
    assert resp.json()["has_api_key"] is True

    # 非法的 provider 被拒绝
    resp = client.put("/api/ai/config", json={"provider": "gemini-wire"})
    assert resp.status_code == 422

    # 为后续测试重置：重建引擎状态以清除 key
    engine.api_key = ""
    engine.client = None


def test_chat_empty_messages_422(client, monkeypatch):
    engine = client.app.state.ai_engine
    monkeypatch.setattr(engine, "api_key", "sk-test")
    monkeypatch.setattr(engine, "client", object())
    resp = client.post("/api/ai/chat", json={"messages": []})
    assert resp.status_code == 422


class FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, block_id: str, name: str, block_input: dict) -> None:
        self.id = block_id
        self.name = name
        self.input = block_input


def test_chat_anthropic_tool_use_loop(client, monkeypatch):
    """模拟 Anthropic 客户端：一轮 tool_use 后返回最终文本。"""
    engine = client.app.state.ai_engine

    responses = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[
                FakeTextBlock("我来查一下账户。"),
                FakeToolUseBlock("tu_1", "get_accounts", {}),
            ],
        ),
        SimpleNamespace(
            stop_reason="end_turn",
            content=[FakeTextBlock("你的账户权益是 100 万。")],
        ),
    ]
    calls = {"n": 0}

    def fake_create(**kwargs):
        result = responses[calls["n"]]
        calls["n"] += 1
        return result

    fake_client = SimpleNamespace(
        messages=SimpleNamespace(create=fake_create)
    )
    monkeypatch.setattr(engine, "provider", "anthropic")
    monkeypatch.setattr(engine, "api_key", "sk-test")
    monkeypatch.setattr(engine, "client", fake_client)
    monkeypatch.setattr(engine.tools, "client", client)
    monkeypatch.setattr(engine.tools, "base_url", "http://testserver/api")

    resp = client.post(
        "/api/ai/chat",
        json={"messages": [{"role": "user", "content": "查账户"}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "你的账户权益是 100 万。"
    assert len(data["tool_calls"]) == 1
    assert data["tool_calls"][0]["name"] == "get_accounts"
    assert data["tool_calls"][0]["output"].startswith("[")
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][-1]["role"] == "assistant"


def test_chat_openai_tool_use_loop(client, monkeypatch):
    """模拟 OpenAI 协议客户端：一轮工具调用后返回最终文本。"""
    engine = client.app.state.ai_engine

    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="get_accounts", arguments="{}"),
    )
    responses = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None, tool_calls=[tool_call]
                    )
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="权益 100 万。", tool_calls=None
                    )
                )
            ]
        ),
    ]
    calls = {"n": 0}

    def fake_create(**kwargs):
        # 在该协议下 system 提示词必须作为第一条消息传入
        assert kwargs["messages"][0]["role"] == "system"
        result = responses[calls["n"]]
        calls["n"] += 1
        return result

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fake_create)
        )
    )
    monkeypatch.setattr(engine, "provider", "openai")
    monkeypatch.setattr(engine, "api_key", "sk-test")
    monkeypatch.setattr(engine, "client", fake_client)
    monkeypatch.setattr(engine.tools, "client", client)
    monkeypatch.setattr(engine.tools, "base_url", "http://testserver/api")

    resp = client.post(
        "/api/ai/chat",
        json={"messages": [{"role": "user", "content": "查账户"}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "权益 100 万。"
    assert data["tool_calls"][0]["name"] == "get_accounts"
    # 返回的历史中不包含 system 消息
    assert data["messages"][0]["role"] == "user"
    # 历史中存在 tool 结果消息
    assert any(m.get("role") == "tool" for m in data["messages"])
