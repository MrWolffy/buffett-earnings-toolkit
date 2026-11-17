# Buffett Earnings Toolkit

[English](README.md) | [简体中文](README.zh-CN.md)

0.2.0 版将原生 Codex subagent 设为主要研究流程。Python 负责冻结输入、验证研究和原子发布；父会话为每家选中公司派发独立子代理，并承担实质复核。

当前研究契约为 **`buffett-research-v2.5`**，估值政策为 **`buffett-valuation-6`**，父层政策为 **`parent-assumption-tolerance-2026-11-v3`**。底层基于证据的 Buffett 思想框架仍保留自身的 2.0 版本号，两者分别管理。本项目不声称复制巴菲特的完整判断过程。

## 安装

需要 Python 3.11 或更高版本。请保留完整仓库、使用 editable 安装，并在仓库根目录运行：运行器需要冻结仓库内的说明和 skill 参考文件。不支持脱离源码布局单独安装 wheel 后运行研究。

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

v2 研究使用当前 Codex 会话及可用浏览、数据工具，无需 `OPENAI_API_KEY` 或 Claude Agent SDK。`tzdata` 为 Windows 提供 IANA 时区数据；`pandas`、`yfinance` 用于独立的财报日历工具。

## 启动研究

先读 [AGENTS.md](AGENTS.md) 和 [v2 完整流程](docs/buffett_v2/README.md)。

```bash
python -m src.tools.buffett_v2_pipeline universe
python -m src.tools.buffett_v2_pipeline context
```

`universe` 默认读取 `data/company_pool.csv`，再合并现有研究、监测状态和可选旧输出中的 ticker。`universe` 与 `prepare` 均支持 `--universe-csv PATH`；发现范围和建轮时应使用同一输入。公司池只提供标识，不代表已获全量研究授权，也不导入旧估值。

不支持或不安全的 ticker 行会跳过，并在 `skipped_row_count` 汇总；接受监测覆盖前应查看 `inbox/universe.json` 或该轮 `scope.json` 的 `company_pool.skipped_rows` 明细。源 CSV 保持不变。

用户明确指定公司后，使用 ticker 和原因建立手动研究轮。例如用户已要求研究微软时：

```bash
python -m src.tools.buffett_v2_pipeline prepare --mode manual --tickers MSFT --reason "用户明确要求研究微软"
```

命令返回 run ID，并创建 `outputs/buffett_v2/runs/<run-id>/`，冻结契约、政策、父子代理任务、框架和运行代码。`prepare` 本身不派发研究。

随后在本仓库要求 Codex 使用原生 subagent 继续该轮。父层仅派发 `plan.selected`，每家公司一个子代理，按实际槽位分波。子代理读取冻结说明，只写本公司目录，交付 `research.json`、`research_report.md`、`metadata.json`；入场政策保持 `pending_parent`，不得分配档位或填写父层决定与审核。若原生子代理不可用，必须明确报告限制，不暗中换用模型 API 或声称已完成派发。

父层独立核查来源、算术、经济假设和风险闸门，写入哈希绑定的 `parent_entry_decision.json` 后运行：

```bash
python -m src.tools.buffett_v2_pipeline apply-entry --run-id <run-id> --ticker MSFT
```

实质复核后，父层填写哈希绑定的 `parent_review.json`，再验收：

```bash
python -m src.tools.buffett_v2_pipeline validate --run-id <run-id> --tickers MSFT
```

具体字段和十项审核见 [父层说明](docs/buffett_v2/PARENT_INSTRUCTIONS.md)。机器通过不能替代经济判断，任何产物修改后都须重验。

## 日常财报和发布

先用 `context` 固定真实 UTC cutoff 和纽约日期，在完整监测范围核实一手来源的实际发布。日历或预告只能提供线索。为本轮建立独立的 `inbox/<input-id>/events.json` 和覆盖全部 ticker 的 `checks.json`，再建轮：

```bash
python -m src.tools.buffett_v2_pipeline prepare --mode daily_earnings --cutoff <UTC-cutoff> --events outputs/buffett_v2/inbox/<input-id>/events.json --checks outputs/buffett_v2/inbox/<input-id>/checks.json --since <context.initial_scan_from>
```

首次日常运行传 `--since`，后续省略，保留逐公司检查水位和已完成事件去重。较早启动积压未经授权继续待办；上次成功检查后首次核实的新财报，即使纽约日期变化仍可派发。政策升级、无评分或价格变化不授权历史研究。完整输入和恢复规则见 [流程](docs/buffett_v2/README.md)、[迁移规则](docs/buffett_v2/MIGRATION.md)、[日常任务提示词](docs/buffett_v2/automation_prompt.txt)。提示词供任务复用，不会创建定时任务。

研究完成复核与验收后，取得真实已完成交易日行情，按文档准备精确覆盖范围的 `quotes.json`、`outcomes.json`、`quote_review.json`。研究审核使用文件字节 SHA256；行情审核使用 `pipeline.value_hash(quotes对象)`。

```bash
python -m src.tools.buffett_v2_pipeline publish --run-id <run-id> --quotes outputs/buffett_v2/inbox/<input-id>/quotes.json --outcomes outputs/buffett_v2/inbox/<input-id>/outcomes.json --quote-review outputs/buffett_v2/inbox/<input-id>/quote_review.json
python -m src.tools.buffett_v2_pipeline status
```

发布先校验产物，再原子切换 `current.json` 至不可变 generation。新财报研究未完成时，停用该公司原价格参考。生成研究、报告和缓存保存在忽略的 `outputs/` 或 `.cache/`，不手改生产 CSV/state；恢复旧轮使用该轮冻结代码，详见完整流程。

## 四锚经济模型

以同币种、同经济股权和同折现率的独立经济路径形成 `V_central`、`V_conservative`、`V_adverse`、`V_floor`。adverse 只容纳单一核心论点失败，或最多两项直接因果相连的失败；floor 是低位持续经营经济底部，清算另作回收参考。固定折扣或换折现率不能生成四锚。

五档依次为 `watch`、`research`、`starter`、`add`、`fat_pitch`，再由证据、生存和论点破坏闸门独立限制。可靠 floor 缺失时不得判胖球，六项胖球检查均须证据。研究输出中的布尔 `actionable` 始终为 `false`，不授权交易或仓位配置。

## 财报日历与旧版兼容

独立财报日历仍可运行：

```bash
python -m src.tools.earnings_calendar refresh --limit 5
```

保留 `src/tools/buffett_pipeline.py` 供旧版用户及日历辅助函数使用。其 local/GPT/skill/Batch 分析和旧买区输出属于旧流程。只有使用旧模式才安装 `python -m pip install -e ".[legacy]"`，可选 API 配置见 `.env.example`；旧 API 模式可能单独计费。v2 不调用 Files、Batch、Responses 或 Agents 研究 API，不导入旧估值、评分、买区或事件完成状态，旧适配器仅读取 ticker。

## 测试与内置数据

```bash
python -m pytest
```

测试以离线合成数据验证契约、闸门、调度、验收和发布。`data/company_pool.csv` 仅包含公开公司标识与分类，不包含用户账户或研究结果。当前事实仍须重新核实，Yahoo Finance 财报日历数据可能不完整。

## 许可证

本项目尚未选择开源许可证。如希望明确授予使用、修改和再分发权利，请在发布前添加 `LICENSE`。
