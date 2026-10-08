# 固定人工状态实验归档（2026-09-21—22）

> 2026-10-06 归档：以下保留当时的实验条件、结果和判断，不是当前执行计划。旧文中的“下一步”“必须先 SFT”“未使用 final test”等不代表现状；四主通道已反复分析，fixed-state accuracy 不等于 rollout 终态质量。当前主线见 [方法与实验状态](../../项目方法与实验状态.md)。代码和 `output/` 路径均相对于仓库根目录。

<a id="qwen"></a>

原文件：`CH30_QWEN_BASELINE_ANALYSIS_20260921.md`

## CH30 base Qwen fixed-state baseline：统一分析

**日期**：2026-09-21  
**模型**：`Qwen/Qwen3.5-4B`（未微调）  
**设备**：Runpod H100 80 GB  
**评估对象**：`cM2-e008_021-028_CH30` 的 90 条人工 edit-action 状态

> 当前用于后续模型对比的正式结果是 `action-only-json-v2`。本文保留首轮 legacy 运行的分析，用于审计协议修复前后的差异。

### 0. 冻结协议后的正式 v2 结果

2026-09-21 已在同一 H100、同一模型、同一 CH30 90 条样本上完成 `action-only-json-v2` 复跑。关闭 thinking，`max_tokens=32`，默认开启严格 JSON schema，每条只允许 `{"action":"ACTION"}`。

| 指标 | 首轮 legacy diagnostic | 正式 `action-only-json-v2` |
|---|---:|---:|
| Accuracy | 36/90 = 0.400 | **34/90 = 0.378** |
| Macro-F1 | 0.531 | **0.530** |
| 可解析完整输出 | 87/90 | **90/90** |
| 严格 action-only JSON | 0/90 | **90/90** |
| 无效/截断输出 | 3 | **0** |
| 预测分布 | D15 / M6 / S61 / K5 / invalid3 | **D17 / M6 / S54 / K13** |

v2 的 action-level 结果：

| 人工动作 | Support | Precision | Recall | F1 | Correct |
|---|---:|---:|---:|---:|---:|
| DISCARD | 41 | 0.059 | 0.024 | 0.034 | 1 |
| MERGE | 6 | 1.000 | 1.000 | 1.000 | 6 |
| SPLIT | 43 | 0.500 | 0.628 | 0.557 | 27 |
| 总体 | 90 | — | — | macro-F1 0.530 | 34/90 |

两次运行有 71/90 个预测一致，19/90 发生改变；5 条从对变错，3 条从错变对，净减 2 条。这说明结构化输出约束不只改变包装格式，也会轻微改变 base model 的动作分布，因此 prompt/schema 必须作为评测协议的固定部分。但核心结论没变：DISCARD 仍只命中 1/41，base Qwen 不能进入 autonomous rollout。

正式归档位于 `output/runpod_collected/qwen35_ch30_action_v2_full90_h100_20260921/`；统一派生分析位于 `output/action_baseline_analysis/qwen35_ch30_action_v2_full90_20260921/`。

### 1. 首轮 legacy 处理范围与证据

- 输入：每个 SPLIT-stage 样本 4 张真实诊断图，每个 MERGE-stage 样本 3 张真实诊断图，同时包含数值指标。
- 运行方式：固定人工 pre-action state 上逐样本推理；不是 autonomous rollout，不执行模型动作，也不改变 MAT。
- 禁用：OpenAI API、mock、RAG、thinking。
- 配置：temperature 0、`max_tokens=64`、未启用 response schema。
- 90/90 请求返回 HTTP 200；没有 provider、缺图或服务失败。
- 3-sample smoke 是全 90 条中的前三条，不能与全量结果相加。两次独立启动中，前三条的 action 和 raw response 完全相同，可作为 temperature 0 下的重复性检查。
- 原始运行包保持不变并通过 checksum：`output/runpod_collected/qwen35_ch30_real_image_full90_h100_20260921/`。
- 统一派生结果：`output/action_baseline_analysis/qwen35_ch30_full90_20260921/`，包含 `evaluation_report.json`、`confusion_matrix.csv` 和 `error_cases.csv`。

### 2. 数据组成

| Stage | 人工动作 | 数量 |
|---|---|---:|
| split | DISCARD | 41 |
| split | SPLIT | 43 |
| merge | MERGE | 6 |
| 合计 | — | 90 |

该集合是 **expert edit-action log**：记录专家实际执行的编辑，不包含显式 KEEP 或 NOT_MERGE 真值。因此它能评估已执行动作的复现，但不能评估完整动作空间中的“何时不编辑”。尤其是 6 个 merge-stage 样本全部是正 MERGE，无法测量 false-positive merge。

### 3. 统一 action-level 结果

| 人工动作 | Support | Precision | Recall | F1 | Correct |
|---|---:|---:|---:|---:|---:|
| DISCARD | 41 | 0.067 | 0.024 | 0.036 | 1 |
| MERGE | 6 | 1.000 | 1.000 | 1.000 | 6 |
| SPLIT | 43 | 0.475 | 0.674 | 0.558 | 29 |
| 总体 | 90 | — | — | macro-F1 0.531 | 36/90 |

总体 accuracy 为 **0.400**。预测分布为：SPLIT 61、DISCARD 15、MERGE 6、KEEP 5、INVALID_ACTION 3。

#### Confusion matrix

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

### 4. 输出协议审计

| 项目 | 结果 |
|---|---:|
| 可解析完整 action JSON | 87/90（96.7%） |
| 严格单动作输出 | 0/90 |
| 截断/无效 JSON | 3/90 |
| ABSTAIN | 0/90 |

当前数据 prompt 要求输出 `{"action": ..., "rationale": ...}`，评估器又追加“只输出一个 action token”的指令，两者互相冲突；同时没有启用 response schema。模型最终全部尝试输出 JSON+rationale，其中 3 条在 64 tokens 处截断。

这 3 条无效输出都能从开头恢复出 `SPLIT`，对应 GT 都是 DISCARD，因此即使宽松恢复，accuracy 仍是 36/90。也就是说，**格式问题不解释低准确率，但会污染正式模型对比，必须先修复**。

### 5. 数值特征与 shortcut 风险

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

### 6. Rationale 错误透露的模型问题

Rationale 不是人工 ground truth，也不应作为正式 CoT 证据，但可用于错误分析：

- 模型把 0.29% 和 0.43% ISI violation 描述为“high”，显示数值标度未校准。
- 多次把 `n_overclusters=1` 解释为存在需要 SPLIT 的子结构。
- 模型主要依据 ISI/overcluster 的严重程度选择动作，没有稳定判断“是否还存在值得挽救的生理 unit”。
- 专家 DISCARD 理由则大量依赖波形过宽、多相、下降过慢、非生理形态、spike 太少，以及“没有足够证据表明内部还有有效 unit”。

因此当前问题不只是模型容量不足，也包括动作语义和领域判据没有被 prompt 校准。

### 7. 统一结论

1. **工程链路成功**：真实多图输入、数值特征、H100/vLLM、90 条推理、日志和结果归档均正常，运行基础设施不再是阻塞项。
2. **base Qwen 决策能力不足**：正式 v2 为 37.8% accuracy、DISCARD F1 0.034，且低于 54.4% 的同集 stage-majority 诊断参照，不能用于自主执行。
3. **最严重风险是错误动作语义**：模型将“复杂但可继续拆分”和“应整体丢弃”混淆；若进入 rollout，会产生大量错误 SPLIT，并漏掉应 DISCARD 的噪声 cluster。
4. **图像增益尚未证明**：CH30 标签被数值特征强烈分隔，必须与 numeric-only 和 image ablation 比较。
5. **MERGE 结论受数据结构限制**：6/6 只证明能复现正例，不能证明会拒绝错误 merge。
6. **当前结果是有效的首轮 diagnostic baseline，不是最终冻结协议成绩，也不是 SOTA 证据**。

### 8. 对项目当前状态的影响

- 项目已经从“数据/环境是否能跑”进入“模型是否学到正确 curation policy”的实验阶段。
- 当前不能开始 autonomous rollout：DISCARD recall 太低，错误动作会改变后续状态并放大偏差。
- 当前也不应直接用 CH30 反复调 prompt/阈值后汇报同一 CH30 成绩，否则会把 validation 变成 training。
- 该结果支持继续研究本地 student，但不能单独证明必须 SFT；必须先确认 numeric-only baseline 和图像增益。
- SFT 的主要目标应是学习“可挽救复合 cluster 与不可挽救噪声”的边界，而不是只提高总体 accuracy。
- 论文中应把该结果作为 base VLM fixed-state action baseline/失败分析；项目贡献应落在无泄漏监督、视觉增益、sequential safety 和终态质量，而不是“调用 VLM 即可 curation”。

### 9. 接下来的固定顺序

1. **已完成** `action-only-json-v2` 真实 vLLM 3-sample smoke 和 CH30 90 条正式复跑；严格格式合规率均为 100%。
2. 只用 train recording 拟合 numeric-only Random Forest/gradient boosting，在 CH30 validation 上一次性评估。
3. 做 numeric-only、images-only、combined ablation，判断 VLM 是否真正利用诊断图。
4. 在相同协议下测试 Gemma；再根据效果、显存和格式稳定性选择 SFT backbone。
5. 补 KEEP/NOT_MERGE 负例并保留 provenance 后，训练 action-only LoRA/SFT。
6. 只有 fixed-state 的 DISCARD、安全性和负例指标合格后，才进入 autonomous rollout 和 cluster-level 评估。

---

<a id="open-vlm"></a>

原文件：`CH30_OPEN_VLM_COMPARISON_20260921.md`

## CH30 open-VLM fixed-state comparison（2026-09-21）

### 1. 实验问题

在完全相同的 `action-only-json-v2` 协议下，比较不同未微调 open VLM 对 CH30 人工编辑动作的复现能力，并判断单纯增大或更换 base model 是否足以解决 curation 决策问题。

本轮不是 SFT，也不是 autonomous rollout。每条样本都从人工轨迹中读取正确的 pre-action state，独立预测一次动作，因此属于 teacher-forced / fixed-state action-level evaluation。

### 2. 固定条件

- 数据：`cM2-e008_021-028_CH30`，90 条真实人工动作状态；GT 为 DISCARD 41、SPLIT 43、MERGE 6。
- 输入：与训练导出一致的真实诊断图和数值指标。
- 输出：严格 `{"action":"ACTION"}`；response schema 开启，`additionalProperties=false`。
- 推理：thinking 关闭、temperature 0、`max_tokens=32`、本地 vLLM 0.29、Runpod H100 80 GB。
- 隔离：未调用 OpenAI API、mock 或 RAG；未修改 MAT；未执行预测动作或更新 cluster 状态。
- 所有模型使用同一 90 条样本、同一 prompt 归一化逻辑和同一评分脚本。

### 3. 结果

| 模型 | Accuracy | Macro-F1 | DISCARD P/R/F1 | SPLIT P/R/F1 | MERGE P/R/F1 | 预测分布 |
|---|---:|---:|---|---|---|---|
| Qwen3.5-2B | 0.067 | 0.333 | 0 / 0 / 0 | 0 / 0 / 0 | 1 / 1 / 1 | KEEP 80、DISCARD 4、MERGE 6 |
| Qwen3.5-4B | 0.378 | 0.530 | .059 / .024 / .034 | .500 / .628 / .557 | 1 / 1 / 1 | SPLIT 54、DISCARD 17、KEEP 13、MERGE 6 |
| Qwen3.5-9B | 0.500 | 0.570 | 0 / 0 / 0 | .582 / .907 / .709 | 1 / 1 / 1 | SPLIT 67、KEEP 16、DISCARD 1、MERGE 6 |
| Gemma-4-E4B-it | 0.544 | 0.578 | 0 / 0 / 0 | .581 / 1 / .735 | 1 / 1 / 1 | SPLIT 74、KEEP 10、MERGE 6 |

四个正式运行均为严格 action-only JSON `90/90`，没有解析失败、截断或 schema 回退。因此本表反映的是决策差异，不再混入输出格式错误。

同一评估集上事后计算的 stage-majority 诊断参照准确率也是 `49/90 = 0.544`。Gemma 的准确率与它相同，但动作序列并不完全相同；这只说明 Gemma 的总正确数没有超过一个利用 stage 分布的弱参照，不能据此证明图像理解带来增益。

### 4. 结论

1. **模型规模有帮助，但没有解决任务。** Qwen 从 2B 的 6.7% 提升到 9B 的 50.0%，说明容量影响明显；然而 9B 仍然没有命中任何 DISCARD。
2. **Gemma 的最高 raw accuracy 不能当作胜利。** 它正确识别 43/43 SPLIT 和 6/6 MERGE，却对 41 个 DISCARD 为 0/41，且总准确率没有超过 stage-majority 诊断参照。
3. **DISCARD 是当前主要失败点。** 4B 仅命中 1/41，其他三个模型均为 0/41。当前 base VLM 不应执行 autonomous curation，否则会在“保留噪声”和“错误删除真实 unit”之间产生不可接受的偏差。
4. **MERGE 的 6/6 不是可靠泛化证据。** 当前 CH30 只含 6 个实际执行的 MERGE，没有 NOT_MERGE 负例；模型可能利用阶段和候选构造，而不是学会了完整 merge 判断。
5. **现有数据只覆盖 expert edits。** KEEP/NOT_MERGE 没有人工 GT；模型输出 KEEP 会被计错，但数据无法回答某个未执行候选是否本应 KEEP/NOT_MERGE。因此本轮应称为 expert edit-action baseline，而不是完整决策策略评测。

### 5. 对项目方向的影响

- 已经完成足够的 base-model 横向检查，不需要继续无目的扩大 未做领域微调的模型列表。
- Qwen3.5-9B 可作为第一版 LoRA/SFT 的主候选；Gemma-4-E4B-it 保留为跨架构复核。选择依据不是 Gemma 高 4 个正确样本，而是 Qwen 9B 容量、现有 Qwen 工具链和后续同-backbone base/SFT 对照的一致性。
- SFT 前仍需完成 train-recording-only numeric baseline，并补齐或明确派生 KEEP/NOT_MERGE。前者检验模型是否只是读取数值 shortcut，后者避免只拿“实际执行的正向编辑”训练一个无法停止或拒绝候选的策略。
- 第一版 SFT 继续采用 action-only target；现有 train blocks 没有可靠人工 rationale，不应生成伪推理监督。
- 只有 fixed-state 的 DISCARD/负例安全指标达到预设门槛后，才进入 autonomous rollout；DISCARD 应先 quarantine/可回滚，不直接永久删除。

### 6. 可复核产物

- `output/runpod_collected/qwen35_2b_ch30_action_v2_full90_h100_20260921/`
- `output/runpod_collected/qwen35_ch30_action_v2_full90_h100_20260921/`
- `output/runpod_collected/qwen35_9b_ch30_action_v2_full90_h100_20260921/`
- `output/runpod_collected/gemma4_e4b_ch30_action_v2_full90_h100_20260921/`

每个新归档已在本地通过 `checksums.sha256`；结果包只包含预测、manifest 和日志，不包含原始图片、MAT、模型权重或密钥。

---

<a id="numeric"></a>

原文件：`CH30_NUMERIC_BASELINE_20260921.md`

## CH30 numeric-only baseline（2026-09-21）

### 1. 目的与协议

检验仅使用当前流程已经计算出的数值指标，能否复现 CH30 的专家编辑动作，并为后续 images-only / combined VLM 提供必须超过的监督学习基线。

- Train：`cM2-e004_001-003`、`cM2-e004_011-015`、`cM2-e007_012-017`，共 973 条动作。
- Validation：`cM2-e008_021-028` 的 CH30，共 90 条动作。
- Final test：`cM2-e004_004-006`，本轮未纳入训练、模型选择或评估。
- 无图像、无 API、无 GPU、无 RAG；未修改 MAT 或 cluster 状态。
- 模型选择只使用三个 train recording 的 leave-one-recording-block-out 交叉验证；CH30 结果出来后不更换主模型。

#### 可学习的任务边界

Train 中 split stage 有 831 条：SPLIT 457、DISCARD 374。使用四项指标：

1. `log1p(n_spikes)`；
2. `log1p(n_overclusters)`；
3. `isi_violation_rate`；
4. `amplitude_cv`。

Train 中 merge stage 的 142 条全部是 MERGE；CH30 的 6 条也全部是 MERGE。由于没有 NOT_MERGE 或 merge-stage DISCARD，不能训练或验证 merge 分类器。本报告对这些已记录的正候选显式使用 constant MERGE，并将其与真正学得的 split-stage 结果分开。

### 2. Train-only 模型选择

| 模型 | Grouped OOF accuracy | Grouped OOF macro-F1 | DISCARD recall | SPLIT recall |
|---|---:|---:|---:|---:|
| Random Forest | 0.904 | 0.903 | 0.904 | 0.904 |
| HistGradientBoosting | 0.889 | 0.889 | 0.901 | 0.880 |

Random Forest 在查看 CH30 前被锁定为 primary model。三个独立 holdout recording 上的 RF accuracy 为 0.877、0.963、0.909，说明高分不是单一 recording 内随机切分造成的直接泄漏。

### 3. CH30 validation 结果

| 模型 | Split stage | Overall expert edits | Overall macro-F1 | DISCARD P/R/F1 | SPLIT P/R/F1 |
|---|---:|---:|---:|---|---|
| Stage-majority reference | 43/84 = 0.512 | 49/90 = 0.544 | 0.559 | 0 / 0 / 0 | .512 / 1 / .677 |
| **Random Forest（primary）** | **78/84 = 0.929** | **84/90 = 0.933** | **0.952** | **1 / .854 / .921** | **.878 / 1 / .935** |
| HistGradientBoosting（secondary） | 79/84 = 0.940 | 85/90 = 0.944 | 0.960 | 1 / .878 / .935 | .896 / 1 / .945 |

RF 的 6 个错误全部是人工 DISCARD 被预测为 SPLIT；没有把人工 SPLIT 错判为 DISCARD。MERGE 的 6/6 来自 positive-only constant policy，不是模型学会了拒绝错误 merge。

HistGradientBoosting 在 CH30 多对 1 条，但 train-only grouped CV 略低，因此不能事后替换 RF 成为 primary；它只作为结果稳健性参考。

### 4. 与 base VLM 的比较

| 方法 | 是否使用 train 动作监督 | CH30 accuracy | DISCARD recall |
|---|---|---:|---:|
| Qwen3.5-4B base VLM | 否 | 0.378 | 0.024 |
| Qwen3.5-9B base VLM | 否 | 0.500 | 0 |
| Gemma-4-E4B-it base VLM | 否 | 0.544 | 0 |
| Random Forest numeric-only | 是，831 条 split-stage | 0.933 | 0.854 |

该差距证明当前专家动作与数值指标高度相关，也说明未经领域训练的 base VLM 不是合格策略。但它**不能**单独证明图像无用：numeric model 已经看过 831 条监督标签，而 base VLM 未做领域微调。公平的增量问题应比较使用相同 train blocks 训练的 numeric-only、images-only 和 images+numeric 模型。

### 5. 特征与风险

RF impurity importance 为：`n_spikes` 0.601、`n_overclusters` 0.241、ISI 0.102、amplitude CV 0.056。该值只描述 RF 的分裂使用情况，不是因果解释；前两个特征也可能高度相关。

高分有两种可能同时存在：

- 专家本来就主要依据 cluster 大小、overcluster 数和质量指标做 SPLIT/DISCARD；
- 数据生成和动作日志使这些指标泄漏了候选构造规则，模型学到 lab/dataset shortcut。

因此当前可以声明“numeric supervision 在未见 CH30 recording 上强于 未做领域微调的 base VLM”，不能声明 SOTA、不能跳过图像消融，也不能据此进入 autonomous rollout。

### 6. 产物

- 运行脚本：`scripts/analysis/run_numeric_action_baseline.py`
- 单元测试：`tests/test_numeric_action_baseline.py`
- 结果：`output/numeric_action_baseline_20260921/`
- 主模型：`primary_split_model.joblib`（约 2.8 MiB）
- 完整报告：`report.json`；逐样本预测：`detail_predictions.csv`
- 所有结果文件已通过 `checksums.sha256`。

结果目录、模型和数据仍由 `.gitignore` 排除；Git 只保存脚本、测试和本报告。

---

<a id="api-vlm"></a>

原文件：`CH30_OPENAI_API_FIXED_STATE_20260922.md`

## CH30 OpenAI API-VLM fixed-state evaluation（2026-09-22）

### Protocol

- Data: the current audited `cM2-e008_021-028_CH30` dataset, not a different legacy MAT file.
- Targets: 90 expert edits: 41 `DISCARD`, 43 `SPLIT`, and 6 `MERGE`.
- Evaluation: fixed expert pre-action states; predictions were scored but never executed.
- Inputs: four diagnostic images for split-stage states, three pairwise diagnostic images for merge-stage states, plus the exported numerical metrics.
- Output: strict `action-only-json-v2` with JSON schema enforcement; no rationale or schema fallback.
- Provider: official OpenAI API; temperature 0; no mock, RAG, MAT modification, or cluster-state update.
- GPT-4.1 used `max_tokens=32`; GPT-5.1 used medium reasoning and `max_output_tokens=2048`.

An exploratory GPT-5.1 run with a 512-token output budget was stopped and excluded after one response exhausted its reasoning budget and returned no final JSON. The formal run used 2,048 tokens as a ceiling and completed with 90/90 strict JSON responses. Per-sample checkpointing and `--resume` support were added before the formal rerun.

### Results

| Model | Actual API model | Accuracy | Macro-F1 | DISCARD P/R/F1 | SPLIT P/R/F1 | MERGE P/R/F1 |
|---|---|---:|---:|---|---|---|
| GPT-4.1 | `gpt-4.1-2025-04-14` | 35/90 = 0.389 | 0.575 | 0 / 0 / 0 | .784 / .674 / .725 | 1 / 1 / 1 |
| GPT-5.1 | `gpt-5.1-2025-11-13` | 33/90 = 0.367 | 0.564 | 0 / 0 / 0 | .771 / .628 / .692 | 1 / 1 / 1 |

Prediction distributions:

- GPT-4.1: 37 `SPLIT`, 47 `KEEP`, 6 `MERGE`.
- GPT-5.1: 35 `SPLIT`, 49 `KEEP`, 6 `MERGE`.
- The two models agreed on 88/90 predictions (97.8%). The only differences were samples 49 and 60, where GPT-4.1 predicted `SPLIT` and GPT-5.1 predicted `KEEP`.

Both models returned strict action-only JSON for 90/90 samples. Neither model predicted `DISCARD` once. The 6/6 `MERGE` result remains positive-only because CH30 contains no `NOT_MERGE` target.

Token usage recorded by the API:

- GPT-4.1: 221,485 input tokens, 4,864 cached input tokens, 530 output tokens, 222,015 total tokens.
- GPT-5.1: 184,225 input tokens, 85,120 cached input tokens, 18,438 output tokens, including 16,652 reasoning tokens; 202,663 total tokens.

### Interpretation

This rerun is useful because it places the API VLMs, open VLMs, and numeric baseline on the same current CH30 action-level task. GPT-4.1 and GPT-5.1 do not outperform Qwen3.5-9B (0.500) or Gemma-4-E4B-it (0.544), and all remain far below the supervised numeric Random Forest on learned `SPLIT/DISCARD` states (78/84 = 0.929).

The result does not invalidate the older GPT closed-loop cluster-level findings. It shows that the older endpoint F1 and the current expert-action accuracy measure different properties. Under the frozen action protocol, neither API model reproduces the expert `DISCARD` rule, so neither should be used as a ground-truth teacher for that action without calibration.

### Artifacts

- GPT-4.1: `output/openai_fixed_state/gpt41_ch30_action_v2_full90_20260922/`
- GPT-5.1: `output/openai_fixed_state/gpt51_ch30_action_v2_full90_20260922_v2/`
- Both directories contain per-sample predictions, strict-format analysis, run manifests, token usage, error cases, and incremental checkpoints.
