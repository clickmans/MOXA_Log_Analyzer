# -*- coding: utf-8 -*-
"""多厂商 AI API 客户端。

统一适配 OpenAI Chat Completions 兼容协议，以及对 Anthropic(Claude) 的专有
messages 接口。支持：
  - test_connection  测试 API Key 有效性
  - fetch_models     拉取可用模型列表（部分厂商使用预置列表）
  - stream_analysis  流式获取 AI 分析结果（逐字回调）
"""

from __future__ import annotations

import json
from typing import Callable, List, Optional, Tuple

import requests

from config import AI_PROVIDERS

# 请求超时（秒）
TIMEOUT = 30

# 系统提示词：定义 AI 的专家角色与输出风格
SYSTEM_PROMPT = (
    "你是一位资深的工业网络设备（MOXA 工业以太网交换机）运维与故障诊断专家，"
    "精通 EDS / G 系列交换机的环网（Turbo Ring / Turbo Chain）、VLAN、"
    "认证与安全特性。请用专业、简洁、条理清晰的中文回答，避免臆测，"
    "结论需基于所提供的日志数据。"
)


class AIClientError(Exception):
    """AI 客户端统一异常。"""


# ---------------------------------------------------------------------------
# 内部：OpenAI 兼容流式
# ---------------------------------------------------------------------------
def _stream_openai(base_url: str, model: str, api_key: str, prompt: str,
                   on_token: Callable[[str], None]) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
        "temperature": 0.3,
    }
    resp = requests.post(url, headers=headers, json=body, stream=True, timeout=TIMEOUT)
    resp.raise_for_status()

    collected: List[str] = []
    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        text = raw_line.decode("utf-8", errors="replace")
        if not text.startswith("data:"):
            continue
        data = text[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
            delta = obj["choices"][0]["delta"].get("content", "")
            if delta:
                collected.append(delta)
                on_token(delta)
        except (ValueError, KeyError, IndexError):
            # 跳过心跳/不完整分片
            continue
    return "".join(collected)


# ---------------------------------------------------------------------------
# 内部：Anthropic 流式
# ---------------------------------------------------------------------------
def _stream_anthropic(base_url: str, model: str, api_key: str, prompt: str,
                      on_token: Callable[[str], None]) -> str:
    url = base_url.rstrip("/") + "/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": model,
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    resp = requests.post(url, headers=headers, json=body, stream=True, timeout=TIMEOUT)
    resp.raise_for_status()

    collected: List[str] = []
    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        text = raw_line.decode("utf-8", errors="replace")
        if not text.startswith("data:"):
            continue
        data = text[len("data:"):].strip()
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        if obj.get("type") == "content_block_delta":
            delta = obj.get("delta", {})
            if delta.get("type") == "text_delta":
                piece = delta.get("text", "")
                if piece:
                    collected.append(piece)
                    on_token(piece)
    return "".join(collected)


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------
def _provider_cfg(provider: str) -> dict:
    if provider not in AI_PROVIDERS:
        raise AIClientError(f"未知厂商: {provider}")
    return AI_PROVIDERS[provider]


def test_connection(provider: str, model: str, api_key: str) -> Tuple[bool, str]:
    """测试 API Key 是否有效。返回 (成功?, 消息)。"""
    if not api_key or not api_key.strip():
        return False, "未填写 API Key"
    cfg = _provider_cfg(provider)
    try:
        if cfg.get("format") == "anthropic":
            url = cfg["base_url"].rstrip("/") + "/messages"
            headers = {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }
            body = {"model": model, "max_tokens": 8, "messages": [
                {"role": "user", "content": "ping"}]}
            r = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
        else:
            url = cfg["base_url"].rstrip("/") + "/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            body = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 8,
                "stream": False,
            }
            r = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)

        if r.status_code == 200:
            return True, "连接成功，API Key 有效"
        if r.status_code == 401:
            return False, "认证失败（401）：API Key 无效或权限不足"
        if r.status_code == 404:
            return False, "接口不存在（404）：请确认厂商与模型名称是否正确"
        if r.status_code == 429:
            return False, "请求过于频繁（429）：请稍后重试"
        return False, f"连接失败：HTTP {r.status_code} - {r.text[:200]}"
    except requests.exceptions.Timeout:
        return False, f"连接超时（{TIMEOUT}s），请检查网络或代理"
    except requests.exceptions.ConnectionError:
        return False, "网络连接失败，请检查网络连通性"
    except Exception as exc:  # noqa: BLE001
        return False, f"连接异常：{exc}"


def fetch_models(provider: str, api_key: str) -> Tuple[bool, List[str], str]:
    """获取厂商可用模型列表。

    返回 (success, models, note)：
      - success=True  已从官方 /models 端点拉取成功，models 为实时列表；
      - success=False 未能动态拉取（无 API Key / 厂商无端点 / 请求失败），
        models 为预置列表，note 说明原因，供 UI 区分“成功”与“回退”展示。

    对标记为 preset_only 或不提供 /models 端点的厂商（Anthropic / Gemini）
    直接返回预置列表，且 success=False（属“无端点回退”，而非拉取成功）。
    """
    cfg = _provider_cfg(provider)
    preset = list(cfg.get("models", []))

    if cfg.get("preset_only") or cfg.get("format") == "anthropic":
        return False, preset, "该厂商无 /models 端点，使用预置列表"

    if not api_key or not api_key.strip():
        return False, preset, "未填写 API Key，使用预置列表"

    try:
        url = cfg["base_url"].rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            models = [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]
            if models:
                return True, models, f"已获取 {len(models)} 个模型"
            return False, preset, "接口未返回可用模型，已保留预置列表"
        return False, preset, f"刷新失败：HTTP {r.status_code}，已保留预置列表"
    except requests.exceptions.Timeout:
        return False, preset, f"刷新失败：连接超时（{TIMEOUT}s），已保留预置列表"
    except requests.exceptions.ConnectionError:
        return False, preset, "刷新失败：网络连接失败，已保留预置列表"
    except Exception as exc:  # noqa: BLE001
        return False, preset, f"刷新失败：{exc}，已保留预置列表"


def stream_analysis(provider: str, model: str, api_key: str, prompt: str,
                    on_token: Callable[[str], None]) -> str:
    """流式调用 AI 进行分析。on_token 每收到一段文本即被回调。返回完整文本。"""
    if not api_key or not api_key.strip():
        raise AIClientError("未填写 API Key")
    cfg = _provider_cfg(provider)
    try:
        if cfg.get("format") == "anthropic":
            return _stream_anthropic(cfg["base_url"], model, api_key, prompt, on_token)
        return _stream_openai(cfg["base_url"], model, api_key, prompt, on_token)
    except requests.exceptions.Timeout as exc:
        raise AIClientError(f"请求超时（{TIMEOUT}s）：{exc}") from exc
    except requests.exceptions.ConnectionError as exc:
        raise AIClientError(f"网络连接失败：{exc}") from exc
    except requests.exceptions.HTTPError as exc:
        raise AIClientError(f"HTTP 错误：{exc}") from exc
    except AIClientError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AIClientError(f"分析失败：{exc}") from exc


if __name__ == "__main__":
    # 简单自检：打印各厂商预置模型
    for name, info in AI_PROVIDERS.items():
        print(name, "->", info["models"])
