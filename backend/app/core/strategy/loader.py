"""策略类发现。

扫描两个来源：
1. 内置包 ``app.core.strategy.strategies``；
2. 用户插件目录（默认为 ``backend/strategies``），其中的
   ``*.py`` 文件会被动态加载。

名称冲突时用户类覆盖内置类。导入失败的文件会被跳过并记录
警告日志（隔离有问题的插件）。
"""

import importlib
import importlib.util
import inspect
import logging
import pkgutil
import traceback
from pathlib import Path

from .template import StrategyTemplate

logger = logging.getLogger("custom_trader")


def _classes_in_module(module) -> dict[str, type[StrategyTemplate]]:  # noqa: ANN001
    """提取模块中定义的 StrategyTemplate 子类。"""
    found: dict[str, type[StrategyTemplate]] = {}
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if (
            issubclass(obj, StrategyTemplate)
            and obj is not StrategyTemplate
            and obj.__module__ == module.__name__
        ):
            found[name] = obj
    return found


def load_strategy_classes(
    user_dir: Path | None = None,
) -> dict[str, type[StrategyTemplate]]:
    """发现所有可用的策略类。"""
    classes: dict[str, type[StrategyTemplate]] = {}

    # 1. 内置策略包
    from . import strategies as builtin_pkg

    for module_info in pkgutil.iter_modules(builtin_pkg.__path__):
        module_name = f"{builtin_pkg.__name__}.{module_info.name}"
        try:
            module = importlib.import_module(module_name)
            classes.update(_classes_in_module(module))
        except Exception:
            logger.warning(
                "Failed to load builtin strategy module %s:\n%s",
                module_name,
                traceback.format_exc(),
            )

    # 2. 用户插件目录
    if user_dir and user_dir.exists():
        for path in sorted(user_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module_name = f"user_strategies_{path.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                user_classes = _classes_in_module(module)
                for name in user_classes:
                    if name in classes:
                        logger.warning(
                            "User strategy %s overrides builtin class", name
                        )
                classes.update(user_classes)
            except Exception:
                logger.warning(
                    "Failed to load user strategy file %s:\n%s",
                    path,
                    traceback.format_exc(),
                )

    return classes
