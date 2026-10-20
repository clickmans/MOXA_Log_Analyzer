# -*- coding: utf-8 -*-
"""MOXA 交换机日志解析引擎。

支持 MOXA EDS-4000 / G4000 系列工业交换机的 400A(A 系列) 与 500E(E 系列) 日志，
进行本地智能解析、事件分类、风险预警与排查建议生成。
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 事件类型中文标签（用于图表/界面展示）
# ---------------------------------------------------------------------------
EVENT_TYPE_LABELS: Dict[str, str] = {
    "port_link_on": "端口启用",
    "port_link_off": "端口停用",
    "admin_auth_ok": "管理员登录",
    "auth_success": "登录成功",
    "auth_fail": "认证失败",
    "module_removed": "模块拔出",
    "module_inserted": "模块插入",
    "module_unidentified": "模块未识别",
    "cold_start": "冷启动",
    "warm_restart": "热启动",
    "warm_fw_upgrade": "固件升级",
    "warm_factory": "恢复出厂",
    "topology_changed": "拓扑改变",
    "ring_master_changed": "Master变更",
    "master_mismatch": "Master不匹配",
    "config_activated": "配置生效",
    "unknown": "未识别",
}

# 严重级别常量
SEV_INFO = "INFO"
SEV_WARNING = "WARNING"
SEV_RISK = "RISK"

# 月份名称 -> 数字
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# ---------------------------------------------------------------------------
# 事件规则表
# 每条规则: (正则, 事件类型, 严重级别, 描述模板)
# 描述模板支持 {port} 与 {user} 占位符。
# ---------------------------------------------------------------------------
EVENT_RULES: List[Tuple[re.Pattern, str, str, str]] = [
    # 端口链路（兼容数字口 13 / 堆叠口 4-2 / 千兆口 G12 等带字母或连字符的标识）
    (re.compile(r"Port\s+([\w-]+)\s+link\s+on", re.I), "port_link_on", SEV_INFO,
     "端口{port} 已连接"),
    (re.compile(r"Port\s+([\w-]+)\s+link\s+off", re.I), "port_link_off", SEV_WARNING,
     "端口 {port} 已断开"),
    # 管理员登录：带源 IP 的写法优先（group 1 捕获 IP）
    (re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})\s+admin\s+Auth\.\s*ok", re.I),
     "admin_auth_ok", SEV_INFO, "管理员从 {ip} 登录成功"),
    # 管理员登录：无源 IP 的兜底写法（容错空格）
    (re.compile(r"admin\s+Auth\.\s*ok", re.I), "admin_auth_ok", SEV_INFO,
     "使用 admin 账号登录后台成功"),
    # 账户认证（容错 auth. 后可选空格，兼容紧凑与间隔两种写法）
    (re.compile(r"Account\s+'?([^'\s]+)'?\s+auth\.\s*success", re.I), "auth_success", SEV_INFO,
     "用户 '{user}' 登录成功"),
    (re.compile(r"Authentication\s+fail", re.I), "auth_fail", SEV_RISK,
     "登录认证失败（用户名或密码错误）"),
    (re.compile(r"Account\s+'?([^'\s]+)'?\s+auth\.\s*fail", re.I), "auth_fail", SEV_RISK,
     "用户 '{user}' 登录失败"),
    # 端口模块热插拔 / 识别（新增类型）
    (re.compile(r"Port\s+module\s+(\d+)\s+is\s+removed", re.I), "module_removed", SEV_WARNING,
     "端口模块 {port} 被拔出"),
    (re.compile(r"Port\s+module\s+(\d+)\s+is\s+inserted", re.I), "module_inserted", SEV_INFO,
     "端口模块 {port} 已插入"),
    (re.compile(r"Port\s+module\s+(\d+)\s+is\s+unable\s+to\s+identify", re.I),
     "module_unidentified", SEV_RISK, "端口模块 {port} 无法识别"),
    (re.compile(r"Cold\s+start", re.I), "cold_start", SEV_WARNING, "系统冷启动"),
    (re.compile(r"Warm\s+start\s+by\s+System\s+Restart", re.I), "warm_restart", SEV_INFO,
     "系统热启动（系统重启）"),
    (re.compile(r"Warm\s+start\s+by\s+Firmware\s+Upgrade", re.I), "warm_fw_upgrade", SEV_INFO,
     "固件已升级并热启动"),
    (re.compile(r"Warm\s+start\s+by\s+Factory\s+Default", re.I), "warm_factory", SEV_WARNING,
     "恢复出厂设置并热启动"),
    (re.compile(r"Turbo\s+Ring\s+V2\s+Topology\s+changed", re.I), "topology_changed", SEV_WARNING,
     "Turbo Ring V2 拓扑结构改变"),
    (re.compile(r"Turbo\s+Ring\s+V2\s+-\s+master\s+is\s+changed", re.I), "ring_master_changed",
     SEV_WARNING, "Turbo Ring V2 的 Master 已更改"),
    (re.compile(r"Turbo\s+Chain\s+Topology\s+changed", re.I), "topology_changed", SEV_WARNING,
     "Turbo Chain 拓扑结构改变"),
    (re.compile(r"Topology\s+changed", re.I), "topology_changed", SEV_WARNING,
     "网络拓扑结构改变"),
    (re.compile(r"Master\s+setting\s+is\s+mismatched", re.I), "master_mismatch", SEV_RISK,
     "环网 Master 配置与实际 Master 不匹配"),
    (re.compile(r"Turbo\s+Ring-Ring\s+master\s+setting\s+is\s+mismatched", re.I),
     "master_mismatch", SEV_RISK, "Turbo Ring 的 Master 配置与实际 Master 不匹配"),
    (re.compile(r"Configuration\s+change\s+activated", re.I), "config_activated", SEV_INFO,
     "更改的配置已生效"),
]

# 频繁闪断判定阈值（同一端口 link off 次数）
LINK_OFF_THRESHOLD = 3


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class LogEvent:
    """单条日志事件。"""

    index: int
    timestamp: Optional[datetime]
    raw: str
    event_type: str
    severity: str
    description: str
    port: Optional[str] = None
    bootup: Optional[int] = None           # 启动次数（表格第 2 列）
    startup_offset: Optional[timedelta] = None  # System Startup Time 相对偏移

    def time_str(self) -> str:
        if self.timestamp is None:
            return "????-??-?? ??:??:??"
        return self.timestamp.strftime("%Y-%m-%d %H:%M:%S")

    def to_line(self) -> str:
        return f"{self.time_str()} [{self.severity}] {self.description}  | {self.raw.strip()}"


@dataclass
class ParseResult:
    """一次完整解析的结果。"""

    source_file: str = ""
    file_size: int = 0
    total_lines: int = 0
    parsed_events: List[LogEvent] = field(default_factory=list)
    unparsed_lines: List[str] = field(default_factory=list)
    time_start: Optional[datetime] = None
    time_end: Optional[datetime] = None

    # -- 统计 ----------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        by_type: Counter = Counter(e.event_type for e in self.parsed_events)
        by_sev: Counter = Counter(e.severity for e in self.parsed_events)
        return {
            "total": len(self.parsed_events),
            "by_type": dict(by_type),
            "by_severity": dict(by_sev),
            "normal": by_sev.get(SEV_INFO, 0),
            "warnings": by_sev.get(SEV_WARNING, 0),
            "risks": by_sev.get(SEV_RISK, 0),
        }

    # -- 风险预警 ------------------------------------------------------------
    def risks(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []

        auth_fails = [e for e in self.parsed_events if e.event_type == "auth_fail"]
        if auth_fails:
            items.append({
                "level": SEV_RISK,
                "title": f"检测到 {len(auth_fails)} 次认证失败",
                "detail": "存在暴力破解或凭据泄露风险，请核查来源 IP 与账号安全。",
            })

        link_off = [e for e in self.parsed_events if e.event_type == "port_link_off"]
        per_port: Counter = Counter(e.port for e in link_off if e.port)
        for port, cnt in per_port.items():
            if cnt >= LINK_OFF_THRESHOLD:
                items.append({
                    "level": SEV_WARNING,
                    "title": f"端口 {port} 频繁闪断（{cnt} 次）",
                    "detail": "可能存在网线/接头接触不良、对端设备不稳定或光模块故障。",
                })

        mismatch = [e for e in self.parsed_events if e.event_type == "master_mismatch"]
        if mismatch:
            items.append({
                "level": SEV_RISK,
                "title": "环网 Master 配置不匹配",
                "detail": "配置的主设备(Master)与实际选举出的 Master 不一致，可能导致环网切换异常。",
            })

        factory = [e for e in self.parsed_events if e.event_type == "warm_factory"]
        if factory:
            items.append({
                "level": SEV_WARNING,
                "title": "设备曾恢复出厂设置",
                "detail": "请确认该操作是否为运维人员有意执行，避免配置丢失。",
            })

        cold = [e for e in self.parsed_events if e.event_type == "cold_start"]
        if len(cold) >= 2:
            items.append({
                "level": SEV_WARNING,
                "title": f"多次冷启动（{len(cold)} 次）",
                "detail": "频繁断电重启可能意味着供电不稳定或设备硬件异常。",
            })

        # 端口模块无法识别：可能模块损坏 / 型号不兼容 / 未完全插入
        mod_unidentified = [e for e in self.parsed_events
                            if e.event_type == "module_unidentified"]
        if mod_unidentified:
            items.append({
                "level": SEV_RISK,
                "title": f"端口模块无法识别（{len(mod_unidentified)} 次）",
                "detail": "端口模块无法识别：可能模块损坏、型号不兼容或未完全插入，"
                          "请核查插槽与模块规格。",
            })

        # 多次端口模块拔出：确认是否为计划内维护
        mod_removed = [e for e in self.parsed_events
                       if e.event_type == "module_removed"]
        if len(mod_removed) >= 3:
            items.append({
                "level": SEV_WARNING,
                "title": f"多次端口模块拔出（{len(mod_removed)} 次）",
                "detail": "多次端口模块拔出，请确认是否为计划内维护。",
            })

        return items

    # -- 排查建议 ------------------------------------------------------------
    def suggestions(self) -> List[str]:
        risks = self.risks()
        if not risks:
            return ["当前日志未检测到明显异常，建议保持定期巡检与日志归档。"]

        sugg: List[str] = []
        titles = {r["title"] for r in risks}
        if any("认证失败" in t for t in titles):
            sugg.append("立即修改 admin 等特权账号密码，并限制管理网段访问（ACL/VLAN）。")
        if any("频繁闪断" in t for t in titles):
            sugg.append("检查对应端口的网线、水晶头与光模块，必要时更换线缆或端口。")
        if any("Master" in t for t in titles):
            sugg.append("登录交换机核对 Turbo Ring 的 Master 设置，确保与网络规划一致。")
        if any("恢复出厂" in t for t in titles):
            sugg.append("核实恢复出厂操作来源，并重新下发标准配置模板。")
        if any("冷启动" in t for t in titles):
            sugg.append("检查设备供电（冗余电源/UPS），排除电压波动导致的反复重启。")
        if any("模块" in t for t in titles):
            sugg.append("检查对应插槽模块型号与设备兼容性，重新插拔并观察识别状态；"
                        "若持续无法识别请更换模块或联系厂商。")
        sugg.append("建议导出并归档本次日志，结合 SNMP/网管平台做长期趋势监控。")
        return sugg

    # -- 时间校准 ------------------------------------------------------------
    def calibrate_time(self, new_last_time: datetime) -> Optional[Dict[str, Any]]:
        """基于 System Startup Time 相对偏移，从最后一条事件倒推校准本次启动周期内所有事件。

        以手动设定的最后一条事件时间为基准（new_last_time），结合基准行记录的相对启动偏移
        推算本次启动时刻：startup_time = new_last_time - 基准行偏移；
        本次启动周期（基准行所属 Bootup）内每条日志的新时间 = startup_time + 该行偏移。
        其他 Bootup（重启前历史）周期的时间戳保持原样，不做任何修改。

        返回 {'calibrated': n, 'kept': m, 'bootup': B, 'startup_time': datetime}；
        无事件 / 基准行缺少 System Startup Time 字段（bootup 或 offset 缺失）时返回 None。
        """
        if not self.parsed_events:
            return None
        base = self.parsed_events[-1]
        if base.bootup is None or base.startup_offset is None:
            return None  # 无 System Startup Time 字段，无法倒推校准
        bootup = base.bootup
        startup_time = new_last_time - base.startup_offset
        calibrated = 0
        for ev in self.parsed_events:
            if ev.bootup == bootup and ev.startup_offset is not None:
                ev.timestamp = startup_time + ev.startup_offset
                calibrated += 1
            # 其他（重启前周期 / 无偏移）保持原样
        ts_list = [e.timestamp for e in self.parsed_events if e.timestamp is not None]
        self.time_start = min(ts_list) if ts_list else None
        self.time_end = max(ts_list) if ts_list else None
        kept = len(self.parsed_events) - calibrated
        return {"calibrated": calibrated, "kept": kept, "bootup": bootup,
                "startup_time": startup_time}

    # -- AI 分析 Prompt 构造 ------------------------------------------------
    def build_analysis_prompt(self, max_raw: int = 200) -> str:
        stats = self.stats()
        lines: List[str] = []
        lines.append(
            "你是一位资深的工业网络设备（MOXA 交换机）运维专家。"
            "下面是一台 MOXA EDS-4000 / G4000 系列工业交换机的日志解析结果，"
            "请基于这些数据进行专业、严谨的分析。"
        )
        lines.append("")
        lines.append("【日志基本信息】")
        lines.append(f"文件：{self.source_file}")
        lines.append(f"大小：{self.file_size} 字节")
        lines.append(
            f"时间范围：{self.time_start.strftime('%Y-%m-%d %H:%M:%S') if self.time_start else '未知'}"
            f" ~ {self.time_end.strftime('%Y-%m-%d %H:%M:%S') if self.time_end else '未知'}"
        )
        lines.append(
            f"事件总数：{stats['total']}"
            f"（正常 {stats['normal']} / 警告 {stats['warnings']} / 风险 {stats['risks']}）"
        )
        lines.append("")
        lines.append("【事件类型统计】")
        for et, cnt in sorted(stats["by_type"].items(), key=lambda x: -x[1]):
            label = EVENT_TYPE_LABELS.get(et, et)
            lines.append(f"- {label}({et}): {cnt}")
        lines.append("")
        lines.append("【风险预警】")
        for r in self.risks():
            lines.append(f"- [{r['level']}] {r['title']}：{r['detail']}")
        if not self.risks():
            lines.append("- 未检测到明显风险事件")
        lines.append("")
        lines.append(f"【原始日志样本（前 {max_raw} 条）】")
        for ev in self.parsed_events[:max_raw]:
            lines.append(ev.to_line())
        lines.append("")
        lines.append("【分析要求】请按如下结构输出：")
        lines.append("1. 整体健康评估（给出 健康/一般/风险 的结论及 0-100 评分）")
        lines.append("2. 问题诊断（逐条列出发现的问题）")
        lines.append("3. 根因分析（可能导致上述问题的原因）")
        lines.append("4. 解决建议（可落地执行的运维操作步骤）")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 时间戳解析
# ---------------------------------------------------------------------------
def _extract_timestamp(line: str) -> Tuple[Optional[datetime], str]:
    """从行首提取时间戳，返回 (datetime, 剩余消息)。无法识别则返回 (None, 整行)。"""

    # YYYY-MM-DD HH:MM:SS  / YYYY/MM/DD HH:MM:SS  （可选秒）
    m = re.match(
        r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?\s+(.*)$",
        line,
    )
    if m:
        y, mo, d, h, mi, s, rest = m.groups()
        s = s or "0"
        try:
            return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s)), rest
        except ValueError:
            pass

    # MM-DD HH:MM:SS / MM/DD HH:MM:SS （补当前年份）
    m = re.match(
        r"^(\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?\s+(.*)$", line
    )
    if m:
        mo, d, h, mi, s, rest = m.groups()
        s = s or "0"
        try:
            return (
                datetime(datetime.now().year, int(mo), int(d), int(h), int(mi), int(s)),
                rest,
            )
        except ValueError:
            pass

    # Syslog 风格：Mon DD HH:MM:SS
    m = re.match(r"^([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})\s+(.*)$", line)
    if m:
        mon_name, d, h, mi, s, rest = m.groups()
        mo = _MONTHS.get(mon_name.lower())
        if mo:
            try:
                return (
                    datetime(datetime.now().year, mo, int(d), int(h), int(mi), int(s)),
                    rest,
                )
            except ValueError:
                pass

    # 表格型兜底：时间戳位于行中任意位置（YYYY-MM-DD HH:MM:SS，秒可选）。
    # 例如交换机表格日志行首为序号/启动次数，时间列在中间。
    m = re.search(
        r"(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?", line
    )
    if m:
        y, mo, d, h, mi, s = m.groups()
        s = s or "0"
        try:
            return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s)), line
        except ValueError:
            pass

    return None, line


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------
class LogParser:
    """MOXA 日志解析器。"""

    # 编码回退顺序：优先 UTF-8(带 BOM 透明剥离)，其次 GBK（中文 Windows 常见），最后 Latin-1 兜底
    ENCODINGS = ("utf-8-sig", "gbk", "latin-1")

    @staticmethod
    def load_text(path: str) -> str:
        """以多种编码尝试读取文本文件，保证健壮性。"""
        for enc in LogParser.ENCODINGS:
            try:
                with open(path, "r", encoding=enc) as handle:
                    return handle.read()
            except (UnicodeDecodeError, UnicodeError):
                continue
        # 兜底：二进制读取并 latin-1 解码（不会失败）
        with open(path, "rb") as handle:
            return handle.read().decode("latin-1", errors="replace")

    @staticmethod
    def parse(path: str) -> ParseResult:
        """解析给定日志文件，返回 ParseResult。"""
        raw = LogParser.load_text(path)
        lines = raw.splitlines()

        events: List[LogEvent] = []
        unparsed: List[str] = []
        idx = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # 表格日志脚手架行（表头/分隔线）：跳过，避免误判为 unknown 事件。
            # 规则保持保守：真实事件行（如 "Port 13 link on"、含时间戳的表格数据行）
            # 不会被误判。
            s_low = stripped.lower()
            if s_low.startswith("index") and ("date" in s_low or "time" in s_low or "event" in s_low):
                continue
            if re.fullmatch(r"[\s\-=|+]+", stripped):
                continue

            # 表格日志字段：启动次数（Bootup，第 2 列）与 System Startup Time 相对偏移。
            # 仅当行首为「两个数字列」的表格数据行才可能提取到；
            # syslog / 普通时间戳行行首非数字自然为 None。
            bootup_m = re.match(r"^\s*(\d+)\s+(\d+)\s+", stripped)
            bootup: Optional[int] = int(bootup_m.group(2)) if bootup_m else None
            offset_m = re.search(r"(\d+)d(\d+)h(\d+)m(\d+)s", stripped)
            startup_offset: Optional[timedelta] = (
                timedelta(days=int(offset_m.group(1)), hours=int(offset_m.group(2)),
                          minutes=int(offset_m.group(3)), seconds=int(offset_m.group(4)))
                if offset_m else None
            )

            ts, msg = _extract_timestamp(stripped)
            matched = False

            for pattern, etype, severity, tmpl in EVENT_RULES:
                m = pattern.search(msg)
                if not m:
                    continue

                port: Optional[str] = None
                user: Optional[str] = None
                ip: Optional[str] = None
                if m.groups():
                    g = m.group(1)
                    # 按事件类型语义区分捕获组：端口保留为字符串（如 "G12"/"4-2"/"5"），
                    # 管理员带 IP 规则捕获到 IP，其余归为用户名。
                    if etype.startswith("port_link") or etype.startswith("module"):
                        port = g
                    elif etype == "admin_auth_ok":
                        ip = g
                    else:
                        user = g

                desc = tmpl
                if "{port}" in desc and port is not None:
                    desc = desc.format(port=port)
                elif "{user}" in desc and user is not None:
                    desc = desc.format(user=user)
                elif "{ip}" in desc and ip is not None:
                    desc = desc.format(ip=ip)

                events.append(LogEvent(
                    index=idx, timestamp=ts, raw=line, event_type=etype,
                    severity=severity, description=desc, port=port,
                    bootup=bootup, startup_offset=startup_offset,
                ))
                idx += 1
                matched = True
                break

            if not matched:
                events.append(LogEvent(
                    index=idx, timestamp=ts, raw=line, event_type="unknown",
                    severity=SEV_INFO, description="未识别事件", port=None,
                    bootup=bootup, startup_offset=startup_offset,
                ))
                unparsed.append(line)
                idx += 1

        # 时间范围
        ts_list = [e.timestamp for e in events if e.timestamp is not None]
        time_start = min(ts_list) if ts_list else None
        time_end = max(ts_list) if ts_list else None

        try:
            file_size = os.path.getsize(path) if os.path.exists(path) else len(raw.encode("utf-8"))
        except Exception:
            file_size = len(raw.encode("utf-8"))

        return ParseResult(
            source_file=os.path.basename(path),
            file_size=file_size,
            total_lines=len(lines),
            parsed_events=events,
            unparsed_lines=unparsed,
            time_start=time_start,
            time_end=time_end,
        )


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        res = LogParser.parse(sys.argv[1])
        print("文件:", res.source_file, "大小:", res.file_size)
        print("统计:", res.stats())
        print("风险:", res.risks())
        print("建议:", res.suggestions())
    else:
        print("用法: python log_parser.py <日志文件>")
