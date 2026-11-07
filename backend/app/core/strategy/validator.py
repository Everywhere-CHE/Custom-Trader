"""基于 AST 的用户 / AI 提交策略代码校验。

这是防止意外破坏的纵深防御层（并非沙箱）：它会拒绝危险的
导入和调用，并要求文件必须实际定义一个 StrategyTemplate 子类。
资金安全由 AI_ALLOW_TRADING 和 RiskEngine 保障，而非本校验器。
"""

import ast
import re

FILENAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}\.py$")

# 策略文件不应导入的模块
FORBIDDEN_MODULES = {
    "os",
    "sys",
    "subprocess",
    "shutil",
    "socket",
    "requests",
    "httpx",
    "urllib",
    "importlib",
    "ctypes",
    "pickle",
    "multiprocessing",
    "signal",
    "tempfile",
    "webbrowser",
}

# 直接拒绝的调用名称
FORBIDDEN_CALLS = {"eval", "exec", "__import__", "open", "compile", "input"}


class StrategyCodeError(ValueError):
    """提交的策略代码未通过校验时抛出。"""


def validate_filename(filename: str) -> None:
    """按白名单模式检查策略文件名。"""
    if not FILENAME_PATTERN.match(filename):
        raise StrategyCodeError(
            f"非法文件名 {filename!r}：必须匹配 ^[A-Za-z][A-Za-z0-9_]*\\.py$ "
            "（不允许下划线开头、路径分隔符或其他扩展名）"
        )


def validate_strategy_code(code: str) -> list[str]:
    """校验策略源代码；返回策略类名列表。

    出现语法错误、禁止的导入 / 调用，或未定义 StrategyTemplate
    子类时抛出 StrategyCodeError。
    """
    if len(code.encode("utf-8")) > 100 * 1024:
        raise StrategyCodeError("策略代码超过 100KB 限制")

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise StrategyCodeError(
            f"语法错误（第 {e.lineno} 行）: {e.msg}"
        ) from e

    strategy_classes: list[str] = []

    for node in ast.walk(tree):
        # 导入检查
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_MODULES:
                    raise StrategyCodeError(
                        f"禁止导入模块 {alias.name!r}（第 {node.lineno} 行）"
                    )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in FORBIDDEN_MODULES:
                raise StrategyCodeError(
                    f"禁止导入模块 {node.module!r}（第 {node.lineno} 行）"
                )

        # 危险的内置函数调用
        elif isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in FORBIDDEN_CALLS:
                raise StrategyCodeError(
                    f"禁止调用 {name}()（第 {node.lineno} 行）"
                )

        # 策略类识别（在 AST 层面按基类名匹配）
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ""
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name == "StrategyTemplate":
                    strategy_classes.append(node.name)

    if not strategy_classes:
        raise StrategyCodeError(
            "代码中未找到 StrategyTemplate 的子类；策略类必须继承 StrategyTemplate"
        )

    return strategy_classes
