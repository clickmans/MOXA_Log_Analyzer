# MOXA 交换机日志分析工具

把 MOXA 工业交换机导出的**纯文本日志**，变成一份能直接看懂的诊断结论：事件分类、分布图表、
风险预警、时间校准，再加一份可以发给客户或写进报告的 PDF。

> **免责声明**
> 本项目为**第三方个人开发的辅助工具，非 MOXA 官方产品**，与 MOXA Inc. 无隶属或授权关系。
> 分析结论仅供参考，不能替代厂商正式技术支持；请仅在你有权分析的日志与设备上使用。

---

## 解决什么问题

现场排障时拿到的交换机日志通常是这样：几千行英文、时间戳还不准、关键事件淹在端口 up/down 的
刷屏里。人工翻一遍要半小时，还容易漏掉真正的原因。

这个工具做三件事：

1. **本地先过一遍** —— 不联网、不调用任何服务，把日志解析成结构化事件（端口 / 认证 / 启动重启 /
   拓扑与环网 Master / 模块插拔），统计完给出事件分布图和风险预警。
2. **时间先摆正** —— MOXA 日志只记录 `System Startup Time` 和相对小时数，没有绝对时间。工具支持
   以「你知道的最后一条事件真实时间」为锚点，倒推校准本次启动周期内所有事件，重启前的历史周期保持原样。
3. **再让 AI 深挖** —— 把本地结论压缩成结构化提示词，交给你自己选的大模型（DeepSeek / Kimi /
   智谱 / 豆包 / OpenAI / Claude / Gemini / 通义 / 文心）做二次诊断，最后连同 AI 结论一起导出 PDF。

## 功能

**本地解析（离线可用）**

- 识别 17 类事件：端口启用/停用、管理员登录、登录成功、认证失败、模块插入/拔出/未识别、
  冷启动、热启动、固件升级、恢复出厂、拓扑改变、环网 Master 变更、Master 不匹配、配置生效、未识别
- 兼容 MOXA EDS-4000 / G4000 系列的 400A（A 系列）与 500E（E 系列）日志格式
- 严重级别三档：`INFO` / `WARNING` / `RISK`
- 时间线支持升序/降序切换、按时间段筛选（可只填一端）

**风险预警规则**

| 触发条件 | 级别 | 含义 |
|---|---|---|
| 出现认证失败 | RISK | 疑似暴力破解或凭据泄露，需核查来源 IP 与账号 |
| 同一端口频繁 link down（≥ 阈值） | WARNING | 网线/接头接触不良、对端不稳或光模块故障 |
| Master 配置不匹配 | RISK | 配置的 Master 与实际选举结果不一致，环网切换可能异常 |
| 曾恢复出厂设置 | WARNING | 确认是否为计划内操作，避免配置丢失 |
| 冷启动 ≥ 2 次 | WARNING | 频繁断电重启，疑似供电不稳或硬件异常 |
| 端口模块无法识别 | RISK | 模块损坏、型号不兼容或未完全插入 |
| 端口模块拔出 ≥ 3 次 | WARNING | 确认是否为计划内维护 |

**可视化**

- 事件分布饼图 + 柱状图（matplotlib，随窗口自适应）
- 概览统计：事件总数、时间跨度、风险条目数等

**AI 深度分析**

- 9 家厂商、统一 OpenAI Chat Completions 兼容协议（Claude 走 Anthropic 专有 messages 接口）
- 支持「刷新模型列表」从厂商 `/models` 端点动态拉取（Gemini 无该端点，使用预置列表）
- 内置系统提示词可编辑，发送前可预览将要发给模型的内容，支持一键恢复默认
- 流式输出，边分析边显示
- **不内置任何 API Key**：Key 由你在设置页填写，或通过环境变量
  `MOXA_API_KEY` / `DEEPSEEK_API_KEY` 提供（环境变量仅在内存生效，不写盘）

**PDF 报告**

- 统计概览、事件分类、风险清单、排查建议、AI 分析结论
- 附带时间校准记录，便于事后追溯「数据被怎么改过」

## 下载即用

到 [Releases](../../releases) 下载最新的 `MOXA_Log_Analyzer_v*.exe`，双击运行，
**免安装、无需 Python 环境**（单文件 64 位 Windows 程序）。

首次运行会在 `%APPDATA%\MOXA_Log_Analyzer\config.json` 创建配置文件，用来记住你选的厂商与模型。

### 使用流程

1. 点「浏览...」选择日志文件（`.txt` / `.log`），或把文件拖进窗口
2. 点「开始解析」，左侧看概览与统计，右侧看事件分布图、时间线、风险预警
3. 时间不准就先校准：填「最后一条事件的真实时间」→「应用校准」
4. 需要 AI 深度分析：先到「⚙ 设置」选厂商/模型、填自己的 API Key（可先点「测试连接」）
5. 回主界面点「发送 AI 分析」，看完结论后点「导出 PDF 报告」

## 从源码运行

需要 Python 3.10+（64 位）和 Tk 支持。

```bash
pip install -r requirements.txt
python main.py
```

依赖只有 6 个：`customtkinter`、`requests`、`urllib3`、`matplotlib`、`reportlab`、`Pillow`。
没有 Web 框架、没有数据库、没有后台服务。

## 自己打包 exe

```bat
build.bat
```

等价于：

```bash
python -m PyInstaller --noconfirm --onefile --windowed ^
    --icon=assets/app_icon.ico ^
    --add-data "assets/app_icon.ico;assets" ^
    --name="MOXA_Log_Analyzer" ^
    --hidden-import=customtkinter ^
    --hidden-import=matplotlib.backends.backend_tkagg ^
    --hidden-import=reportlab.pdfbase.ttfonts ^
    main.py
```

产物在 `dist\MOXA_Log_Analyzer.exe`。图标缺失时程序会自动生成一枚占位图标，不影响功能。

## 测试

仓库自带三个独立验证脚本，全部不需要真实 API Key（联网相关用例为可选）：

```bash
python test_smoke.py    # 编译 + import + 解析 + PDF 生成（无 GUI）
python test_qa.py       # 解析边界 / AI 错误处理 / PDF 边界 / config 健壮性
python test_ui_fix.py   # 界面修正相关用例（需图形环境）
```

`sample_test.log` 是一份可直接拿来跑的样例日志。

## 项目结构

```
main.py                  主入口与 GUI（customtkinter）
log_parser.py            日志解析引擎：事件识别、风险规则、排查建议、时间校准
ai_client.py             多厂商 AI 客户端（流式输出、连接测试、模型列表）
pdf_exporter.py          PDF 报告生成（reportlab）
config.py                厂商/模型清单 + 本地配置持久化
assets/app_icon.ico      应用图标
requirements.txt         Python 依赖
MOXA_Log_Analyzer.spec   PyInstaller 打包配置
build.bat                一键打包脚本
test_smoke.py            基础功能测试
test_qa.py               边界 / 健壮性测试套件
test_ui_fix.py           界面相关用例
sample_test.log          样例日志
```

## 隐私与数据流向

- **日志解析、图表、PDF 生成全部在本机完成**，不上传任何日志内容
- 只有在点击「发送 AI 分析」时，才会把**压缩后的结构化摘要**发给你选定的那家厂商 API，
  使用你自己的 Key，走你自己的账号额度
- API Key 只存在本机 `%APPDATA%\MOXA_Log_Analyzer\config.json`，源码中不含任何真实密钥

## 已知限制

- 日志格式解析针对 MOXA 400A / 500E 系列常见输出；其他系列或自定义格式可能出现「未识别」事件
- 时间校准依赖日志中的 `System Startup Time` 字段，缺失或异常时无法校准（会明确提示）
- 时间线筛选只影响界面显示，不改变底层数据与导出内容中的完整事件集
- 模型列表随厂商迭代变化，预置清单可能滞后，建议用「刷新模型列表」获取最新可用模型
- exe 未做代码签名，首次运行可能触发 SmartScreen 提示，选择「更多信息 → 仍要运行」即可

## 版本

- **v1.0.0** —— 首个公开版本：本地解析 + 可视化 + 时间校准 + 9 厂商 AI 分析 + PDF 报告
