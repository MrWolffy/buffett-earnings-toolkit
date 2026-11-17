# 父层四锚政策、复核与发布任务

先读本轮 manifest、plan、frozen/schema.json、policy.json、SUBAGENT_INSTRUCTIONS.md、MIGRATION.md 和框架。只派发 plan.selected。父层负责经济判断、统一五档政策及验收；不能把研究子代理的建议直接当作通过结论。

## 四锚与独立闸门

采用 parent-assumption-tolerance-2026-11-v3（buffett-valuation-6 / buffett-research-v2.5）。核验 V_central ≥ V_conservative ≥ V_adverse ≥ V_floor ≥ 0。四锚来自不同经济情景，同币种、同每经济股普通股权利、同 discount_rate_id；允许相等和空区间，不为排序人为改数。

central 是最可支持的正常经济；conservative 容纳多项普通小误差。adverse 仅为单一核心论点显著失败，或最多两项直接因果相连的假设失败：ONE important investment thesis materially wrong，或 at most TWO directly causally linked thesis assumptions failing together。无直接因果关系的独立驱动维持 conservative 假设；禁止叠加无关诉讼、税务、融资、宏观和资本冲击，除非是该失败的直接传导机制且有证据。父层必须退回/修订多因素灾难包，并核验企业与普通股仍可生存，不靠未证实融资、救助或严重低价增发。

V_floor 的 schema label 仍为 floor，明确指当前可行企业的持续经营经济底部（GOING-CONCERN ECONOMIC FLOOR）：无增长价值、无质量/倍数溢价、无未证实新项目，采用低位但有证据支持的正常化经营状态。核查全部维持投入、SBC、税、营运资本、股份、债务、监管资本、租赁与优先索偿；不得因 floor 标签自动摧毁现有客户、特许经营或资产。仅有结构性衰退/有限寿命证据的企业可用有限期/run-off floor。零 floor 仅限有据低位持续经营普通股经济价值实际≤0，或有限期/run-off 企业在优先索偿后无普通股回收；对可行持续经营企业，仅清算价值为零不足以推出零 floor。

参照 JBSS/EVRG/ED/GMED 式受损但持续经营经济，不照搬数字；拒绝 EXPE/FICO/APP 式多重坍塌叠加仅因 floor 标签充当底部。这是情景方法示例，不是公司当前风险结论。清算/有序退出/回收价值另记 risk_ledger/cross_checks 作为 recovery reference，不是 V_floor，也不自动构成同前提反证；不添加第五条买区边界。禁止用提高折现率、统一比例折扣、插值或旧四利率价格改名代替经济假设。

| 价格区间 | economic_tier |
|---|---|
| P > V_central | watch：仅观望 |
| V_conservative < P ≤ V_central | research：认真研究 / 最多极小仓 |
| V_adverse < P ≤ V_conservative | starter：开始建仓 |
| V_floor < P ≤ V_adverse | add：明显加仓 |
| P ≤ V_floor | fat_pitch：Buffett 式胖球候选 |

用未舍入价格判断，等号归较低价格侧。floor=0 时正价格不可能成为胖球。floor 缺失且价格不高于 adverse 时 economic_tier 不可精确确定，classification_status=floor_unavailable；不得把未知当零，允许最高 add 但仍受事实闸门限制。一般未知应继续合理估计，只有真实无法识别 floor 才接受缺失和具体理由。

独立定义 evidence_cap、survival_cap；通常 actionable_tier 为 economic_tier 与两 cap 按 watch<research<starter<add<fat_pitch 的最低档。证据限制须说明具体缺口、经济后果与解除条件；不因 mixed、无评分或一般保守偏好机械限为 research。actionable_tier 是研究条件允许的最高档，不是订单或仓位授权，actionable 始终 false。

thesis_break 单列 none/research_cap/reject，不是第六价格区间。具体可信且未解决、可能摧毁普通股或推翻 floor 且无法合理界定/吸收的尾部，权属/欺诈、融资不可行、强制重大稀释或控制人转移资产等事实，应限至 research 或 reject；reject 的 actionable_tier=null。保留真正永久损失否决。

fat_pitch 还须逐项判断 floor_independent、no_specific_equity_destroying_tail、financing_viable、no_major_dilution、capital_distributable、cross_check_resolved。每项记录 passed 和可核验证据 reason。不能用一个宽泛风险段落代替；不可独立论证的 floor、未证实近期再融资、不可核验可分配资本或第二方法重大反证均不能放行胖球。经济价格很低也不绕过闸门。

no_specific_equity_destroying_tail 通过，除非存在具体、可信、未解决的法律/监管/经营/权属尾部，可能摧毁普通股或推翻 floor 且无法合理界定或吸收。一般未知、缺少数学最大损失不导致失败；不能把预算冒充法律赔偿上限。普通不确定性、mixed 或无评分本身不得令任一胖球检查失败。

生存上限按确定性分级，不设泛化数值严重性分数：no_specific_equity_destroying_tail、financing_viable、no_major_dilution 任一失败，survival_cap≤research（或 thesis_break=research_cap/reject）；仅 capital_distributable 失败则 survival_cap≤add，可取 add，不能仅据分配限制强制 research。全部生存检查通过允许 survival_cap=fat_pitch，再由证据决定。任一胖球检查失败，至少一个 cap≤add（或逻辑破坏覆盖），不因普通证据缺口一律限 research；无约束 fat_pitch 须六项全通过。

## 决定文件

先复核研究并运行 research 阶段验收，再由父层独立写 parent_entry_decision.json，字段如下。以下是带解释性伪值的模板，不是合法可执行 JSON 决定；所有 <...> 均须依据实际证据替换，不设 research/add 默认上限，也不能复制为通过结论：

```json
{
  "defined_by": "parent",
  "policy_id": "parent-assumption-tolerance-2026-11-v3",
  "research_sha256": "research.json文件字节SHA256",
  "basis": "四情景选择与经济证据",
  "risk_overlap_resolution": "风险在现金、准备及事实条件中的非重复处理",
  "anchor_scenarios": {"V_central":"central", "V_conservative":"conservative", "V_adverse":"adverse", "V_floor":"floor"},
  "anchor_rationales": {"V_central":"中央依据", "V_conservative":"组合误差依据", "V_adverse":"单一失败因果链与生存依据", "V_floor":"持续经营低位经济/有据有限期依据"},
  "tier_conditions": {"watch":["跟踪具体假设"], "research":["核查具体证据"], "starter":["初步投入所需事实"], "add":["追加所需事实"], "fat_pitch":["全部胖球事实检查通过"]},
  "gates": {
    "evidence_cap":"<按证据选择 TIERS 值>", "evidence_reason":"填写实际证据限制及解除条件",
    "survival_cap":"<按生存分级选择 TIERS 值>", "survival_reason":"填写实际资本与生存判断",
    "thesis_break":"none", "thesis_break_reason":"填写核查结论",
    "floor_cross_check_resolution":{"status":"not_needed","reason":"说明独立方法是否存在反证","source_ids":[]},
    "fat_pitch_checks": {
      "floor_independent":{"passed":<证据判断 true/false>,"reason":"填写实际核验"},
      "no_specific_equity_destroying_tail":{"passed":<证据判断 true/false>,"reason":"填写实际核验"},
      "financing_viable":{"passed":<证据判断 true/false>,"reason":"填写实际核验"},
      "no_major_dilution":{"passed":<证据判断 true/false>,"reason":"填写实际核验"},
      "capital_distributable":{"passed":<证据判断 true/false>,"reason":"填写实际核验"},
      "cross_check_resolved":{"passed":<证据判断 true/false>,"reason":"填写实际核验"}
    }
  }
}
```

floor 真不可得时 anchor_scenarios.V_floor=null，anchor_rationales 写明原因并与 research.floor_limitation 对齐。cap 和 passed 均必须依据实际事实逐公司选择，模板没有默认上限或失败结论。

运行 apply-entry 生成 analysis.json/report.md；生成器只按已复核决定计算，不替父层选择假设。若需改研究，由子代理修订或由父层明确记录修订理由，重新绑定 research 哈希并重做全部验收。

## 实质验收

parent_review.json 绑定 analysis.json、metadata.json、report.md、research.json、parent_entry_decision.json 五份字节 SHA256。每项 {passed:true,notes:"真实核查结论"}：sources_cutoff、arithmetic、cash_flow_bridge、risk_overlap、reserve_terminal、cross_check、entry_policy、business_quality_independent、anchor_economics、evidence_survival_gates。另填 veto_reason_reviewed（与分析原文一致）、review_flags_acknowledged（冻结 contract 返回的精确列表）；有评分时填 quality_score_reviewed。

anchor_economics 应逐锚解释可错范围、现金/终值/权益桥和排序原因；必须拒绝 adverse 无关冲击叠加、可行企业 floor 被清算替代或自动摧毁特许经营。evidence_survival_gates 应审查流动性、融资、稀释、资本可分配性、具体普通股毁损尾部、独立 floor 与解除条件，并执行上述生存分级。不能以机器通过代替判断，不能把所有 checks 自动写 true。

validate 后只把真实已验收公司标 completed；其余 pending/failed。按 README 取得真实行情并作 quote_review，调用 publish 原子发布。旧不兼容记录只归档，不能混入新报告或行情集合。状态、哈希绑定或竞争发布失败须先解决，不手工改生产文件。最后核对 status、current.json 和 summary，分别汇报本轮试点/日常范围、待办、证据上限与研究档位。

独立方法差异的父审：gates.floor_cross_check_resolution 必填 {status:not_needed/resolved/unresolved,reason,source_ids}。cross_check_resolved 表示重大差异已解决，不要求所有估值数值接近。低于 floor 超过项目复核阈值的第二方法，父层必须引用已有来源并写实质 reason：证明该方法测量不同经济状态/权利（如清算 vs 可行持续经营），或解决同前提模型反证，才可填 resolved 并通过 cross_check_resolved；同前提较低独立估计未解释必须 unresolved、检查 false、禁止胖球。仅换标签或空泛“前提不同”不能忽略真正生存失败。解决清算/持续经营差异可保持原 V_floor，不机械平均或下调。

floor_independent 另须独立支持持续经营 floor 前提（或行业适配且有据的有限期 floor），可行企业不能只凭清算证据通过。唯一新增可选字段 cross_checks[].premise="same_as_scenario"/"different_state_or_right" 用于机器区分；省略即声明同前提，清算/持续经营差异必须填 different_state_or_right 且 explanation/risk_ledger 写清来源与理由。不同前提参考不计入 floor_independent 支持，仍保留其风险审查；无需新增边界或改变现有 resolution 结构。高于 floor 的数值差异仍须复核但不自动否定 floor；第三方报价不是必需，独立方法可来自可核验的经营/资产资料。
