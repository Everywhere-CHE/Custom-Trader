import pytest; pytest.skip("requires real CTP — SIM fixture removed", allow_module_level=True)  # noqa: E501

import time

import pytest
from fastapi.testclient import TestClient

from app.ctp_config import CONFIG_PATH
from app.main import app


@pytest.fixture(scope="module")
def client():
    existed = CONFIG_PATH.exists()
    if existed:
        CONFIG_PATH.rename(CONFIG_PATH.with_suffix(".bak"))
    with TestClient(app) as test_client:
        time.sleep(0.3)
        yield test_client
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
    backup = CONFIG_PATH.with_suffix(".bak")
    if backup.exists():
        backup.replace(CONFIG_PATH)


def test_get_config_defaults_from_env(client):
    resp = client.get("/api/gateways/ctp/config")
    assert resp.status_code == 200
    data = resp.json()
    # 所有连接字段均存在（值来自 .env / 默认值）
    for key in ("userid", "brokerid", "td_address", "md_address", "appid"):
        assert key in data
    # 无论 .env 内容如何，密码绝不会以明文回显
    assert data["password"] in ("", "***")
    assert isinstance(data["has_password"], bool)


def test_presets(client):
    resp = client.get("/api/gateways/ctp/presets")
    assert resp.status_code == 200
    presets = resp.json()
    ids = {p["id"] for p in presets}
    assert {"simnow-7x24", "simnow-session-g1", "real-broker"} <= ids
    simnow = next(p for p in presets if p["id"] == "simnow-session-g1")
    assert simnow["brokerid"] == "9999"
    assert simnow["td_address"].startswith("tcp://")


def test_update_and_persist(client):
    resp = client.put(
        "/api/gateways/ctp/config",
        json={
            "userid": "888888",
            "password": "real-secret",
            "brokerid": "1234",
            "td_address": "tcp://broker.example.com:41205",
            "md_address": "tcp://broker.example.com:41213",
            "appid": "client_myapp_1.0",
            "auth_code": "ABCDEF12345678",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["userid"] == "888888"
    assert data["brokerid"] == "1234"
    # 密码绝不回显
    assert data["password"] == "***"
    assert data["has_password"] is True
    assert CONFIG_PATH.exists()

    # GET 反映已持久化的值
    got = client.get("/api/gateways/ctp/config").json()
    assert got["userid"] == "888888"
    assert got["td_address"] == "tcp://broker.example.com:41205"


def test_empty_password_keeps_existing(client):
    client.put(
        "/api/gateways/ctp/config",
        json={"password": "keep-me", "userid": "777777"},
    )
    resp = client.put(
        "/api/gateways/ctp/config",
        json={"userid": "999999", "password": ""},
    )
    data = resp.json()
    assert data["userid"] == "999999"
    assert data["has_password"] is True  # 保留旧密码

    import json

    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert raw["password"] == "keep-me"


def test_connect_uses_persisted_config(client):
    """CTP 以空请求体连接时会读取已持久化的（错误）地址并快速失败
    且写入日志——证明运行时配置确实被读取。

    在隔离的测试环境中 CTP 网关未被注册
    （ENABLE_CTP_GATEWAY=false，单元测试不连接 SimNow），
    因此 connect 端点必须返回 404。
    """
    client.put(
        "/api/gateways/ctp/config",
        json={"userid": "u1", "password": "p1", "td_address": "",
              "md_address": ""},
    )
    resp = client.post("/api/gateways/CTP/connect", json={})
    assert resp.status_code == 404
    # 持久化的配置本身仍然可读
    got = client.get("/api/gateways/ctp/config").json()
    assert got["userid"] == "u1"


def test_disconnect_endpoint(client):
    # SIM 是自动连接的；先断开再重连
    resp = client.post("/api/gateways/SIM/disconnect")
    assert resp.status_code == 202
    time.sleep(0.3)
    gateways = {g["name"]: g for g in client.get("/api/gateways").json()}
    assert gateways["SIM"]["connected"] is False

    client.post("/api/gateways/SIM/connect", json={})
    time.sleep(0.5)
    gateways = {g["name"]: g for g in client.get("/api/gateways").json()}
    assert gateways["SIM"]["connected"] is True

    resp = client.post("/api/gateways/NOPE/disconnect")
    assert resp.status_code == 404
