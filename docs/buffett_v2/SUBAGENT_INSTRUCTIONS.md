# 单公司四经济锚研究任务

读取父任务指定的 RUN_DIR/manifest.json、plan.json、frozen/schema.json、frozen/policy.json、frozen/framework/SKILL.md 及相关参考章节。只研究 plan.selected 中指定的 TICKER。所有来源摘录、中间材料、模型和交付均写在 RUN_DIR/companies/TICKER/。本轮契约为 buffett-research-v2.5，政策为 buffett-valuation-6。冻结 schema 是字段格式的唯一依据。

使用原生 Codex subagent 的浏览和数据连接器。不得调用 OpenAI Files/Batch/Responses/Agents API；Python 只做确定性计算、数据读取和文件操作。本任务 standalone_screen，不访问账户、不分配仓位、不交易。先研究再结构化，不读取旧估值作为价格锚。可复用经重新核验的一手事实，但不得把旧 base/downside 或 P8/P10/P12/P15 改名充当四锚。

交付 research.json（严格 schema，无 wrapper）、research_report.md（中文研究及末尾与 research.json 完全相同的 fenced JSON）、metadata.json。metadata 为 {ticker, route:"codex_subagent", task_scope:"standalone_screen", cutoff, earnings_event_id, framework_files_read:["SKILL.md", "references/06-valuation-capital.md", ...]}。不虚构模型 ID。reason=new_earnings 时必须引用触发财报一手 URL、使用真实 event_id；手动显式研究的 earnings_event_id=null。事实必须在 cutoff 前公开；无法确定公开时间则 published_at=null 并披露限制，不能编造午夜时间。

## 四锚的经济含义

以同一币种、每经济股普通股权益口径和同一 discount_rate_id 估计四条独立经济现金路径，情景 label 为 central、conservative、adverse、floor。差异来自可解释的经营、投资、资本和兑现假设，不来自机械改变折现率、统一百分比折扣、插值或乐观悲观平均。

| 锚 | 必须回答的问题 |
|---|---|
| V_central | 当前证据最支持的正常经济结果是什么？完整计入维持与增长投入、SBC、税、融资、租赁和其他权利，不虚构安全折扣，也不采用乐观愿景。 |
| V_conservative | 多项普通小误差同时发生时仍能支持多少价值？参数须在历史、当前和行业合理范围内，说明组合误差，不仅换成 bear case 标签。 |
| V_adverse | 单一核心论点显著失败，或最多两项直接因果相连的假设失败时，企业和普通股仍可生存的价值是什么？独立驱动维持保守假设，不得依赖未证实再融资、严重低价增发或救助。 |
| V_floor | 当前可行企业的持续经营经济底部（GOING-CONCERN ECONOMIC FLOOR）是什么？无增长价值、无质量/倍数溢价、无未证实新项目，采用低位但有证据支持的正常化经营状态，保留现有客户、特许经营和资产的有据经济价值。 |

adverse 必须说明 ONE important investment thesis materially wrong，或 at most TWO directly causally linked thesis assumptions failing together；变化项可以是同一失败的多个传导结果，不能靠计数变量冒充因果链。与失败无直接因果关系的独立驱动停留在 conservative 假设；禁止把无关诉讼、税务、融资、宏观和资本冲击叠加，仅直接传导机制可纳入且须证据说明。父层必须退回/修订多因素灾难包，不能因 adverse 标签再把一切调低。

每情景填 assumption_changes:[{variable,from_central,scenario_value,basis,source_ids}]（central 可空，其余非空）、tolerable_errors:[文本]、survival_assessment:文本，说明关键变量变化、证据或估计依据、允许错到什么程度、现金/资本影响和失效条件。必要注资用负现金流体现；从 FCFF 起算必须桥接到普通股权益，不能填企业价值。

floor 是有条件的持续经营经济底部，不是破产最坏情景或保证股价底。完整计入维持投入、经济 SBC、税、营运资本、融资、租赁和优先索偿，不能借去增长忽略维持现有业务所需投入。行业适配：稳定企业用低/零增长全成本现金；周期制造业用有据周期低位；金融用信用成本、监管资本、低位可持续 ROE 和可分配收益，不直接拿账面价值。不得因 floor 标签自动摧毁现有客户、特许经营或资产。仅有结构性衰退/有限寿命证据的企业可用有限期/run-off floor，并计足退出成本、优先索偿，不能硬塞永续终值。

参照 JBSS/EVRG/ED/GMED 式“受损但仍持续经营”的经济语义，不能照搬其数字。明确拒绝把 EXPE/FICO/APP 式多重坍塌叠加仅因标签为 floor 就当作底部；这是情景方法反例，不是对这些公司当前风险的判断。清算、 orderly exit 或 recovery value 另放 risk_ledger/cross_checks 作回收参考/风险复核，不是 V_floor，也不自动构成同前提反证，不添加第五条买区边界。

一般性未知、mixed、无评分或谨慎不能成为拒绝估计的理由。子代理与父层应补证并完成有依据的四锚。真正无法识别可靠 floor 时省略 floor 情景并在顶层 floor_limitation 写具体理由；存在 floor 时该字段为空字符串。不得用缺失代替常规估计，不伪造零、不插值；floor 缺失不得成为胖球。零 floor 仅限有据低位持续经营普通股经济价值实际≤0，或有限期/run-off 企业在优先索偿后无普通股回收。对可行持续经营企业，仅清算价值为零不足以推出零 floor，须交付真实现金和索偿模型。

## 证据与风险交接

业务质量、证据充分度、经济价格档位和生存条件分开判断。quality_score=null、quality_assessment=null；只有冻结政策声明了真实 rubric 才评分。评分不得决定现金流、折现率或买区。无实质否决时 veto_reason=""。

明确识别具体、可信、未解决且可能摧毁普通股或推翻 floor、无法合理界定或吸收的法律/监管/经营/权属尾部，以及融资不可行、强制重大稀释、欺诈或控制人转移资产等事实；保留真正永久损失否决，不能以低价掩盖。no_specific_equity_destroying_tail 对一般未知或不存在数学最大损失仍应通过；预算不是法律赔偿上限。向父层说明具体经济后果和可验证解除条件，不添加第六价格档。仅 capital_distributable 失败应将 survival_cap 限至 add；严重尾部、financing_viable 或 no_major_dilution 失败须限至 research（或 thesis_break/reject）。

reserves 金额为估值币种整单位，不是未标单位的“百万”或每股数。满足 amount=consumed+released+terminal_balance，列释放条件和终值归属。经营现金/监管资本已在经营价值体现时不可重复加回；临时准备未耗用时不能无解释消失。added_separately 说明进入现金或终值的位置及每股桥接；excluded_with_reason 给出经济理由。

risk_ledger 解释每项风险在现金、利率、入场事实条件和准备中的处理；跨多处出现须解释非重复计价。安全边际由可承受的经济错误表达，不追加统一折扣。

central 和 floor 均提供独立方法 cross_checks，或明确不可得原因；检验经济状态、权利和资本口径，不能只是换折现率或换标题。金融用可分配收益与剩余收益/ROE-PB；资产型持续经营业务用支持同前提的经营/资产证据，有限寿命资产可用有限期净回收；扩产企业桥接投资期现金与正常化收益。floor_independent 必须独立支持持续经营 floor 前提（或有据有限期 floor）；清算证据单独不能满足可行企业的 floor_independent。

cross_checks 可选 premise="same_as_scenario"/"different_state_or_right" 是唯一新增字段，用于机器区分可支持 floor 的方法与不同状态/权利参考；省略等于声明 same_as_scenario，既有字段保持兼容。清算对持续经营 floor 必须标 different_state_or_right，并在 explanation/risk_ledger 说明经济差异、来源及生存风险，不能省略以冒充同前提支持。父层在既有 floor_cross_check_resolution 中写有来源的解决理由；cross_check_resolved 允许解决后的不同状态差异，同前提较低估计未解释则 unresolved、禁止胖球。差异不机械平均，清算参考不能代替另一个支持持续经营前提的独立方法。

## 禁止代替父层决定

entry_policy 必须为 status=pending_parent、policy_id=null、defined_by=unassigned、boundaries=[]、tiers=[]、gates=null，并填写 basis 交接说明。子代理只提交经济情景、容错证据和风险事实，禁止自行给 watch/research/starter/add/fat_pitch 映射，包括 proposed 档位；不得写 parent_entry_decision.json、parent_review.json 或替父层填写通过结论。

分析者估计的折现率标 analyst_assumption 并解释依据；不得编造用户回报偏好。可保留 return_sensitivity 做稳健性检验，不能映射成买区。禁止使用旧 parent_boundary_* 利率生成新锚。

完成前用 RUN_DIR/frozen/buffett_v2_contract.py 的 validate_analysis(research, policy, stage="research") 复算。报告披露来源、未知、四锚假设、最强反方及失效条件；不展示旧买区或旧评分对照栏。父层独立选择政策、审查四锚及闸门，再生成和验收正式报告。
