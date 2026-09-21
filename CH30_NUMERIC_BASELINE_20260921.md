# CH30 numeric-only baseline（2026-09-21）

## 1. 目的与协议

检验仅使用当前流程已经计算出的数值指标，能否复现 CH30 的专家编辑动作，并为后续 images-only / combined VLM 提供必须超过的监督学习基线。

- Train：`cM2-e004_001-003`、`cM2-e004_011-015`、`cM2-e007_012-017`，共 973 条动作。
- Validation：`cM2-e008_021-028` 的 CH30，共 90 条动作。
- Final test：`cM2-e004_004-006`，本轮未纳入训练、模型选择或评估。
- 无图像、无 API、无 GPU、无 RAG；未修改 MAT 或 cluster 状态。
- 模型选择只使用三个 train recording 的 leave-one-recording-block-out 交叉验证；CH30 结果出来后不更换主模型。

### 可学习的任务边界

Train 中 split stage 有 831 条：SPLIT 457、DISCARD 374。使用四项指标：

1. `log1p(n_spikes)`；
2. `log1p(n_overclusters)`；
3. `isi_violation_rate`；
4. `amplitude_cv`。

Train 中 merge stage 的 142 条全部是 MERGE；CH30 的 6 条也全部是 MERGE。由于没有 NOT_MERGE 或 merge-stage DISCARD，不能训练或验证 merge 分类器。本报告对这些已记录的正候选显式使用 constant MERGE，并将其与真正学得的 split-stage 结果分开。

## 2. Train-only 模型选择

| 模型 | Grouped OOF accuracy | Grouped OOF macro-F1 | DISCARD recall | SPLIT recall |
|---|---:|---:|---:|---:|
| Random Forest | 0.904 | 0.903 | 0.904 | 0.904 |
| HistGradientBoosting | 0.889 | 0.889 | 0.901 | 0.880 |

Random Forest 在查看 CH30 前被锁定为 primary model。三个独立 holdout recording 上的 RF accuracy 为 0.877、0.963、0.909，说明高分不是单一 recording 内随机切分造成的直接泄漏。

## 3. CH30 validation 结果

| 模型 | Split stage | Overall expert edits | Overall macro-F1 | DISCARD P/R/F1 | SPLIT P/R/F1 |
|---|---:|---:|---:|---|---|
| Stage-majority reference | 43/84 = 0.512 | 49/90 = 0.544 | 0.559 | 0 / 0 / 0 | .512 / 1 / .677 |
| **Random Forest（primary）** | **78/84 = 0.929** | **84/90 = 0.933** | **0.952** | **1 / .854 / .921** | **.878 / 1 / .935** |
| HistGradientBoosting（secondary） | 79/84 = 0.940 | 85/90 = 0.944 | 0.960 | 1 / .878 / .935 | .896 / 1 / .945 |

RF 的 6 个错误全部是人工 DISCARD 被预测为 SPLIT；没有把人工 SPLIT 错判为 DISCARD。MERGE 的 6/6 来自 positive-only constant policy，不是模型学会了拒绝错误 merge。

HistGradientBoosting 在 CH30 多对 1 条，但 train-only grouped CV 略低，因此不能事后替换 RF 成为 primary；它只作为结果稳健性参考。

## 4. 与 base VLM 的比较

| 方法 | 是否使用 train 动作监督 | CH30 accuracy | DISCARD recall |
|---|---|---:|---:|
| Qwen3.5-4B base VLM | 否 | 0.378 | 0.024 |
| Qwen3.5-9B base VLM | 否 | 0.500 | 0 |
| Gemma-4-E4B-it base VLM | 否 | 0.544 | 0 |
| Random Forest numeric-only | 是，831 条 split-stage | 0.933 | 0.854 |

该差距证明当前专家动作与数值指标高度相关，也说明未经领域训练的 base VLM 不是合格策略。但它**不能**单独证明图像无用：numeric model 已经看过 831 条监督标签，而 base VLM 是 zero-shot。公平的增量问题应比较使用相同 train blocks 训练的 numeric-only、images-only 和 images+numeric 模型。

## 5. 特征与风险

RF impurity importance 为：`n_spikes` 0.601、`n_overclusters` 0.241、ISI 0.102、amplitude CV 0.056。该值只描述 RF 的分裂使用情况，不是因果解释；前两个特征也可能高度相关。

高分有两种可能同时存在：

- 专家本来就主要依据 cluster 大小、overcluster 数和质量指标做 SPLIT/DISCARD；
- 数据生成和动作日志使这些指标泄漏了候选构造规则，模型学到 lab/dataset shortcut。

因此当前可以声明“numeric supervision 在未见 CH30 recording 上强于 zero-shot base VLM”，不能声明 SOTA、不能跳过图像消融，也不能据此进入 autonomous rollout。

## 6. 产物

- 运行脚本：`scripts/analysis/run_numeric_action_baseline.py`
- 单元测试：`tests/test_numeric_action_baseline.py`
- 结果：`output/numeric_action_baseline_20260921/`
- 主模型：`primary_split_model.joblib`（约 2.8 MiB）
- 完整报告：`report.json`；逐样本预测：`detail_predictions.csv`
- 所有结果文件已通过 `checksums.sha256`。

结果目录、模型和数据仍由 `.gitignore` 排除；Git 只保存脚本、测试和本报告。
