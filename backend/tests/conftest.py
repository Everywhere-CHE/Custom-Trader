"""Pytest 配置：完全隔离的运行时环境。

环境变量必须在 ``app.config`` 读取配置之前设置好，因此这段代码在
conftest 导入时执行。单元测试绝不能触碰真实的部署状态或外部柜台：

- DATABASE_URL     -> 临时目录中的一次性 SQLite
- QT_DATA_DIR      -> 临时目录（watchlist/ctp/stock/ai 配置）
- CTP              -> 完全禁用（测试中不连接 SimNow）
- STOCK            -> 注册但不自动连接（网络测试会显式连接，
                      并标记为 ``network``）
- 模拟器 Gateway   -> 已移除（代码中不含任何合成数据源）

注意：所有真实网关在测试中均禁用。依赖网关的集成测试（原
SimulatorGateway 夹具）现在需要真实 CTP/真实库——用 ``make_client``
或 ``authed_client`` 夹具（127.0.0.1 loopback 旁路绕过 JWT）。
纯逻辑单元测试（事件/转换器/指标）不受影响。
"""

import os
import tempfile

import pytest

_tmp_dir = tempfile.mkdtemp(prefix="quant_trader_test_")

os.environ["DATABASE_URL"] = (
    f"sqlite:///{os.path.join(_tmp_dir, 'test.db')}"
)
os.environ["QT_DATA_DIR"] = os.path.join(_tmp_dir, "data")

# 真实网关：绝不在单元测试中使用
os.environ["ENABLE_CTP_GATEWAY"] = "false"
os.environ["AUTO_CONNECT_CTP"] = "false"
os.environ["AUTO_CONNECT_STOCK"] = "false"

# 模拟器 Gateway 已彻底移除（代码中不含任何合成数据源）


def make_client():
    """创建一个绕过 JWT 鉴权的 FastAPI TestClient。

    将 client host 设为 ``127.0.0.1`` 以触发 ``main.py`` 的 loopback
    旁路：``127.0.0.1`` 且无 ``X-Forwarded-For`` 头的请求跳过 JWT 验证。
    用于需要真实网关（CTP/STOCK）的集成测试。

    测试环境需启用 ``ENABLE_CTP_GATEWAY=true`` 并确保
    ``data/ctp_config.json`` 有有效 SimNow 凭据。
    """
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture(scope="session")
def authed_client():
    """已通过 JWT 鉴权的 TestClient 会话（127.0.0.1 loopback 旁路）。"""
    with make_client() as client:
        yield client
