"""主流模型预设（供前端快速填充的快捷方式）。

两种通信协议即可覆盖所有情况：
- ``anthropic``: Anthropic Messages API（官方或兼容中转）
- ``openai``: OpenAI Chat Completions API —— 被 OpenAI、DeepSeek、
  Moonshot/Kimi、Qwen、GLM、豆包、OpenRouter 以及几乎所有
  API 中转站（one-api / new-api 风格）使用。

预设是可编辑的默认值，并非硬性列表：用户可以填写任意
base_url/model 组合。
"""

PRESETS: list[dict] = [
    # ----- Anthropic 官方 -----
    {
        "id": "claude-sonnet",
        "group": "Anthropic",
        "label": "Claude Sonnet 4.5",
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "base_url": "",
    },
    {
        "id": "claude-opus",
        "group": "Anthropic",
        "label": "Claude Opus 4.5",
        "provider": "anthropic",
        "model": "claude-opus-4-5",
        "base_url": "",
    },
    {
        "id": "claude-haiku",
        "group": "Anthropic",
        "label": "Claude Haiku 4.5",
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "base_url": "",
    },
    # ----- OpenAI 官方 -----
    {
        "id": "gpt-5",
        "group": "OpenAI",
        "label": "GPT-5",
        "provider": "chat",
        "model": "gpt-5",
        "base_url": "https://api.openai.com/v1",
    },
    {
        "id": "gpt-5-mini",
        "group": "OpenAI",
        "label": "GPT-5 mini",
        "provider": "chat",
        "model": "gpt-5-mini",
        "base_url": "https://api.openai.com/v1",
    },
    {
        "id": "gpt-4o",
        "group": "OpenAI",
        "label": "GPT-4o",
        "provider": "chat",
        "model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
    },
    # ----- DeepSeek -----
    {
        "id": "deepseek-chat",
        "group": "DeepSeek",
        "label": "DeepSeek V3 (chat)",
        "provider": "chat",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
    },
    {
        "id": "deepseek-reasoner",
        "group": "DeepSeek",
        "label": "DeepSeek R1 (reasoner)",
        "provider": "chat",
        "model": "deepseek-reasoner",
        "base_url": "https://api.deepseek.com/v1",
    },
    # ----- 月之暗面 Kimi -----
    {
        "id": "kimi-k2",
        "group": "Kimi",
        "label": "Kimi K2",
        "provider": "chat",
        "model": "kimi-k2-0905-preview",
        "base_url": "https://api.moonshot.cn/v1",
    },
    # ----- 阿里 通义千问 -----
    {
        "id": "qwen-max",
        "group": "通义千问",
        "label": "Qwen Max",
        "provider": "chat",
        "model": "qwen-max",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    {
        "id": "qwen-plus",
        "group": "通义千问",
        "label": "Qwen Plus",
        "provider": "chat",
        "model": "qwen-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    # ----- 智谱 GLM -----
    {
        "id": "glm-4-6",
        "group": "智谱",
        "label": "GLM-4.6",
        "provider": "chat",
        "model": "glm-4.6",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
    # ----- 字节 豆包 -----
    {
        "id": "doubao",
        "group": "豆包",
        "label": "豆包 Seed 1.6（填接入点）",
        "provider": "chat",
        "model": "doubao-seed-1-6-250615",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    },
    # ----- 聚合 / 中转 -----
    {
        "id": "openrouter",
        "group": "中转/聚合",
        "label": "OpenRouter（任意模型）",
        "provider": "chat",
        "model": "anthropic/claude-sonnet-4.5",
        "base_url": "https://openrouter.ai/api/v1",
    },
    {
        "id": "relay-openai",
        "group": "中转/聚合",
        "label": "中转站·OpenAI 协议（自填地址）",
        "provider": "chat",
        "model": "gpt-4o",
        "base_url": "https://your-relay.example.com/v1",
    },
    {
        "id": "relay-anthropic",
        "group": "中转/聚合",
        "label": "中转站·Anthropic 协议（自填地址）",
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "base_url": "https://your-relay.example.com",
        },

    # ----- Responses 协议（OpenAI /responses 端点）-----
    {
        "id": "gpt-5-responses",
        "group": "OpenAI",
        "label": "GPT-5 (Responses)",
        "provider": "responses",
        "model": "gpt-5",
        "base_url": "https://api.openai.com/v1",
    },
]
