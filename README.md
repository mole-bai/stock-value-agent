# 自选股价值投资监控 Agent

这是一个面向个人研究的三股票 Agent：每天获取价格、监控官方公告语义变化、读取经过人工核对的财务事实，运行确定性的价值评分、三情景估值与建议规则，最后生成带来源链接、相对上次变化及通知记录的中文 Markdown 日报。默认自选股为腾讯控股、泡泡玛特和贵州茅台。

当前已具备：日报、公告去噪、待复核队列、point-in-time 历史、建议变化归因、事件日历/ICS、P0–P3 本地提醒及去重、静默时段、情景价格重算、历史和建议解释命令。详细范围见 [`ROADMAP.md`](ROADMAP.md)。

## 使用边界

- `live` 行情优先来自腾讯轻量行情接口，Yahoo 与新浪作为备用，**不是交易所官方或持牌 consolidated feed**。所有价格均按“临时价格”处理，可能延迟、缺失或被供应商修订，不能用于交易执行或对外再分发。
- 财务事实来自公司投资者关系页面、HKEXnews、上交所或巨潮等官方报告，当前以 [`data/fundamentals.json`](data/fundamentals.json) 中的人工核验快照提供。报告保留原始文件链接；新财报发布后仍需人工更新并复核该文件。
- 输出仅是默认 3–5 年期限的**公司级研究观点**，不考虑个人税务、流动性、风险承受力或组合集中度，不提供个性化仓位和交易数量。
- 项目没有券商连接、下单接口或自动交易能力。估值依赖假设，报告不能替代原始披露、持牌投顾或个人独立判断。

## 环境与安装

需要 Python 3.11 或更高版本。项目运行时只使用 Python 标准库。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

安装后可先查看命令帮助：

```bash
stock-agent --help
stock-agent validate --help
stock-agent offline --help
stock-agent live --help
stock-agent calendar --help
stock-agent history --help
stock-agent explain --help
stock-agent reviews --help
stock-agent review --help
stock-agent scenario --help
```

以下示例均从仓库根目录执行。

`--config` 和 `--fundamentals` 的默认值分别是 `config/watchlist.json` 与 `data/fundamentals.json`，因此未修改目录布局时也可直接运行 `stock-agent validate`、`stock-agent offline` 或 `stock-agent live`。示例显式写出路径，便于调度任务审计。

## 三种运行命令

### 1. 校验配置与本地数据

```bash
stock-agent validate \
  --config config/watchlist.json \
  --fundamentals data/fundamentals.json
```

`validate` 只检查配置、自选股、推荐政策和本地财务快照能否安全加载，不访问网络，也不生成投资日报。应在修改 JSON 或添加定时任务前先运行；校验失败时进程返回非零退出码并打印具体字段错误。

### 2. 离线生成可复现日报

```bash
stock-agent offline \
  --config config/watchlist.json \
  --fundamentals data/fundamentals.json
```

`offline` 不访问网络，使用仓库内的确定性价格样本与本地财务快照跑完整链路。它适合首次验收、开发和故障排查；同一输入应得到相同的数字、信号和建议规则结果。报告中的价格会明确标记为离线/临时数据，不能当作最新市价。

离线模式默认也不会监控官方页面变化；它不会以“没有联网结果”推断为“没有新公告”。

### 3. 获取临时行情并生成日报

```bash
stock-agent live \
  --config config/watchlist.json \
  --fundamentals data/fundamentals.json
```

`live` 会访问原型行情源，获取三只股票的最新可用价格，再与本地官方财报快照组合生成日报。某一来源不可用、价格过期或数据缺失时，运行状态会降级并在报告中显示警告；系统不会把旧价格伪装成最新价格。

默认配置将报告写入 `reports/`，状态写入 `var/state.json`。命令输出会显示本次报告的实际路径。文件投递使用同目录临时文件和原子替换，避免留下半份日报。

`live` 和 `offline` 还支持以下运行级覆盖参数：

```bash
stock-agent live \
  --output-dir reports \
  --state var/state.json \
  --no-page-watch
```

- `--output-dir DIR`：覆盖配置中的报告目录；
- `--state PATH`：覆盖配置中的状态文件；
- `--no-page-watch`：跳过官方 IR/交易所公告的语义变化检查。行情和本地财务分析仍会运行，但日报不会包含本轮公告扫描结果。

公告监控比较规范化后的标题、日期、稳定文档 URL 和文档 ID；随机 token、时间戳、UTM 参数、空白和网页导航变化不会触发公告告警。第一次成功扫描只建立基线，不把页面已有文件全部误报为新增。动态页面无法解析或请求失败时会明确降级，绝不把空结果表述为“没有公告”。新文件会进入人工复核队列，并在完成语义复核和必要的重估前冻结正面观点。

## 日历、历史、复核与情景命令

导出未来 30 天投资者事件；官方确认日期与推测日期会明确分开：

```bash
stock-agent calendar --days 30 --ics reports/investor-events.ics
```

查看不可变运行快照，并解释腾讯最新建议相对上次为什么变化：

```bash
stock-agent history --limit 10
stock-agent explain 0700.HK
```

查看并完成新官方文件复核：

```bash
stock-agent reviews
stock-agent review '0700.HK:DOCUMENT_ID' \
  --decision non_material \
  --note '不改变财务事实或估值假设'
```

可用的决定为 `material`、`non_material`、`data_update_required`、`duplicate` 和 `updated_and_revalued`。`material` 或 `data_update_required` 会继续冻结正面观点；它们不会自动捏造或覆盖财务数字。根据原文更新并校验 `data/fundamentals.json`、完成重估后，再用 `updated_and_revalued` 关闭该项。

按假设价格运行与正式日报相同的估值和建议闸门，不写状态、不交易：

```bash
stock-agent scenario 0700.HK --price 400
```

运行历史保存在 `var/snapshots/history/`，最新快照是 `var/snapshots/latest.json`。本地通知写入 `reports/notifications/`；相同通知会去重，严重度升级可重发，默认 `22:00–08:00` 静默时段仅允许 P0 硬风险立即通过。

## 配置说明

主配置是 [`config/watchlist.json`](config/watchlist.json)：

- `timezone`：报告时区，默认 `Asia/Shanghai`；
- `report_time`：期望的每日运行时间，默认 `18:30`；CLI 本身是单次运行，真正调度由外部 cron/systemd 完成；
- `output_dir`、`state_file`：报告目录与本地状态文件；
- `recommendation_policy`：研究期限、最低回报和安全边际；MVP 固定为 `company_research`，`personalized` 必须为 `false`；
- `notifications`：是否启用本地通知、静默开始/结束、可绕过静默的最高优先级以及是否同时写 Markdown；
- `watchlist`：证券代码、市场、币种、长期逻辑、风险、失效条件和官方来源。

财务快照是 [`data/fundamentals.json`](data/fundamentals.json)。金额以字符串保存以避免二进制浮点误差；每只股票的 `latest_period`、`observed_at`、`confidence` 和 `sources` 必须随更新一起复核。不要用新闻摘要或原型行情接口覆盖官方财报数字。

### 价值评估算法 v2

`value_scorecard.v2` 将原来固定为通过的“投资案例质量”改成可审计评分。框架参考质量研究中常见的盈利、增长、安全性和资本回报维度，并保留价值因子；研究背景可见 [Quality Minus Junk](https://www.aqr.com/-/media/AQR/Documents/Insights/Working-Papers/Quality-Minus-Junk.pdf) 与 [Fama–French 五因子原始论文](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2287202)。这里的阈值是个人研究规则，不是论文收益的复现或保证。

- 固定维度权重：盈利能力 20%、增长质量 15%、现金流 15%、资产负债表 15%、资本配置 10%、估值吸引力 25%；
- 每个因子都记录事实字段、方向、0 分/100 分阈值和实际得分；公司差异化阈值位于 `fundamentals.json` 的 `assessment`；
- 缺失维度按中性 50 分进入总分，但覆盖率同步下降；覆盖率不足会阻断正面质量结论，避免“缺数据反而高分”；
- 黄色、橙色和红色财务信号产生显式风险扣分，红色硬风险仍优先覆盖任何估值；
- 中低置信度和悲观/乐观情景离散度会提高实际要求的安全边际与目标年化回报；
- “买入候选”必须同时通过数据质量、公告复核、质量分、综合分、价格安全边际和目标回报。综合分只能增加约束，不能绕过原有安全门。

## 日报内容

日报包含：

- 数据截至时间、完整/降级运行状态和运行警告；
- 三只股票的价格变化、关键财务指标、事件及财务信号；
- 公司级研究观点、置信度和规则依据；
- 价值综合分、质量分、六维分项、数据覆盖率及风险扣分；
- 悲观、基准、乐观三情景内在价值、安全边际与预期回报；
- 主要风险、投资逻辑失效条件、数据缺口和来源链接；
- 未来 30 天财报/业绩日历，并区分官方确认与推测日期；
- 相对上次的价格、指标、事件、信号、估值与建议差异，以及变化归因；
- 财务和行情时效、公告入口提取状态、待复核文件和通知投递结果；
- 研究用途免责声明。

任何“买入候选、等待、持有观察、减持候选、风险回避或无建议”都只是有条件的公司研究结论，不会触发交易。

## 每日调度

仓库在 [`ops/`](ops/) 提供 cron 和 systemd 示例，默认按 `Asia/Shanghai` 每天 `18:30` 运行 `live`。这些文件只是模板，本项目不会读取、复制或安装系统任务。使用前必须替换绝对路径、确认时区并先执行 `validate`。

仓库也提供 [`.github/workflows/daily-stock-monitor.yml`](.github/workflows/daily-stock-monitor.yml)：推送到 GitHub 后，Actions 会在每天北京时间 `18:30` 自动校验配置、运行测试并执行 `live`，也支持在 Actions 页面手工触发。日报会显示在单次任务的 Summary，并作为私有 Actions artifact 保存 90 天；`var/` 通过 Actions cache 延续公告基线、历史比较和通知去重状态。工作流使用公开原型数据源，不需要仓库 Secret；若未来接入付费行情、邮件或消息服务，应仅通过 GitHub Actions Secrets 注入凭据，不能提交到仓库。

同一工作流还会把最新 JSON 日报裁剪为不含本地路径、通知记录和完整审计字段的公开数据，注入 [`site/`](site/) 的响应式仪表盘，再通过仓库专用部署密钥发布到独立公共站点仓库 `mole-bai/stock-value-dashboard`。页面提供三只股票的价格、建议、三情景估值、安全边际、预期回报、关键财务指标、风险信号与未来事件；只在 `main` 分支运行成功后更新线上版本，失败或降级信息会原样显示，不会沿用一份看似正常的新页面掩盖数据问题。Agent 源码、运行状态、通知记录和完整审计数据仍保留在私有仓库。

当前这台 Mac 已额外安装并实测 `com.stockagent.daily` LaunchAgent：每天本地时间 18:30 运行，运行副本及状态位于 `~/Library/Application Support/StockAgent/`。之所以不直接从本项目的 `Documents` 路径运行，是为了遵守 macOS 对无界面后台任务的文件隐私限制。定时日报写入 `~/Library/Application Support/StockAgent/reports/`，日志写入同目录下的 `var/`；手工运行仍默认写入本项目的 `reports/`。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

网络连接器测试使用固定响应或注入传输，不应依赖真实行情服务。手工验收 `live` 时，还应将报告价格、报告期及关键数字逐项与对应来源核对。
