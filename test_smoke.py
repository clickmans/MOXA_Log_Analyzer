# -*- coding: utf-8 -*-
"""独立功能测试：编译、import、解析、PDF 生成（无 GUI）。"""
import os
import sys
import py_compile
import tempfile
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

ok = True
errors = []

# 1. 编译所有 .py
print("== 1. py_compile ==")
for fn in ["main.py", "config.py", "log_parser.py", "ai_client.py", "pdf_exporter.py"]:
    try:
        py_compile.compile(os.path.join(ROOT, fn), doraise=True)
        print(f"  [OK] {fn}")
    except py_compile.PyCompileError as e:
        ok = False
        errors.append(f"compile {fn}: {e}")
        print(f"  [FAIL] {fn}: {e}")

# 2. import 模块
print("== 2. import ==")
try:
    import config
    import log_parser
    import ai_client
    import pdf_exporter
    print("  [OK] 全部模块 import 成功")
except Exception as e:
    ok = False
    errors.append(f"import: {e}")
    print(f"  [FAIL] import: {e}")
    traceback.print_exc()

# 3. 解析样例日志
print("== 3. 解析日志 ==")
if ok:
    try:
        res = log_parser.LogParser.parse(os.path.join(ROOT, "sample_test.log"))
        stats = res.stats()
        print(f"  事件总数: {stats['total']}")
        print(f"  正常/警告/风险: {stats['normal']}/{stats['warnings']}/{stats['risks']}")
        print(f"  时间范围: {res.time_start} ~ {res.time_end}")
        risks = res.risks()
        print(f"  风险项({len(risks)}):")
        for r in risks:
            print(f"    - {r['level']} {r['title']}")
        sugg = res.suggestions()
        print(f"  建议({len(sugg)}):")
        for s in sugg:
            print(f"    - {s}")

        # 断言关键结果
        assert stats["total"] == 24, f"期望24事件, 实际{stats['total']}"
        assert stats["risks"] >= 2, "应至少识别2个风险"
        assert any("认证失败" in r["title"] for r in risks), "应识别认证失败"
        assert any("Master" in r["title"] for r in risks), "应识别Master不匹配"
        assert any("频繁闪断" in r["title"] for r in risks), "应识别端口5频繁闪断"
        print("  [OK] 解析断言全部通过")
    except Exception as e:
        ok = False
        errors.append(f"parse: {e}")
        print(f"  [FAIL] parse: {e}")
        traceback.print_exc()

# 4. PDF 生成
print("== 4. PDF 生成 ==")
if ok:
    try:
        res = log_parser.LogParser.parse(os.path.join(ROOT, "sample_test.log"))
        pdf_path = os.path.join(tempfile.gettempdir(), "moxa_test_report.pdf")
        pdf_exporter.PDFExporter().generate(res, "这是一段模拟的 AI 分析文本，用于验证 PDF 输出功能。", pdf_path)
        size = os.path.getsize(pdf_path)
        print(f"  [OK] PDF 已生成: {pdf_path} ({size} 字节)")
        assert size > 1000, "PDF 文件过小"
    except Exception as e:
        ok = False
        errors.append(f"pdf: {e}")
        print(f"  [FAIL] pdf: {e}")
        traceback.print_exc()

# 5. AI client 结构验证
print("== 5. AI client 配置 ==")
if ok:
    try:
        providers = config.AI_PROVIDERS
        assert len(providers) == 9, f"期望9家厂商, 实际{len(providers)}"
        # 安全加固：不得内置任何真实 API Key（占位符 YOUR_API_KEY 除外）
        assert config.DEFAULT_TEST_KEYS == {}, \
            f"DEFAULT_TEST_KEYS 应为空, 实际 {config.DEFAULT_TEST_KEYS!r}"
        for val in config.DEFAULT_TEST_KEYS.values():
            assert not val.startswith("sk-"), "源码不得内置 sk- 前缀真实密钥"
        assert callable(ai_client.test_connection)
        assert callable(ai_client.fetch_models)
        assert callable(ai_client.stream_analysis)
        print("  [OK] 9家厂商, 无内置测试密钥(安全加固), 接口齐全")
    except Exception as e:
        ok = False
        errors.append(f"ai_client: {e}")
        print(f"  [FAIL] ai_client: {e}")

print("\n== 总结 ==")
if ok:
    print("✅ 全部测试通过")
else:
    print("❌ 存在失败项:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
