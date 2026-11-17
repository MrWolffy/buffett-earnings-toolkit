# 独立 Buffett v2 四经济锚 pipeline

当前 schema 为 `buffett-research-v2.5`，政策为 `buffett-valuation-6`，父层 policy_id 为 `parent-assumption-tolerance-2026-11-v3`。四个经济锚表示假设可以错到什么程度，五档来自四锚区间；折现率敏感性不再定义买区。

执行前阅读 [子代理任务](SUBAGENT_INSTRUCTIONS.md)、[父层任务](PARENT_INSTRUCTIONS.md)、[迁移规则](MIGRATION.md)。冻结 schema 决定字段格式，冻结 policy 决定可采用政策。研究采用项目自定义政策，不声称复制巴菲特的完整判断。

## 存储与入口

所有输入、中间材料、行情缓存和新报告写 `outputs/buffett_v2/`，旧 `outputs/buffett/` 只读。`legacy_tickers()` 是唯一旧数据适配器，仅提取 ticker；不导入旧估值、评分、买区、成功标记和事件完成状态。

```text
outputs/buffett_v2/
  storage.json
  inbox/<input-id>/            # 每轮独立输入
  cache/
  runs/<run-id>/
    frozen/                    # 契约、政策、父子任务、迁移规则、框架和代码
    manifest.json / plan.json  # 截止点、触发、范围与哈希
    companies/<ticker>/        # 研究、模型、来源、父层决定与复核
    accepted/
  publications/<generation>/  # 不可变 registry/state/行情/CSV/报告/迁移状态
  current.json                # 原子发布入口
```

只有 current.json 引用的 generation 算发布成功。last_daily_report 与 last_manual_report 分开保留；手动试点不冒充全量日常任务。新正式报告只展示当前契约验收的研究，旧 v2.1/v2.2/v2.3 仅内部归档；历史文件不改写。迁移待办与财报事件待办分别维护，schema 升级不自动研究历史公司。

## 日常财报入口

在项目根使用现有 .venv：

```powershell
.venv\Scripts\python.exe -m src.tools.buffett_v2_pipeline universe
.venv\Scripts\python.exe -m src.tools.buffett_v2_pipeline context
```

CLI universe 默认读取 data/company_pool.csv，并输出公开公司池、旧 ticker、新研究与监测状态的去重并集；prepare 使用相同的 --universe-csv 参数。自定义公司池必须有 ticker 列，其他字段不导入研究；无法通过证券标识契约的行及原因在 inbox/universe.json 和每轮 scope.json 的 company_pool.skipped_rows 披露。CLI 摘要同时返回 skipped_row_count；公司池只定义监测范围，不授权全量研究。Python 函数调用仅在显式传入 universe_csv 时加入公司池，以兼容既有调用。context 返回真实 UTC cutoff、纽约 as_of、initial_scan_from。先固定 cutoff，再核查完整范围截至该时点的发布事实，输入写本轮独立 inbox/<input-id>/，不复用仍在写入的文件。

events.json 为数组，每项包含 ticker、稳定 event_id（发行人报告编号或 SEC accession）、kind=earnings_release、status=released、primary_source_verified=true、带时区 published_at、report_period、source_url（一手 HTTPS）。日历/预告不算已发布。checks.json 为全部 ticker 的字典，值为 {status:"ok"|"incomplete",checked_through:<context cutoff>}；缺源、到期未核实记 incomplete。

```powershell
.venv\Scripts\python.exe -m src.tools.buffett_v2_pipeline prepare --mode daily_earnings --cutoff <UTC-cutoff> --events outputs/buffett_v2/inbox/<input-id>/events.json --checks outputs/buffett_v2/inbox/<input-id>/checks.json --since <首次扫描起点>
```

首次 --since 传 context.initial_scan_from（默认回看7天，只扫描实际发布），后续省略并沿用新版逐公司水位。失败检查不推进水位；已完成事件去重，不因政策升级复活。启动扫描范围与本轮研究范围不同：默认只选择本纽约日新财报、既有 research_authorized=true 的获授权事件或 failed/running 恢复事件。较早积压保留在 plan.deferred_earnings 和 pending 中，不能丢弃或偷偷研究。仅 plan.selected 可派发。价格、旧格式、空评分和配置变化不是触发器。

## 手动显式升级入口

```powershell
.venv\Scripts\python.exe -m src.tools.buffett_v2_pipeline prepare --mode manual --tickers GDEV INTR JBSS RSI --reason "用户授权四经济锚试点"
```

必须有 ticker 与 reason，不从旧 needs_reanalysis 批量选择。手动与日常共用 prepare→原生研究→父层政策→验收→publish；手动不推进财报检查水位、不伪造 earnings_event_id。后续日常只能根据一手来源和 cutoff 判断研究是否已覆盖某次事件，不能仅凭报告日期跳过。

## 四锚与五档

V_central 为最可支持的正常经济，V_conservative 容纳多项普通小误差。V_adverse 仅为单一核心论点显著失败，或最多两项直接因果相连的假设失败；无关独立驱动维持 conservative 假设，禁止叠加无关诉讼/税务/融资/宏观/资本冲击，除非为该失败的直接传导机制。父层退回多因素灾难包，并保留普通股生存要求。

V_floor 保留 floor 标签，指当前可行企业的持续经营经济底部（GOING-CONCERN ECONOMIC FLOOR）：无增长价值、质量/倍数溢价或未证实新项目，完整计入维持、SBC、税、营运资本与所有索偿，采用低位但有证据支持的正常化经营，不自动毁掉现有客户、特许经营或资产。仅有结构性衰退/有限寿命证据才可用有限期/run-off floor。清算/有序退出/回收价值在 risk_ledger/cross_checks 单列风险参考，不是 V_floor，不自动成为同前提矛盾，不添加第五条买区边界。参照 JBSS/EVRG/ED/GMED 的受损但持续经营语义，拒绝 EXPE/FICO/APP 式无关坍塌叠加仅凭 floor 标签充当底部。

四锚相同币种、普通股经济权利与 discount_rate_id；场景差异必须来自经济假设，不能按利率、固定折扣、插值或旧价格改名产生。

| 价格区间 | economic_tier |
|---|---|
| P > V_central | watch：仅观望 |
| V_conservative < P ≤ V_central | research：认真研究 / 最多极小仓 |
| V_adverse < P ≤ V_conservative | starter：开始建仓 |
| V_floor < P ≤ V_adverse | add：明显加仓 |
| P ≤ V_floor | fat_pitch：Buffett 式胖球候选 |

要求 V_central ≥ V_conservative ≥ V_adverse ≥ V_floor ≥ 0，允许等值和空区间。未舍入值分类，等号归较低价格一侧。floor=0 时正价格不属于胖球。零 floor 仅限有据低位持续经营普通股经济价值实际≤0，或有限期/run-off 在优先索偿后无普通股回收；可行企业仅清算为零不足以推出零 floor。floor 是条件经济估计，不保证股价底；确实不可识别时明示 floor_limitation，不伪造数字。此时价格不高于 adverse 的经济档位无法精确判断，classification_status=floor_unavailable；不得给胖球。

父层另定义 evidence_cap、survival_cap 和 thesis_break。actionable_tier 取经济价格档位与事实许可上限的最低档；thesis_break=research_cap 时最高 research，reject 时为空。floor 不可得时最多 add，仍受其他上限约束。胖球须通过 floor_independent、no_specific_equity_destroying_tail、financing_viable、no_major_dilution、capital_distributable、cross_check_resolved 六项检查。具体可信未解决且无法合理界定/吸收、可能摧毁普通股或推翻 floor 的尾部才令尾部项失败，一般未知或无数学最大损失不失败，预算不等于法律上限。尾部/融资/重大稀释失败须 survival_cap≤research（或 thesis_break/reject）；仅资本分配受限须 survival_cap≤add，可取 add，不强制 research。全部生存项通过可许可 fat_pitch，再受证据限制。任一胖球项失败至少一个 cap≤add（或逻辑破坏覆盖），不因普通未知、mixed 或无评分失败或强制 research。保留永久损失否决。actionable_tier 是条件研究档位，布尔 actionable 始终 false，无自动订单或仓位授权。

cross_check_resolved 表示重大差异已有来源和理由解决；清算与可行持续经营状态/权利差异可在 floor_cross_check_resolution 中填 resolved 并保持 V_floor，同前提较低估计未解释则 unresolved、禁止胖球。floor_independent 另须独立支持持续经营前提（或有据有限期 floor），清算证据单独不足。可选 cross_checks[].premise=same_as_scenario/different_state_or_right，省略声明同前提，清算参考必须标不同状态/权利并用 explanation/risk_ledger 解释；机器不把不同前提参考计为 floor 支持。其余 resolution 字段不变。

普通未知应以有依据的估计、范围和失效条件处理，不能因 mixed、无评分或一般谨慎拒绝估计四锚。证据闸门说明具体经济后果与解除条件，不把质量标签机械转成折扣或 cap。

## 两阶段研究和验收

父会话按实际并发槽派出原生 Codex subagent，每家公司一个；Python 不调用研究 API。子代理只写本公司目录，交付 research.json、research_report.md、metadata.json；entry_policy=pending_parent、policy_id=null、boundaries/tiers为空、gates=null。禁止子代理写政策档位和父层决定。

四情景 label 为 central/conservative/adverse/floor，新增 assumption_changes、tolerable_errors、survival_assessment。父层真实复核后写哈希绑定的 parent_entry_decision.json，包含四 anchor_scenarios、anchor_rationales、逐档条件、gates；格式及规则见 PARENT_INSTRUCTIONS.md。

```powershell
.venv\Scripts\python.exe -m src.tools.buffett_v2_pipeline apply-entry --run-id <run-id> --ticker INTR
```

父层核验来源/cutoff、现金桥、逐股算术、终值、准备滚动、风险重复、独立方法、四锚经济语义与证据/生存闸门，再写 parent_review.json。十项审核为 sources_cutoff、arithmetic、cash_flow_bridge、risk_overlap、reserve_terminal、cross_check、entry_policy、business_quality_independent、anchor_economics、evidence_survival_gates，各含 passed:true 及实质 notes。绑定 analysis.json、metadata.json、report.md、research.json、parent_entry_decision.json 的文件字节 SHA256；另填 veto_reason_reviewed、review_flags_acknowledged，有评分时填 quality_score_reviewed。不能自动复制通过。

```powershell
.venv\Scripts\python.exe -m src.tools.buffett_v2_pipeline validate --run-id <run-id> --tickers GDEV INTR JBSS RSI
```

研究阶段用 validate_analysis(research,policy,stage="research")；final 需要父层采用政策并通过闸门契约。验收复算价值、准备恒等式、引用、锚顺序及报告末尾 JSON，重建父层决定比较最终对象，保存 calculated.json；任何修改须重验。机器通过不替代经济判断，不执行子代理提供的任意 Python。

## 行情与原子发布

quotes.json 必须精确覆盖“当前契约已发布公司 + 本轮成功验收公司”，不含旧未升级公司。值为 {price,date,currency,source,observed_at,status}；status=active/missing/inactive，missing/inactive 的 price/date 为 null。来源、币种、真实获取时间必填；日期不得晚于 as_of，最多7个自然日，默认覆盖不足90%拒绝发布。

行情可在 prepare 后读取，但须使用 as_of 当日或以前已完成交易日，不把盘中数当收盘。observed_at 是真实获取时间且不能早于报价日期；休市用真实最近有效交易日。inactive 须有一手交易终止/转换 HTTPS 公告并由父层核验。

outcomes.json 精确覆盖全部 selected，值为 completed/pending/failed；completed 必须先验收。新研究未完成时停用对应旧价格参考。新研究失败仅记录 operations，不回填旧口径。quote_review.json 为 {quotes_sha256,quotes_reviewed:true,notes}，哈希使用 pipeline.value_hash(quotes对象) 规范 JSON，**不是文件字节哈希**。

```powershell
.venv\Scripts\python.exe -m src.tools.buffett_v2_pipeline publish --run-id <run-id> --quotes outputs/buffett_v2/inbox/<input-id>/quotes.json --outcomes outputs/buffett_v2/inbox/<input-id>/outcomes.json --quote-review outputs/buffett_v2/inbox/<input-id>/quote_review.json
.venv\Scripts\python.exe -m src.tools.buffett_v2_pipeline status
```

发布持有系统互斥锁，复验新/缓存产物和哈希，通过 generation CAS 防并发覆盖。先写不可变批次，最后原子替换 current.json，使研究与事件状态同时生效。文件占用或失败保持旧指针；同一已提交轮幂等返回，竞争发布须按最新状态建新轮。只保留现场，不手工改生产 CSV/state。

流程改版后旧轮用 `python outputs/buffett_v2/runs/<run-id>/frozen/buffett_v2_pipeline.py --root <v2绝对根> ...` 恢复，不改快照。最后检查 status、current.json 及报告，分别汇报当前契约覆盖、归档待升级、本轮完成/失败/待办、日历缺口、行情覆盖和两类档位。

跨日补充：上次成功检查水位之后首次核验的新财报，属于正常新增事件，即使纽约日期已变化仍可派发；仅较早已保存的未授权启动积压继续待办，避免漏掉两次定时检查之间的晚间发布。
