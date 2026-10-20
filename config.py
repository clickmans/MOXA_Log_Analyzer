# -*- coding: utf-8 -*-
"""配置管理模块。

负责定义 AI 厂商/模型清单，以及本地配置（config.json）的读写。
API Key 一律运行时获取（GUI 输入 / 环境变量 MOXA_API_KEY、DEEPSEEK_API_KEY /
配置文件），源码不内置任何真实密钥。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# AI 厂商配置
# ---------------------------------------------------------------------------
# 各厂商统一采用 OpenAI Chat Completions 兼容协议，除 Anthropic(Claude) 采用其
# 专有 messages 接口（format="anthropic"）。Google Gemini 走 OpenAI 兼容网关，
# 但其官方不提供 /models 端点，故标记为 preset_only。
# 模型清单核验日期：2026-08-05（官方文档/定价页联网核验）。
# 各厂商 models 列表首个为当前最新/推荐模型（_sync_ai_widgets 与
# ConfigManager._sanitize 会以 models[0] 作为默认值）。
AI_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "DeepSeek": {
        "base_url": "https://api.deepseek.com/v1",
        # 官方文档当前仅列 V4 系列（deepseek-v4-pro 旗舰 / deepseek-v4-flash 快速）
        "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
        "api_key_param": "Authorization",  # Bearer token
    },
    "Kimi (月之暗面)": {
        "base_url": "https://api.moonshot.cn/v1",
        # kimi-k3 为 2026-07 发布的最新旗舰（官方示例默认模型），kimi-k2.6 次新
        "models": ["kimi-k3", "kimi-k2.6", "moonshot-v1-128k", "moonshot-v1-32k"],
    },
    "智谱 AI (Zhipu)": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        # GLM-5.2 为 2026-06-16 官宣的新一代旗舰（1M 上下文）；glm-5.1/glm-5/glm-4.6 均
        # 有官方文档页与调用示例（model="glm-5" 等）验证为有效 API id
        "models": ["glm-5.2", "glm-5.1", "glm-5", "glm-4.6"],
    },
    "豆包 (Doubao/字节)": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        # 火山方舟最新为 Doubao Seed 2.1 系列（2026-06-28 版）；seed-evolving 为动态最新入口
        "models": [
            "doubao-seed-2-1-pro-260628",
            "doubao-seed-2-1-turbo-260628",
            "doubao-seed-evolving",
        ],
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        # GPT-5.6 家族 2026-07-09 GA：Sol 旗舰推理 / Terra 均衡 / Luna 快速
        "models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4"],
    },
    "Claude (Anthropic)": {
        "base_url": "https://api.anthropic.com/v1",
        # 官方 Models overview 最新对比表：Fable 5(最强) / Opus 5(复杂 agentic 编码默认推荐,
        # claude-opus-5) / Sonnet 5 / Haiku 4.5(claude-haiku-4-5)
        "models": [
            "claude-fable-5",
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-haiku-4-5",
        ],
        "format": "anthropic",  # 特殊格式
    },
    "Gemini (Google)": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        # 官方 Models 页：3.1 Pro 的 API id 为 gemini-3.1-pro-preview（无后缀变体不存在）；
        # 3.5 Flash / 3.1 Flash-Lite 为稳定版
        "models": [
            "gemini-3.5-flash",
            "gemini-3.1-pro-preview",
            "gemini-3.1-flash-lite",
        ],
        "preset_only": True,  # 不提供 /models 端点，使用预置列表
    },
    "通义千问 (阿里)": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        # 阿里云百炼最新命名体系：qwen3.8-max / qwen3.7-plus / qwen3.7-flash
        "models": ["qwen3.8-max", "qwen3.7-plus", "qwen3.7-flash"],
    },
    "文心一言 (百度)": {
        "base_url": "https://qianfan.baidubce.com/v2",
        # 千帆推荐模型（2026-07-09 更新）：ERNIE-5.1 当前旗舰 / 5.0 / 4.5-Turbo-128K / 4.5-8K
        "models": ["ernie-5.1", "ernie-5.0", "ernie-4.5-turbo-128k", "ernie-4.5-8k"],
    },
}

# 各厂商的默认测试密钥。
# 【安全加固】不再内置任何真实密钥：真实密钥一律由运行时提供（GUI 输入 /
# 环境变量 MOXA_API_KEY、DEEPSEEK_API_KEY / config.json），本常量仅保留空结构
# 以兼容历史引用。若需展示示例写法，请统一使用占位符 YOUR_API_KEY，切勿提交真实密钥。
DEFAULT_TEST_KEYS: Dict[str, str] = {}

def _default_config_path() -> str:
    """返回跨运行持久化的配置路径。

    使用系统应用数据目录，避免 PyInstaller --onefile 运行时 __file__ 指向
    %TEMP%\\_MEIxxxx 临时目录导致 config.json 退出后被删除、设置无法保存。

    Windows: %APPDATA%/MOXA_Log_Analyzer/config.json
    其他:    ~/.moxa_log_analyzer/config.json
    """
    app_name = "MOXA_Log_Analyzer"
    if os.name == "nt":
        base = os.getenv("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, app_name)
    else:
        d = os.path.expanduser(os.path.join("~", ".moxa_log_analyzer"))
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.expanduser("~")
    return os.path.join(d, "config.json")


# 配置文件路径：跨运行持久化（不依赖 __file__ 所在目录）
CONFIG_FILE = _default_config_path()


class ConfigManager:
    """本地配置管理器，负责持久化用户选择的厂商/模型/API Key。"""

    def __init__(self, path: str = CONFIG_FILE) -> None:
        self.path = path
        self.data: Dict[str, Any] = {
            "provider": next(iter(AI_PROVIDERS.keys())),
            "model": AI_PROVIDERS[next(iter(AI_PROVIDERS.keys()))]["models"][0],
            "api_key": "",  # 不预填任何真实密钥，运行时由 config.json / 环境变量 / GUI 提供
        }
        self.load()

    # -- 读写 ----------------------------------------------------------------
    def load(self) -> None:
        """从磁盘加载配置；若文件缺失或字段非法则回落到默认值。

        Key 优先级：config.json 中已保存的 Key > 环境变量（MOXA_API_KEY /
        DEEPSEEK_API_KEY，仅内存生效、不写回磁盘）> 空串。
        环境变量兜底仅在 config.json 无 Key 时生效，避免覆盖用户已保存的 Key。
        """
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as handle:
                    stored = json.load(handle)
                if isinstance(stored, dict):
                    self.data.update(stored)
        except Exception:
            # 任何异常（损坏的 JSON 等）都忽略，使用默认值
            pass
        self._sanitize()
        # 环境变量兜底：仅当磁盘配置无 Key 时读取，且不自动写回磁盘（仅内存生效）；
        # 用户手动输入 Key 并保存后，config.json 中的 Key 优先于环境变量。
        if not self.data.get("api_key"):
            env_key = os.environ.get("MOXA_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
            if env_key:
                self.data["api_key"] = env_key  # 仅内存，不写盘

    def save(self) -> None:
        """将当前配置写回磁盘。"""
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _sanitize(self) -> None:
        """保证 provider 合法，model 为空时回退到该厂商预置列表首个模型。

        注意：非空 model 名称不再强制限制在静态预置集合内——「刷新模型列表」
        动态拉取到的新模型（如 gpt-5.6-sol）不在预置里时，仍应允许被保存，
        否则刷新结果会被静默改回旧模型，造成“看似更新、实际未生效”。
        """
        if self.data.get("provider") not in AI_PROVIDERS:
            self.data["provider"] = next(iter(AI_PROVIDERS.keys()))
        valid_models = AI_PROVIDERS[self.data["provider"]]["models"]
        if not self.data.get("model"):
            self.data["model"] = valid_models[0]
        if "api_key" not in self.data:
            self.data["api_key"] = ""

    # -- 访问器 --------------------------------------------------------------
    def get_provider(self) -> str:
        return str(self.data.get("provider", next(iter(AI_PROVIDERS.keys()))))

    def get_model(self) -> str:
        return str(self.data.get("model", ""))

    def get_api_key(self) -> str:
        return str(self.data.get("api_key", ""))

    def get_state(self) -> Dict[str, Any]:
        return dict(self.data)

    def set(self, provider: str | None = None, model: str | None = None,
            api_key: str | None = None) -> None:
        """更新并持久化若干字段。"""
        if provider is not None:
            self.data["provider"] = provider
        if model is not None:
            self.data["model"] = model
        if api_key is not None:
            self.data["api_key"] = api_key
        self._sanitize()
        self.save()

    def get_models_for(self, provider: str) -> List[str]:
        """返回指定厂商的模型列表。"""
        return list(AI_PROVIDERS.get(provider, {}).get("models", []))


if __name__ == "__main__":
    # 简单自检
    cfg = ConfigManager()
    print("provider:", cfg.get_provider())
    print("model:", cfg.get_model())
    print("has_key:", bool(cfg.get_api_key()))
