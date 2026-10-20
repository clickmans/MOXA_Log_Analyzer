# -*- coding: utf-8 -*-
"""PDF 报告生成模块（基于 reportlab）。

生成专业的中文 PDF 报告，包含：标题页、日志基本信息、事件统计、异常清单、
AI 分析结果、排查建议、页眉页脚（含页码）。中文显示依赖 Windows 自带微软雅黑字体。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from log_parser import EVENT_TYPE_LABELS, ParseResult

# 候选中文字体（Windows 自带），按顺序尝试
FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyh.ttf",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]

_FONT_NAME = "Helvetica"  # 默认；若找到中文字体则替换


def _register_font() -> str:
    """注册中文字体，返回可用的字体名。"""
    global _FONT_NAME
    if _FONT_NAME != "Helvetica":
        return _FONT_NAME
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                # msyh.ttc 是 TTC 集合，需指定子字体索引
                if path.lower().endswith(".ttc"):
                    pdfmetrics.registerFont(TTFont("CJK", path, subfontIndex=0))
                else:
                    pdfmetrics.registerFont(TTFont("CJK", path))
                _FONT_NAME = "CJK"
                return _FONT_NAME
            except Exception:
                continue
    return _FONT_NAME


# ---------------------------------------------------------------------------
# 附录等宽字体（Windows 自带）
# 中文使用 SimSun（宋体，全角等宽观感），ASCII 使用 Consolas/Courier（等宽）。
# Paragraph 以「中文字体 + <font> 内联标签」组合渲染，保证中文无豆腐块、
# ASCII 等宽、raw 原样可读。
# ---------------------------------------------------------------------------
MONO_FONT_CANDIDATES = [
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/simsun.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/cour.ttf",
]

_MONO_CJK_NAME = "Helvetica"    # 等宽中文字体名（默认占位）
_MONO_ASCII_NAME = "Helvetica"  # 等宽 ASCII 字体名（默认占位）


def _register_mono_font() -> None:
    """注册附录等宽字体：SimSun 渲染中文，Consolas/Courier 渲染 ASCII。"""
    global _MONO_CJK_NAME, _MONO_ASCII_NAME
    if _MONO_CJK_NAME != "Helvetica" and _MONO_ASCII_NAME != "Helvetica":
        return

    cjk_name: Optional[str] = None
    for path in MONO_FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            if path.lower().endswith(".ttc"):
                pdfmetrics.registerFont(TTFont("MonoCJK", path, subfontIndex=0))
            else:
                pdfmetrics.registerFont(TTFont("MonoCJK", path))
            cjk_name = "MonoCJK"
            break
        except Exception:
            continue
    # SimSun 不可用时回退到正文中文字体（msyh），保证中文不出现豆腐块
    if cjk_name is None:
        cjk_name = _FONT_NAME if _FONT_NAME != "Helvetica" else "Helvetica"

    ascii_name: Optional[str] = None
    for path in ("C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/cour.ttf"):
        if not os.path.exists(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont("MonoASCII", path))
            ascii_name = "MonoASCII"
            break
        except Exception:
            continue
    if ascii_name is None:
        ascii_name = "Courier"

    _MONO_CJK_NAME = cjk_name
    _MONO_ASCII_NAME = ascii_name


def _escape_xml(text: str) -> str:
    """Paragraph 必需的 XML 转义，保证特殊字符显示原样、不被解析破坏。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _mono_paragraph_text(text: str) -> str:
    """将日志文本拆分为 ASCII / 非 ASCII 片段，分别用等宽字体渲染。

    返回带 <font> 内联标签的 Paragraph 文本：ASCII 片段用 Consolas/Courier
    （真正等宽），中文片段用 SimSun（全角等宽观感），XML 特殊字符原样转义。
    """
    if not text:
        return ""
    parts: List[str] = []
    buf: List[str] = []
    cur = "ascii" if ord(text[0]) < 128 else "cjk"
    for ch in text:
        bucket = "ascii" if ord(ch) < 128 else "cjk"
        if bucket != cur:
            font = _MONO_ASCII_NAME if cur == "ascii" else _MONO_CJK_NAME
            parts.append(f'<font name="{font}">{_escape_xml("".join(buf))}</font>')
            buf = []
            cur = bucket
        buf.append(ch)
    font = _MONO_ASCII_NAME if cur == "ascii" else _MONO_CJK_NAME
    parts.append(f'<font name="{font}">{_escape_xml("".join(buf))}</font>')
    return "".join(parts)


def _format_delta(delta: timedelta) -> str:
    """格式化 timedelta 为 "+3 天 2:30:00" / "-1 天 0:00:00"。"""
    sign = "+" if delta >= timedelta(0) else "-"
    d = abs(delta)
    hh, rem = divmod(d.seconds, 3600)
    mm, ss = divmod(rem, 60)
    return f"{sign}{d.days} 天 {hh}:{mm:02d}:{ss:02d}"


# ---------------------------------------------------------------------------
# 页眉页脚
# ---------------------------------------------------------------------------
def _on_page(canvas, doc) -> None:
    canvas.saveState()
    w, h = A4
    # 页眉
    canvas.setFont(_FONT_NAME, 9)
    canvas.setFillColor(colors.HexColor("#5b6b7b"))
    canvas.drawString(20 * mm, h - 14 * mm, "MOXA 交换机日志智能分析报告")
    canvas.setStrokeColor(colors.HexColor("#cccccc"))
    canvas.line(20 * mm, h - 16 * mm, w - 20 * mm, h - 16 * mm)
    # 页脚
    canvas.drawString(20 * mm, 12 * mm, "Generated by MOXA Log Analyzer")
    canvas.drawRightString(w - 20 * mm, 12 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


class PDFExporter:
    """PDF 报告导出器。"""

    def __init__(self) -> None:
        self.font = _register_font()
        _register_mono_font()

    # -- 样式 ---------------------------------------------------------------
    def _styles(self):
        ss = getSampleStyleSheet()
        title = ParagraphStyle(
            "TitleZH", parent=ss["Title"], fontName=self.font, fontSize=24,
            textColor=colors.HexColor("#1f2d3d"), spaceAfter=6,
        )
        subtitle = ParagraphStyle(
            "SubZH", parent=ss["Normal"], fontName=self.font, fontSize=12,
            textColor=colors.HexColor("#5b6b7b"), alignment=TA_CENTER,
        )
        h2 = ParagraphStyle(
            "H2ZH", parent=ss["Heading2"], fontName=self.font, fontSize=14,
            textColor=colors.HexColor("#1f6feb"), spaceBefore=10, spaceAfter=6,
        )
        body = ParagraphStyle(
            "BodyZH", parent=ss["Normal"], fontName=self.font, fontSize=10,
            leading=15, alignment=TA_LEFT,
        )
        small = ParagraphStyle(
            "SmallZH", parent=ss["Normal"], fontName=self.font, fontSize=9,
            leading=13, textColor=colors.HexColor("#444444"),
        )
        risk = ParagraphStyle(
            "RiskZH", parent=ss["Normal"], fontName=self.font, fontSize=10,
            leading=15, textColor=colors.HexColor("#c0392b"),
        )
        # 附录：校准说明（橙色突出）
        calib = ParagraphStyle(
            "CalibZH", parent=ss["Normal"], fontName=self.font, fontSize=10,
            leading=15, textColor=colors.HexColor("#b45309"),
        )
        # 附录：日志条目（等宽中文字体为基底，ASCII 片段由内联 <font> 标签接管）
        log = ParagraphStyle(
            "LogMono", parent=ss["Normal"], fontName=_MONO_CJK_NAME,
            fontSize=8.5, leading=12, spaceBefore=2, spaceAfter=1,
        )
        log_raw = ParagraphStyle(
            "LogRawMono", parent=ss["Normal"], fontName=_MONO_CJK_NAME,
            fontSize=8, leading=11, leftIndent=6,
            textColor=colors.HexColor("#555555"), spaceAfter=4,
        )
        return {
            "title": title, "subtitle": subtitle, "h2": h2,
            "body": body, "small": small, "risk": risk,
            "calib": calib, "log": log, "log_raw": log_raw,
        }

    # -- 主入口 -------------------------------------------------------------
    def generate(self, result: ParseResult, ai_analysis: str,
                 filepath: str,
                 calibration: Optional[dict] = None) -> None:
        styles = self._styles()
        doc = BaseDocTemplate(
            filepath, pagesize=A4,
            leftMargin=20 * mm, rightMargin=20 * mm,
            topMargin=22 * mm, bottomMargin=18 * mm,
            title="MOXA 交换机日志分析报告",
            author="MOXA Log Analyzer",
        )
        frame = Frame(
            doc.leftMargin, doc.bottomMargin,
            doc.width, doc.height, id="main",
        )
        doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=_on_page)])

        story: List = []

        # ---- 标题页 ----
        story.append(Spacer(1, 40 * mm))
        story.append(Paragraph("MOXA 交换机日志", styles["title"]))
        story.append(Paragraph("智能分析报告", styles["title"]))
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            styles["subtitle"],
        ))
        story.append(PageBreak())

        # ---- 基本信息 ----
        story.append(Paragraph("一、日志基本信息", styles["h2"]))
        info_rows = [
            ["文件名", result.source_file],
            ["文件大小", f"{result.file_size} 字节"],
            ["总行数", str(result.total_lines)],
            ["解析事件数", str(len(result.parsed_events))],
            ["时间范围", (
                f"{(result.time_start.strftime('%Y-%m-%d %H:%M:%S') if result.time_start else '未知')}"
                f" ~ {(result.time_end.strftime('%Y-%m-%d %H:%M:%S') if result.time_end else '未知')}"
            )],
        ]
        info_table = Table(info_rows, colWidths=[40 * mm, 120 * mm])
        info_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), self.font),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f7")),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#1f6feb")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(info_table)
        story.append(Spacer(1, 6 * mm))

        # ---- 事件统计 ----
        story.append(Paragraph("二、事件统计分析", styles["h2"]))
        stats = result.stats()
        stat_rows = [
            ["指标", "数量"],
            ["事件总数", str(stats["total"])],
            ["正常 (INFO)", str(stats["normal"])],
            ["警告 (WARNING)", str(stats["warnings"])],
            ["风险 (RISK)", str(stats["risks"])],
        ]
        stat_table = Table(stat_rows, colWidths=[60 * mm, 40 * mm])
        stat_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), self.font),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f6feb")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("ALIGN", (1, 0), (1, -1), "CENTER"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")]),
        ]))
        story.append(stat_table)
        story.append(Spacer(1, 4 * mm))

        # 事件类型分布表
        story.append(Paragraph("事件类型分布：", styles["body"]))
        type_rows = [["事件类型", "含义", "数量"]]
        for et, cnt in sorted(stats["by_type"].items(), key=lambda x: -x[1]):
            type_rows.append([et, EVENT_TYPE_LABELS.get(et, et), str(cnt)])
        type_table = Table(type_rows, colWidths=[45 * mm, 75 * mm, 25 * mm])
        type_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), self.font),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ]))
        story.append(type_table)
        story.append(PageBreak())

        # ---- 异常事件清单 ----
        story.append(Paragraph("三、异常事件预警清单", styles["h2"]))
        risks = result.risks()
        if not risks:
            story.append(Paragraph("未检测到明显风险事件。", styles["body"]))
        else:
            risk_rows = [["级别", "预警项", "说明"]]
            for r in risks:
                risk_rows.append([r["level"], r["title"], r["detail"]])
            risk_table = Table(
                risk_rows, colWidths=[22 * mm, 48 * mm, 90 * mm])
            style_cmds = [
                ("FONTNAME", (0, 0), (-1, -1), self.font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#c0392b")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
            for i, r in enumerate(risks, start=1):
                if r["level"] == "RISK":
                    style_cmds.append(
                        ("TEXTCOLOR", (0, i), (0, i), colors.HexColor("#c0392b")))
                    style_cmds.append(
                        ("FONTNAME", (0, i), (0, i), self.font))
            risk_table.setStyle(TableStyle(style_cmds))
            story.append(risk_table)
        story.append(Spacer(1, 6 * mm))

        # ---- AI 分析结果 ----
        story.append(Paragraph("四、AI 深度分析结果", styles["h2"]))
        if ai_analysis and ai_analysis.strip():
            for block in ai_analysis.split("\n"):
                if block.strip():
                    story.append(Paragraph(block.replace("&", "&amp;").replace("<", "&lt;"),
                                           styles["body"]))
                    story.append(Spacer(1, 2 * mm))
        else:
            story.append(Paragraph("（本次未执行 AI 分析）", styles["small"]))
        story.append(PageBreak())

        # ---- 排查建议 ----
        story.append(Paragraph("五、排查建议汇总", styles["h2"]))
        for i, sugg in enumerate(result.suggestions(), start=1):
            story.append(Paragraph(f"{i}. {sugg}", styles["body"]))
            story.append(Spacer(1, 2 * mm))

        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph(
            "本报告由 MOXA 交换机日志智能分析工具自动生成，仅供参考。",
            styles["small"],
        ))

        # ---- 附录：系统日志全文 ----
        self._append_appendix(story, result, calibration, styles)

        doc.build(story)

    # -- 附录 ---------------------------------------------------------------
    def _append_appendix(self, story: List, result: ParseResult,
                         calibration: Optional[dict], styles: dict) -> None:
        """追加「附录：系统日志全文」章节，展示全部日志条目（校准后）。"""
        story.append(PageBreak())
        story.append(Paragraph("附录：系统日志全文", styles["h2"]))
        story.append(Paragraph(
            f"共 {len(result.parsed_events)} 条日志，按时间先后升序排列；"
            "时间戳缺失的条目排在最末。",
            styles["small"],
        ))
        story.append(Spacer(1, 3 * mm))

        if calibration:
            story.append(self._calibration_note(calibration, styles))
            story.append(Spacer(1, 3 * mm))

        events = sorted(
            result.parsed_events,
            key=lambda ev: (ev.timestamp is None, ev.timestamp or datetime.min),
        )
        if not events:
            story.append(Paragraph("无日志记录", styles["body"]))
            return

        # 每条日志一个 KeepTogether 组（时间行 + 原始行），避免单条跨页截断；
        # platypus 自动分页，长行自动换行不丢失内容。
        for ev in events:
            ts = (
                ev.timestamp.strftime("%Y-%m-%d %H:%M:%S")
                if ev.timestamp is not None else "????-??-?? ??:??:??"
            )
            label = EVENT_TYPE_LABELS.get(ev.event_type, ev.event_type)
            line1 = f"[{ts}] [{ev.severity}] [{label}] {ev.description}"
            line2 = f"原始: {ev.raw.strip()}"
            story.append(KeepTogether([
                Paragraph(_mono_paragraph_text(line1), styles["log"]),
                Paragraph(_mono_paragraph_text(line2), styles["log_raw"]),
            ]))

    @staticmethod
    def _calibration_note(calibration: dict, styles: dict) -> Paragraph:
        """构造校准说明段落（橙色突出，含校准时间点与偏移量，供追溯）。"""

        def fmt(value) -> str:
            if isinstance(value, datetime):
                return value.strftime("%Y-%m-%d %H:%M:%S")
            return "未知"

        when = calibration.get("when")
        old_last = calibration.get("old_last")
        new_last = calibration.get("new_last")
        bootup = calibration.get("bootup", "?")
        calibrated = calibration.get("calibrated", "?")
        kept = calibration.get("kept", "?")
        startup_time = calibration.get("startup_time")

        if isinstance(old_last, datetime) and isinstance(new_last, datetime):
            delta_txt = _format_delta(new_last - old_last)
        else:
            delta_txt = "无法计算（校准前无有效时间基准）"

        note = (
            f"校准发生时间：{fmt(when)}；"
            f"校准前最后一条日志时间：{fmt(old_last)}；"
            f"校准后：{fmt(new_last)}；"
            f"时间偏移量：{delta_txt}；"
            f"本次启动周期 Bootup {bootup} 共 {calibrated} 条已校准，"
            f"重启前 {kept} 条保持原样；"
            f"推算启动时间：{fmt(startup_time)}。"
        )
        return Paragraph(_escape_xml(note), styles["calib"])


if __name__ == "__main__":
    # 自检：用一份空结果测试 PDF 生成
    import tempfile

    res = ParseResult(source_file="sample.log", file_size=1024)
    out = os.path.join(tempfile.gettempdir(), "test_report.pdf")
    PDFExporter().generate(res, "示例 AI 分析内容", out)
    print("PDF 已生成:", out)
