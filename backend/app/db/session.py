"""数据库引擎与会话工厂。"""

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from .models import Base

_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def get_engine() -> Engine:
    """惰性创建 SQLAlchemy 引擎。"""
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.resolved_database_url
        kwargs: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            # 事件线程和 API 线程池都会访问数据库
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kwargs)

        if url.startswith("sqlite"):
            # WAL 允许一个写入者与多个并发读取者
            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _record) -> None:  # noqa: ANN001
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                # 当另一线程持有写锁时（tick 批量插入与 bar 预热保存
                # 冲突）等待而不是直接报错
                cursor.execute("PRAGMA busy_timeout=15000")
                cursor.close()

    return _engine


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), expire_on_commit=False
        )
    return _session_factory


def create_session() -> Session:
    """创建一个新会话（由调用方负责关闭）。"""
    return get_session_factory()()


def init_db() -> None:
    """创建所有表并应用轻量级迁移。"""
    engine = get_engine()
    Base.metadata.create_all(engine)

    # 轻量级迁移：为已有表添加 create_all 不会补充的列
    # （对 sqlite 和 postgres 均有效）。
    with engine.connect() as conn:
        try:
            cols = {
                row[1] if engine.dialect.name == "sqlite" else row[0]
                for row in conn.execute(
                    text(
                        "PRAGMA table_info(strategies)"
                        if engine.dialect.name == "sqlite"
                        else "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'strategies'"
                    )
                )
            }
            if "variables" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE strategies "
                        "ADD COLUMN variables TEXT DEFAULT '{}'"
                    )
                )
                conn.commit()
            if "auto_start" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE strategies "
                        "ADD COLUMN auto_start INTEGER DEFAULT 0"
                    )
                )
                conn.commit()
        except Exception:
            pass  # 首次运行时表可能尚不存在


def dispose_db() -> None:
    """销毁引擎（用于测试收尾 / 应用关闭）。"""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
