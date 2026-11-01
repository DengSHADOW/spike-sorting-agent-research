# 接下来的方向与方案（2026-11-01）

来源有三处：两次会议（10-30/31 定方案，报告之后的 follow-up 会），Tianmin 在 Slack 上的两条消息，以及 2026-10-31—11-01 对 Lv-replay 结果的复核。关键术语用英文写。

文中所有数字都是 **development-set** 结果。原因有两条：方法是在这 4 个 channel 上反复试出来的；ours（rules + context）是在看过 ACE 等方案失败之后才设计的。

---

## 0. 现在的结果

- 协议是 strict expert replay：Qwen3.5-27B，thinking off，只问 Jacob 实际做过的 edit，只给第一次作答计分。
- within-channel 的 overall accuracy：

  | | CH30 | CH20 | CH31 | CH3 |
  |---|---|---|---|---|
  | ours | 87% | 82% | 78% | 84% |
  | simple context | 77% | 70% | 77% | 78% |
  | base | 48% | 56% | 44% | 49% |

- transfer：CH31→CH3 为 88%，CH3→CH31 为 85%。在 target channel 上从零学（from scratch）分别是 84% 和 78%。
- 学到的基本是一条规则："`firing_rate_hz` 低于约 1–2 Hz 就 DISCARD"。4 个 channel 里，Jacob 的 DISCARD/SPLIT 分界都在约 2 Hz。不同 channel 的 spike count 阈值看起来不一样，只是因为录音时长不同。
- 学习曲线的形状是**第一条规则被接受时跳一下**，大约在第 8–10 题，之后基本是平的，不是持续上升。
- 图和数字：`output/shadow/final_figures_20261101/`，由 `scripts/analysis/plot_replay_final_figures.py` 生成。

---

## 1. 正在做

| 事项 | 状态 | 位置 |
|---|---|---|
| SpikeAgent 跑 seed 1 和 2（Slack 要求） | **已完成**（11-01 11:32–13:54 UTC，8 个 run 都没有失败）。3 个 seed 的 accuracy：CH30 40.7%、CH20 36.3%、CH31 48.3%、CH3 46.9%，s.e. 都不到 1 个百分点 | `output/shadow/spikeagent_seeds_20261101/`，项目总文档 §5.9 |
| 最终图：4 张 online 加 2 张 transfer，PDF，标签写明各方法是什么 | **已完成**，用了 SpikeAgent 的 3 个 seed | `output/shadow/final_figures_20261101/` |
| failure cases | **已完成** | `output/shadow/failure_cases_20261101/REPORT.md` |

SpikeAgent 这边有两点限制：
- 代码不允许 SpikeAgent 用 `--edit-only`，所以按旧协议跑，再在各方法共同的题上计分。
- 它的 prompt 里没有 SPLIT，几乎总是答 DISCARD，预计 seed 之间差别很小。

---

## 2. thinking 方案（Tianmin：打开 thinking 会不会更好？）

### 2.1 现在的设置（已核对代码和模型 README）

- Lv 代码里的 `--no-thinking` 只把 `chat_template_kwargs.enable_thinking` 设为 false，**采样参数没动**（`run_expert_replay.py` 第 173–175 行写明 "sampling unchanged"）。所以现在所有 no-thinking 的 run 用的是 `temperature 1.0, top_p 0.95, top_k 20, presence_penalty 1.5`。
- Qwen3.5-27B 的 README 给 non-thinking 列了两档：一般任务 `temperature 0.7, top_p 0.8`，推理任务 `temperature 1.0, top_p 0.95`，其余参数相同。现在的设置正好是推理任务那一档，符合官方推荐（§5.8 也这样记录）。
- 不过 1.0 是两档里较"热"的一档。报告里"改写几个字就会翻转约 1/3 的答案"，有可能部分来自这一点。换成 0.7/0.8 能不能降低噪声，还没有验证。
- 模型每次调用都走同一套设置：每题作答、proposer（提规则）、evaluator（筛选时重答旧题）。代码里的 `side()` 也让学习调用共用同一份 profile。

### 2.2 能控制到什么粒度

- **按请求开关**：`chat_template_kwargs: {"enable_thinking": true/false}`，每个请求可以不一样。Qwen3.5 没有 `/think`、`/no_think` 这种写在 prompt 里的软开关。
- **限制 thinking 长度**：vLLM 0.32 支持按请求设置 `thinking_token_budget`。前提是服务启动时配了 reasoning parser；我们启动时带了 `--reasoning-parser qwen3`，所以可以用。
- **按调用类型开关**：要给 Lv 代码加一个小参数，比如 `--thinking {none,proposer,all}`，让 proposer 和 evaluator 各用自己的 profile。注意 evaluator 必须和第一次作答用同一种模式，否则筛选规则时的环境和规则实际使用时不一样。
- **提速**（我们的环境上都没验证过）：
  - MTP speculative decoding（`qwen3_5_mtp`）；
  - 把 `max-num-seqs` 从 4 往上调；
  - 多个 channel 并发跑。

### 2.3 实验设计（先小后大）

先在 CH20（45 题）上跑 3 个 seed。值得做时再上 CH30（81 题）。

| 代号 | 内容 | 回答的问题 | 代价 |
|---|---|---|---|
| A0 | 现有 no-thinking 结果 | 对照 | 0（复用） |
| A1 | no-thinking，换成 README 一般任务那一档采样（0.7 / 0.8） | 采样调凉之后，seed 之间的噪声会不会变小、结果会不会变好 | 和现有 run 相同 |
| B1 | base，开 thinking | thinking 本身能不能让 zero-shot 更贴近 Jacob | 中 |
| C1 | ours，只给 proposer 开 thinking | 能不能更早、更准地找到阈值；proposer 每 8 题才调一次，最便宜 | 低 |
| C2 | ours，所有调用都开 thinking | 模型能不能更好地照规则作答。例如 CH30 上"高发放率但很脏的簇 Jacob 是 SPLIT"，规则写明了，模型照样 DISCARD | 最高 |

**顺序**：
1. 先 smoke 5 道题，量出每题耗时、thinking token 数、截断率和 JSON 失败率。已有的一次参考（§5.8，harness v2 的 skill_v4 decider，CH31 前 20 题）：开 thinking 每题约 86 秒（输出中位 3,899 token），答对 16/20；关 thinking 每题约 9 秒（429 token），答对 14/20。题太少，看不出 thinking 是否更准。但可以据此估计，每题作答开 thinking 后大约慢 10 倍。
2. 跑 A1、C1。
3. 再跑 B1。
4. 只有 C1 或 B1 有收益时才跑 C2。

**要看的指标**：
- overall accuracy，分 DISCARD 和 SPLIT 两类的 accuracy，false DISCARD 数；
- 第一条规则被接受时是第几题，最后学到哪些规则；
- 每题耗时、thinking token 数、截断和失败次数。

**判定**：按题、按 seed 配对比较。收益要超过 seed 之间的波动才算数。现有 CH20 上，ours 3 个 seed 分别答对 35、39、36 题。

**风险**：
- 会上 Zhuoxuan 说 ours 每个 channel 要 1–1.5 小时，但 Lv 的 `result.json` 记录显示，在 H200 上实际是 6–28 分钟（CH20 约 7 分钟，CH30 18–28 分钟）。按上面约 10 倍估算，C2（所有调用都开）每个 channel 大约要 1–5 小时，所以先 smoke，再定 `thinking_token_budget`。C1 只给 proposer 开，每 8 题才多一次慢调用，代价小得多。
- 关键先后：C1 和 C2 之前必须先改代码，加上按调用类型开关的参数。

---

## 3. 非 LLM 参照线（建议加进报告；Tianmin 很可能会问）

做法：同样的题、同样的顺序、同样的反馈，只用 14 个测量值做 online 学习，每揭示一题就重新拟合一次。single 题用 logistic regression；pair 题一律答 MERGE，因为这批数据里 Jacob 对 pair 题全选了 MERGE。脚本在 scratchpad，没有调用模型。

| | CH31（40 题） | CH3（71 题） | CH30（81 题） | CH20（45 题） |
|---|---|---|---|---|
| online logistic regression | 36（90%） | 66（93%） | 78（96%） | 40（89%） |
| online 1-NN | 35 | 64 | 78 | 39 |
| ours（3 个 seed 平均） | 31.3（78%） | 59.3（84%） | 70.7（87%） | 36.7（82%） |

- 结论：在这批数据上，一个简单分类器比最好的 LLM 方法多答对 3–7 题。
- 讲法：LLM 的价值不在"分得更准"，而在下面几点：
  - 不需要人工设计特征；
  - 能给出 rationale，能和人沟通；
  - 能用图像；
  - 能接受 Jacob 的口头规则。
- 应对：可以把参照线放进图里，主动说明。也可以把它变成方法的一部分，见 §6 第 1 项。

---

## 4. "持续学习"还是"一次学会一个阈值"

- 现有曲线显示，提升基本来自第一条被接受的规则，之后是平的。
- 要回答"是不是一直在学"，需要在**固定的 held-out 题**上看每一步的表现。目前只有 CH20、seed 0 一组（`snapshot_eval.py`）。
- **建议**：4 个 channel 各做 3 个 seed。单个 run 大约要重答"测试题数 × 学习步数"那么多次，CH20 是 23 × 22 ≈ 500 次调用，需要 GPU 时间。
- 更根本的办法是换数据：如果新数据里 Jacob 的判断不只看一个阈值，才能看出有没有持续学习。这和 §7 的新数据是同一件事。
- failure cases 已经指出了第二条判断标准（`output/shadow/failure_cases_20261101/REPORT.md`）。ours 在 3 个 seed 里都答错的题，有一部分是 Jacob 因为**波形形状**删掉的簇（"太宽""单相""超过 3 相"），发放率在 1.4–3.8 Hz，已经高于阈值。现在的规则只有大小，学不到这一条。如果能学到，曲线在第一次跳升之后还会继续上升，这正好可以拿来检验"持续学习"。测量值里已经有 `trough_half_width_ms`（波形宽度），但没有相数和单相/多相这类形状特征，模型只能从波形图上看。可以考虑补上这类特征，或者让 proposer 能引用 Jacob 理由里的形状描述。

---

## 5. Active querying：agent 自己知道什么时候该问（Tianmin 的研究方向）

**在 replay 里怎么定义**：
- 每一步，agent 先作答，再决定"问"还是"不问"。
- 问：揭示 Jacob 的动作和理由，就是现在的做法。
- 不问：不给反馈，但状态仍然按 Jacob 的动作往下走（teacher forcing），这样题目序列不变。
- 计分照旧算每一步的 first answer。另外统计问了多少次。

**主要指标**：
- 问的次数和 accuracy 的关系曲线（budget–accuracy curve）；
- 达到 80% 需要问几次。

**对照方法**：每一步都问（现在的做法）、随机问（同样次数）、只问前 n 题。

**不确定性信号**（从便宜到贵）：
1. 规则给出的答案和案例给出的答案不一致；
2. 离已学到的阈值很近；
3. 多次采样（self-consistency）结果不一致；
4. 模型自报的 confidence。

**可以马上在 CPU 上做的第一步**：用现有 3 个 seed 的答案做离线分析。看"只在 seed 之间不一致的题上问"能省多少次提问、损失多少 accuracy。这一步不用 GPU。

**更难的一步**：主动提出"缺了什么知识"的问题，比如"判断标准是不是发放率？"。可以先用 Jacob 已经写过的 rationale 模拟回答，再找 Jacob 实测。Tianmin 建议去和一位做 "when to ask" 的同学聊。

---

## 6. 方法改进（结合之前的文献检索）

1. **给规则学习器一个数值工具**（最推荐）。
   - 问题：阈值其实就在案例里，但 LLM 自己算不出来。只给案例时，CH30 上 DISCARD 题只答对 48%。
   - 做法：proposer 选特征、提出规则形式，由一个小工具在已揭示的题上算出最佳切点，再经现有的 screen 和 accept 流程验证。
   - 预期：有希望追上 §3 的参照线，同时保留规则的可读性。
   - 相关文献：HtT（arXiv 2310.07064）、RLIE（2510.19698）、LSP（2406.17224）。
2. **减少规则之间的相互干扰**。会上提到后段下降，Zhuoxuan 认为是新规则干扰旧判断。
   - 每条规则单独做 ablation；
   - 加规则时检查有没有和已有规则冲突；
   - 把 40k 字符的全量案例换成按相似度检索：ReasoningBank（2509.25140）、Memento（2508.16153）、ExpeL（2308.10144）。
3. **ACE / GEPA / Dynamic Cheatsheet**。
   - ACE（2510.04618）已经试过，失败原因记在报告里：反思时已经看到答案；投票反映不出规则有没有用；条目上限把好规则挤掉。
   - GEPA（2507.19457）适合离线优化 source channel 的 prompt，再拿去做 transfer。
   - Dynamic Cheatsheet（2504.07952）和现有的"规则 + 案例"很接近。
4. **Training-Free GRPO**（2510.08191）。思路是同一题多次作答、比较组内优劣来提炼经验，代价高，排在后面。
5. **非 LLM 的领域参照**：UnitRefine（bioRxiv 2025.03.30.645770）和 TabPFN（Nature 2025）可以作为 related work 和更强的表格参照。

---

## 7. 真实使用验证（和 Jacob 一起，会上定的下一步）

**做法**：
- 从 4 个 trained agent 里挑 overall 最好的一个，冻结，不再 optimization。
- 拿它去跑同一批实验里其他 channel 的新数据。数据只需要 Jacob 的最终 decision，不需要 rationale。

**测什么**：
1. 相对 Jacob 的最终 decision，agent 的 accuracy；
2. Jacob 用 agent 相比自己独立 curate，能快多少。要事先定好怎么计时：Jacob 是逐条审阅 agent 的建议，还是只看被标出来的那些。

**数据**：Tianmin 发邮件问 Jacob 要。不过盘上很可能已经有了：`Jacob Bedke-Annotated_spike_sorting_data_w_chronux/not_annotated/` 里有 13 个没有理由标注的 channel。MAT 里存着 Jacob 按顺序做的动作（`curation.action`）和最终分群，可以照原样做严格专家重放，代码也支持从 MAT 读动作（`mat_internal`）。但项目有数据隔离规定（`configs/curation/protocol_v1.json`）：
- 现在就能用的只有开发块里的两个：`cM2-e004_011-015_CH17`（60 步，和 CH20 同一次录音）和 `cM2-e004_004-006_CH1`（160 步，和 CH3、CH31 同一次录音）。
- 新录音 `cM2-e007_012-017`（5 个 channel）从 10-06 起保留为 held-out，`cM2-e004_001-003` 处于隔离状态。动用它们要由用户和 Tianmin 决定。

另一个不碰保留数据的做法：在现有 4 个 channel 之间做完整的 transfer 矩阵。CH30（e008）、CH20（e004_011-015）和 CH31/CH3（e004_004-006）分属三次不同的录音，所以其中有一部分是真正跨录音的迁移。

**风险和准备**：
- 新录音的时长不同，按 spike count 写的规则会失效，按 Hz 写的规则才可迁移。挑 agent 前要检查它的规则用的是 `firing_rate_hz`。CH31 有两个 seed 学到了 0.12–0.2 Hz 这种过低的阈值，这样的 agent 不要选。
- KEEP 仍然不计分。新数据上，agent 会不会把 Jacob 保留的簇误删，要另外统计。

---

## 8. 把 4 个 channel 合起来训练一个 general agent（Zhuoxuan 提议，Tianmin 会和 Shreesh 讨论）

- 评测要用 leave-one-channel-out：在 3 个 channel 上学，在第 4 个上测。注意 CH3 和 CH31 可能来自同一次录音，两者不能一个在训练集、一个在测试集。
- 这和 10-30/31 会上"不做跨 channel 结论"的决定有冲突，要作为新实验单独说明。

---

## 9. KEEP

- Tianmin 会请 Jacob 通过网站补写 KEEP 的 rationale。
- Zhuoxuan 想试 simple context 能不能学会 KEEP。
- 风险：不考虑 KEEP，等于告诉模型"这个簇一定要动"，误删的问题就被藏起来了。一旦把 KEEP 加回来，现有结果会掉多少，要单独测。

---

## 10. 先放一放的

- **题目顺序**：现在用 Jacob 的顺序，题型沿顺序变化很大。可以试随机顺序或按难度排序。
- **DISCARD 不可逆**：误删的代价比漏删高，可以考虑非对称的计分方式。
- **"拿不准就问人"**：和 §5 合并。

---

## 11. 优先级建议

| 优先级 | 事项 | 要 GPU 吗 | 依赖 |
|---|---|---|---|
| 1 | SpikeAgent 补 seed，最终图 | 是，实际用了 2 小时 20 分钟 | **已完成** |
| 2 | 非 LLM 参照线进图（§3） | 否 | 无 |
| 3 | active querying 离线分析（§5 第一步） | 否 | 无 |
| 4 | thinking：smoke 加 A1、C1（§2） | 是 | C1 要先改代码 |
| 5 | 4 个 channel × 3 seed 的固定测试集（§4） | 是 | 无 |
| 6 | 数值工具版规则学习器（§6 第 1 项） | 是 | 改代码 |
| 7 | 真实使用验证（§7） | 是 | 等 Jacob 的数据 |
| 8 | general agent（§8）、KEEP（§9） | 是 | 等讨论结果 |
