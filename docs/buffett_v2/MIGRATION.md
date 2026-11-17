# 四经济锚迁移规则

当前契约为 buffett-research-v2.5 / buffett-valuation-6。四个 V 锚代表有经济依据的容错情景，不是旧利率边界的新名字。

1. v1 目录 outputs/buffett 保持只读。唯一旧数据入口 legacy_tickers 只提取 ticker，财报触发须重新核验一手发布；不导入旧估值、评分、买区、成功标志和事件完成状态。
2. 旧 v2.1/v2.2/v2.3/v2.4 运行和 publication 保持不可变。用各轮冻结代码核验完整性，旧契约只进入内部 archived_records，不进入新 signals、行情集合或正式报告。不把旧 P 边界重命名为 V 锚，不给旧 base/downside 直接贴新标签。
3. 保留既有 v2 universe、initial_scan_from、逐公司检查水位、已完成事件去重和历史待办。更换 schema/policy 不重置事件状态，不把已完成事件重新排队。migration_status.json 单独记录待升级，不能据此自动派发研究。
4. 日常入口仍为核验后确已发布且未被有效研究覆盖的新财报。默认选本轮纽约日新财报，或既有 research_authorized=true 的获授权事件及 failed/running 失败恢复。较早启动积压保存在 plan.deferred_earnings 和 pending 状态，research_authorized=false；不通过 manual 或改时间绕过范围限制。配置变化、旧格式、空评分、价格提醒都不是研究触发器。
5. 用户显式给出公司和理由时可用 prepare --mode manual 建新轮。四公司试点属于显式迁移，不扩大为历史全量重跑。
6. 可复用重新核验且截止日合格的一手事实、来源摘录和账目桥接；必须重新建立四经济情景、独立验证 floor、由父层审查生存/证据条件。新研究、决定、复核和报告写新 run，完整绑定哈希。
7. 新批次只展示当前契约验收结果，旧报告仍可通过历史 generation 查阅。current.json 原子指向新 generation；last_daily_report 与 last_manual_report 分开保留。手动迁移不推进财报水位、不伪造事件完成。
8. 冻结运行不得原地改流程文件。恢复旧运行用该轮 frozen/buffett_v2_pipeline.py；改变经济判断、schema 或政策须建新轮验收。发布失败保持现场和旧指针，不手修生产 CSV/state。

迁移不保证价格一定降低：改变的是价格形成方法和证据责任。合理中央估计不能被一般未知清空；floor 确实无法可靠识别时明确说明并禁止胖球，不能补造一个数。

v2.5 使用 parent-assumption-tolerance-2026-11-v3：adverse 仅单一论点失败或最多两项直接因果相连假设失败，独立驱动维持保守值；floor 是可行企业的持续经营经济底部，有限期只限有据 run-off，不自动用清算代替或据清算零值置零。清算/退出/回收单列风险参考。cross_check_resolved 替代原一致性检查，允许来源与理由解决不同状态/权利差异，同前提较低估计未解释仍阻止胖球；no_specific_equity_destroying_tail 仅针对具体可信且无法合理界定/吸收的未解决普通股毁损尾部。严重生存失败最高 research（或逻辑破坏限制），仅 capital_distributable 失败最高 add，不设数值严重性分数。floor_independent 另须支持 floor 前提；唯一新增可选 cross_checks[].premise 区分 same_as_scenario 与 different_state_or_right，省略声明同前提，清算参考须标不同前提。其余结构、四边界分类、缺失 floor 行为和真正永久损失否决保持。

跨日补充：上次成功检查水位之后首次核验的新财报，属于正常新增事件，即使纽约日期已变化仍可派发；仅较早已保存的未授权启动积压继续待办，避免漏掉两次定时检查之间的晚间发布。
