# -*- coding: utf-8 -*-
"""MOXA 交换机日志智能分析工具 —— 主入口与 GUI。

基于 customtkinter 构建亮色清晰界面，集成：
  - 日志读取与本地智能解析（log_parser）
  - 多厂商 AI 深度分析（ai_client）
  - 专业 PDF 报告导出（pdf_exporter）
  - 配置持久化（config）
"""

from __future__ import annotations

import os
import re
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox
from typing import Any, Dict, List, Optional, Tuple

import customtkinter as ctk

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from ai_client import fetch_models, stream_analysis, test_connection
from config import AI_PROVIDERS, ConfigManager
from log_parser import EVENT_TYPE_LABELS, LogParser, ParseResult, SEV_RISK, SEV_WARNING

# 尝试让 matplotlib 支持中文（依赖系统中文字体）
try:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "Arial"]
    plt.rcParams["axes.unicode_minus"] = False
except Exception:
    pass

# 资源路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BASE_DIR, "assets", "app_icon.ico")

# 主题
ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")


def ensure_icon() -> Optional[str]:
    """确保应用图标存在；不存在则用标准库生成一枚简单图标。"""
    if os.path.exists(ICON_PATH):
        return ICON_PATH
    try:
        os.makedirs(os.path.dirname(ICON_PATH), exist_ok=True)
        import struct

        W = H = 64
        pixels = bytearray()
        rows = []
        for y in range(H):
            row = bytearray()
            for x in range(W):
                dx, dy = x - W / 2, y - H / 2
                if dx * dx + dy * dy < (W * 0.38) ** 2:
                    r, g, b = 255, 255, 255
                else:
                    r, g, b = 31, 111, 235
                row += bytes([b, g, r, 255])
            rows.append(row)
        # BMP 自底向上存储
        for row in reversed(rows):
            pixels += row
        and_mask = bytearray(((W + 31) // 32 * 4) * H)
        bih = struct.pack("<IiiHHIIiiII", 40, W, 2 * H, 1, 32, 0, 0, 0, 0, 0, 0)
        img = bih + bytes(pixels) + bytes(and_mask)
        icondir = struct.pack("<HHH", 0, 1, 1)
        entry = struct.pack("<BBBBHHII", W, H, 0, 0, 1, 32, len(img), 6 + 16)
        with open(ICON_PATH, "wb") as fh:
            fh.write(icondir + entry + img)
        return ICON_PATH
    except Exception:
        return None


class MoxaLogAnalyzerApp(ctk.CTk):
    """主应用窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.title("🔧 MOXA交换机日志智能分析工具---买MOXA，找明高")
        self.geometry("1280x800")
        self.minsize(1024, 700)

        icon = ensure_icon()
        if icon:
            try:
                self.iconbitmap(icon)
            except Exception:
                pass

        self.config_manager = ConfigManager()
        self.current_result: Optional[ParseResult] = None
        self.default_prompt: str = ""          # 内置默认提示词快照
        self.prompt_modified: bool = False     # 用户是否修改过提示词
        self.preview_win: Optional[ctk.CTkToplevel] = None  # 发送前预览窗口（复用）
        self._preview_box: Optional[ctk.CTkTextbox] = None  # 预览窗口只读文本区
        self._preview_prompt: str = ""         # 预览确认时待发送的提示词
        self.last_calibration: Optional[dict] = None
        self.current_log_path: Optional[str] = None
        self.timeline_filter: Optional[Tuple[Optional[datetime], Optional[datetime]]] = None
        self.timeline_sort: str = "asc"            # 时间线排序方向: "asc" 升序 / "desc" 降序
        self.ai_analysis_text: str = ""
        self.chart_widgets: List[Any] = []
        self.settings_win: Optional[ctk.CTkToplevel] = None
        self.help_win: Optional[ctk.CTkToplevel] = None

        self._build_header()
        self._build_body()
        self._build_footer()
        self._sync_ai_widgets()

    # =====================================================================
    # 界面构建
    # =====================================================================
    def _build_header(self) -> None:
        self.header = ctk.CTkFrame(self, height=48, corner_radius=0)
        self.header.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.header.grid_propagate(False)

        title = ctk.CTkLabel(
            self.header, text="🔧 MOXA交换机日志智能分析工具---买MOXA，找明高",
            font=ctk.CTkFont(size=16, weight="bold"))
        title.pack(side="left", padx=16, pady=10)

        self.btn_settings = ctk.CTkButton(
            self.header, text="⚙ 设置", width=80, command=self.open_settings)
        self.btn_settings.pack(side="right", padx=(0, 8), pady=8)

        self.btn_help = ctk.CTkButton(
            self.header, text="❓ 帮助", width=80, command=self.show_help)
        self.btn_help.pack(side="right", padx=(0, 12), pady=8)

    def _build_body(self) -> None:
        # 左侧固定栏
        self.left = ctk.CTkFrame(self, width=380)
        self.left.grid(row=1, column=0, sticky="ns", padx=(8, 4), pady=8)
        self.left.grid_propagate(False)
        self.left.grid_columnconfigure(0, weight=1)

        # 右侧滚动区
        self.right = ctk.CTkScrollableFrame(self)
        self.right.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=8)
        self.right.grid_columnconfigure(0, weight=1)

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)

        self._build_left()
        self._build_right()

    def _build_left(self) -> None:
        # ---- 文件选择 ----
        f_file = ctk.CTkFrame(self.left)
        f_file.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        f_file.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(f_file, text="📂 选择日志文件", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=8, pady=(6, 3))

        self.drop_frame = ctk.CTkFrame(f_file, height=48, fg_color=("gray92", "gray20"),
                                       border_width=2, border_color=("#1f6feb", "#1f6feb"))
        self.drop_frame.pack(fill="x", padx=8, pady=3)
        self.drop_frame.pack_propagate(False)
        self.drop_label = ctk.CTkLabel(
            self.drop_frame, text="点击选择日志文件\n（支持 .txt / .log）",
            font=ctk.CTkFont(size=10))
        self.drop_label.pack(expand=True)
        self.drop_frame.bind("<Button-1>", lambda _e: self.browse_file())
        self.drop_label.bind("<Button-1>", lambda _e: self.browse_file())

        self.btn_browse = ctk.CTkButton(f_file, text="浏览...", command=self.browse_file)
        self.btn_browse.pack(fill="x", padx=8, pady=(2, 3))

        # 文件信息
        self.lbl_file = ctk.CTkLabel(f_file, text="文件: -", anchor="w")
        self.lbl_file.pack(fill="x", padx=8, pady=(1, 0))
        self.lbl_size = ctk.CTkLabel(f_file, text="大小: -", anchor="w")
        self.lbl_size.pack(fill="x", padx=8, pady=(0, 0))
        self.lbl_range = ctk.CTkLabel(f_file, text="时间: -", anchor="w")
        self.lbl_range.pack(fill="x", padx=8, pady=(0, 4))

        self.btn_parse = ctk.CTkButton(
            f_file, text="▶ 开始解析", command=self.do_parse, state="disabled")
        self.btn_parse.pack(fill="x", padx=8, pady=(0, 6))

        # ---- 概览 ----
        f_over = ctk.CTkFrame(self.left)
        f_over.grid(row=1, column=0, sticky="ew", padx=8, pady=6)
        f_over.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(f_over, text="📊 日志概览", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 3))

        self.lbl_total = self._stat_cell(f_over, "总事件数", "0", 1, 0)
        self.lbl_risk = self._stat_cell(f_over, "风险事件", "0", 1, 1)
        self.lbl_normal = self._stat_cell(f_over, "正常事件", "0", 2, 0)
        self.lbl_warn = self._stat_cell(f_over, "警告事件", "0", 2, 1)

        # ---- 风险预警 ----
        f_risk = ctk.CTkFrame(self.left)
        f_risk.grid(row=2, column=0, sticky="nsew", padx=8, pady=6)
        f_risk.grid_columnconfigure(0, weight=1)
        f_risk.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(f_risk, text="⚠️ 风险预警", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(6, 3))
        self.risk_text = ctk.CTkTextbox(f_risk, height=330, wrap="word", font=ctk.CTkFont(size=11))
        self.risk_text.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.risk_text.insert("0.0", "解析后此处显示风险预警。")
        self.risk_text.configure(state="disabled")

        self.left.grid_rowconfigure(2, weight=1)

    def _stat_cell(self, parent, title: str, value: str, row: int, col: int):
        frame = ctk.CTkFrame(parent)
        frame.grid(row=row, column=col, padx=4, pady=2, sticky="nsew")
        lbl_v = ctk.CTkLabel(frame, text=value, font=ctk.CTkFont(size=17, weight="bold"),
                             text_color="#1f6feb")
        lbl_v.pack(pady=(4, 0))
        lbl_t = ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=10))
        lbl_t.pack(pady=(0, 4))
        return lbl_v

    def _build_right(self) -> None:
        # ---- 图表 ----
        f_chart = ctk.CTkFrame(self.right)
        f_chart.pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(f_chart, text="📈 事件分布", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=8, pady=(8, 4))
        self.chart_row = ctk.CTkFrame(f_chart)
        self.chart_row.pack(fill="x", padx=8, pady=(0, 8))
        self.chart_row.grid_columnconfigure((0, 1), weight=1)
        self.chart_hint = ctk.CTkLabel(
            self.chart_row, text="解析后在此显示事件分布饼图与柱状图。",
            font=ctk.CTkFont(size=11))
        self.chart_hint.grid(row=0, column=0, columnspan=2, padx=8, pady=10)

        # ---- 时间线 ----
        f_tl = ctk.CTkFrame(self.right)
        f_tl.pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(f_tl, text="📋 事件时间线", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 4))

        # ---- 时间校准工具条 ----
        f_calib = ctk.CTkFrame(f_tl)
        f_calib.pack(fill="x", padx=10, pady=(0, 4))
        f_calib.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(f_calib, text="时间校准", font=ctk.CTkFont(size=11)).grid(
            row=0, column=0, padx=(0, 6), pady=4, sticky="w")
        self.lbl_last_time = ctk.CTkLabel(
            f_calib, text="最后: -", anchor="w", font=ctk.CTkFont(size=10))
        self.lbl_last_time.grid(row=0, column=1, padx=4, pady=4, sticky="w")
        self.calib_entry = ctk.CTkEntry(
            f_calib, placeholder_text="YYYY-MM-DD HH:MM:SS", width=150)
        self.calib_entry.grid(row=0, column=2, padx=4, pady=4, sticky="w")
        self.btn_calib = ctk.CTkButton(
            f_calib, text="应用校准", width=90, command=self.calibrate_time_action,
            state="disabled")
        self.btn_calib.grid(row=0, column=3, padx=4, pady=4)
        # 校准状态提示：成功时显示绿色结果文案（仅覆盖本次启动周期）
        self.lbl_calib_status = ctk.CTkLabel(
            f_calib, text="", anchor="w", font=ctk.CTkFont(size=10),
            text_color="#2ecc71")
        self.lbl_calib_status.grid(row=0, column=4, padx=(8, 0), pady=4, sticky="w")

        # ---- 时间段筛选工具条 ----
        f_filt = ctk.CTkFrame(f_tl)
        f_filt.pack(fill="x", padx=10, pady=(0, 4))
        f_filt.grid_columnconfigure(6, weight=1)
        ctk.CTkLabel(f_filt, text="时间段筛选", font=ctk.CTkFont(size=11)).grid(
            row=0, column=0, padx=(0, 6), pady=4, sticky="w")
        self.filter_start_entry = ctk.CTkEntry(
            f_filt, placeholder_text="开始 YYYY-MM-DD HH:MM:SS", width=130)
        self.filter_start_entry.grid(row=0, column=1, padx=4, pady=4, sticky="w")
        self.filter_end_entry = ctk.CTkEntry(
            f_filt, placeholder_text="结束 YYYY-MM-DD HH:MM:SS", width=130)
        self.filter_end_entry.grid(row=0, column=2, padx=4, pady=4, sticky="w")
        self.btn_filter_apply = ctk.CTkButton(
            f_filt, text="应用筛选", width=90, command=self.apply_filter, state="disabled")
        self.btn_filter_apply.grid(row=0, column=3, padx=4, pady=4)
        self.btn_filter_clear = ctk.CTkButton(
            f_filt, text="清除筛选", width=90, command=self.clear_filter, state="disabled")
        self.btn_filter_clear.grid(row=0, column=4, padx=4, pady=4)

        # ---- 排序工具条 ----
        f_sort = ctk.CTkFrame(f_tl)
        f_sort.pack(fill="x", padx=10, pady=(0, 4))
        f_sort.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(f_sort, text="排序方式", font=ctk.CTkFont(size=11)).grid(
            row=0, column=0, padx=(0, 6), pady=4, sticky="w")
        self.sort_selector = ctk.CTkSegmentedButton(
            f_sort, values=["升序", "降序"], command=self._on_timeline_sort_change,
            width=120)
        self.sort_selector.set("升序")
        self.sort_selector.grid(row=0, column=1, padx=4, pady=4, sticky="w")
        self.lbl_timeline_stats = ctk.CTkLabel(
            f_sort, text="", anchor="w", font=ctk.CTkFont(size=10),
            text_color="#666666")
        self.lbl_timeline_stats.grid(row=0, column=2, padx=(8, 0), pady=4, sticky="w")

        self.timeline_text = ctk.CTkTextbox(f_tl, height=260, wrap="none",
                                            font=ctk.CTkFont(size=11, family="Consolas"))
        self.timeline_text.pack(fill="x", padx=10, pady=(0, 10))
        # 行距优化：CTkTextbox 底层是 tk.Text，可直接配置行前/行后间距（实测 spacing1/3 有效）；
        # 旧版 customtkinter 无 _textbox 时静默回退，仅保留字号统一。
        try:
            self.timeline_text._textbox.configure(spacing1=2, spacing3=2)
        except Exception:  # noqa: BLE001
            pass
        self.timeline_text.insert("0.0", "解析后在此显示按时间排序的事件列表。")
        self.timeline_text.configure(state="disabled")

        # ---- AI 分析 ----
        f_ai = ctk.CTkFrame(self.right)
        f_ai.pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(f_ai, text="🤖 AI 深度分析", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 4))

        # ---- 可编辑提示词区（发送按钮上方）----
        prompt_header = ctk.CTkFrame(f_ai, fg_color="transparent")
        prompt_header.pack(fill="x", padx=10, pady=(2, 0))
        prompt_header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(prompt_header, text="✏️ AI 分析提示词 (Prompt)",
                     font=ctk.CTkFont(size=11, weight="bold")).grid(
            row=0, column=0, padx=(0, 6), pady=4, sticky="w")

        self.lbl_prompt_status = ctk.CTkLabel(
            prompt_header, text="当前使用默认提示词", text_color="#666666",
            font=ctk.CTkFont(size=10))
        self.lbl_prompt_status.grid(row=0, column=1, sticky="w", padx=4, pady=4)

        self.btn_prompt_reset = ctk.CTkButton(
            prompt_header, text="恢复默认提示词", width=110,
            command=self.restore_default_prompt)
        self.btn_prompt_reset.grid(row=0, column=2, padx=4, pady=4)

        self.prompt_text = ctk.CTkTextbox(f_ai, height=130, wrap="word",
                                          font=ctk.CTkFont(size=11))
        self.prompt_text.pack(fill="x", padx=10, pady=(2, 0))
        self.prompt_text.bind("<KeyRelease>", self._on_prompt_changed)

        ctk.CTkLabel(
            f_ai, text="默认内置系统提示词，可编辑后作为发送内容；可通过下方按钮恢复默认",
            font=ctk.CTkFont(size=10), text_color="#666666").pack(
            anchor="w", padx=10, pady=(2, 4))

        ctrl = ctk.CTkFrame(f_ai)
        ctrl.pack(fill="x", padx=10, pady=4)
        ctrl.grid_columnconfigure(1, weight=1)

        self.btn_send = ctk.CTkButton(ctrl, text="发送 AI 分析", width=140,
                                      command=self.send_ai)
        self.btn_send.grid(row=0, column=0, padx=4, pady=4, sticky="w")

        self.lbl_ai_status = ctk.CTkLabel(ctrl, text="", text_color="#555555",
                                          font=ctk.CTkFont(size=10))
        self.lbl_ai_status.grid(row=0, column=1, sticky="w", padx=4, pady=4)

        self.ai_result = ctk.CTkTextbox(f_ai, height=260, wrap="word",
                                        font=ctk.CTkFont(size=11))
        self.ai_result.pack(fill="x", padx=10, pady=(0, 10))
        self.ai_result.insert("0.0", "AI 分析结果将在此流式显示。")
        self.ai_result.configure(state="disabled")

    def _build_footer(self) -> None:
        self.footer = ctk.CTkFrame(self, height=50, corner_radius=0)
        self.footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.footer.grid_propagate(False)
        self.footer.grid_columnconfigure(0, weight=1)

        self.btn_export = ctk.CTkButton(
            self.footer, text="📄 导出 PDF 报告", width=180, command=self.export_pdf,
            state="disabled", fg_color="#1f6feb", hover_color="#155bb5")
        self.btn_export.grid(row=0, column=0, sticky="e", padx=16, pady=9)

    # =====================================================================
    # 文件与解析
    # =====================================================================
    def browse_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 MOXA 日志文件",
            filetypes=[("日志文件", "*.txt *.log"), ("所有文件", "*.*")])
        if not path:
            return
        self.current_log_path = path
        self.btn_parse.configure(state="normal")
        self.drop_label.configure(text=os.path.basename(path))
        try:
            size = os.path.getsize(path)
        except Exception:
            size = 0
        self.lbl_file.configure(text=f"文件: {os.path.basename(path)}")
        self.lbl_size.configure(text=f"大小: {size} 字节")

    def do_parse(self) -> None:
        if not self.current_log_path:
            return
        try:
            result = LogParser.parse(self.current_log_path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("解析失败", f"无法解析日志文件：{exc}")
            return

        self.current_result = result
        # 解析成功：生成内置默认提示词并填充输入框（用户可编辑；修改后以输入框内容为准）
        try:
            self.default_prompt = result.build_analysis_prompt()
            self.prompt_text.delete("1.0", "end")
            self.prompt_text.insert("1.0", self.default_prompt)
        except Exception:  # noqa: BLE001
            self.default_prompt = ""
        self.prompt_modified = False
        self._set_prompt_status("当前使用默认提示词", "#666666")
        self.last_calibration = None  # 解析新文件后清除上次校准元数据
        self.timeline_filter = None
        self._update_overview(result)
        self._draw_charts(result)
        self._update_timeline(result)
        self._update_risks(result)
        self.btn_export.configure(state="normal")
        self.btn_calib.configure(state="normal")
        self.btn_filter_apply.configure(state="normal")
        self.btn_filter_clear.configure(state="normal")
        self.lbl_calib_status.configure(text="")

        rng = (
            f"{(result.time_start.strftime('%Y-%m-%d %H:%M:%S') if result.time_start else '未知')}"
            f" ~ {(result.time_end.strftime('%Y-%m-%d %H:%M:%S') if result.time_end else '未知')}"
        )
        self.lbl_range.configure(text=f"时间: {rng}")
        self._update_last_time_label(result)

    # =====================================================================
    # 概览 / 时间线 / 风险
    # =====================================================================
    def _update_overview(self, result: ParseResult) -> None:
        s = result.stats()
        self.lbl_total.configure(text=str(s["total"]))
        self.lbl_risk.configure(text=str(s["risks"]))
        self.lbl_normal.configure(text=str(s["normal"]))
        self.lbl_warn.configure(text=str(s["warnings"]))
        self.lbl_risk.configure(
            text_color="#e74c3c" if s["risks"] > 0 else "#1f6feb")

    @staticmethod
    def _is_scaffolding(raw: str) -> bool:
        """判断一行是否为表格日志的脚手架（表头 / 分隔线），而非真实事件。"""
        s = raw.strip()
        if not s:
            return True
        # 表头行：如 "Index Bootup Date Time Event"
        if s.lower().startswith("index"):
            return True
        # 纯分隔线：仅由空白、-、=、| 组成
        if re.fullmatch(r"[\s\-=|]+", s):
            return True
        return False

    @staticmethod
    def _truncate(text: str, max_len: int, keep: int) -> str:
        """超过 max_len 个字符时截断为 keep 个字符 + '...'，防止超长文本破坏对齐。"""
        if len(text) > max_len:
            return text[:keep] + "..."
        return text

    @staticmethod
    def _timeline_sort_key(ev) -> Tuple[bool, Any]:
        """时间线排序键（升序）：第一维保证无时间戳事件恒排最后，其余按时间早→晚。

        说明：不使用 timestamp() 做数值键（Windows 上早于本地纪元的时间戳会抛
        OSError），因此降序在 _update_timeline 中通过仅反转有时间戳部分实现。
        """
        return (ev.timestamp is None, ev.timestamp or datetime.min)

    def _update_timeline(self, result: ParseResult) -> None:
        self.timeline_text.configure(state="normal")
        self.timeline_text.delete("0.0", "end")
        placeholder = "????-??-?? ??:??:??"
        flt = self.timeline_filter
        start, end = (flt if flt is not None else (None, None))
        # 排序：先按升序（早→晚）且无时间戳事件恒排最后；
        # 降序时仅反转有时间戳部分，无时间戳事件仍保持在末尾。
        desc = self.timeline_sort == "desc"
        events = sorted(result.parsed_events, key=self._timeline_sort_key)
        if desc:
            timed = [e for e in events if e.timestamp is not None]
            untimed = [e for e in events if e.timestamp is None]
            events = timed[::-1] + untimed
        shown = 0
        for ev in events:
            # 时间段筛选：仅渲染落在 [start, end] 范围内的事件；无时间戳事件在筛选时跳过。
            # 校准已对 ev.timestamp 平移，因此筛选自动基于校准后的时间。
            if flt is not None:
                if ev.timestamp is None:
                    continue
                if start is not None and ev.timestamp < start:
                    continue
                if end is not None and ev.timestamp > end:
                    continue
            # 跳过表格脚手架行（表头/分隔线）：这类行无时间戳，若显示会产生无意义的 ???? 占位符
            if ev.event_type == "unknown" and ev.timestamp is None and self._is_scaffolding(ev.raw):
                continue
            # 时间戳缺失时优先从原始行提取候选时间做 fallback 显示，取不到再用同宽占位符
            if ev.timestamp is None:
                m = re.search(r'(\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)', ev.raw)
                time_str = (m.group(1) if m else placeholder)[:19]
            else:
                time_str = ev.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            # 固定列宽 + 等宽字体（Consolas）保证 ASCII 列严格对齐：
            # 时间 19 宽、级别徽章 9 宽，desc/raw 截断防超长换行与横向滚动拖垮布局
            sev_badge = f"[{ev.severity}]".ljust(9)
            desc_txt = self._truncate(ev.description, 26, 23)
            raw = self._truncate(ev.raw.strip(), 120, 117)
            self.timeline_text.insert("end", f"{time_str:<19}  {sev_badge} {desc_txt} | {raw}\n")
            shown += 1
        # 筛选激活时在时间线顶部插入一行统计提示
        if flt is not None:
            self.timeline_text.insert("0.0", f"显示 {shown} / {len(result.parsed_events)} 条（筛选范围）\n")
        # 工具条统计：共 N 条（或筛选后显示 X / N 条），并标注当前排序方向
        order_label = "降序" if desc else "升序"
        if flt is not None:
            self.lbl_timeline_stats.configure(
                text=f"显示 {shown} / {len(result.parsed_events)} 条（筛选范围）· 按时间{order_label}")
        else:
            self.lbl_timeline_stats.configure(
                text=f"共 {len(result.parsed_events)} 条，按时间{order_label}")
        self.timeline_text.configure(state="disabled")

    # =====================================================================
    # 时间校准 / 时间段筛选
    # =====================================================================
    @staticmethod
    def _last_event_time(result: ParseResult) -> Optional[datetime]:
        """取解析结果中最后一条带时间戳的事件时间；无则返回 None。"""
        for ev in reversed(result.parsed_events):
            if ev.timestamp is not None:
                return ev.timestamp

    def _update_last_time_label(self, result: ParseResult) -> None:
        """刷新「最后事件时间」只读标签。"""
        t = self._last_event_time(result)
        self.lbl_last_time.configure(
            text=f"最后: {t.strftime('%Y-%m-%d %H:%M:%S') if t else '未知'}")

    def calibrate_time_action(self) -> None:
        """时间校准：以用户输入作为本次启动周期最后一条事件的新时间，基于 System
        Startup Time 相对偏移逐行倒推校准该周期内所有事件；重启前周期保持原样。"""
        if self.current_result is None:
            messagebox.showwarning("提示", "请先解析日志文件，再进行时间校准。")
            return
        raw_ts = self.calib_entry.get().strip()
        if not raw_ts:
            messagebox.showwarning("提示", "请输入新的最后事件时间（格式 YYYY-MM-DD HH:MM:SS）。")
            return
        try:
            new_ts = datetime.strptime(raw_ts, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            messagebox.showerror("输入错误", "时间格式无效，请使用 YYYY-MM-DD HH:MM:SS。")
            return
        # 校准前记录最后一条事件的时间戳（可能为 None：校准前无有效时间基准）
        old_last: Optional[datetime] = None
        if self.current_result.parsed_events:
            old_last = self.current_result.parsed_events[-1].timestamp
        info = self.current_result.calibrate_time(new_ts)
        if info is None:
            messagebox.showwarning(
                "无法校准", "日志缺少 System Startup Time 字段或该字段缺失/异常，已跳过校准。")
            return
        # 记录本次校准元数据，供 PDF 附录追溯展示（导出时传入 exporter）
        self.last_calibration = {
            "when": datetime.now(),
            "old_last": old_last,
            "new_last": new_ts,
            "bootup": info["bootup"],
            "calibrated": info["calibrated"],
            "kept": info["kept"],
            "startup_time": info["startup_time"],
        }
        result = self.current_result
        rng = (
            f"{(result.time_start.strftime('%Y-%m-%d %H:%M:%S') if result.time_start else '未知')}"
            f" ~ {(result.time_end.strftime('%Y-%m-%d %H:%M:%S') if result.time_end else '未知')}"
        )
        self.lbl_range.configure(text=f"时间: {rng}")
        self._update_timeline(result)
        self._update_last_time_label(result)
        self.lbl_calib_status.configure(
            text=(
                f"已校准本次启动周期（Bootup {info['bootup']}）{info['calibrated']} 条；"
                f"重启前 {info['kept']} 条保持原样；"
                f"推算启动时间 {info['startup_time']:%Y-%m-%d %H:%M:%S}"
            ),
            text_color="#2ecc71")

    def apply_filter(self) -> None:
        """时间段筛选：仅保留 [start, end] 范围内的事件渲染到时间线（可只填一端）。"""
        if self.current_result is None:
            messagebox.showwarning("提示", "请先解析日志文件，再进行时间段筛选。")
            return
        start_raw = self.filter_start_entry.get().strip()
        end_raw = self.filter_end_entry.get().strip()
        start: Optional[datetime] = None
        end: Optional[datetime] = None
        if start_raw:
            try:
                start = datetime.strptime(start_raw, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                messagebox.showerror("输入错误", "开始时间格式无效，请使用 YYYY-MM-DD HH:MM:SS。")
                return
        if end_raw:
            try:
                end = datetime.strptime(end_raw, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                messagebox.showerror("输入错误", "结束时间格式无效，请使用 YYYY-MM-DD HH:MM:SS。")
                return
        if start is not None and end is not None and start > end:
            messagebox.showwarning("提示", "开始时间晚于结束时间，请检查输入。")
            return
        self.timeline_filter = (start, end)
        self._update_timeline(self.current_result)

    def clear_filter(self) -> None:
        """清除时间段筛选，恢复完整时间线。"""
        self.timeline_filter = None
        if self.current_result is not None:
            self._update_timeline(self.current_result)

    def _on_timeline_sort_change(self, value: str) -> None:
        """排序切换：升序/降序（CTkSegmentedButton 回调），切换后刷新时间线。"""
        self.timeline_sort = "asc" if value == "升序" else "desc"
        if self.current_result is not None:
            self._update_timeline(self.current_result)

    def _update_risks(self, result: ParseResult) -> None:
        self.risk_text.configure(state="normal")
        self.risk_text.delete("0.0", "end")
        risks = result.risks()
        if not risks:
            self.risk_text.insert("0.0", "✅ 未检测到明显风险事件。")
        else:
            for r in risks:
                self.risk_text.insert("end", f"[{r['level']}] {r['title']}\n")
                self.risk_text.insert("end", f"    {r['detail']}\n\n")
        self.risk_text.configure(state="disabled")

    # =====================================================================
    # 图表（matplotlib 嵌入）
    # =====================================================================
    def _draw_charts(self, result: ParseResult) -> None:
        for w in self.chart_widgets:
            try:
                w.destroy()
            except Exception:
                pass
        self.chart_widgets.clear()
        if self.chart_hint is not None:
            try:
                self.chart_hint.destroy()
            except Exception:
                pass
            self.chart_hint = None

        by_type = result.stats()["by_type"]
        if not by_type:
            return

        # 仅展示数量前 10 的类型，其余归入“其他”
        items = sorted(by_type.items(), key=lambda x: -x[1])
        if len(items) > 10:
            top = items[:10]
            others = sum(c for _, c in items[10:])
            top.append(("其他", others))
            items = top
        labels = [EVENT_TYPE_LABELS.get(k, k) for k, _ in items]
        values = [c for _, c in items]

        # 饼图
        fig_pie = Figure(figsize=(4.0, 3.0), dpi=110)
        fig_pie.patch.set_alpha(0.0)
        ax_pie = fig_pie.add_subplot(111)
        ax_pie.pie(values, labels=labels, autopct="%1.0f%%", textprops={"fontsize": 10},
                   wedgeprops={"width": 0.4})
        ax_pie.set_title("事件类型占比", fontsize=12)
        canvas_pie = FigureCanvasTkAgg(fig_pie, master=self.chart_row)
        canvas_pie.draw()
        widget_pie = canvas_pie.get_tk_widget()
        widget_pie.grid(row=0, column=0, padx=3, pady=3, sticky="nsew")
        self.chart_widgets.append(widget_pie)

        # 柱状图
        fig_bar = Figure(figsize=(4.6, 3.0), dpi=110)
        fig_bar.patch.set_alpha(0.0)
        ax_bar = fig_bar.add_subplot(111)
        ax_bar.bar(range(len(labels)), values, color="#1f6feb")
        ax_bar.set_xticks(range(len(labels)))
        ax_bar.set_xticklabels(labels, rotation=35, ha="right", fontsize=9)
        ax_bar.set_title("事件数量分布", fontsize=12)
        fig_bar.tight_layout()
        canvas_bar = FigureCanvasTkAgg(fig_bar, master=self.chart_row)
        canvas_bar.draw()
        widget_bar = canvas_bar.get_tk_widget()
        widget_bar.grid(row=0, column=1, padx=3, pady=3, sticky="nsew")
        self.chart_widgets.append(widget_bar)

    # =====================================================================
    # AI 控件
    # =====================================================================
    def _sync_ai_widgets(self) -> None:
        """从配置同步设置窗口 AI 控件（主窗口 AI 区提供可编辑提示词 + 发送按钮）。"""
        self._sync_settings_widgets()

    def _sync_settings_widgets(self) -> None:
        if self.settings_win is None:
            return
        sw = self._settings_widgets
        provider = self.config_manager.get_provider()
        model = self.config_manager.get_model()
        key = self.config_manager.get_api_key()
        sw["provider"].set(provider)
        models = AI_PROVIDERS.get(provider, {}).get("models", [])
        sw["model"].configure(values=models)
        sw["model"].set(model if model in models else (models[0] if models else ""))
        sw["key"].configure(show="●")
        sw["key"].delete(0, "end")
        sw["key"].insert(0, key)

    def _set_ai_status(self, text: str, color: str = "#555555") -> None:
        self.lbl_ai_status.configure(text=text, text_color=color)
        if self.settings_win is not None:
            self._settings_widgets["status"].configure(text=text, text_color=color)

    # =====================================================================
    # AI 提示词（可编辑输入 + 恢复默认 + 状态提示）
    # =====================================================================
    def _set_prompt_status(self, text: str, color: str = "#666666") -> None:
        """更新提示词状态标签；控件未就绪时静默跳过。"""
        lbl = getattr(self, "lbl_prompt_status", None)
        if lbl is None:
            return
        try:
            lbl.configure(text=text, text_color=color)
        except Exception:  # noqa: BLE001
            pass

    def _on_prompt_changed(self, _event=None) -> None:
        """提示词输入变化：三态判定（默认 / 已修改 / 已清空）。"""
        try:
            cur = self.prompt_text.get("1.0", "end-1c")
        except Exception:  # noqa: BLE001
            return
        if cur == self.default_prompt:
            self.prompt_modified = False
            self._set_prompt_status("当前使用默认提示词", "#666666")
        elif cur.strip() == "":
            self.prompt_modified = True
            self._set_prompt_status("提示词已清空（发送前请填写）", "#e67e22")
        else:
            self.prompt_modified = True
            self._set_prompt_status("已修改自定义提示词", "#1f6feb")

    def restore_default_prompt(self) -> None:
        """一键还原为内置默认提示词（default_prompt 快照）。"""
        try:
            self.prompt_text.delete("1.0", "end")
            self.prompt_text.insert("1.0", self.default_prompt)
        except Exception:  # noqa: BLE001
            return
        self.prompt_modified = False
        self._set_prompt_status("当前使用默认提示词", "#666666")

    # =====================================================================
    # AI 操作（在后台线程执行，避免界面卡死）
    # =====================================================================
    def send_ai(self) -> None:
        if self.current_result is None:
            messagebox.showwarning("提示", "请先解析日志文件，再发送 AI 分析。")
            return
        # 从可编辑提示词输入框取当前内容（不再临时构造）
        try:
            prompt = self.prompt_text.get("1.0", "end-1c").strip()
        except Exception:  # noqa: BLE001
            prompt = ""
        if not prompt:
            messagebox.showwarning(
                "提示", "提示词为空，请填写内容或点击「恢复默认提示词」。")
            return
        # 发送前预览确认：确认后才发起流式请求，取消则不发送
        self._show_prompt_preview(prompt)

    def _show_prompt_preview(self, prompt: str) -> None:
        """发送前预览确认窗口：只读展示完整提示词 + 「确认发送」/「取消」。"""
        self._preview_prompt = prompt
        win = self.preview_win
        if win is not None:
            try:
                if win.winfo_exists():
                    win.focus()
                else:
                    win = None
            except Exception:  # noqa: BLE001
                win = None
        if win is None:
            win = ctk.CTkToplevel(self)
            win.title("确认发送 AI 分析")
            win.geometry("640x480")
            win.minsize(520, 380)
            try:
                win.iconbitmap(ICON_PATH)
            except Exception:  # noqa: BLE001
                pass
            self.preview_win = win
            win.protocol("WM_DELETE_WINDOW", self._close_prompt_preview)

            ctk.CTkLabel(
                win, text="请确认以下提示词内容（只读），确认后将发起 AI 流式分析：",
                font=ctk.CTkFont(size=11), text_color="#666666").pack(
                anchor="w", padx=16, pady=(12, 4))

            box = ctk.CTkTextbox(win, wrap="word", font=ctk.CTkFont(size=11))
            box.pack(fill="both", expand=True, padx=16, pady=4)
            self._preview_box = box

            btn_row = ctk.CTkFrame(win, fg_color="transparent")
            btn_row.pack(fill="x", padx=16, pady=(8, 12))

            btn_cancel = ctk.CTkButton(
                btn_row, text="取消", width=110, command=self._close_prompt_preview)
            btn_cancel.pack(side="right")

            btn_ok = ctk.CTkButton(
                btn_row, text="确认发送", width=140, fg_color="#1f6feb",
                hover_color="#155bb5", command=self._confirm_send)
            btn_ok.pack(side="right", padx=(0, 8))

        # 复用窗口时同步刷新内容
        box = getattr(self, "_preview_box", None)
        if box is not None:
            try:
                box.configure(state="normal")
                box.delete("1.0", "end")
                box.insert("1.0", prompt)
                box.configure(state="disabled")
            except Exception:  # noqa: BLE001
                pass

    def _close_prompt_preview(self) -> None:
        """关闭预览窗口（仅销毁窗口，不影响主窗口状态）。"""
        if self.preview_win is not None:
            try:
                self.preview_win.destroy()
            except Exception:  # noqa: BLE001
                pass
            self.preview_win = None
        self._preview_box = None

    def _confirm_send(self) -> None:
        """预览窗口「确认发送」：关闭预览并执行实际 AI 分析请求。"""
        prompt = getattr(self, "_preview_prompt", "")
        self._close_prompt_preview()
        self._do_send_ai(prompt)

    def _do_send_ai(self, prompt: str) -> None:
        """预览确认后执行实际 AI 流式分析（provider/model/key 从配置读取）。"""
        provider = self.config_manager.get_provider()
        model = self.config_manager.get_model()
        api_key = self.config_manager.get_api_key().strip()
        if not api_key:
            messagebox.showwarning(
                "提示",
                "请先在设置中填写自己的 API Key；"
                "或通过环境变量 MOXA_API_KEY / DEEPSEEK_API_KEY 提供（无需填框）。")
            return

        self.ai_result.configure(state="normal")
        self.ai_result.delete("0.0", "end")
        self.ai_result.insert("0.0", "正在分析，请稍候...\n")
        self.ai_result.configure(state="disabled")
        self._set_ai_status("AI 分析中（流式输出）...", "#555555")
        self.btn_send.configure(state="disabled")

        buffer = []

        def on_token(tok: str):
            buffer.append(tok)
            self.ai_result.configure(state="normal")
            self.ai_result.insert("end", tok)
            self.ai_result.see("end")
            self.ai_result.configure(state="disabled")

        def worker():
            try:
                stream_analysis(provider, model, api_key, prompt, on_token)
                full = "".join(buffer)
                self.ai_analysis_text = full

                def done():
                    self.ai_result.configure(state="disabled")
                    self.btn_send.configure(state="normal")
                    self._set_ai_status("AI 分析完成", "#2ecc71")

                self.after(0, done)
            except Exception as exc:  # noqa: BLE001
                err = str(exc)

                def fail():
                    self.ai_result.configure(state="normal")
                    self.ai_result.insert("end", f"\n\n❌ 分析失败：{err}")
                    self.ai_result.configure(state="disabled")
                    self.btn_send.configure(state="normal")
                    self._set_ai_status(f"分析失败：{err}", "#e74c3c")

                self.after(0, fail)

        threading.Thread(target=worker, daemon=True).start()

    # =====================================================================
    # PDF 导出
    # =====================================================================
    def export_pdf(self) -> None:
        if self.current_result is None:
            return
        path = filedialog.asksaveasfilename(
            title="导出 PDF 报告", defaultextension=".pdf",
            filetypes=[("PDF 文件", "*.pdf")],
            initialfile=f"MOXA_Log_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
        if not path:
            return
        try:
            from pdf_exporter import PDFExporter

            PDFExporter().generate(
                self.current_result, self.ai_analysis_text, path,
                calibration=self.last_calibration)
            messagebox.showinfo("导出成功", f"PDF 报告已保存：\n{path}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", f"生成 PDF 时出错：{exc}")

    # =====================================================================
    # 设置窗口
    # =====================================================================
    def open_settings(self) -> None:
        if self.settings_win is not None:
            self.settings_win.focus()
            return
        win = ctk.CTkToplevel(self)
        win.title("⚙ 设置")
        win.geometry("420x470")
        win.resizable(False, False)
        try:
            win.iconbitmap(ICON_PATH)
        except Exception:
            pass
        self.settings_win = win
        win.protocol("WM_DELETE_WINDOW", self._close_settings)

        self._settings_widgets: Dict[str, Any] = {}
        sw = self._settings_widgets

        ctk.CTkLabel(win, text="AI 厂商设置", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=16, pady=(12, 6))

        frm = ctk.CTkFrame(win)
        frm.pack(fill="x", padx=16, pady=4)
        frm.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frm, text="厂商:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        sw["provider"] = ctk.CTkComboBox(
            frm, values=list(AI_PROVIDERS.keys()),
            command=self.on_settings_provider_change)
        sw["provider"].grid(row=0, column=1, padx=6, pady=6, sticky="ew")

        ctk.CTkLabel(frm, text="模型:").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        sw["model"] = ctk.CTkComboBox(frm)
        sw["model"].grid(row=1, column=1, padx=6, pady=6, sticky="ew")

        ctk.CTkLabel(frm, text="Key:").grid(row=2, column=0, padx=6, pady=6, sticky="w")
        sw["key"] = ctk.CTkEntry(frm, show="●")
        sw["key"].grid(row=2, column=1, padx=6, pady=6, sticky="ew")
        sw["key_shown"] = False
        sw["toggle"] = ctk.CTkButton(
            frm, text="👁", width=36, command=self.toggle_settings_key)
        sw["toggle"].grid(row=2, column=2, padx=6, pady=6)

        btn_fill = ctk.CTkButton(
            frm, text="如何获取 Key", width=110, command=self.show_key_guidance)
        btn_fill.grid(row=3, column=0, columnspan=2, padx=6, pady=6, sticky="w")
        btn_test = ctk.CTkButton(frm, text="测试连接", width=90, command=self.test_ai_settings)
        btn_test.grid(row=3, column=2, padx=6, pady=6)
        btn_refresh = ctk.CTkButton(
            frm, text="刷新模型列表", width=110, command=self.refresh_models_settings)
        btn_refresh.grid(row=4, column=0, columnspan=2, padx=6, pady=6, sticky="w")

        sw["status"] = ctk.CTkLabel(
            win, text="", text_color="#666666", font=ctk.CTkFont(size=10))
        sw["status"].pack(anchor="w", padx=16, pady=(4, 0))

        ctk.CTkLabel(
            win,
            text="设置自动保存到本地 config.json。\n"
                 "请使用您自己的 API Key；未配置时也可通过环境变量 "
                 "MOXA_API_KEY / DEEPSEEK_API_KEY 提供（无需填框）。",
            font=ctk.CTkFont(size=10), text_color="#666666",
            justify="left").pack(anchor="w", padx=16, pady=(8, 0))

        btn_close = ctk.CTkButton(win, text="保存并关闭", command=self._close_settings)
        btn_close.pack(side="bottom", padx=16, pady=12, fill="x")

        self._sync_settings_widgets()
        self._set_ai_status(self.lbl_ai_status.cget("text"))

    def _close_settings(self) -> None:
        if self.settings_win is not None:
            self.settings_win.destroy()
            self.settings_win = None
        self._sync_ai_widgets()

    def on_settings_provider_change(self, _event=None) -> None:
        provider = self._settings_widgets["provider"].get()
        models = AI_PROVIDERS.get(provider, {}).get("models", [])
        self._settings_widgets["model"].configure(values=models)
        if models:
            self._settings_widgets["model"].set(models[0])
        self.config_manager.set(provider=provider, model=models[0] if models else "")
        self._sync_ai_widgets()

    def toggle_settings_key(self) -> None:
        sw = self._settings_widgets
        sw["key_shown"] = not sw["key_shown"]
        sw["key"].configure(show="" if sw["key_shown"] else "●")
        sw["toggle"].configure(text="🙈" if sw["key_shown"] else "👁")

    def show_key_guidance(self) -> None:
        """引导用户获取自己的 API Key。

        本软件不内置任何测试密钥；Key 可通过以下任一方式提供：
          1. 在设置窗口的 Key 输入框中粘贴自己的 API Key 并保存；
          2. 通过环境变量 MOXA_API_KEY / DEEPSEEK_API_KEY 提供（无需填框，
             仅本次运行生效，不会写回磁盘）。
        """
        messagebox.showinfo(
            "填写您的 API Key",
            "本软件不内置任何测试密钥。\n\n"
            "请前往对应厂商官网注册并获取自己的 API Key，"
            "然后在 Key 输入框粘贴保存；\n"
            "或通过环境变量 MOXA_API_KEY / DEEPSEEK_API_KEY 提供（无需填框）。",
        )

    def test_ai_settings(self) -> None:
        provider = self._settings_widgets["provider"].get()
        model = self._settings_widgets["model"].get()
        api_key = self._settings_widgets["key"].get().strip()
        self.config_manager.set(provider=provider, model=model, api_key=api_key)
        self._set_ai_status("正在测试连接...", "#555555")

        def worker():
            ok, msg = test_connection(provider, model, api_key)
            color = "#2ecc71" if ok else "#e74c3c"

            def done():
                self._set_ai_status(msg, color)

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _provider_uses_preset_only(self, provider: str) -> bool:
        """该厂商是否不提供 /models 端点（Gemini / Claude），刷新走预置回退属预期行为。"""
        cfg = AI_PROVIDERS.get(provider, {})
        return bool(cfg.get("preset_only") or cfg.get("format") == "anthropic")

    def refresh_models_settings(self) -> None:
        provider = self._settings_widgets["provider"].get()
        api_key = self._settings_widgets["key"].get().strip()
        self.config_manager.set(provider=provider, api_key=api_key)
        self._set_ai_status("正在刷新模型列表...", "#555555")

        def worker():
            success, models, note = fetch_models(provider, api_key)

            def done():
                sw = self._settings_widgets
                sw["model"].configure(values=models)
                if models:
                    sw["model"].set(models[0])
                if success:
                    # 拉取成功：旧配置模型已下线（不在新列表）时自动切换到首个新模型；
                    # 否则保留用户当前选择，避免覆盖用户已选中的模型
                    current = self.config_manager.get_model()
                    if current not in models:
                        new_model = models[0] if models else ""
                        self.config_manager.set(model=new_model)
                        if new_model:
                            note = f"{note}；旧模型已下线，已切换到 {new_model}"
                    color = "#2ecc71"
                elif not api_key:
                    note = "请先在 Key 中填写 API Key 再刷新（当前使用预置列表）"
                    color = "#e67e22"
                elif self._provider_uses_preset_only(provider):
                    # 无 /models 端点（Gemini/Claude）回退预置属预期，非错误，用中性色
                    color = "#666666"
                else:
                    color = "#e74c3c"
                self._sync_ai_widgets()
                self._set_ai_status(note, color)

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    # =====================================================================
    # 帮助
    # =====================================================================
    def show_help(self) -> None:
        if self.help_win is not None:
            self.help_win.focus()
            return
        text = (
            "MOXA 交换机日志智能分析工具 使用说明\n"
            "====================================\n\n"
            "1. 点击左侧「浏览...」或拖拽区域选择日志文件（.txt / .log）。\n"
            "2. 点击「开始解析」，工具将本地智能识别端口状态、登录认证、"
            "启动/重启、拓扑与环网 Master 等事件。\n"
            "3. 右侧查看事件分布图表、时间线与风险预警。\n"
            "4. 在「⚙ 设置」中选择厂商/模型并填写自己的 API Key（本软件不内置"
            "测试密钥；也可通过环境变量 MOXA_API_KEY / DEEPSEEK_API_KEY 提供），"
            "回到主界面点击「发送 AI 分析」获取深度诊断。\n"
            "5. 点击「导出 PDF 报告」生成包含统计、风险与 AI 分析的专业报告。\n\n"
            "支持的厂商：DeepSeek / Kimi / 智谱 / 豆包 / OpenAI / Claude / "
            "Gemini / 通义千问 / 文心一言。\n\n"
            "日志解析在本地完成，AI 分析需联网调用对应厂商 API。"
        )
        win = ctk.CTkToplevel(self)
        win.title("❓ 帮助")
        win.geometry("560x420")
        try:
            win.iconbitmap(ICON_PATH)
        except Exception:
            pass
        self.help_win = win
        win.protocol("WM_DELETE_WINDOW", self._close_help)
        box = ctk.CTkTextbox(win, wrap="word", font=ctk.CTkFont(size=11))
        box.pack(fill="both", expand=True, padx=16, pady=16)
        box.insert("0.0", text)
        box.configure(state="disabled")

    def _close_help(self) -> None:
        if self.help_win is not None:
            self.help_win.destroy()
            self.help_win = None


def main() -> None:
    app = MoxaLogAnalyzerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
