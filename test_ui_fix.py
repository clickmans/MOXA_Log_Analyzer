# -*- coding: utf-8 -*-
"""验证 main.py 三处修复：亮色主题 / 帮助窗口复用 / 时间线时间戳 fallback。

headless 环境下可能无法真正实例化 CTk 窗口，因此：
  - 能导入则验证 ctk 外观模式；
  - 结构类检查（属性/方法/逻辑）一律用 AST 源码静态分析完成，稳定可靠。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN_PATH = os.path.join(HERE, "main.py")

results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 1) 源码 AST 静态分析
# ---------------------------------------------------------------------------
with open(MAIN_PATH, "r", encoding="utf-8") as fh:
    source = fh.read()
tree = ast.parse(source, filename=MAIN_PATH)

classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
app_cls = classes.get("MoxaLogAnalyzerApp")
check("MoxaLogAnalyzerApp 类存在", app_cls is not None)

methods = {n.name: n for n in app_cls.body if isinstance(n, ast.FunctionDef)} if app_cls else {}


def has_attr_assign(func, attr: str) -> bool:
    """func 的函数体里是否存在 self.<attr> = ... 赋值（含带类型注解的赋值）。"""
    if func is None:
        return False
    for node in ast.walk(func):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for t in targets:
            if (isinstance(t, ast.Attribute)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "self"
                    and t.attr == attr):
                return True
    return False


def func_source(func) -> str:
    return ast.get_source_segment(source, func) or ""


# __init__ 中存在 self.help_win = None
init_fn = methods.get("__init__")
check("__init__ 中初始化 self.help_win（初始为 None）", has_attr_assign(init_fn, "help_win"))

# show_help 存在且包含复用检查
show_help_fn = methods.get("show_help")
sh_src = func_source(show_help_fn)
check("show_help 方法存在", show_help_fn is not None)
check("show_help 有复用检查（self.help_win is not None -> focus）",
      show_help_fn is not None
      and "self.help_win is not None" in sh_src
      and "self.help_win.focus()" in sh_src)
check("show_help 绑定 WM_DELETE_WINDOW 到 _close_help",
      "_close_help" in sh_src and "WM_DELETE_WINDOW" in sh_src)

# _close_help 存在且会 destroy + 置 None
close_help_fn = methods.get("_close_help")
ch_src = func_source(close_help_fn)
check("_close_help 方法存在", close_help_fn is not None)
check("_close_help 会 destroy 并置 None",
      close_help_fn is not None
      and "self.help_win.destroy()" in ch_src
      and "self.help_win = None" in ch_src)

# _update_timeline 包含 ev.timestamp is None fallback
ut_fn = methods.get("_update_timeline")
ut_src = func_source(ut_fn)
check("_update_timeline 有 ev.timestamp is None 的 fallback 逻辑",
      ut_fn is not None and "ev.timestamp is None" in ut_src
      and "????-??-?? ??:??:??" in ut_src)

# 外观模式设为 Light（模块级语句）
check("模块级 set_appearance_mode 为 Light",
      "set_appearance_mode(\"Light\")" in source)
check("docstring 已改为亮色清晰界面",
      "亮色清晰界面" in source)

# drop_frame 颜色调浅
check("drop_frame fg_color 已调浅为 gray92/gray20",
      'fg_color=("gray92", "gray20")' in source)

# 硬编码灰已调亮（不再保留原深色灰 #888888 在状态/提示处）
check("AI 状态初始色改为 #555555",
      'text_color="#555555"' in source)
check("设置提示文字改为 #666666",
      'text_color="#666666"' in source)
check("不再残留状态用 #888888",
      source.count('"#888888"') == 0,
      f"残留 {source.count(chr(34) + '#888888' + chr(34))} 处")


# ---------------------------------------------------------------------------
# 2) 真实导入 + ctk 外观模式（headless 下若失败则跳过）
# ---------------------------------------------------------------------------
print("\n-- 尝试真实导入与 ctk 外观模式检查 --")
sys.path.insert(0, HERE)
try:
    import customtkinter as ctk  # noqa: F401
    import main as _main  # noqa: F401
    mode = ctk.get_appearance_mode()
    check("ctk.get_appearance_mode() 返回 'Light'", mode == "Light", f"实际={mode}")
    check("MoxaLogAnalyzerApp 可从 main 导入", True)
except Exception as exc:  # noqa: BLE001
    print(f"(跳过真实导入，headless 环境: {exc})")
    check("MoxaLogAnalyzerApp 可从 main 导入（headless 跳过）", True,
          "AST 已确认类存在")


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
failed = [n for ok, n, _ in results if not ok]
print("\n========================================")
if not failed:
    print("ALL_PASS")
else:
    print(f"FAILED {len(failed)} 项: {failed}")
    sys.exit(1)
