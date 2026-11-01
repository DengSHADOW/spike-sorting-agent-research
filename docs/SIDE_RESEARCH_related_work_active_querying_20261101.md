# 支线研究：related work 地图与 active querying（2026-11-01）

用途：这是用户自己做的支线，和主线（`docs/NEXT_DIRECTIONS_20261101.md`、`项目总文档.md` §5.9）分开。内容来自 2026-11-01 的文献检索；我只读了摘要和搜索摘录，标 * 的读过全文。写论文前要按原文复核。

---

## 1. related work 地图

每一行是一个研究方向：它在研究什么、代表工作、和我们方法（ours = rules + context，见 §5.9）的关系。

| 方向 | 研究什么 | 代表工作 | 和 ours 的关系 |
|---|---|---|---|
| **test-time / continual learning via context** | 不改模型权重，把做过的题和总结出的经验写进 context，让模型在使用中越来越好 | Reflexion（2023）、ExpeL（[2308.10144](https://arxiv.org/abs/2308.10144)）、Dynamic Cheatsheet（[2504.07952](https://arxiv.org/abs/2504.07952)）、ACE（[2510.04618](https://arxiv.org/abs/2510.04618)，ICLR 2026）、ReasoningBank（[2509.25140](https://arxiv.org/abs/2509.25140)）、Memento（[2508.16153](https://arxiv.org/abs/2508.16153)） | ours 的主干。ExpeL 是"规则 + 案例"的原型：从成败对比里提炼 insight，作答时再附上检索到的相似轨迹 |
| **prompt optimization** | 部署前用训练题反复改写 prompt 文字、按验证分数筛选 | APE、OPRO、MIPROv2、GEPA（[2507.19457](https://arxiv.org/abs/2507.19457)，ICLR 2026 oral）；[2610.27468](https://arxiv.org/abs/2610.27468) 把 GEPA 式进化用于持续更新 | ours 的"提议 → 筛选 → 全量验证 → 接受"是 GEPA 验证流程的在线简化版；没有 Pareto frontier 和 crossover |
| **规则归纳与验证** | 从例子里归纳"如果…就…"的规则，在例子上验证后才用 | HtT（[2310.07064](https://arxiv.org/abs/2310.07064)）、LSP（[2406.17224](https://arxiv.org/abs/2406.17224)）、RLIE（[2510.19698](https://arxiv.org/abs/2510.19698)） | ours 的验证门来自 HtT。RLIE 的"LLM 选特征、统计模型定权重"正对应我们"LLM 算不准阈值"的问题 |
| **学到的经验的治理（governance）** | 记录每条经验的可靠性、冲突时听谁的、不确定时弃答、何时停用 | *Closing the Feedback Loop（[2606.17591](https://arxiv.org/abs/2606.17591)，Amazon，ICML 2026 RLxF workshop）、Agent Gym（[2608.15591](https://arxiv.org/abs/2608.15591)）、TAME（[2602.03224](https://arxiv.org/abs/2602.03224)） | 和"验证后收录、可删"同一思路。它们多出 evidence 层（每条规则的历史表现）和 abstain；Closing the Feedback Loop 的 Reflexion 式基线低于 zero-shot，和我们 ACE-only 的结果一样 |
| **持续学习 agent 的评测** | 怎么证明 agent 真的在学，而不是题目碰巧变简单；遗忘怎么量 | *CL-Bench（[2606.05661](https://arxiv.org/abs/2606.05661)）、Evo-Memory（[2511.20857](https://arxiv.org/abs/2511.20857)）、PATH-Bench（[2608.01149](https://arxiv.org/abs/2608.01149)）、Do Self-Evolving Agents Forget?（[2605.09315](https://arxiv.org/abs/2605.09315)）、When Does CL Require Learning（[2607.07847](https://arxiv.org/abs/2607.07847)） | gain metric（同一题上有状态减无状态）、monitor set（永不参与学习的题）、稳定性 / 可塑性分解。CL-Bench 发现普通 ICL 胜过专门的记忆系统，和我们"long-context agent 已经很强"一致 |
| **综述与框架** | 把这些方法放进一个坐标系 | *Experience Compression Spectrum（[2604.15877](https://arxiv.org/abs/2604.15877)）、The Rise of Verbal RL（[2609.01597](https://arxiv.org/abs/2609.01597)）、Self-Improving Test-Time Intelligence survey（[2609.01679](https://arxiv.org/abs/2609.01679)） | 压缩光谱：L1 案例（5–20×）、L2 技能（50–500×）、L3 规则（1000×+）。它说 L3 几乎是空的、跨层系统只有 ExpeL 和 AutoAgent。ours 是 L1 + L3 的跨层实例 |
| **可靠性的质疑** | agent 不一定照着自己积累的经验做 | Not Faithful Self-Evolvers（[2601.22436](https://arxiv.org/abs/2601.22436)） | 对应我们看到的"规则写了，模型照样按直觉删" |
| **人在回路 / 何时提问** | agent 自己判断什么时候该问人、问什么 | 见 §2 | active querying 方向的依据 |
| **领域内** | spike sorting 的 curation | SpikeAgent、UnitRefine（bioRxiv 2025.03.30.645770）、TabPFN（Nature 2025） | 基线、related work、更强的表格参照 |

---

## 2. active querying

### 2.1 问题

现在的协议是"每一题都揭示 Jacob 的答案"，人每步都在教。active querying 要让 agent **先答，再决定问不问**；只有问了才揭示。目标：用更少的提问达到现在的水平，并且 agent 自己知道"学会了没有"。这是 Tianmin 在 10-31 后的会上提出的研究方向（"agent 自己意识到没学会、主动去问"）。

### 2.2 现有研究（2025–2026）

**(a) 专家反馈 + 预算：最接近我们的场景**

| 工作 | 内容 | 对我们的启发 |
|---|---|---|
| SynAct（[2610.12651](https://arxiv.org/abs/2610.12651)） | 把专家注意力当稀缺资源。按"这一步的决定对最终结果影响多大"（多次 rollout 间动作与结果的互信息）挑最值得问的决策；专家纠正提炼成可复用的规则，部署时不再需要专家。16 个设置平均 +11.7 个百分点；"一次对准的纠正胜过 7 次盲目重试" | 和"挑哪些簇问 Jacob、把反馈变成规则"几乎是同一个问题；它的选择信号需要多次 rollout，我们可以用多次采样代替 |
| Active Domain Knowledge Acquisition with 100-Dollar Budget（[2508.17202](https://arxiv.org/abs/2508.17202)，EMNLP Findings 2025） | 固定预算下选择问哪位专家、问什么；多 agent RL；敏感领域（医疗） | 预算化的提问 |
| One Human, N Agents（[2607.28317](https://arxiv.org/abs/2607.28317)） | 一个人审多个 agent 时预算怎么分。模型置信度不准时，按置信度排序审查不如随机 | **模型自报的把握不可信**，提问信号要用经验信号 |

**(b) 什么时候该问：不确定性与澄清**

| 工作 | 内容 |
|---|---|
| Uncertainty Quantification for LLM Agents（[2609.07395](https://arxiv.org/abs/2609.07395)） | 把"问人、弃答、延后"统一成一个决定；关键不是不确定性多大，而是"问一次的期望收益是否大于成本" |
| Uncertainty Decomposition for Clarification Seeking（[2606.19559](https://arxiv.org/abs/2606.19559)） | 把"题目本身模糊"和"模型能力不够"分开，只在前者时提问 |
| Uncertainty-Aware Clarification with Information Gain（[2606.03135](https://arxiv.org/abs/2606.03135)） | 按一个问题能减少多少关于目标的不确定性来决定问不问 |
| ReDAct（[2604.07036](https://arxiv.org/abs/2604.07036)） | 不确定时把决策延后 |
| Learning to Ask（[2409.00557](https://arxiv.org/abs/2409.00557)） | Ask-when-Needed 提示法 |
| KnowNo / Robots That Ask For Help（[2307.01928](https://arxiv.org/abs/2307.01928)） | 源头工作：用 conformal prediction 决定机器人何时求助，并保证求助次数与成功率的关系 |

**(c) 评测基准**

| 工作 | 内容 |
|---|---|
| HiL-Bench（[2604.09408](https://arxiv.org/abs/2604.09408)） | "agent 知道什么时候该求助吗"。指标 **Ask-F1**（提问精确率与阻塞召回率的调和平均），同时惩罚乱问和闷头猜。前沿模型都有很大的判断差距：过度自信、发现不确定仍继续错、大面积乱问。用 Ask-F1 做奖励对 32B 模型做 RL 有效 |
| Ask Now, Use Later（[2605.28108](https://arxiv.org/abs/2605.28108)，EMNLP 2026） | 长期使用的 agent 不会提前问它以后会用到的信息（proactivity gap） |
| Don't Guess, Ask Wisely（ACL Anthology 2026.emnlp-main.1370） | 七个模型里，题目信息不全时只有不到 11% 会追问 |
| Ask or Assume?（[2603.26233](https://arxiv.org/abs/2603.26233)） | 编程 agent 在需求不清时的澄清 |

**应用场景**：机器人规划求助、编程 agent 的需求澄清、医疗等敏感领域的专家咨询、多 agent 的人工审计分配。**科学数据标注 / curation 这个场景没有找到现成工作**，是一个空位。

### 2.3 在本项目里的定义

- 每一步：agent 先作答，再决定"问 / 不问"。问 = 揭示 Jacob 的动作和理由（现在的做法）；不问 = 不给反馈，但状态仍按 Jacob 的动作往下走（teacher forcing），题目序列不变。
- 计分照旧算每一步的 first answer；另外统计提问次数。
- **主要指标**：提问次数对 accuracy 的曲线（budget–accuracy curve）；达到 80% 需要问几次；HiL-Bench 的 Ask-F1 可以作为第二个指标（把"答错且没问"算 miss，"答对还问"算 false alarm）。
- **对照**：每一步都问（现在）、随机问同样次数、只问前 n 题、按不确定性问。
- **不确定性信号**（便宜 → 贵）：规则给出的答案和案例给出的答案不一致；离已学阈值很近；多次采样不一致（self-consistency）；和记忆库里的案例不像（novelty）；模型自报 confidence（最不可靠）。
- **收敛信号**：最近几次提问 Jacob 的答案都和 agent 自己的一致 → 降低提问频率。这就是"agent 知道自己学会了"。

### 2.4 第一步：离线分析，不用 GPU

用现有 3 个 seed 的答案（`/workspace/Lv-replay/zs-online-hybrid/output/hybrid_20261031/hybrid/`、`.../overnight_20261031/context_online/`）模拟：
1. 信号 = "3 个 seed 答案不一致"。只在不一致的题上问，统计提问次数和假设只学这些题时的 accuracy（需要重跑才能精确，但可以先估上界：不问的题不进案例池）。
2. 画 budget–accuracy 曲线，和随机问、全问比。
3. 看错题里有多少是"seed 一致但全错"（自信的错误）。这部分 active querying 抓不到，对应 HiL-Bench 说的 overconfident failure，要靠别的信号（离阈值的距离、novelty）。

第二步才需要 GPU：把"问 / 不问"做进 replay（一个 `--query-policy` 参数），跑按不确定性问的 3 个 seed。

### 2.5 风险

- 自报 confidence 不可靠（One Human N Agents、ACE 的计数、CL-Bench）；用经验信号。
- 不问的题得不到反馈，案例池变小，规则学得慢：这是要测的代价。
- DISCARD 不可逆：在 rollout 里，不确定的 DISCARD 应延后到问完再执行。

---

## 3. 建议的阅读顺序

1. Closing the Feedback Loop（2606.17591）全文：governance 的系统化版本，最适合做 related work 锚点。
2. SynAct（2610.12651）全文：专家预算下的查询选择。
3. HiL-Bench（2604.09408）：Ask-F1 的定义。
4. Experience Compression Spectrum（2604.15877）：定位 ours。
5. CL-Bench（2606.05661）：gain metric 与评测协议。
6. ExpeL、HtT 原文：ours 的两个直接前驱。
