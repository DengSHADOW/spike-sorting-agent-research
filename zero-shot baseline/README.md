# Minimal-prompt VLM curation：方法与结果

实验日期：2026-10-07 至 2026-10-08。四通道 stepwise 与完整闭环均已完成。

## 1. 实验条件

模型：`gpt-6-astra`；reasoning：`high`；每次输出上限 4000 tokens；标准服务层。每项评测运行一次，无多次取优。所有通道共用相同的 [Phase 1 prompt](prompts/phase1.txt) 和 [Phase 2 prompt](prompts/phase2.txt)，输出 `action` 与简短 `rationale`。

输入为当前 cluster 的诊断图、数值和计算定义；不提供任务示例、专家评论、标准答案、RAG 或 teacher 反馈，未做任务微调，不传入前次对话。这是固定流程中的 **zero-shot prompting curation**，不是从原始电信号开始的端到端自主 spike sorting。

## 2. 结果

Stepwise 测“给定专家操作前状态，动作是否与专家一致”；full rollout 测“模型动作实际改变分群后，最终结果与专家标注的一致程度”。两者不能合成一个 accuracy。

### Full rollout：最终分群结果

四通道均完成完整闭环。以下比较最终 spike 分配与专家 MAT 终态，**不是每一步动作的 accuracy**。

| 通道 | Precision | Recall | 最终 F1 | 初态 F1 |
|---|---:|---:|---:|---:|
| CH3 | 19.95% | 100.00% | 33.27% | 24.40% |
| CH20 | 22.05% | 100.00% | 36.13% | 35.97% |
| CH30 | 19.46% | 87.42% | 31.83% | 30.61% |
| CH31 | 38.70% | 100.00% | 55.81% | 50.48% |
| 四通道宏平均 | 25.04% | 96.86% | 39.26% | 35.36% |

最终宏平均 F1 比初态提高 **3.90 个百分点**。

| 通道 | 闭环成功判断数 | 最终群数 | 误删专家 spikes | 误保留噪声 spikes |
|---|---:|---:|---:|---:|
| CH3 | 369 | 66 | 0 | 73,666 |
| CH20 | 260 | 53 | 0 | 65,281 |
| CH30 | 1,589 | 67 | 0 | 318,201 |
| CH31 | 535 | 50 | 0 | 79,718 |

**主要观察：** 专家 spikes 未被删除，但大量参考噪声被保留，Precision 低。CH30 的 91,105 个专家 spikes 中，79,644 个正确匹配；另外 11,461 个仍保留，却与其他专家 unit 混在同一预测群内，所以 Recall 为 87.42%，不是删除造成。

#### 为什么 Recall 高、Precision 低？

以 CH3 为例：专家有效 spikes 为 **18,364**，全部正确匹配，故 Recall＝18,364/18,364＝**100%**；模型同时保留了 **73,666** 个参考噪声 spikes，故 Precision＝18,364/(18,364+73,666)＝**19.95%**。即有效信号没漏掉，但大量噪声也没清掉；不是“100% 分群正确”。当前 many-to-one 匹配还允许一个专家 unit 被拆成多个预测群而保持高 Recall。

- **已确认的模型判断：** 一些纯参考噪声群被判为 KEEP。例如 CH31 cluster 13 的 18,065 spikes 全部被专家标为 noise，模型却以“波形一致、幅度单峰、ISI 违规率低”等理由保留。这些特征不足以保证符合专家的保留标准；模型理由是其自述，不是已验证的因果解释。
- **已确认的程序行为：** NOT_MERGE 只拒绝合并，不删除 source；没有合适目标时仍保留。CH30 有 1,280 次 NOT_MERGE，但这不等于 1,280 个噪声群，也不能将拒绝合并直接改为删除。
- **已确认的问题、影响尚未量化：** 合并前复查提出 SPLIT 时，controller 没有执行拆分，只保留该群并排除其候选资格，可能使问题群继续留下；尚不能证明修复后能提高多少分数。

**结论边界：** 低 Precision 的直接统计原因是参考噪声误保留，CH30 另有专家 unit 混群。prompt、图像/数值观测、controller，以及专家 noise 标准与模型理解的差异各有多少影响，尚未通过受控对照分离；不能认定某一项是唯一原因，也不能据此认定专家标签错误。案例证据见 [失败分析归档](../实验失败案例分析与归档.md)。

### Stepwise：单步动作结果

| 通道 | 正确/合格 | 动作 accuracy |
|---|---:|---:|
| CH3 | 37/97 | 38.14% |
| CH20 | 19/53 | 35.85% |
| CH30 | 32/90 | 35.56% |
| CH31 | 14/54 | 25.93% |
| 合计 | 102/294 | 34.69% |

## 3. 数据与预处理

来源：`Jacob Bedke-Annotated_spike_sorting_data_w_chronux/<dataset_id>/CH*_spikes.mat`。CH 表示电极通道，不是神经元。四个 MAT 的采样率均为 30 kHz；下表总 spikes 含 noise。

| Dataset ID | 总 spikes | 初始活跃群数 |
|---|---:|---:|
| cM2-e004_004-006_CH3 | 94,743 | 25 |
| cM2-e004_011-015_CH20 | 84,283 | 12 |
| cM2-e008_021-028_CH30 | 430,937 | 18 |
| cM2-e004_004-006_CH31 | 133,091 | 17 |

MAT 已包含检测出的 spike 波形、时间、初始分群和聚合树。本次不重新滤波、检测 spikes 或训练初始聚类。读取 `Fs`、`spiketimes`、`waveforms`、`overcluster.assigns`、`hierarchy.assigns`、`hierarchy.tree`，校验文件哈希及数组对齐，必要时转置；不新增幅度归一化，不改原 MAT。时间按秒解释，幅度保留源单位，不假定为 µV。

本地程序从当前群成员重新画图和计算指标，使用 `raw_member_v1`，不是把资料包内已有截图直接交给模型。

| 阶段 | 图片（按发送顺序） | 数值 |
|---|---|---|
| Phase 1 | 波形叠加、ISI、幅度分布、聚合树 | 群 ID、spike 数、初始子群数、ISI 违规率、幅度 CV |
| Phase 2 | source 波形、target 波形、假设合并后的 ISI | 双方 ID/数量、双方及合并后的 ISI 违规率、平均波形相关系数 |

- **波形：** 按全局 spike 行号的 SHA256 确定性抽样，每群最多显示 500 条；指标仍用全部成员。图为 8×4 英寸、110 dpi；横轴毫秒、纵轴源单位，纵轴范围按各群全部波形极值加 5% 边距。Phase 2 两张波形图各自定纵轴，未统一尺度，也未加中位数/分位带。
- **ISI：** 全部时间排序后，间隔小于 2 ms 的数量除以 N−1；N<2 时为 null。图显示 0–50 ms、100 个 bin，数值分母仍包含全部间隔。合并指标用两群时间拼接、排序后计算。2 ms 是测量定义，不是自动删除阈值。
- **幅度：** 每条波形的峰峰值；直方图 50 个 bin；CV＝总体标准差/均值，均值为零时 null。相关系数为两群全部成员平均波形之间的 Pearson correlation，方差为零时 null。
- **树与发送：** 聚合树由上游合并历史重建，高度为 `max(子节点高度)+(1−similarity)`；成员不一致则显示 unavailable。PNG 以 base64 `input_image`、`detail=high` 实际发送，另附采样率、窗口时长和单位定义；模型不直接读取 MAT。

## 4. 运行与评分

**Stepwise：** 离线回放专家日志，核对每步前后状态哈希，给模型提供操作前状态及指定 source/target/phase。模型预测不推进下一样本。CH3/30/31 参照配套 Excel，CH20 参照 MAT 内部动作日志。CH20 第 5 步 `s 0` 为从 noise 恢复分群，超出当前动作接口，预先排除评分但仍用于本地重建后续状态。共 294 例：DISCARD 158、SPLIT 126、MERGE 10；无 KEEP/NOT_MERGE 参考样本。动作完全相等计正确。

**Full rollout：** 从各 MAT 的 `hierarchy.assigns` 开始，不接续 stepwise 人工终态。程序遍历群，模型做决定：

1. Phase 1：KEEP 保留；DISCARD 设为 noise；SPLIT 撤销最后一次层次合并，子群继续判断。
2. Phase 1 后，小于 4000 spikes 的群作为 source，其余为 target 候选；不比较 small–small 配对。4000 是调度门槛，不是删除门槛。
3. 合并前复查大群：KEEP 进入候选，DISCARD 删除；**SPLIT 只保留并排除候选，没有执行拆分**，这是本次流程已知的动作语义问题。
4. 每个 source 依次与候选比较；MERGE 合入目标，DISCARD 删除 source，NOT_MERGE 继续下一候选；无目标或全部拒绝时保留 source。旧 500/5000 数量自动删除均关闭。

先完成 stepwise，再按 CH31→CH3→CH20→CH30 完成闭环。接口/解析错误或预算不足停止，不自动重试、不回退 mock。续跑核验历史响应、状态与请求哈希，仅从未完成处继续，不重复计费；每通道 10,000 次安全调用上限本轮未触发。

**终态评分：** 按同一 MAT 的 spike 行号与 `curation.assigns` 比较。每个预测群匹配重叠最多的非零专家群，允许 many-to-one；TP 为最佳重叠数之和，FP＝保留 spikes−TP，FN＝专家非零 spikes−TP。P=TP/(TP+FP)，R=TP/(TP+FN)，F1 为调和平均。初态按同一方法评分，通道间取简单平均。

## 5. 解释结果时的限制

- 四通道此前已用于开发，不是未接触的 held-out 测试；每项仅运行一次，未测重复稳定性。
- Stepwise 只测给定对象下的编辑动作，未覆盖 KEEP/NOT_MERGE，也不测自主目标选择。人工动作日志与 MAT 终态并非完全一致，两者分别作参照。
- Many-to-one 会低估过度碎分；没有采用一对一 unit matching 或时间容差匹配。高 Recall 不等于分群正确，专家标注也不是绝对生物学真值。
- 结果属于“模型＋观测＋controller”整体，不能仅归因于模型或 prompt。500 条波形抽样、独立纵轴和复查时不执行 SPLIT 都可能影响判断，尚未隔离验证其影响。
- CH30 的 1,330 次合并比较中，1,280 次为 NOT_MERGE，提示候选调度低效。此轮本身不能证明图像增量价值，也不能据此声称 SOTA。

## 6. 费用与复核入口

| 评测 | 推理 API 尝试数 | 标准价估算 USD | 保守账本 USD |
|---|---:|---:|---:|
| Stepwise | 294 | 10.43 | 12.08 |
| Full rollout | 2,754 | 94.28 | 107.87 |
| 合计 | 3,048 | 104.71 | 119.95 |

闭环含 2,753 次成功判断及 1 次 HTTP503；保守账本含 $0.2441875 未结算预留。金额为估算，不是官方账单。

结果：[完整 score.json（本地）](runs/minimal_astra_high_dual_20261007_resume150/score.json)、[汇总报告](results/minimal_astra_high_dual_20261007_resume150/REPORT.md)。代码：[运行器](../scripts/run/run_minimal_prompt_baseline.py)、[观测生成](../src/agent/curation_observation_v1.py)、[stepwise](../scripts/analysis/minimal_stepwise.py)、[controller](../src/pipeline/curation_engineering_v1.py)、[终态评分](../src/eval/metrics.py)。原始数据和运行文件不随本文上传。
