# GPT-6 Astra high：真实数据详细规则提示 rollout（2026-09-29）

> 2026-10-06 归档：以下保留当时的实验条件、结果和判断，不是当前执行计划。旧文中的“下一步”“必须先 SFT”“未使用 final test”等不代表现状；四主通道已反复分析，fixed-state accuracy 不等于 rollout 终态质量。当前主线见 [方法与实验状态](../../项目方法与实验状态.md)。代码和 `output/` 路径均相对于仓库根目录。

## 目标与授权

用户授权 GPT-6 Astra、reasoning high，CH30/31 保留原执行规则，CH3/20
加入删除保护；四通道总预算 $30。这是完整自主 curation，不是固定人工状态动作测试。
未进行 SFT、RL、RAG，也不使用专家标签指导模型选择。

## 冻结条件

| 项目 | 条件 |
|---|---|
| 上游来源 | JianZhi 仓库 commit `bfcca625e4c637f27f869c07196e5a4d30270e08` |
| 数据 | 与 9/28 相同四个 MAT，从 `hierarchy.assigns` 开始；记录 SHA256 |
| Prompt | 固定上游谨慎阶段模板；不重写、不简化 |
| 图像 | Phase 1：waveform/ISI/tree；Phase 2：small waveform/large waveform/merged ISI |
| 抽样 | 每通道 NumPy seed=0，最多显示 5,000 波形；相同 seed 不保证轨迹分叉后的图片仍相同 |
| API | Responses，`gpt-6-astra`，`reasoning.effort=high`，standard/default service tier |
| 输出 | 上游 strict action+rationale JSON；单次 4,000 output tokens（含 reasoning）；不发送 temperature |
| CH30/31 | 上游原 controller；自动过滤阈值 500 / 合并大小边界 4,000 / 最终过滤阈值 5,000 |
| CH3/20 | 冻结本地保护 controller；关闭前后数量过滤；保留 4,000 合并边界 |
| 保护动作 | Phase 1 的 DISCARD 且 n≥4,000：多个 overclusters 则 SPLIT，否则 ABSTAIN；Phase 2 DISCARD→ABSTAIN |
| 保护补充 | 无合并目标、全部 NOT_MERGE 均保留；Phase 2 只有 KEEP 的大 cluster 可作为合并目标 |
| 失败处理 | SDK 不自动重试；首个接口错误、截断、格式错误即停；不回退 mock，不代填 DISCARD |
| 运行顺序 | CH30 → CH31 → CH3 → CH20；串行运行，避免本地内存压力 |
| 评测 | 终态对 MAT `curation.assigns`，使用固定上游评测器；失败/预算停止不产生终态分数 |

兼容层在最后发送请求时把固定上游构建出的模型/effort 替换为 Astra/high，
避免上游仅识别 `gpt-5` 推理模型而走错接口；真实请求和响应分别记录。
CH3/20 的 controller 使用冻结副本；不是改写所有实验的默认 controller。

## 预算与停止

每次推理前用官方 token-counting 接口计算包含图片和 schema 的输入量，
预留输入余量及全部 4,000 输出 tokens 的费用。输入预算按较高 cache-write
价格保守计算，完成后按实际 usage 结算。剩余预算不足下一次预留额度就停止。
接口失联时保留该次预留、不自动重发。预算记录为本地估算，最终以平台账单为准。
不设置另一个总调用次数限制，但 $30 费用限制优先；中途停止不冒称完整 rollout。

官方依据：[模型与价格](https://developers.openai.com/api/docs/models/gpt-6-astra)、
[token counting](https://developers.openai.com/api/docs/guides/token-counting)。

## 解释边界

- 四通道含两种 controller 协议，不合并为“纯模型对照”的宏平均。
- CH30/31 与 5.1 medium 的差异包含模型和 reasoning 设置，不能单独归因模型。
- CH3/20 是删除保护救援实验；保留数据不等于正确分群，需同时报告 Precision/Recall/F1。
- 上游评测允许多个预测 cluster 匹配同一专家 cluster，不能仅靠 Recall 宣称神经元恢复完美。
- raw 数据、图像、API 响应和结果只存本地忽略目录，不提交 Git，不上传新仓库。

## 入口与结果

入口：`scripts/run/run_astra_guarded_baseline.py`。
离线测试：`tests/test_astra_guarded_baseline.py`。
结果根目录：`output/astra_high_channel_guards_budget30_20260929/`。
其中 `REPORT.md` / `collected_metrics.json` / CSV 自动汇总；`budget.json` 记录用量，
`batch_status.json` 记录执行状态；每通道保留原始和保护后的动作、图像哈希及状态快照。

本文件仅记录预先确定的协议；完成状态和数值以结果目录为准。

## 预算中断与续跑授权（2026-09-29，美东；UTC 09-30）

- 原批次在 CH30 的第 589 次推理发送前被预算预留检查停止。已完成 588 次请求，
  用时约 2h56m，标准价格用量估算 $26.61093，保守预算记账 $29.77540。
  第 589 条 requests 记录是待发送请求，不是一次成功推理；原汇总的 calls=589 包含它。
- CH30 Phase 1 输出 54 个 cluster，Phase 2 有 39 个小 cluster、14 个有效大目标。
  停在第 38 个小 cluster 280：剩余目标 102/28/31；最后还有 cluster 411，故最多剩 17 次比较。
  此时无终态，不能报告完整 rollout 指标；其它三个通道尚未开始。
- 用户将**整个批次总预算提高到 $70，包含此前费用**。模型、high、prompt、三图、
  4,000 输出额度及各通道 controller 条件均不变。先完成 CH30，再依原顺序运行 CH31/3/20。
- 新入口 `scripts/run/resume_astra_guarded_baseline.py`：使用保存的响应作本地回放，
  每一步核对 assignments/tree 和动态 prompt，复用已保存图片并恢复原抽样随机序列；
  第 589 次图片重新生成，要求与中断前保存的图片哈希一致，才开始新推理。
  已有 588 次不重新请求 API，不重复计费；这一步是恢复原轨迹，不是新一次独立实验。
- 33 项离线测试覆盖费用继承、禁止旧响应重复调用、输入/状态漂移停止、恢复边界和保护规则。
  首次离线审计遇到 SDK 保存的 `schema_` 字段与重建校验不兼容，未产生新推理费；
  改为只读适配保存响应，失败目录保留在 `*_offline_validation_failed`，不删除。
- 续跑结果根目录：`output/astra_high_channel_guards_budget70_resume_20260930/`。
  原 $30 结果目录保持原样。`replay_preflight.json` 是恢复校验，`budget.json` 包含累计费用，
  `REPORT.md` 和 `collected_metrics.json/csv` 为四通道统一汇总；具体完成状态以这些文件为准。

### 恢复核验通过

- UTC 2026-09-30 00:41:08，588 次保存响应的离线回放全部通过状态与输入校验。
  第 589 次重新生成的三图、动态 prompt、schema 和 cluster 状态均与原中断记录一致。
- 审计阶段新增推理调用为 0；原费用继承后额度为 $70。续跑进程已启动。
  续跑启动时先复用旧响应恢复控制流；日志中的前 588 步是缓存回放，不是重复付费推理。
