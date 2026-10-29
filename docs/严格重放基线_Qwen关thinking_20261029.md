# 严格重放基线：Qwen3.5-27B（关 thinking），Tengyang harness `f1ca871`

日期：2026-10-29
运行目录：RunPod `/workspace/replay-baseline-f1ca871`
结果文件：RunPod `/workspace/replay-baseline-f1ca871/output/replay_qwen27b_nothink_baseline_20261029_summary/`（`SUMMARY.md`、`summary.json`、`steps.csv`），本地副本在 `output/replay_qwen27b_nothink_baseline_20261029_summary/`（该目录被 git 忽略）。

本次运行是不学习的固定 agent 基线。后续自我进化版需要在相同的模型、thinking 设置、题目集和评分口径下与它比较。

## 1. 实验设置

**代码。** 使用 Tengyang 仓库 GitHub `main` 的提交 `f1ca871`（harness v2 rounds 2-4），在一个干净的检出上运行。没有使用 `/workspace/spike-sorting-agent` 下 Tengyang 正在使用的工作目录，也没有使用 Tianmin 副本。在这个提交之外只新增了三个文件：`src/agent/expert_simulation.py`、`scripts/run/run_expert_replay.py`、`scripts/analysis/summarize_expert_replay.py`。对上游文件只改了一处：`scripts/analysis/audit_real_action_sources.py` 里的 sklearn import 被移到了审计函数内部，因为 pod 的 `.venv` 没有 sklearn。这个文件只负责解析专家动作表，不参与 agent 的决策。

**Agent。** 使用上游默认配置：skill `skill_v4`（sha256 前缀 `74d742ca0dd8`，带 gate），observation `observation-v2.3`，输出格式 `observe-then-act-v3`，contract sha256 前缀 `5f970bb37f32`。

**模型。** Qwen3.5-27B，BF16，vLLM 0.32.0，单块 H100 80GB。服务用 Tengyang 的启动脚本 `local_models.py serve qwen27b` 启动，与 `f1ca871` 中的启动脚本和配置逐字节一致，`max_num_seqs=4`，`max_model_len=24576`。采样参数保持 profile 原值：temperature 1.0、top_p 0.95、top_k 20、presence_penalty 1.5，每个请求的 seed 由请求内容的哈希决定。运行时通过 `--no-thinking` 把 `enable_thinking` 改成 false，覆盖记录写在每次运行的 `run_manifest.json` 中。这是唯一一处偏离 Tengyang 配置的地方。

**题目（严格重放）。** 题目完全由专家的有效动作序列决定，被撤回的动作和对应的撤销都已剔除。每道题都在专家执行该动作之前的精确状态上提问：SPLIT 和 DISCARD 是单簇题，MERGE 是成对题（source 为被并入的 cluster，target 为专家的合并目标）。不论模型怎么回答，都执行专家的动作。KEEP 只按一条规则补充：某个 cluster 出现后（初始、split 子 cluster 或合并结果），如果按时间往后专家再也没有碰过它，就问它一次 KEEP。来源标为 `derived_no_later_expert_action`，理由留空。合并前 KEEP 默认关闭。四个通道上，专家的有效操作都被 100% 问到，重放后的终态也都与专家重放的结果一致。

**运行方式。** 四个通道依次运行，不开预取。严格重放的大多数轮次只有一道题，所以测到的单步时间基本是单请求延迟。在本次设置下，accuracy 与运行方式无关。

**计分。** 以 harness 的最终动作（gate 之后）对照专家动作。为了说明有多少一致率来自题目构成本身，同时列出两个不看图的参照策略："永远 DISCARD"，以及"提供 SPLIT 就拆，否则删"。

## 2. 题目构成

| 通道 | 角色 | 题数 | 专家真实动作 | 推断 KEEP |
|---|---|---|---|---|
| CH31 `cM2-e004_004-006_CH31` | 学习 | 57 | 54（DISCARD 34，SPLIT 20） | 3 |
| CH3 `cM2-e004_004-006_CH3` | 学习 | 99 | 97（DISCARD 60，SPLIT 37） | 2 |
| CH30 `cM2-e008_021-028_CH30` | 学习 | 104 | 90（DISCARD 41，SPLIT 43，MERGE 6） | 14 |
| CH20 `cM2-e004_011-015_CH20` | 测试 | 62 | 50（DISCARD 22，SPLIT 25，MERGE 3） | 12 |
| 合计 | | 322 | 291 | 31 |

CH20 的动作来源是 MAT 内部记录，其中有两次撤回（第 4 步的丢弃在第 5 步撤回，第 39 步的合并在第 40 步撤回），所以有效动作比 manifest 的计数少两条。CH20 的专家动作没有理由文字。

## 3. 最终 accuracy

| 通道 | 模型 | 永远 DISCARD | 提供 SPLIT 就拆，否则删 | 只看专家真实动作 | 只看推断 KEEP |
|---|---|---|---|---|---|
| CH31 | 43/57（75.4%） | 59.6% | 59.6% | 41/54（75.9%） | 2/3 |
| CH3 | 73/99（73.7%） | 60.6% | 63.6% | 72/97（74.2%） | 1/2 |
| CH30 | 76/104（73.1%） | 39.4% | 50.0% | 74/90（82.2%） | 2/14 |
| CH20（测试） | 37/62（59.7%） | 35.5% | 48.4% | 35/50（70.0%） | 2/12 |

三个学习通道合计 192/260（73.8%），四个通道合计 229/322（71.1%）。

四个通道合并后按专家动作拆分：

| 专家动作 | 模型一致 | 主要错误去向 |
|---|---|---|
| SPLIT | 112/125（89.6%） | DISCARD 9，KEEP 4 |
| DISCARD | 109/157（69.4%） | KEEP 25，SPLIT 23 |
| KEEP | 7/31（22.6%） | DISCARD 20，SPLIT 4 |
| MERGE | 1/9（11.1%） | NOT_MERGE 7，DISCARD 1 |

skill gate 在四个通道上一共改写了 10 次模型的动作：G2 6 次（模型原本答 KEEP），G5 3 次（模型原本答 DISCARD），G3 1 次。上表统计的都是 gate 之后的最终动作。

## 4. 时间

| 通道 | 模型单步（中位数 / P90） | 画图单步 | 重放耗时 | 输出 token |
|---|---|---|---|---|
| CH31 | 9.3 / 10.6 秒 | 1.0 秒 | 9.4 分钟 | 23,763 |
| CH3 | 9.3 / 10.5 秒 | 1.0 秒 | 16.9 分钟 | 41,190 |
| CH30 | 9.8 / 10.7 秒 | 1.1 秒 | 17.4 分钟 | 43,666 |
| CH20 | 9.6 / 10.5 秒 | 1.0 秒 | 9.3 分钟 | 25,533 |

322 步的模型作答累计约 50.5 分钟，每步平均约 420 个输出 token，没有截断。每个通道开始前还要额外几分钟加载 MAT 并重建专家路径，CH30 约 6 分钟。

作为对照，开 thinking 时（Tengyang 原配置）在 CH31 的前 20 步做过一次 smoke：单步中位数 80.0 秒，P90 126.7 秒，最长 149.6 秒，每步约 3,800 个输出 token，一致率 16/20。同样 20 步关 thinking 后，一致率是 14/20，差别都在 DISCARD（12/16 对 10/16）。20 步的差异不足以下结论。

## 5. 主要观察

第一，题目构成会抬高总一致率。严格重放的题目大多是专家要改的 cluster，所以"永远 DISCARD"在 CH31 和 CH3 上就能拿到约 60%。模型在这两个通道上比参照策略只高 10 到 16 个百分点，在 KEEP 和 MERGE 较多的 CH30 和 CH20 上高出 11 到 34 个百分点。所以比较时不能只看总一致率，要按动作拆开看。

第二，错误集中在三处。一是 KEEP：专家最终保留的 unit 有 20/31 被判成 DISCARD，也就是把真神经元删掉，这在 CH30 和 CH20 上尤其明显。二是 MERGE：9 次专家合并只答对 1 次。三是 DISCARD：专家要删的有 25 次被留下，23 次被拆开。SPLIT 是模型最好的一类。

第三，测试集 CH20 明显低于学习集，59.7% 对 73.8%。CH20 的 KEEP（2/12）和 MERGE（1/3）都很弱。

## 6. 局限

本基线把 thinking 关掉了，已经不是 Tengyang 原配置下的 agent。后续自我进化版必须同样关闭 thinking 才能比较。

严格重放每一步都强制回到专家状态，错误不会累积。这里的一致率不等于自主运行时的最终分群效果，两者需要分开报告。

推断 KEEP 只是"专家此后未再碰过"的推断，不是专家明确给出的保留判断；数据中也恢复不出专家"看过但没动"的记录。

采样 temperature 为 1.0，seed 固定由请求内容的哈希决定。本次每个通道只跑了一次，还没有测运行间的波动。

CH3 和 CH31 来自同一个录音块，所有学习通道和测试通道都在以往的开发中出现过，因此不能声称是严格的 held-out 泛化。

## 7. 复现

在 GPU pod 上，先从 `/workspace/spike-sorting-agent` 启动服务：

```bash
setsid nohup .venv-model-tools/bin/python scripts/deploy/local_models.py serve qwen27b \
  > /workspace/replay-baseline-f1ca871/output/gpu_logs/vllm_qwen27b.log 2>&1 < /dev/null &
```

然后在 `/workspace/replay-baseline-f1ca871` 运行 `output/gpu_logs/run_baseline_nothink.sh`。脚本会依次对 CH31、CH3、CH30、CH20 执行：

```bash
/workspace/spike-sorting-agent/.venv/bin/python scripts/run/run_expert_replay.py --model qwen27b --no-thinking \
  --parallel 4 --dataset <dataset> --output-root output/replay_qwen27b_nothink_<channel>_baseline_20261029
```

最后执行 `scripts/analysis/summarize_expert_replay.py` 生成汇总。如果只需要 accuracy、不需要干净的时间数据，可以加 `--prefetch`，先画完所有观测图再并发作答。
