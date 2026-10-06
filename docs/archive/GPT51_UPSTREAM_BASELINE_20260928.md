# GPT-5.1 上游代码 baseline：2026-09-28

> 2026-10-06 归档：以下保留当时的实验条件、结果和判断，不是当前执行计划。旧文中的“下一步”“必须先 SFT”“未使用 final test”等不代表现状；四主通道已反复分析，fixed-state accuracy 不等于 rollout 终态质量。当前主线见 [方法与实验状态](../../项目方法与实验状态.md)。代码和 `output/` 路径均相对于仓库根目录。

## 目标与版本

按 JianZhi 当前上游实现重新运行真实数据 full rollout，获得闭源模型基线。
固定来源：[bfcca625](https://github.com/jiseshen/spike-sorting-agent/tree/bfcca625e4c637f27f869c07196e5a4d30270e08)。
这不是执行历史保存动作；每个动作由新的 GPT API 请求产生。
历史动作本地回放已在四通道恢复相同终态、平均 F1=0.7718；该数字仅作为历史参考。

## 冻结条件

| 项目 | 本次条件 |
|---|---|
| 数据 | CH20、CH3、CH30、CH31；初态 `hierarchy.assigns`；MAT SHA256 记录在 protocol.json |
| 模型 | 请求 `gpt-5.1`，实际返回版本逐次保存；reasoning=`medium` |
| 源码 | 从固定 Git commit 提取独立 `src/`；启动前验证每个源码 SHA256 |
| Prompt | 直接调用上游模板；所有通道使用相同阶段模板，无本地重写 |
| Phase 1 图 | waveform / ISI / hierarchy tree |
| Phase 2 图 | small waveform / large waveform / merged ISI |
| 图片抽样 | 上游随机最多 5,000 波形；每通道 NumPy seed=0 |
| 输出 | 上游 strict JSON schema，必需 action 和 rationale |
| 单次输出上限 | 1,000 tokens；保留上游值，记录 incomplete 和重试 |
| 总调用上限 | 无 |
| Controller | 上游原始 split/merge/discard 语义；阈值 500 / 4,000 / 5,000 |
| RAG / SFT / 删除保护 | 均不启用 |
| 评测 | 用本次终态与 MAT `curation.assigns` 比较，调用上游评测器；空终态显式计零 |

监测层记录实际 SDK 请求、完整响应、图片哈希、每次请求前的状态及有效动作。
上游耗尽重试后若试图回退 mock，或以解析失败默认动作继续，监测层停止该次实验。
正常解析的决策全部按上游 controller 执行。运行依赖使用本地环境，版本已记录；
不宣称恢复了历史软件环境或历史随机抽样。

## 启动与收集

运行器：`scripts/run/run_pinned_upstream_baseline.py`。
收集器：`scripts/analysis/summarize_pinned_upstream_baseline.py`。

```bash
.venv/bin/python scripts/run/run_pinned_upstream_baseline.py \
  --output-root output/upstream_baseline_bfcca625_gpt51_seed0_20260928 --prepare
.venv/bin/python scripts/run/run_pinned_upstream_baseline.py \
  --output-root output/upstream_baseline_bfcca625_gpt51_seed0_20260928 --execute
python3 scripts/analysis/summarize_pinned_upstream_baseline.py \
  output/upstream_baseline_bfcca625_gpt51_seed0_20260928
```

上述目录已用于本次实验，不能重新执行准备命令或覆盖结果。
首次执行在 CH20 第二个有效动作后等待本地输入核验，放行记录为
`continue_after_ch20_check.json`；核验不得依据预期分数选择运行。

## 运行记录

- 离线预检通过，无网络调用；真实 CH20 前两次请求的 prompt、三图哈希、schema
  和参数与预检完全一致，prompt 也与历史同状态记录逐字节一致。
- 前两次真实动作均为 SPLIT；实际模型为 `gpt-5.1-2025-11-13`，响应完整。
- 后续遇到输出 tokens 全用于 reasoning、正文为空的 `max_output_tokens` 截断；
  已记录，由上游原重试逻辑继续。不能把这些请求误判为模型选择 DISCARD。
- 最终结果以本次输出目录的 `collected_metrics.json`、`collected_metrics.csv` 和
  `REPORT.md` 为准；批次未全部完成时不计算四通道均值。

原始数据、图片、逐次响应和生成的结果均在 Git 忽略的 `output/` 下，密钥不进入实验记录。

## 上游原预算运行结果与后续授权

- 原预算批次于 CH20 中途失败：28 次 API 请求，21 个有效决策
  （14 SPLIT / 4 KEEP / 3 DISCARD），7 次输出截断；约 8.9 分钟。
- 最后连续三次响应为 `incomplete/max_output_tokens`，后两次 1,000 tokens
  全用于 reasoning，没有动作正文。未生成终态指标，其余三通道未启动。
- 此次失败不是 CH20 全部 DISCARD，也不能解释历史所有分数差异。
- 用户随后允许只将单次输出预算改为 4,000：先验证保存的失败输入，再开启独立新批次。
  Prompt、三图、medium、schema、controller、阈值保持不变；总调用次数仍不设上限。
  新批次明确标为预算调整版，不冒称完全原参数复现，不覆盖旧记录。

## 新增文件的用途

| 文件 | 用途 |
|---|---|
| `scripts/run/run_pinned_upstream_baseline.py` | 从固定上游 commit 提取代码，核验数据与源码哈希，运行四通道，保存真实请求/响应及状态；新增可显式冻结的 4,000-token 预算选项 |
| `scripts/analysis/summarize_pinned_upstream_baseline.py` | 自动整理终态指标、调用量、失败状态，生成 JSON/CSV/Markdown；失败不能冒充零分或复用旧分数 |
| `tests/test_pinned_baseline_collection.py` | 检查空终态计零、失败不计分、报告预算正确，以及预算调整不改变其它请求字段 |
| 本文 | 记录协议、失败原因、调整授权和结果位置 |

这些是实验运行与可追溯记录，不是新模型或另造一版 prompt。
`GPT51_CAUTIOUS_FULL_ROLLOUT_20260923.md`、`GPT51_DELETE_PROTECTED_FULL_ROLLOUT_20260923.md`
是此前不同实验的记录，本轮不删除或混入其结果。

预算调整版结果目录：
`output/upstream_baseline_bfcca625_gpt51_seed0_budget4000_20260928/`。
其中 `budget_check/` 是一次保存输入的输出完整性验证，不是 rollout，也不算进四通道指标；
CH20/CH3/CH30/CH31 则从各自初态重新调用 API 完整运行，不沿用旧动作。
逐通道结束会自动更新 `REPORT.md`、`collected_metrics.json/csv`。
原 1,000-token 运行器已留存在旧结果目录 `harness_snapshot.py`，供审计。

调整依据：OpenAI Docs 的[推理模型说明](https://developers.openai.com/api/docs/guides/reasoning)
指出单次输出预算包含 reasoning 和可见输出，额度耗尽可能在生成正文前发生。

### 4,000-token 批次启动记录

- 保存输入验证通过：使用原 CH20 第 28 次请求的相同 prompt、相同三张图片字节和 schema；
  仅输出上限改为 4,000，返回完整 SPLIT，消耗 2,268 输入 / 454 输出 tokens
  （其中 reasoning 349）。这是一次技术验证，不是准确率样本，也不证明预算是两次动作不同的唯一原因。
- 新鲜四通道 rollout 于 2026-09-28 17:58 UTC 启动，顺序 CH20 → CH3 → CH30 → CH31；
  从原始 hierarchy 初态运行，未把上一轮 21 个有效动作拼接进来。
- 技术验证与 full rollout 用量分别保存。最终指标仍以新批次 `REPORT.md` 为准，
  未完成的通道不填分数，不计算不完整的四通道均值。
