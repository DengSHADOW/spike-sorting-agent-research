# CH30 open-VLM fixed-state comparison（2026-09-21）

## 1. 实验问题

在完全相同的 `action-only-json-v2` 协议下，比较不同未微调 open VLM 对 CH30 人工编辑动作的复现能力，并判断单纯增大或更换 base model 是否足以解决 curation 决策问题。

本轮不是 SFT，也不是 autonomous rollout。每条样本都从人工轨迹中读取正确的 pre-action state，独立预测一次动作，因此属于 teacher-forced / fixed-state action-level evaluation。

## 2. 固定条件

- 数据：`cM2-e008_021-028_CH30`，90 条真实人工动作状态；GT 为 DISCARD 41、SPLIT 43、MERGE 6。
- 输入：与训练导出一致的真实诊断图和数值指标。
- 输出：严格 `{"action":"ACTION"}`；response schema 开启，`additionalProperties=false`。
- 推理：thinking 关闭、temperature 0、`max_tokens=32`、本地 vLLM 0.29、Runpod H100 80 GB。
- 隔离：未调用 OpenAI API、mock 或 RAG；未修改 MAT；未执行预测动作或更新 cluster 状态。
- 所有模型使用同一 90 条样本、同一 prompt 归一化逻辑和同一评分脚本。

## 3. 结果

| 模型 | Accuracy | Macro-F1 | DISCARD P/R/F1 | SPLIT P/R/F1 | MERGE P/R/F1 | 预测分布 |
|---|---:|---:|---|---|---|---|
| Qwen3.5-2B | 0.067 | 0.333 | 0 / 0 / 0 | 0 / 0 / 0 | 1 / 1 / 1 | KEEP 80、DISCARD 4、MERGE 6 |
| Qwen3.5-4B | 0.378 | 0.530 | .059 / .024 / .034 | .500 / .628 / .557 | 1 / 1 / 1 | SPLIT 54、DISCARD 17、KEEP 13、MERGE 6 |
| Qwen3.5-9B | 0.500 | 0.570 | 0 / 0 / 0 | .582 / .907 / .709 | 1 / 1 / 1 | SPLIT 67、KEEP 16、DISCARD 1、MERGE 6 |
| Gemma-4-E4B-it | 0.544 | 0.578 | 0 / 0 / 0 | .581 / 1 / .735 | 1 / 1 / 1 | SPLIT 74、KEEP 10、MERGE 6 |

四个正式运行均为严格 action-only JSON `90/90`，没有解析失败、截断或 schema 回退。因此本表反映的是决策差异，不再混入输出格式错误。

同一评估集上事后计算的 stage-majority 诊断参照准确率也是 `49/90 = 0.544`。Gemma 的准确率与它相同，但动作序列并不完全相同；这只说明 Gemma 的总正确数没有超过一个利用 stage 分布的弱参照，不能据此证明图像理解带来增益。

## 4. 结论

1. **模型规模有帮助，但没有解决任务。** Qwen 从 2B 的 6.7% 提升到 9B 的 50.0%，说明容量影响明显；然而 9B 仍然没有命中任何 DISCARD。
2. **Gemma 的最高 raw accuracy 不能当作胜利。** 它正确识别 43/43 SPLIT 和 6/6 MERGE，却对 41 个 DISCARD 为 0/41，且总准确率没有超过 stage-majority 诊断参照。
3. **DISCARD 是当前主要失败点。** 4B 仅命中 1/41，其他三个模型均为 0/41。当前 base VLM 不应执行 autonomous curation，否则会在“保留噪声”和“错误删除真实 unit”之间产生不可接受的偏差。
4. **MERGE 的 6/6 不是可靠泛化证据。** 当前 CH30 只含 6 个实际执行的 MERGE，没有 NOT_MERGE 负例；模型可能利用阶段和候选构造，而不是学会了完整 merge 判断。
5. **现有数据只覆盖 expert edits。** KEEP/NOT_MERGE 没有人工 GT；模型输出 KEEP 会被计错，但数据无法回答某个未执行候选是否本应 KEEP/NOT_MERGE。因此本轮应称为 expert edit-action baseline，而不是完整决策策略评测。

## 5. 对项目方向的影响

- 已经完成足够的 base-model 横向检查，不需要继续无目的扩大 zero-shot 模型列表。
- Qwen3.5-9B 可作为第一版 LoRA/SFT 的主候选；Gemma-4-E4B-it 保留为跨架构复核。选择依据不是 Gemma 高 4 个正确样本，而是 Qwen 9B 容量、现有 Qwen 工具链和后续同-backbone base/SFT 对照的一致性。
- SFT 前仍需完成 train-recording-only numeric baseline，并补齐或明确派生 KEEP/NOT_MERGE。前者检验模型是否只是读取数值 shortcut，后者避免只拿“实际执行的正向编辑”训练一个无法停止或拒绝候选的策略。
- 第一版 SFT 继续采用 action-only target；现有 train blocks 没有可靠人工 rationale，不应生成伪推理监督。
- 只有 fixed-state 的 DISCARD/负例安全指标达到预设门槛后，才进入 autonomous rollout；DISCARD 应先 quarantine/可回滚，不直接永久删除。

## 6. 可复核产物

- `output/runpod_collected/qwen35_2b_ch30_action_v2_full90_h100_20260921/`
- `output/runpod_collected/qwen35_ch30_action_v2_full90_h100_20260921/`
- `output/runpod_collected/qwen35_9b_ch30_action_v2_full90_h100_20260921/`
- `output/runpod_collected/gemma4_e4b_ch30_action_v2_full90_h100_20260921/`

每个新归档已在本地通过 `checksums.sha256`；结果包只包含预测、manifest 和日志，不包含原始图片、MAT、模型权重或密钥。
