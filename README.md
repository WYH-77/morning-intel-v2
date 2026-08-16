# 每日早盘全球股票情报精选（增强版 v2.1）

> A股交易日北京时间 09:20 自动执行。使用 **AKShare（主） + WebSearch（兜底）** 作为数据源，
> 自动覆盖 iFinD 同花顺 & TDX 通达信 同类数据能力。生成 Markdown 格式早盘情报报告，
> 通过**企业微信群机器人 Webhook** 推送，并在仓库 `output/` 目录自动归档全部产物。

---

## ✨ 核心功能一览

| 模块 | 说明 |
|------|------|
| Step 0 · 交易日校验 | 自动跳过周末 & 2024/2025/2026 年 A股法定休假日（含调休补班） |
| Step 1 · 大盘环境 | 隔夜美股三大指数 + 金龙 + A50 + 韩国 KOSPI / KOSDAQ；A股三大指数、涨跌比、涨跌停、成交预估、北向/主力/融资/大小盘风格、热门板块涨跌前5 |
| Step 2 · 候选筛选 + 5大类数据 | 按近5日涨幅/量能放大/价格/流通市值/主板/非ST/跑赢板块/行业景气 8 项条件筛选 30-50 只；再抓取量价技术、新闻资讯、基本面、资金流、风险事件5大类数据 |
| Step 3 · 自适应权重打分 | 情绪为【偏强/震荡/偏弱】自动切换权重方案；5大维度共 25+ 评分项；综合得分 → TOP5 主板（60/000/001/002/003 开头，严格排除688/300/301） |
| Step 4 · 报告生成 + 推送 | 严格按规范 Markdown 输出，保存 `output/morning_report_YYYYMMDD.md`；过长自动分段推送到企业微信机器人，防重复推送保护 |
| Step 5 · 历史记录 | 当日选股清单写入 `output/daily_picks/stock_picks_YYYYMMDD.md`；前一日选股当日表现自动追加到 `historical_performance.log`（开盘→收盘涨跌幅、最高涨幅、最大回撤） |

---

## 📁 项目目录结构

```
morning_intel_v2/
├── main.py                             # 主入口（常驻/单次/调度自检 3种模式）
├── report_generator.py                 # 报告生成（严格 Markdown 格式）
├── push_service.py                     # 企业微信推送 + 防重复保护
├── requirements.txt
├── .env.example
├── .gitignore
│
├── config/
│   ├── __init__.py
│   └── settings.py                     # 全量运行时配置（含节假日硬编码）
│
├── core/
│   ├── __init__.py
│   ├── utils.py                        # 日志、重试、交易日、工具函数
│   ├── trading_day.py                  # 交易日校验（含补班日 override）
│   ├── market_overview.py              # Step1 大盘环境检索
│   ├── stock_screener.py               # Step2 候选筛选 + 5大类数据抓取
│   ├── scoring_engine.py               # Step3 自适应权重打分排序
│   └── history_tracker.py              # Step5 历史选股 + 表现跟踪
│
├── data_sources/
│   ├── __init__.py
│   ├── base.py                         # 数据源抽象基类 + 数据模型
│   ├── akshare_source.py               # 主数据源：AKShare
│   ├── websearch_source.py             # 兜底数据源：WebSearch + requests
│   └── manager.py                      # 按优先级顺序调用 + 自动 failover
│
├── logs/                               # 日志（*.gitignore，不入库）
│   └── morning_intel.log.YYYY-MM-DD
│
├── output/                             # 所有产物（每次 CI 自动回写仓库）
│   ├── morning_report_YYYYMMDD.md
│   └── daily_picks/
│       ├── stock_picks_YYYYMMDD.md
│       └── historical_performance.log
│
└── .github/workflows/
    └── daily_morning_report.yml        # GitHub Actions 定时 + 手动双触发
```

---

## 🚀 快速开始（本地）

### 1. 安装依赖（需要 Python 3.10+）

```bash
cd morning_intel_v2
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置企业微信 Webhook（可选，不配置则模拟推送）

```bash
cp .env.example .env
# 编辑 .env，把 WECHAT_WEBHOOK 改成你的机器人地址
WECHAT_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

支持的环境变量命名（任一即可）：
`WECHAT_WEBHOOK` | `QX_WECOM_WEBHOOK` | `WECOM_WEBHOOK` | `QYWX_WEBHOOK`

### 3. 常用命令

```bash
# 🧪 调度配置自检（不跑选股，只校验交易日/配置）
python main.py --test-scheduler

# 🧪 单次运行 + 强制推送（测试用，会跳过交易日校验+当日重复推送保护）
python main.py --once --force

# 🌟 正常单次运行（交易日才执行）
python main.py --once

# 🧪 历史回测模式：模拟指定日期的完整流程（不会推送，但会生成报告）
python main.py --once --force --as-of-date=2026-08-14

# 🌙 常驻模式：每天 09:20（±2 分钟随机抖动）自动触发（本地长期运行）
python main.py
```

---

## ☁️ 部署到 GitHub Actions（推荐，云端定时 + 零运维）

### 1. 把整个 `morning_intel_v2/` 目录放进你的 Git 仓库

```bash
cd morning_intel_v2
git init
git add .
git commit -m "feat: 每日早盘全球股票情报精选（增强版 v2.1）初版"
git remote add origin git@github.com:你的用户名/你的仓库名.git
git branch -M main
git push -u origin main
```

> ⚠️ 注意：你仓库的根目录**就是** `morning_intel_v2/` 里的东西；
> 如果你想把 `morning_intel_v2/` 作为一个子目录放进去，需要把 workflow 里的
> `working-directory: morning_intel_v2` 与 `morning_intel_v2/output/` 等路径一并对应修改。

### 2. 在 GitHub 仓库设置 Secrets

打开你的仓库 → **Settings → Secrets and variables → Actions → New repository secret**：

| Name | Value | 必填 |
|------|-------|------|
| `WECHAT_WEBHOOK` | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx-xxxx...` | 推荐（否则模拟推送） |
| `IFIND_API_KEY` | 同花顺 iFinD Key（留空则自动走 AKShare 主源 + WebSearch 兜底） | 可选 |
| `TDX_API_KEY` | 通达信 TDX Key（留空则自动走 AKShare 主源 + WebSearch 兜底） | 可选 |
| `TDX_TIMEOUT_SEC` | 数据接口超时，默认 `20` | 可选 |

### 3. 可选：变量 Variables（精细调参）

同一页 **Variables → Actions → New repository variable**：

| Name | 默认值 | 说明 |
|------|--------|------|
| `TOP_N` | `5` | 最终精选标的数 |
| `MIN_CANDIDATES` | `30` | 候选股下限（不足则放宽条件补） |
| `MAX_CANDIDATES` | `60` | 候选股上限 |
| `MIN_SCORE_THRESHOLD` | `0.0` | 综合得分最低门槛 |
| `TARGET_PRICE_MIN` | `5.0` | 排除 < X 元的低价股 |
| `TARGET_MV_BILLION_MIN` | `5.0` | 排除流通市值 < X 亿 的小市值 |

### 4. 触发一次手动运行确认没问题

**Actions → 每日早盘全球股票情报精选 → Run workflow**：
- `Force Run` 选 `true`
- 点 `Run workflow`

约 5~15 分钟后会收到机器人推送，并且 `output/` 目录会新增今日报告和选股清单，
`logs` 目录会以 Artifact 形式保留 30 天，方便排错。

### 5. 自动执行

**工作日每日北京时间 09:20（UTC 01:20）** 自动触发。
非交易日程序会在 Step 0 直接退出，不会浪费算力。

---

## 🧠 评分权重方案（自适应）

根据 Step 1 判定的市场情绪 **自动切换**：

| 维度 \ 情绪 | 偏强（趋势市） | 震荡（平衡市） | 偏弱（防御市） |
|------------|---------------|---------------|---------------|
| A. 市场环境 | 10% | 10% | 15% |
| B. 量价技术 | 40% | 30% | 20% |
| C. 新闻资讯 | 25% | 25% | 20% |
| D. 业绩基本面 | 15% | 20% | 35% |
| E. 资金面 | 10% | 15% | 10% |
| **合计** | **100%** | **100%** | **100%** |

关注等级：
- ★★★★★ ≥85
- ★★★★☆ 75–84
- ★★★☆☆ 60–74
- ★★☆☆☆ <60

---

## 🚨 硬性永久约束（代码内已强校验）

1. ✅ 所有数据联网实时调取，检索不到标【暂无有效数据】，绝不编造。
2. ✅ 全文禁止"买入/卖出/持仓/抄底/加仓/推荐/建议"等交易操作建议词。
3. ✅ 新闻仅 24h 内，按置信度（高/中/低）加权并打分。
4. ✅ 美股/韩股/A50 严格区分时区与盘前/盘中/盘后状态。
5. ✅ 每日重新筛选，不复用前一日名单。
6. ✅ 每只标的**强制市场/行业/个股三层风险提示**，个股至少 2 条且针对性生成。
7. ✅ 全链路数据不可用时直接退出，不凑数。
8. ✅ 所有涨跌幅标注统计区间。
9. ✅ **强制主板筛选**：只保留 `60/000/001/002/003` 开头；`688/300/301` 开头 0 容忍。
10. ✅ 双源/多源优先级：主源失败自动切兜底源；两源存在时行情→主源、财务→主源、资讯→时效优先。
11. ✅ 插件/MCP 不可用时自动降级到 WebSearch 全网检索 + requests 直连财经站。
12. ✅ 禁止任何"目标价/支撑位/压力位"。
13. ✅ "关注等级"仅代表评分区间，注释中明确非买卖建议。

---

## 🧩 扩展：接入 iFinD / TDX 正式插件

代码已按"数据源可插拔"架构设计：在 `data_sources/` 下新增 `ifind_source.py` /
`tdx_source.py`，继承 `BaseDataSource`，然后在 `manager.py` 的 `_SOURCES` 与
`settings.py` 的 `data_source_priority` 中加上优先级（例如
`["ifind", "tdx", "akshare", "websearch"]`），即可把 Skill / MCP 调用方式接入。
主体六步流程完全无需改动。

---

## 📜 免责声明

本项目仅对公开市场客观数据进行聚合、统计、分类与打分，所有输出均为情报梳理与逻辑解读，
**不构成任何投资建议**。使用者须独立作出投资判断并自担风险。
