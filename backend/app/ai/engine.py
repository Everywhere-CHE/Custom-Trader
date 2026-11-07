"""AI 引擎：基于共享交易工具的多协议 tool-use 循环。

支持三种通信协议（provider 字段即协议选择）：
- ``anthropic``:  Anthropic Messages API（POST {base}/v1/messages）
- ``chat``:       OpenAI Chat Completions（POST {base}/chat/completions；
                  OpenAI、DeepSeek、Kimi、Qwen、GLM、豆包、OpenRouter 及
                  通用中转站）
- ``responses``:  OpenAI Responses API（POST {base}/responses）

旧配置中的 ``openai`` 载入时自动映射为 ``chat``。

配置可以来自 .env（初始默认值），并可在运行时通过 ``update_config``
修改 —— 持久化到 data/ai_config.json，重启后依然生效，无需改动 .env。
"""

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DATA_DIR
from typing import Any

from .prompts import SYSTEM_PROMPT, build_runtime_context
from .tools import TOOL_SPECS, TradingTools, execute_tool

PROVIDERS = ("anthropic", "chat", "responses")

# 运行时配置持久化位置（重启后依然生效；覆盖 .env）
CONFIG_PATH = DATA_DIR / "ai_config.json"


@dataclass
class ToolCallTrace:
    name: str
    input: dict
    output: str


@dataclass
class ChatResult:
    reply: str
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)


def _spec_to_anthropic(spec: dict) -> dict:
    return {
        "name": spec["name"],
        "description": spec["description"],
        "input_schema": spec["input_schema"],
    }


def _spec_to_openai(spec: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": spec["name"],
            "description": spec["description"],
            "parameters": spec["input_schema"],
        },
    }


def _spec_to_responses(spec: dict) -> dict:
    """Responses 协议的工具定义：扁平 function 结构。"""
    return {
        "type": "function",
        "name": spec["name"],
        "description": spec["description"],
        "parameters": spec["input_schema"],
    }


class AIEngine:
    """与提供商无关的 tool-use 循环。每个请求都是无状态的。"""

    def __init__(self, settings: Any) -> None:
        self.max_turns: int = settings.ai_max_turns
        self.allow_trading: bool = settings.ai_allow_trading
        self.auto_optimize: bool = False

        # 初始配置来自 .env，被持久化的运行时配置覆盖
        self.provider: str = getattr(settings, "ai_provider", "anthropic")
        if self.provider == "openai":  # 旧 .env 值：Chat Completions
            self.provider = "chat"
        self.model: str = settings.ai_model
        self.api_key: str = settings.anthropic_api_key
        self.base_url: str = settings.anthropic_base_url
        self._load_persisted_config()

        self.client: Any = None
        self._build_client()

        self.tools = TradingTools(
            base_url=f"http://127.0.0.1:{settings.port}/api",
            allow_trading=self.allow_trading,
        )

        # 启动自动优化后台任务
        self._start_auto_optimizer()

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and self.client is not None

    def _load_persisted_config(self) -> None:
        try:
            if CONFIG_PATH.exists():
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                provider = data.get("provider", self.provider)
                # 旧配置 "openai" 即 Chat Completions 协议
                if provider == "openai":
                    provider = "chat"
                self.provider = provider
                self.model = data.get("model", self.model)
                self.base_url = data.get("base_url", self.base_url)
                if data.get("api_key"):
                    self.api_key = data["api_key"]
                self.auto_optimize = data.get("auto_optimize", False)
        except Exception:
            pass  # 配置损坏：回退到 .env 的值

    def _persist_config(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(
                {
                    "provider": self.provider,
                    "model": self.model,
                    "base_url": self.base_url,
                    "api_key": self.api_key,
                    "auto_optimize": self.auto_optimize,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _build_client(self) -> None:
        self.client = None
        if not self.api_key:
            return
        if self.provider == "anthropic":
            import anthropic

            kwargs: dict = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            kwargs["timeout"] = 300.0  # 5 分钟
            self.client = anthropic.Anthropic(**kwargs)
        elif self.provider in ("chat", "responses"):
            import openai

            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            # 设置较长的超时时间（AI 模型分析复杂问题时可能耗时较长）
            kwargs["timeout"] = 300.0  # 5 分钟
            self.client = openai.OpenAI(**kwargs)

    def update_config(
        self,
        provider: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        allow_trading: bool | None = None,
        auto_optimize: bool | None = None,
    ) -> dict:
        """应用并持久化一次运行时配置变更。"""
        if provider is not None:
            if provider not in PROVIDERS:
                raise ValueError(f"provider 必须是 {PROVIDERS}")
            self.provider = provider
        if model is not None:
            self.model = model
        if base_url is not None:
            self.base_url = base_url
        if api_key:  # 为空/None 时保留现有密钥
            self.api_key = api_key
        if allow_trading is not None:
            self.allow_trading = allow_trading
            self.tools.allow_trading = allow_trading
        if auto_optimize is not None:
            self.auto_optimize = auto_optimize
            if auto_optimize:
                self._start_auto_optimizer()
        self._build_client()
        self._persist_config()
        return self.status()

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "has_api_key": bool(self.api_key),
            "allow_trading": self.allow_trading,
            "auto_optimize": self.auto_optimize,
        }

    # ------------------------------------------------------------------
    # 自动策略优化（休盘期间自动分析策略表现并调整参数）
    # ------------------------------------------------------------------

    def _start_auto_optimizer(self) -> None:
        """启动自动优化后台线程（仅启动一次）。"""
        if getattr(self, "_optimizer_started", False):
            return
        self._optimizer_started = True

        def worker() -> None:
            import time as _time
            from .prompts import AUTO_OPTIMIZE_PROMPT
            _log = logging.getLogger("custom_trader")

            _time.sleep(60)  # 启动后等一分钟再开始
            while True:
                try:
                    if not self.auto_optimize or not self.enabled:
                        _time.sleep(300)
                        continue

                    # 检查是否在休盘时间
                    now = _time.localtime()
                    hour, minute = now.tm_hour, now.tm_min
                    in_morning = (hour == 9) or (hour == 10) or (hour == 11 and minute < 30)
                    in_afternoon = (hour == 13 and minute >= 30) or (hour == 14)
                    in_night = (hour == 21) or (hour == 22) or (hour == 23 and minute < 30)
                    is_break = not (in_morning or in_afternoon or in_night)
                    if not is_break:
                        _time.sleep(300)
                        continue

                    _log.info("[AutoOptimize] 开始检查运行中的策略...")

                    # 收集策略数据
                    try:
                        strategies = self.tools.list_strategies()
                        trades = self.tools.get_trades(limit=100)
                        accounts = self.tools.get_accounts()
                        performance = self.tools.get_performance(hours=48)
                    except Exception:
                        _time.sleep(600)
                        continue

                    # 只分析运行中的策略
                    running = [
                        s for s in strategies
                        if s.get("variables", {}).get("trading")
                    ]
                    if not running:
                        _log.info("[AutoOptimize] 没有运行中的策略，跳过")
                        _time.sleep(600)
                        continue

                    _log.info("[AutoOptimize] 发现 %d 个运行中的策略", len(running))

                    # 构建分析数据
                    data_lines = [
                        f"## 运行中的策略 ({len(running)})",
                    ]
                    for s in running:
                        v = s.get("variables", {})
                        data_lines.append(
                            f"- {s['strategy_name']} ({s['class_name']}, "
                            f"合约={s['vt_symbol']}), "
                            f"持仓={v.get('pos', 0)}, "
                            f"参数={s.get('parameters', {})}"
                        )
                    data_lines.extend([
                        f"\n## 最近成交 ({len(trades)})",
                        *(f"- {t.get('vt_symbol','')} "
                          f"{t.get('direction','')} "
                          f"{t.get('offset','')} "
                          f"x{t.get('volume','')} "
                          f"@{t.get('price','')} "
                          f"盈亏={t.get('pnl',0)}" for t in trades[:30]),
                        f"\n## 账户资金",
                        *(f"- {a.get('vt_accountid','')} "
                          f"余额={a.get('balance',0)} "
                          f"可用={a.get('available',0)}" for a in accounts),
                        f"\n## 近期绩效 (48h)",
                        f"胜率={performance.get('win_rate','N/A')}%",
                        f"总盈亏={performance.get('total_pnl','N/A')}",
                        f"手续费={performance.get('total_fee','N/A')}",
                    ])

                    prompt = AUTO_OPTIMIZE_PROMPT + "\n\n" + "\n".join(data_lines)
                    messages = [{"role": "user", "content": prompt}]

                    # 调用 AI 分析（不超出 token 限制）
                    if self.provider == "anthropic":
                        result = self._chat_anthropic(messages, prompt)
                    elif self.provider == "responses":
                        result = self._chat_responses(messages, prompt)
                    else:
                        result = self._chat_openai(messages, prompt)
                    if result.reply:
                        _log.info(
                            "[AutoOptimize] AI 优化建议:\n%s",
                            result.reply[:300],
                        )
                        # 尝试解析 JSON 并应用参数优化
                        try:
                            import json as _json
                            import re as _re

                            # 从回复中提取 JSON 块
                            json_match = _re.search(
                                r'```json\s*([\s\S]*?)\s*```',
                                result.reply,
                            )
                            if json_match:
                                opt_data = _json.loads(json_match.group(1))
                            else:
                                # 尝试直接解析整个回复
                                opt_data = _json.loads(result.reply)

                            for opt in opt_data.get("optimizations", []):
                                sname = opt.get("strategy_name", "")
                                params = opt.get("parameters", {})
                                reason = opt.get("reason", "")
                                if not sname or not params:
                                    continue
                                # 不修改手数
                                if "fixed_size" in params:
                                    del params["fixed_size"]
                                if not params:
                                    continue
                                try:
                                    self.tools.edit_strategy(sname, params)
                                    _log.info(
                                        "[AutoOptimize] ✅ %s 参数已更新: %s (%s)",
                                        sname, params, reason,
                                    )
                                except Exception as e:
                                    _log.warning(
                                        "[AutoOptimize] ❌ %s 更新失败: %s",
                                        sname, e,
                                    )
                        except Exception:
                            _log.warning(
                                "[AutoOptimize] JSON 解析失败，跳过自动应用:\n%s",
                                result.reply[:200],
                            )
                except Exception:
                    _log.warning(
                        "[AutoOptimize] 优化过程出错:\n%s",
                        __import__("traceback").format_exc(),
                    )
                _time.sleep(1800)  # 每 30 分钟检查一次

        thread = threading.Thread(target=worker, name="AutoOptimizer", daemon=True)
        thread.start()

    @staticmethod
    def _sanitize_error(msg: str) -> str:
        """清理异常信息，避免泄露 API Key 等敏感信息。"""
        import re
        # 截断过长消息
        if len(msg) > 200:
            msg = msg[:200] + "..."
        # 替换常见的 API Key 模式
        msg = re.sub(
            r'(api[_-]?key["\']?\s*[:=]\s*["\']?)[^"\',\s]{4,}',
            r'\1***',
            msg,
            flags=re.IGNORECASE,
        )
        msg = re.sub(r'(sk-[a-zA-Z0-9]{10,})', 'sk-***', msg)
        return msg

    # ------------------------------------------------------------------
    # 对话入口
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        tool_names: list[str] | None = None,
    ) -> ChatResult:
        """运行 tool-use 循环；从工作线程调用（阻塞式）。

        Args:
            tool_names: 可选的工具名白名单——传入时只携带子集的工具
                schema（大幅降低请求 token，供复盘等单场景调用使用）。
        """
        assert self.client is not None, "AI engine not enabled"
        # 每次对话拼接实时上下文（时间/网关状态/交易权限），
        # 避免模型猜错日期或对未连接的网关反复调用工具。
        try:
            gateways = self.tools.get_gateways()
        except Exception:
            gateways = []
        system_prompt = SYSTEM_PROMPT + build_runtime_context(
            gateways, self.allow_trading
        )
        # 全市场情绪摘要（只读缓存，绝不阻塞聊天）——让模型开口就有市场背景
        try:
            from ..services.market_phase import peek_market_phase

            ph = peek_market_phase()
            if ph and ph.get("status") == "ok":
                s = ph.get("sentiment_now") or {}
                ls = ph.get("ladder_stats") or {}
                system_prompt += (
                    f"\n- 全市场情绪：指数 {s.get('score')}（{s.get('zone')}），"
                    f"周期阶段 {ph.get('phase')}，涨停 {ls.get('limit_up')}/"
                    f"跌停 {ls.get('limit_down')}，最高 {ls.get('max_streak')} 板"
                    f"（数据截至 {ph.get('as_of')}；细节可用 get_market_phase 工具查询）"
                )
        except Exception:
            pass
        specs = (
            [s for s in TOOL_SPECS if s["name"] in tool_names]
            if tool_names
            else TOOL_SPECS
        )
        if self.provider == "anthropic":
            return self._chat_anthropic(messages, system_prompt, specs)
        if self.provider == "responses":
            return self._chat_responses(messages, system_prompt, specs)
        return self._chat_openai(messages, system_prompt, specs)

    # ------------------------------------------------------------------
    # Anthropic Messages API
    # ------------------------------------------------------------------

    def _chat_anthropic(
        self,
        messages: list[dict],
        system_prompt: str = SYSTEM_PROMPT,
        tool_specs: list[dict] | None = None,
    ) -> ChatResult:
        anthropic_tools = [
            _spec_to_anthropic(s) for s in (tool_specs or TOOL_SPECS)
        ]
        convo: list[dict] = list(messages)
        trace: list[ToolCallTrace] = []

        # 合并连续的 user 消息以避免 Anthropic API 报错
        # (tool_result + 追问 = 连续两条 user 消息)
        merged = []
        for msg in convo:
            if merged and merged[-1]["role"] == "user" and msg["role"] == "user":
                # 合并内容数组
                prev = merged[-1]["content"]
                curr = msg["content"]
                if isinstance(prev, list) and isinstance(curr, list):
                    merged[-1]["content"] = prev + curr
                elif isinstance(prev, list) and isinstance(curr, str):
                    merged[-1]["content"] = prev + [{"type": "text", "text": curr}]
                elif isinstance(prev, str) and isinstance(curr, list):
                    merged[-1]["content"] = [{"type": "text", "text": prev}] + curr
                else:
                    merged[-1]["content"] = prev + "\n\n" + curr
            else:
                merged.append(msg)
        convo = merged

        for _ in range(self.max_turns):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=system_prompt,
                    tools=anthropic_tools,
                    messages=convo,
                )
            except Exception as e:
                return ChatResult(
                    reply=f"（API调用出错: {self._sanitize_error(str(e))}）",
                    tool_calls=trace,
                    messages=convo,
                )

            convo.append(
                {
                    "role": "assistant",
                    "content": self._serialize_anthropic_content(
                        response.content
                    ),
                }
            )

            if response.stop_reason != "tool_use":
                return ChatResult(
                    reply=self._anthropic_text(response),
                    tool_calls=trace,
                    messages=convo,
                )

            tool_results = []
            for block in response.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                output = execute_tool(self.tools, block.name, dict(block.input))
                trace.append(
                    ToolCallTrace(
                        name=block.name,
                        input=dict(block.input),
                        output=output[:2000],
                    )
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                    }
                )
            convo.append({"role": "user", "content": tool_results})

        return ChatResult(
            reply="（已达到最大工具调用轮次，请继续对话或换个问法）",
            tool_calls=trace,
            messages=convo,
        )

    @staticmethod
    def _anthropic_text(response: Any) -> str:
        return "\n".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        )

    @staticmethod
    def _serialize_anthropic_content(content: Any) -> Any:
        if isinstance(content, str):
            return content
        result = []
        for block in content:
            if isinstance(block, dict):
                result.append(block)
                continue
            block_type = getattr(block, "type", "")
            if block_type == "text":
                result.append({"type": "text", "text": block.text})
            elif block_type == "tool_use":
                result.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
        return result

    # ------------------------------------------------------------------
    # OpenAI Chat Completions API（也包括中转站）
    # ------------------------------------------------------------------

    def _chat_openai(
        self,
        messages: list[dict],
        system_prompt: str = SYSTEM_PROMPT,
        tool_specs: list[dict] | None = None,
    ) -> ChatResult:
        openai_tools = [_spec_to_openai(s) for s in (tool_specs or TOOL_SPECS)]
        # 在该协议中，系统提示词作为第一条消息传递
        convo: list[dict] = [{"role": "system", "content": system_prompt}]
        convo += [m for m in messages if m.get("role") != "system"]
        trace: list[ToolCallTrace] = []

        for _ in range(self.max_turns):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=4096,
                    tools=openai_tools,
                    messages=convo,
                )
            except Exception as e:
                # 如果 API 调用失败（如无效的 tool_call_id），返回
                # 已有内容，以便用户看到部分结果。
                return ChatResult(
                    reply=f"（API调用出错: {self._sanitize_error(str(e))}）",
                    tool_calls=trace,
                    messages=convo[1:],
                )
            choice = response.choices[0]
            message = choice.message

            assistant_msg: dict = {
                "role": "assistant",
                "content": message.content,
            }
            if message.tool_calls:
                assistant_msg["content"] = message.content or None
                assistant_msg["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in message.tool_calls
                ]
            convo.append(assistant_msg)

            if not message.tool_calls:
                # 返回历史记录前剥离系统消息
                return ChatResult(
                    reply=message.content or "",
                    tool_calls=trace,
                    messages=convo[1:],
                )

            missing_tool_ids = []
            for call in message.tool_calls:
                if not call.id:
                    missing_tool_ids.append(call.function.name)
                    continue
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                output = execute_tool(
                    self.tools, call.function.name, arguments
                )
                trace.append(
                    ToolCallTrace(
                        name=call.function.name,
                        input=arguments,
                        output=output[:2000],
                    )
                )
                convo.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": str(output),
                    }
                )
            if missing_tool_ids:
                convo.append(
                    {
                        "role": "user",
                        "content": f"以下工具调用缺少ID: {', '.join(missing_tool_ids)}，请重试"
                    }
                )

        return ChatResult(
            reply="（已达到最大工具调用轮次，请继续对话或换个问法）",
            tool_calls=trace,
            messages=convo[1:],
        )

    # ------------------------------------------------------------------
    # OpenAI Responses API（POST {base}/responses）
    # ------------------------------------------------------------------

    def _chat_responses(
        self,
        messages: list[dict],
        system_prompt: str = SYSTEM_PROMPT,
        tool_specs: list[dict] | None = None,
    ) -> ChatResult:
        responses_tools = [
            _spec_to_responses(s) for s in (tool_specs or TOOL_SPECS)
        ]
        # input 项序列（Responses 协议线格式）
        items: list[dict] = []
        # 同步维护一份 chat-completions 形状的历史（供会话持久化与跨协议重放）
        convo: list[dict] = []
        trace: list[ToolCallTrace] = []

        # 历史重放：把通用 messages 形状转为 input 项
        for m in messages:
            role = m.get("role")
            if role == "system":
                continue  # 系统提示词走 instructions 参数
            if role == "user":
                items.append({"role": "user", "content": m.get("content") or ""})
                convo.append({"role": "user", "content": m.get("content") or ""})
            elif role == "assistant":
                if m.get("tool_calls"):
                    for call in m["tool_calls"]:
                        fn = call.get("function") or {}
                        items.append(
                            {
                                "type": "function_call",
                                "call_id": call.get("id") or fn.get("name", ""),
                                "name": fn.get("name", ""),
                                "arguments": fn.get("arguments") or "{}",
                            }
                        )
                elif m.get("content"):
                    items.append(
                        {"role": "assistant", "content": m["content"]}
                    )
                    convo.append({"role": "assistant", "content": m["content"]})
            elif role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": m.get("tool_call_id") or "",
                        "output": str(m.get("content") or ""),
                    }
                )

        try:
            for _ in range(self.max_turns):
                try:
                    response = self.client.responses.create(
                        model=self.model,
                        max_output_tokens=4096,
                        instructions=system_prompt,
                        input=items,
                        tools=responses_tools,
                        tool_choice="auto",
                    )
                except Exception as e:
                    return ChatResult(
                        reply=f"（API调用出错: {self._sanitize_error(str(e))}）",
                        tool_calls=trace,
                        messages=convo,
                    )

                # 解析输出项
                output_items = list(getattr(response, "output", None) or [])
                reply_text = ""
                function_calls: list[Any] = []
                for out in output_items:
                    out_type = getattr(out, "type", None)
                    if out_type == "message":
                        for part in getattr(out, "content", None) or []:
                            if getattr(part, "type", None) == "output_text":
                                reply_text += getattr(part, "text", "") or ""
                    elif out_type == "function_call":
                        function_calls.append(out)

                if not function_calls:
                    if reply_text:
                        items.append(
                            {"role": "assistant", "content": reply_text}
                        )
                        convo.append(
                            {"role": "assistant", "content": reply_text}
                        )
                    return ChatResult(
                        reply=reply_text or "（模型未返回内容）",
                        tool_calls=trace,
                        messages=convo,
                    )

                # 记录 assistant 的 function_call 项（下一轮请求要原样带回）
                for out in output_items:
                    out_type = getattr(out, "type", None)
                    if out_type == "function_call":
                        items.append(
                            {
                                "type": "function_call",
                                "call_id": out.call_id,
                                "name": out.name,
                                "arguments": out.arguments or "{}",
                            }
                        )
                    elif out_type == "message":
                        text = "".join(
                            getattr(p, "text", "") or ""
                            for p in (getattr(out, "content", None) or [])
                            if getattr(p, "type", None) == "output_text"
                        )
                        if text:
                            items.append(
                                {"role": "assistant", "content": text}
                            )
                            convo.append(
                                {"role": "assistant", "content": text}
                            )

                for call in function_calls:
                    try:
                        arguments = json.loads(call.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    output = execute_tool(self.tools, call.name, arguments)
                    trace.append(
                        ToolCallTrace(
                            name=call.name,
                            input=arguments,
                            output=output[:2000],
                        )
                    )
                    convo.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.call_id,
                            "content": str(output),
                        }
                    )
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": str(output),
                        }
                    )

            return ChatResult(
                reply="（已达到最大工具调用轮次，请继续对话或换个问法）",
                tool_calls=trace,
                messages=convo,
            )
        except Exception as e:  # noqa: BLE001 —— 协议级异常兜底
            return ChatResult(
                reply=f"（Responses 协议调用出错: {self._sanitize_error(str(e))}）",
                tool_calls=trace,
                messages=convo,
            )
