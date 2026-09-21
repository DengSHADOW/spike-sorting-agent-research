# CH30 base Qwen fixed-state baseline：统一分析

**日期**：2026-09-21  
**模型**：`Qwen/Qwen3.5-4B`（未微调）  
**设备**：Runpod H100 80 GB  
**评估对象**：`cM2-e008_021-028_CH30` 的 90 条人工 edit-action 状态

## 1. 处理范围与证据

- 输入：每个 SPLIT-stage 样本 4 张真实诊断图，每个 MERGE-stage 样本 3 张真实诊断图，同时包含数值指标。
- 运行方式：固定人工 pre-action state 上逐样本推理；不是 autonomous rollout，不执行模型动作，也不改变 MAT。
- 禁用：OpenAI API、mock、RAG、thinking。
- 配置：temperature 0、`max_tokens=64`、未启用 response schema。
- 90/90 请求返回 HTTP 200；没有 provider、缺图或服务失败。
- 3-sample smoke 是全 90 条中的前三条，不能与全量结果相加。两次独立启动中，前三条的 action 和 raw response 完全相同，可作为 temperature 0 下的重复性检查。
- 原始运行包保持不变并通过 checksum：`output/runpod_collected/qwen35_ch30_real_image_full90_h100_20260921/`。
- 统一派生结果：`output/action_baseline_analysis/qwen35_ch30_full90_20260921/`，包含 `evaluation_report.json`、`confusion_matrix.csv` 和 `error_cases.csv`。

## 2. 数据组成

| Stage | 人工动作 | 数量 |
|---|---|---:|
| split | DISCARD | 41 |
| split | SPLIT | 43 |
| merge | MERGE | 6 |
| 合计 | — | 90 |

该集合是 **expert edit-action log**：记录专家实际执行的编辑，不包含显式 KEEP 或 NOT_MERGE 真值。因此它能评估已执行动作的复现，但不能评估完整动作空间中的“何时不编辑”。尤其是 6 个 merge-stage 样本全部是正 MERGE，无法测量 false-positive merge。

## 3. 统一 action-level 结果

| 人工动作 | Support | Precision | Recall | F1 | Correct |
|---|---:|---:|---:|---:|---:|
| DISCARD | 41 | 0.067 | 0.024 | 0.036 | 1 |
| MERGE | 6 | 1.000 | 1.000 | 1.000 | 6 |
| SPLIT | 43 | 0.475 | 0.674 | 0.558 | 29 |
| 总体 | 90 | — | — | macro-F1 0.531 | 36/90 |

总体 accuracy 为 **0.400**。预测分布为：SPLIT 61、DISCARD 15、MERGE 6、KEEP 5、INVALID_ACTION 3。

### Confusion matrix

| GT \ Pred | DISCARD | MERGE | SPLIT | KEEP | INVALID |
|---|---:|---:|---:|---:|---:|
| DISCARD | 1 | 0 | 32 | 5 | 3 |
| MERGE | 0 | 6 | 0 | 0 | 0 |
| SPLIT | 14 | 0 | 29 | 0 | 0 |

核心失败不是随机错误，而是系统性决策偏置：

- 41 个专家 DISCARD 中只有 1 个正确，32 个被改判为 SPLIT。
- 43 个专家 SPLIT 中有 14 个被改判为 DISCARD。
- 模型倾向在中等规模 cluster 上 SPLIT，却将许多非常大、结构复杂的 cluster DISCARD；这与专家“从大型复合 cluster 中继续寻找可用 unit”的策略不一致。
- MERGE 6/6 不能独立证明 merge 能力，因为没有 NOT_MERGE 负例。

## 4. 输出协议审计

| 项目 | 结果 |
|---|---:|
| 可解析完整 action JSON | 87/90（96.7%） |
| 严格单动作输出 | 0/90 |
| 截断/无效 JSON | 3/90 |
| ABSTAIN | 0/90 |

当前数据 prompt 要求输出 `{"action": ..., "rationale": ...}`，评估器又追加“只输出一个 action token”的指令，两者互相冲突；同时没有启用 response schema。模型最终全部尝试输出 JSON+rationale，其中 3 条在 64 tokens 处截断。

这 3 条无效输出都能从开头恢复出 `SPLIT`，对应 GT 都是 DISCARD，因此即使宽松恢复，accuracy 仍是 36/90。也就是说，**格式问题不解释低准确率，但会污染正式模型对比，必须先修复**。

## 5. 数值特征与 shortcut 风险

split-stage 的人工标签在 CH30 上呈现很强的数值差异：

| GT | median n_spikes | median n_overclusters | median ISI violation | median amplitude CV |
|---|---:|---:|---:|---:|
| DISCARD | 3,954 | 3 | 0.45% | 0.151 |
| SPLIT | 143,804 | 77 | 6.91% | 0.184 |

模型自身的决策方向却接近相反：

| Prediction | 数量 | median n_spikes | median n_overclusters | median ISI violation |
|---|---:|---:|---:|---:|
| DISCARD | 15 | 277,606 | 196 | 17.55% |
| SPLIT | 61 | 19,965 | 11 | 1.02% |

在同一 CH30 上事后选择 `n_spikes > 19,808.5 -> SPLIT` 的单阈值，可对 SPLIT/DISCARD 得到 80/84。该阈值在同一验证集上选择并评估，**不能作为正式 baseline 或泛化成绩**；它只证明数值特征存在强信号，也暴露以下风险：

1. VLM 可能没有正确利用图像，甚至误解了数值指标的领域含义。
2. 后续 SFT 可能仅学习 `n_spikes`/trajectory-order shortcut，而不学习波形形态。
3. 必须先用 train recording 拟合 numeric-only 模型，再在 CH30 固定验证，不能继续在 CH30 上挑阈值。

同一评估集上按 stage 选择多数动作（split stage 恒猜 SPLIT、merge stage 恒猜 MERGE）可得 49/90 = 54.4%，高于 Qwen 的 40.0%。这也是 same-set diagnostic reference，不是独立训练 baseline，但足以说明当前 base Qwen 尚未超过极简单参照。

## 6. Rationale 错误透露的模型问题

Rationale 不是人工 ground truth，也不应作为正式 CoT 证据，但可用于错误分析：

- 模型把 0.29% 和 0.43% ISI violation 描述为“high”，显示数值标度未校准。
- 多次把 `n_overclusters=1` 解释为存在需要 SPLIT 的子结构。
- 模型主要依据 ISI/overcluster 的严重程度选择动作，没有稳定判断“是否还存在值得挽救的生理 unit”。
- 专家 DISCARD 理由则大量依赖波形过宽、多相、下降过慢、非生理形态、spike 太少，以及“没有足够证据表明内部还有有效 unit”。

因此当前问题不只是模型容量不足，也包括动作语义和领域判据没有被 prompt 校准。

## 7. 统一结论

1. **工程链路成功**：真实多图输入、数值特征、H100/vLLM、90 条推理、日志和结果归档均正常，运行基础设施不再是阻塞项。
2. **base Qwen 决策能力不足**：40.0% accuracy、DISCARD F1 0.036，且低于 54.4% 的同集 stage-majority 诊断参照，不能用于自主执行。
3. **最严重风险是错误动作语义**：模型将“复杂但可继续拆分”和“应整体丢弃”混淆；若进入 rollout，会产生大量错误 SPLIT，并漏掉应 DISCARD 的噪声 cluster。
4. **图像增益尚未证明**：CH30 标签被数值特征强烈分隔，必须与 numeric-only 和 image ablation 比较。
5. **MERGE 结论受数据结构限制**：6/6 只证明能复现正例，不能证明会拒绝错误 merge。
6. **当前结果是有效的首轮 diagnostic baseline，不是最终冻结协议成绩，也不是 SOTA 证据**。

## 8. 对项目当前状态的影响

- 项目已经从“数据/环境是否能跑”进入“模型是否学到正确 curation policy”的实验阶段。
- 当前不能开始 autonomous rollout：DISCARD recall 太低，错误动作会改变后续状态并放大偏差。
- 当前也不应直接用 CH30 反复调 prompt/阈值后汇报同一 CH30 成绩，否则会把 validation 变成 training。
- 该结果支持继续研究本地 student，但不能单独证明必须 SFT；必须先确认 numeric-only baseline 和图像增益。
- SFT 的主要目标应是学习“可挽救复合 cluster 与不可挽救噪声”的边界，而不是只提高总体 accuracy。
- 论文中应把该结果作为 zero-shot base VLM baseline/失败分析；项目贡献应落在无泄漏监督、视觉增益、sequential safety 和终态质量，而不是“调用 VLM 即可 curation”。

## 9. 接下来的固定顺序

1. **已完成代码修复**：`action-only-json-v2` 采用唯一结构化目标 `{"action":"..."}`，schema 禁止 rationale/额外字段，删除冲突 prompt，并默认启用标准 JSON schema；尚待真实 vLLM smoke 验证。
2. 先做 3-sample schema smoke；格式 3/3 合规后复跑 CH30 90 条，形成可与其他模型直接比较的正式 base Qwen baseline。
3. 只用 train recording 拟合 numeric-only Random Forest/gradient boosting，在 CH30 validation 上一次性评估。
4. 做 numeric-only、images-only、combined ablation，判断 VLM 是否真正利用诊断图。
5. 在相同协议下测试 Gemma；再根据效果、显存和格式稳定性选择 SFT backbone。
6. 补 KEEP/NOT_MERGE 负例并保留 provenance 后，训练 action-only LoRA/SFT。
7. 只有 fixed-state 的 DISCARD、安全性和负例指标合格后，才进入 autonomous rollout 和 cluster-level 评估。
