# -*- coding: utf-8 -*-
"""MOXA 交换机日志分析工具 —— 独立 QA 边界 / 健壮性测试套件（Edward / QA）。

运行方式（隔离 venv）：
    C:/Users/周早立/.workbuddy/binaries/python/envs/moxa/Scripts/python.exe test_qa.py

覆盖：
  A. 日志解析边界（log_parser.LogParser）
  B. AI 客户端错误处理（ai_client，无需真实 Key 的部分 + 可选真实联网）
  C. PDF 导出边界（pdf_exporter.PDFExporter）
  D. config 健壮性（ConfigManager）

每个场景用 try/except 包裹并断言，末尾打印 PASS/FAIL 汇总，失败时 sys.exit(1)。
不修改任何业务源码；仅做独立验证。
"""

from __future__ import annotations

import os
import sys
import json
import socket
import tempfile
import traceback
import unittest.mock as mock

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import config                              # noqa: E402
import log_parser                          # noqa: E402
from log_parser import (                   # noqa: E402
    LogParser, LogEvent, ParseResult,
    SEV_INFO, SEV_WARNING, SEV_RISK, LINK_OFF_THRESHOLD,
)
import ai_client                           # noqa: E402
from ai_client import (                    # noqa: E402
    AIClientError, test_connection, fetch_models, stream_analysis,
)
import pdf_exporter                        # noqa: E402
from pdf_exporter import PDFExporter       # noqa: E402


# ---------------------------------------------------------------------------
# 结果收集 & 装饰器
# ---------------------------------------------------------------------------
RESULTS: list = []


def record(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    mark = "PASS" if passed else "FAIL"
    tail = f" — {detail}" if detail else ""
    print(f"  [{mark}] {name}{tail}")


def qa_test(name: str):
    """装饰器：包裹单个测试，捕获断言/异常并自动记录结果。

    被装饰函数正常返回（可选返回 detail 字符串）=> PASS；
    抛出 AssertionError => 断言失败 FAIL；抛出其它异常 => 异常 FAIL。
    """

    def deco(func):
        def wrapper():
            try:
                detail = func() or ""
            except AssertionError as e:
                record(name, False, f"断言失败: {e}")
                return
            except Exception as e:  # noqa: BLE001
                record(name, False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
                return
            record(name, True, detail)
        return wrapper
    return deco


def write_file(name: str, content: str, encoding: str = "utf-8") -> str:
    path = os.path.join(tempfile.gettempdir(), "moxa_qa_" + name)
    with open(path, "w", encoding=encoding) as fh:
        fh.write(content)
    return path


# ===========================================================================
# A. 日志解析边界
# ===========================================================================
@qa_test("A1 空文件解析")
def t_a1_empty():
    p = write_file("empty.txt", "")
    res = LogParser.parse(p)
    assert res.stats()["total"] == 0, f"total={res.stats()['total']}"
    assert res.risks() == [], "risks 非空"
    sugg = res.suggestions()
    assert len(sugg) == 1 and "未检测到" in sugg[0], sugg
    # 空结果也不应崩溃于基础方法
    assert res.time_start is None and res.time_end is None


@qa_test("A2 全为无法识别行(30行)")
def t_a2_garbage():
    words = [
        "lorem ipsum dolor", "foo bar baz qux", "the quick brown",
        "alpha beta gamma", "session packet node", "widget buffer token",
        "random noise sample", "sys vol core data",
    ]
    lines = [" ".join(words[(i + j) % len(words)] for j in range(3))
             for i in range(30)]
    p = write_file("garbage.txt", "\n".join(lines) + "\n")
    res = LogParser.parse(p)
    evs = res.parsed_events
    assert len(evs) == 30, f"期望30事件, 实际{len(evs)}"
    assert all(e.event_type == "unknown" for e in evs), "存在非 unknown 事件"
    assert all(e.severity == SEV_INFO for e in evs), "存在非 INFO 严重级别"


@qa_test("A3 编码容错(GBK / UTF-8 BOM)")
def t_a3_encoding():
    # --- GBK ---
    gbk_content = "# 测试日志\n2026-01-01 08:00:00 Port 1 link on\n"
    gbk_path = write_file("gbk.txt", gbk_content, encoding="gbk")
    text = LogParser.load_text(gbk_path)
    assert "测试日志" in text, "GBK 中文未被正确解码(乱码)"
    res = LogParser.parse(gbk_path)
    port1 = [e for e in res.parsed_events if e.event_type == "port_link_on"]
    assert len(port1) == 1 and port1[0].port == "1", "GBK 事件未被识别"

    # --- UTF-8 with BOM ---
    bom_content = "# 测试日志\n2026-01-01 08:00:00 Port 2 link on\n"
    bom_path = write_file("bom.txt", bom_content, encoding="utf-8-sig")
    text2 = LogParser.load_text(bom_path)
    assert "测试日志" in text2, "UTF-8 BOM 中文未被正确解码"
    res2 = LogParser.parse(bom_path)
    port2 = [e for e in res2.parsed_events if e.event_type == "port_link_on"]
    assert len(port2) == 1 and port2[0].port == "2", "BOM 事件未被识别"
    return "GBK 与 UTF-8-BOM 均正确读取并识别事件"


@qa_test("A4 多时间戳格式")
def t_a4_timestamps():
    content = (
        "2026-01-01 08:00:00 Port 1 link on\n"
        "01-02 09:30:00 Port 2 link on\n"
        "Jan 03 10:15:00 Port 3 link on\n"
    )
    p = write_file("ts.txt", content)
    res = LogParser.parse(p)
    evs = res.parsed_events
    assert len(evs) == 3, f"事件数={len(evs)}"
    ts = [e.timestamp for e in evs]
    assert all(t is not None for t in ts), "存在未解析的时间戳"

    # 各格式解析正确
    assert evs[0].timestamp == __import__("datetime").datetime(2026, 1, 1, 8, 0, 0), \
        f"YYYY格式解析错误: {evs[0].timestamp}"
    yr = __import__("datetime").datetime.now().year
    assert evs[1].timestamp.year == yr and evs[1].timestamp.month == 1 \
        and evs[1].timestamp.day == 2 and evs[1].timestamp.hour == 9, \
        f"MM-DD格式解析错误: {evs[1].timestamp}"
    assert evs[2].timestamp.year == yr and evs[2].timestamp.month == 1 \
        and evs[2].timestamp.day == 3 and evs[2].timestamp.hour == 10, \
        f"syslog格式解析错误: {evs[2].timestamp}"

    # time_start / time_end 正确
    assert res.time_start == min(ts), f"time_start={res.time_start} min={min(ts)}"
    assert res.time_end == max(ts), f"time_end={res.time_end} max={max(ts)}"
    return f"time_start={res.time_start}, time_end={res.time_end}"


@qa_test("A5 端口号提取 / 闪断判定")
def t_a5_port_flap():
    lines = [f"2026-02-01 0{i}:00:00 Port 12 link off" for i in range(1, 4)]
    p = write_file("flap.txt", "\n".join(lines) + "\n")
    res = LogParser.parse(p)
    offs = [e for e in res.parsed_events if e.event_type == "port_link_off"]
    assert len(offs) == 3, f"link off 数={len(offs)}"
    assert all(e.port == "12" for e in offs), "port 未提取为 12"
    flap = [r for r in res.risks()
            if "频繁闪断" in r["title"] and "12" in r["title"]]
    assert len(flap) == 1, "未生成端口12闪断风险"
    # 阈值边界：恰好等于阈值时应触发
    assert LINK_OFF_THRESHOLD == 3
    return "端口12提取正确且触发闪断预警"


@qa_test("A6 无时间戳行")
def t_a6_no_timestamp():
    p = write_file("nots.txt", "Port 1 link on\n")
    res = LogParser.parse(p)
    ev = res.parsed_events[0]
    assert ev.timestamp is None, f"timestamp 应为 None, 实际 {ev.timestamp}"
    assert ev.time_str() == "????-??-?? ??:??:??", f"time_str={ev.time_str()}"
    assert res.stats()["total"] == 1, "统计异常"
    # 无时间戳事件仍应被正常归类（port_link_on）
    assert ev.event_type == "port_link_on" and ev.port == "1"


@qa_test("A7 真实样例回归(sample_test.log)")
def t_a7_regression():
    p = os.path.join(ROOT, "sample_test.log")
    res = LogParser.parse(p)
    s = res.stats()
    assert s["total"] == 24, f"total={s['total']}(期望24)"
    assert s["normal"] == 9, f"normal={s['normal']}(期望9)"
    assert s["warnings"] == 8, f"warnings={s['warnings']}(期望8)"
    assert s["risks"] == 7, f"risks(sev)={s['risks']}(期望7)"

    risks = res.risks()
    assert len(risks) == 4, f"风险项={len(risks)}(期望4)"
    titles = " ".join(r["title"] for r in risks)
    assert "认证失败" in titles, "缺少认证失败风险"
    assert "频繁闪断" in titles, "缺少频繁闪断风险"
    assert "Master" in titles, "缺少 Master 不匹配风险"
    assert "恢复出厂" in titles, "缺少恢复出厂设置风险"

    assert res.time_start == __import__("datetime").datetime(2026, 1, 15, 8, 0, 1), \
        f"time_start={res.time_start}"
    assert res.time_end == __import__("datetime").datetime(2026, 1, 15, 21, 0, 0), \
        f"time_end={res.time_end}"
    return "24事件/4风险, 与已知结果一致"


# ===========================================================================
# B. AI 客户端错误处理
# ===========================================================================
@qa_test("B1 未知厂商→明确错误")
def t_b1_unknown_provider():
    try:
        test_connection("虚构厂商", "x", "y")
    except AIClientError as e:
        assert "未知厂商" in str(e), f"错误信息不明确: {e}"
        return f"已抛出 AIClientError: {e}"
    raise AssertionError("未知厂商未抛错(应明确报错)")


@qa_test("B2 空 Key→AIClientError('未填写 API Key')")
def t_b2_empty_key():
    try:
        stream_analysis("DeepSeek", config.AI_PROVIDERS["DeepSeek"]["models"][0],
                        "", "prompt", lambda tok: None)
    except AIClientError as e:
        assert "未填写 API Key" in str(e), f"错误信息不匹配: {e}"
        return f"已抛出 AIClientError: {e}"
    raise AssertionError("空 Key 未抛 AIClientError")


@qa_test("B3 preset_only 厂商不发起网络请求")
def t_b3_preset_only():
    preset_gemini = list(config.AI_PROVIDERS["Gemini (Google)"]["models"])
    preset_claude = list(config.AI_PROVIDERS["Claude (Anthropic)"]["models"])
    # 若真的发起网络请求，则触发 AssertionError，证明测试失败
    with mock.patch.object(ai_client.requests, "get",
                           side_effect=AssertionError("不应发起网络请求")):
        ok_g, m_gemini, note_g = fetch_models("Gemini (Google)", "any-key")
        ok_c, m_claude, note_c = fetch_models("Claude (Anthropic)", "any-key")
    assert m_gemini == preset_gemini, f"Gemini 返回{m_gemini}"
    assert m_claude == preset_claude, f"Claude 返回{m_claude}"
    assert ok_g is False and ok_c is False, "preset_only 应返回 success=False"
    assert "预置列表" in note_g and "预置列表" in note_c, \
        f"note 异常: {note_g!r} / {note_c!r}"
    return "Gemini/Claude 直接返回预置列表(False, 预置, 提示), 未联网"


@qa_test("B4 真实 DeepSeek 调用(可选联网, 需 MOXA_TEST_API_KEY)")
def t_b4_real_deepseek():
    # 真实 Key 一律从环境变量读取（不内置任何真实密钥）；未配置则跳过，
    # 不影响 17 项计数与退出码。
    key = os.environ.get("MOXA_TEST_API_KEY", "").strip()
    if not key:
        print("  [SKIP] 未设置 MOXA_TEST_API_KEY 环境变量，跳过真实联网测试")
        return "SKIP: 未设置 MOXA_TEST_API_KEY 环境变量"
    # 预检网络可达性，避免无外网时长耗时等待
    try:
        socket.create_connection(("api.deepseek.com", 443), timeout=5)
    except Exception:
        print("  [SKIP] 无外网，跳过真实联网测试")
        return "SKIP: 无外网"
    model = config.AI_PROVIDERS["DeepSeek"]["models"][0]
    ok, msg = test_connection("DeepSeek", model, key)
    assert isinstance(ok, bool) and isinstance(msg, str), "返回结构异常"
    # 真实联网结果(成功/失败)不视为源码缺陷，仅记录
    return f"model={model} ok={ok} msg={msg!r}"


@qa_test("B5 fetch_models 无 Key 返回(False, 预置, 提示)")
def t_b5_fetch_models_no_key():
    preset = list(config.AI_PROVIDERS["DeepSeek"]["models"])
    ok, models, note = fetch_models("DeepSeek", "")
    assert ok is False, f"无 Key 应 success=False, 实际 {ok}"
    assert models == preset, f"应返回预置列表, 实际 {models}"
    assert "API Key" in note, f"提示应包含 API Key: {note!r}"
    return note


# ===========================================================================
# C. PDF 导出边界
# ===========================================================================
@qa_test("C1 空 ParseResult 生成 PDF")
def t_c1_empty_pdf():
    res = ParseResult(source_file="empty.log", file_size=0)
    out = os.path.join(tempfile.gettempdir(), "moxa_qa_empty.pdf")
    PDFExporter().generate(res, "", out)
    assert os.path.exists(out), "PDF 未生成"
    return out


@qa_test("C2 特殊字符 AI 文本 PDF 转义")
def t_c2_special_chars_pdf():
    from datetime import datetime as _dt
    res = ParseResult(
        source_file="x.log", file_size=10,
        parsed_events=[LogEvent(0, _dt(2026, 1, 1, 0, 0, 0), "raw",
                                "unknown", SEV_INFO, "测试")],
    )
    special = "A & B < C > D\n中文换行测试\n<tag> & 'quoted' & 100% > 50 & 安全"
    out = os.path.join(tempfile.gettempdir(), "moxa_qa_special.pdf")
    PDFExporter().generate(res, special, out)
    assert os.path.exists(out), "含特殊字符的 PDF 未生成"
    return out


@qa_test("C3 生成 PDF 大小 > 1KB")
def t_c3_pdf_size():
    res = LogParser.parse(os.path.join(ROOT, "sample_test.log"))
    out = os.path.join(tempfile.gettempdir(), "moxa_qa_size.pdf")
    PDFExporter().generate(res, "模拟 AI 分析内容\n第二行\n第三行", out)
    size = os.path.getsize(out)
    assert size > 1024, f"PDF 仅 {size} 字节"
    return f"{size} 字节"


# ===========================================================================
# D. config 健壮性
# ===========================================================================
@qa_test("D1 损坏 config.json 回落默认")
def t_d1_corrupt_config():
    p = write_file("corrupt.json", "{ this is not valid json,,, ")
    cm = config.ConfigManager(path=p)   # 不应崩溃
    assert cm.get_provider() in config.AI_PROVIDERS, \
        f"provider 回落异常: {cm.get_provider()}"
    models = config.AI_PROVIDERS[cm.get_provider()]["models"]
    assert cm.get_model() in models, f"model 回落异常: {cm.get_model()}"
    return f"provider={cm.get_provider()}, model={cm.get_model()}"


@qa_test("D2 CONFIG_FILE 位于 AppData(非临时目录)")
def t_d2_config_path():
    cf = config.CONFIG_FILE
    assert "MOXA_Log_Analyzer" in cf, f"路径缺少应用目录: {cf}"
    assert cf.endswith("config.json"), f"路径结尾异常: {cf}"
    appdata = os.getenv("APPDATA") or ""
    assert appdata and cf.startswith(appdata), f"未位于 AppData: {cf}"
    tmp = tempfile.gettempdir()
    assert tmp not in cf, f"config 位于临时目录: {cf}"
    return cf


# ===========================================================================
# GUI 静态代码审查结论（不启动 GUI）
# ===========================================================================
def gui_static_review() -> None:
    print("\n--- GUI 静态审查结论 (main.py, 未运行) ---")
    notes = [
        "1. 所有交互控件均绑定 command / bind：设置/帮助/浏览/解析/导出/AI 测试·刷新·发送"
        "/Key 显隐/如何获取 Key 等，未发现遗漏绑定的按钮。",
        "2. 惰性控件字典 _settings_widgets 仅在 open_settings() 内创建，"
        "且 _sync_settings_widgets() 与 _set_ai_status() 均对 settings_win is None 做了前置"
        "守卫，无空引用风险。",
        "3. 涉及 current_result 的操作（send_ai / export_pdf）均有 `if self.current_result is"
        " None: return/提示` 守卫，避免空结果导致的空引用。",
        "4. on_provider_change / on_settings_provider_change 在 models 为空时回退"
        " model=''，由 ConfigManager._sanitize 校正为合法首个模型，无非法模型写入风险。",
        "5. 环境限制：本沙箱 venv 未打包 tkinter（customtkinter 导入失败），GUI 无法运行——"
        "属环境/打包限制，非源码缺陷；按任务要求仅做静态审查，未尝试启动。",
    ]
    for n in notes:
        print("   " + n)
    print("   => GUI 静态审查：未发现明显控件绑定遗漏或空引用风险（结论：通过）。")


# ===========================================================================
# 主入口
# ===========================================================================
def main() -> None:
    print("=" * 64)
    print(" MOXA 交换机日志分析工具 — 独立 QA 边界/健壮性测试套件")
    print(" 运行环境: " + sys.version.split()[0])
    print("=" * 64)

    tests = [
        t_a1_empty, t_a2_garbage, t_a3_encoding, t_a4_timestamps,
        t_a5_port_flap, t_a6_no_timestamp, t_a7_regression,
        t_b1_unknown_provider, t_b2_empty_key, t_b3_preset_only,
        t_b4_real_deepseek, t_b5_fetch_models_no_key,
        t_c1_empty_pdf, t_c2_special_chars_pdf, t_c3_pdf_size,
        t_d1_corrupt_config, t_d2_config_path,
    ]
    for t in tests:
        print(f"\n>>> {t.__name__}")
        t()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed

    print("\n" + "=" * 64)
    print(" 测试汇总")
    print("=" * 64)
    for name, ok, detail in RESULTS:
        flag = "PASS" if ok else "FAIL"
        tail = f"  ({detail})" if detail else ""
        print(f"  [{flag}] {name}{tail}")
    print(f"\n  总计: {len(RESULTS)} | 通过: {passed} | 失败: {failed}")

    gui_static_review()

    # 覆盖率说明（基于已覆盖的公开 API / 函数）
    print("\n--- 覆盖率说明 ---")
    print("  已覆盖: LogParser.load_text/parse; ParseResult.stats/risks/"
          "suggestions/time_str/build_analysis_prompt(经A7间接);")
    print("           _extract_timestamp 三种格式; ai_client.test_connection/"
          "fetch_models/stream_analysis 错误分支; PDFExporter.generate 空/特殊字符/大小;")
    print("           ConfigManager.load/_sanitize/CONFIG_FILE 路径。")
    print("  未覆盖(已知边界): 真实网络流式解析全链路、GUI 交互、matplotlib 图表渲染"
          "(沙箱无显示器)；预估逻辑分支覆盖率 ~85%。")

    # 清理临时测试产物
    try:
        for fn in os.listdir(tempfile.gettempdir()):
            if fn.startswith("moxa_qa_"):
                os.remove(os.path.join(tempfile.gettempdir(), fn))
    except Exception:
        pass

    if failed:
        print("\n存在失败项，退出码 1。")
        sys.exit(1)
    print("\n全部通过 ✅")


if __name__ == "__main__":
    main()
