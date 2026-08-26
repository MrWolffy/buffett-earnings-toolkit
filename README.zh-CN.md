# Buffett Earnings Toolkit

[English](README.md) | [简体中文](README.zh-CN.md)

这是一个 Python 工具包，用于筛选公开公司池、生成巴菲特风格的公司分析，以及通过 `yfinance` 维护财报日历。

本仓库只包含可复用源码、测试、巴菲特分析 skill 和公开公司池。分析结果、财报日历结果、报告、日志、断点文件、缓存及凭据均有意排除在版本控制之外。

## 包含内容

- `.agents/skills/buffett/`：巴菲特投资分析说明及参考资料。
- `src/tools/buffett_pipeline.py`：观察名单构建、公司分析、买入区间监控及 OpenAI 批处理流程。
- `src/tools/earnings_calendar.py`：财报日期更新和每日报告。
- `data/company_pool.csv`：两个工具共用的公开公司池，仅包含 ticker、公司名称、板块和行业，不包含筛选或估值结果。
- `tests/`：pipeline 和财报日历逻辑的离线单元测试。

## 安装

需要 Python 3.11 或更高版本。

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

使用 GPT 或 skill 分析模式时，请将 `.env.example` 复制为 `.env`，然后配置 `OPENAI_API_KEY`。请勿提交 `.env`。Skill 模式还会通过 Claude Agent SDK 使用仓库内置的 `.agents/skills/buffett/` 分析说明。

## 使用方法

运行离线依赖检查：

```bash
python -m src.tools.buffett_pipeline preflight --analysis-mode local
```

根据内置公司池构建观察名单：

```bash
python -m src.tools.buffett_pipeline build-watchlist
```

在不调用 OpenAI 的情况下运行少量本地分析：

```bash
python -m src.tools.buffett_pipeline analyze-watchlist --analysis-mode local --limit 5
```

更新少量公司的财报日历：

```bash
python -m src.tools.earnings_calendar refresh --limit 5
```

默认情况下，生成文件写入 `outputs/`，缓存写入 `.cache/`。这两个目录都已被 Git 忽略。

## 测试

```bash
python -m pytest
```

## 数据与 API 说明

市场与财报数据通过 `yfinance` 从 Yahoo Finance 获取。使用 OpenAI 的分析模式可能产生 API 费用，模型输出也可能发生变化。在重新分发数据或用于生产环境之前，请检查相关数据提供商和 API 的使用条款。

## 许可证

本项目尚未选择开源许可证。如果希望他人获得明确的使用、修改和再分发权利，请在发布前添加 `LICENSE` 文件。
