# Minimal-prompt VLM curation：方法与结果

实验日期：2026-10-07 至 2026-10-08。四通道 stepwise 与完整闭环均已完成。

以上完成状态及第 1–6 节结果仅指 Astra 历史实验。**2026-10-12 Qwen3.5-9B／27B 实验已启动，尚未全量完成**，计划和执行记录见第 7 节；Claude 四通道已完成，复核见第 8 节。后续优化候选路线见[主 README](../README.md#latest-baseline-review-and-optimization-routes--2026-10-12)，尚未实施，不覆盖历史记录。

## 1. 实验条件

模型：`gpt-6-astra`；reasoning：`high`；每次输出上限 4000 tokens；标准服务层。每项评测运行一次，无多次取优。所有通道共用相同的 [Phase 1 prompt](prompts/phase1.txt) 和 [Phase 2 prompt](prompts/phase2.txt)，输出 `action` 与简短 `rationale`。

输入为当前 cluster 的诊断图、数值和计算定义；不提供任务示例、专家评论、标准答案、RAG 或 teacher 反馈，未做任务微调，不传入前次对话。这是固定流程中的 **zero-shot prompting curation**，不是从原始电信号开始的端到端自主 spike sorting。

## 2. 结果

Stepwise 测“给定专家操作前状态，动作是否与专家一致”；full rollout 测“模型动作实际改变分群后，最终结果与专家标注的一致程度”。两者不能合成一个 accuracy。

### Full rollout：最终分群结果

四通道均完成完整闭环。以下保留历史 **many-to-one** 口径，比较最终 spike 分配与专家 MAT 终态，**不是每一步动作的 accuracy**。跨模型比较使用第 8 节的一对一重算，不混用两种分数。

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

**结论边界：** 低 Precision 的直接统计原因是参考噪声误保留，CH30 另有专家 unit 混群。prompt、图像/数值观测、controller，以及专家 noise 标准与模型理解的差异各有多少影响，尚未通过受控对照分离；不能认定某一项是唯一原因，也不能据此认定专家标签错误。案例证据见 [失败模式](../项目总文档.md#6-已知问题与失败模式)。

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

## 7. Qwen 双模型实验计划与执行记录（2026-10-12，未完成）

### 实验范围与“全量”定义

目标：在固定 minimal-prompt curation 流程中建立两个未做本项目微调的 VLM 起点，分别测专家动作一致性和自主闭环终态质量。拟通过 OpenRouter 调用 `qwen/qwen3.5-9b` 与 `qwen/qwen3.5-27b`，对应 Qwen3.5-9B／27B。

| 通道 | 完整 dataset ID | 每模型 stepwise 样本 | 每模型 full rollout |
|---|---|---:|---:|
| CH3 | `cM2-e004_004-006_CH3` | 97 | 1 次，从初态到终态 |
| CH20 | `cM2-e004_011-015_CH20` | 53 | 1 次，从初态到终态 |
| CH30 | `cM2-e008_021-028_CH30` | 90 | 1 次，从初态到终态 |
| CH31 | `cM2-e004_004-006_CH31` | 54 | 1 次，从初态到终态 |
| 每模型合计 | 4 通道 | 294 | 4 次 |
| 两模型合计 | 8 个模型×通道组合 | **588 个预测** | **8 次完整闭环** |

- 每项只跑 **1 次**，不多次取优；两模型的两项评测、四通道全部完成才叫“本轮全量完成”。全量不指全部 17 个数据集或约 26 GB 数据包；CH5、其他通道、模拟数据、reserved/quarantined blocks 均不纳入。
- Stepwise 沿用既有审计的 295 条日志：CH20 第 5 步 `s 0` 只用于本地重建后续专家状态，超出模型接口，预先排除评分，固定分母 294。共 DISCARD 158、SPLIT 126、MERGE 10；不补造 KEEP/NOT_MERGE。运行前复核来源和数量，变化则停；不因模型答错或解析失败删样本。
- **8 次 rollout 不等于 8 次 API 调用。** 每个闭环包含实际发生的全部 Phase 1、候选复查和 Phase 2 判断；次数随模型轨迹变化，不能按专家日志长度估计。历史 Astra 的 2,753 次成功判断不是 Qwen 调用量或费用保证。

### 模型输入与冻结条件

- 复用本目录两份 minimal prompt、`raw_member_v1` 观测、既有 MAT/初始分群；Phase 1 四图、Phase 2 三图，附数值及计算定义，输出 `action` + 简短 `rationale`。不重新做原始电信号检测/初始聚类，不修改 MAT。
- 不输入专家评论/理由、专家答案/终态、旧模型响应、few-shot、领域 skill、RAG 或 teacher 反馈。Stepwise 用专家轨迹重建输入状态但不显示答案；rollout 不用专家动作纠偏。
- 初稿拟关闭 thinking；后续讨论改为建议两模型均开启 thinking、输出上限 4000 tokens（包括 reasoning）。这是待 smoke 验证并冻结的设置，不是已完成实验条件；不能把 Qwen 的开关等同于 Astra 的 `high`，也不能把隐藏 reasoning 文本当作关闭 thinking。
- 优先选择两者均可验证的 BF16 端点，固定各自提供商，禁用 provider/model 自动 fallback。记录服务端可见版本、精度、参数/图像支持和价格；拿不到权重 revision 就写“提供商未公开”，不冒用 Hugging Face 哈希。若精度、schema、thinking 无法同时满足，先报告，不暗换 FP8 或弱化协议。
- 不顺手改 controller、画图、4000 调度门槛或添加通道专属删除保护；旧 500/5000 自动删除仍关闭。沿用第 4 节时须披露**候选复查 SPLIT 未执行**的已知问题；若决定修复，先独立版本化和离线验证再冻结，不能把与旧 Astra 的差异全归因于模型。此项仍为付费前核查关口，不写成已修复。
- 新模型各建独立运行目录，记录代码/配置/数据/输入哈希、完整响应、状态及费用；不覆盖历史 output、prompt 或 Astra 结果。

### 判断标准

1. **Stepwise：** 给定专家操作前状态、phase 和 source/target，动作完全相同算正确，预测不推进下一样本。报告每通道及合计 accuracy、逐类 P/R/F1、混淆矩阵、解析/拒答/截断和完成率。保留失败/缺失记录；未完成批次不冒充全量成绩。不评价没有标签的 KEEP/NOT_MERGE 或自主选对象能力。
2. **Full rollout：** 每次从该 MAT 的 `hierarchy.assigns` 独立开始，不从 stepwise 专家终态续跑；模型实际操作至正常终止，再与 `curation.assigns` 比较。预算、接口错误或安全上限停止均为未完成，检查点不是正式终态。
3. **一对一主评分（2026-10-12 已实现核心并离线测试，待接入全量收集）：** 用同一 MAT 的 spike 行号构造重叠矩阵，IoU 为权重，门槛 `IoU >= 0.5`；先将不合格边置零，再用 Hungarian 最大总权重匹配，只保留合格配对，每群至多匹配一次。这是共享 spike ID 的比较，不宣称已运行带时间容差的完整 SpikeInterface 比较。
   - **全输出口径：** TP＝合格匹配对重叠总数，FP＝全部预测非零 spikes−TP，FN＝全部专家非零 spikes−TP；报告 P/R/F1，未匹配预测群不能从分母消失。
   - **Unit 口径：** 列每个专家 unit 的匹配、IoU（unit accuracy）、P/R/F1；未匹配专家 unit 的评分记 0，最佳候选 IoU 另列。报告匹配数/专家 unit 总数及全部专家 unit 宏平均；仅配上者平均只能作明确标注的补充。
   - 同时报告初态/终态变化、最终群数、未匹配预测群数、纯参考噪声群数、误保留噪声 spikes、误删专家 spikes。“未匹配群”不等于“纯噪声群”；不使用专家答案事后删噪声再报高分。
4. 保留第 2 节历史 many-to-one 结果，不能直接与新一对一分数比较。可**离线重算**已有 Astra 初态/终态作同口径参照，不新增 Astra 推理；同口径仍不消除 thinking、模型和服务端差异。Stepwise 与 rollout 不合成一个 accuracy。

四通道已有开发曝光，不是 untouched held-out；CH3/CH31 还共享同一文件名 recording block，不能称四份已确认独立录音。后续 harness 泛化需另设录音级隔离评测。

### 执行步骤、费用与排除项

| 顺序 | 工作 | 是否推理付费 |
|---|---|---|
| 1 | 离线适配现有 Astra 专用运行器；验证四通道/294 状态、答案隔离、图片/schema、零重试、预算停止及恢复；实现并测试一对一评分，核查 controller，冻结 manifest | 否 |
| 2 | 真实图片 smoke：每模型 1 个 Phase 1 + 1 个 Phase 2，共 4 次；分别取 CH31 的首个 Phase 1、CH20 的首个合格 Phase 2 样本（CH31 无 Phase 2 标签），验证多图、thinking、解析、usage、耗时和费用 | 是，计入本轮 $25 |
| 3 | 全量 stepwise：9B、27B 各预测全部 294 状态一次，共 588 次，不因成绩临时改 prompt | 是 |
| 4 | 全量 rollout：依次 9B→27B，每模型按 CH31→CH3→CH20→CH30 独立完成四通道，共 8 次闭环；沿用历史顺序，不按成绩挑通道 | 是，调用量随轨迹变化 |
| 5 | 离线核验正常终态、请求/状态哈希和账本；汇总双模型 stepwise/终态/失败案例/延迟/费用，以及旧 Astra 同口径参照和条件差异 | 否 |

- Smoke 单独记账，不作性能结论，也不抵扣正式样本。本计划已知固定请求数为 **4 + 588 = 592**，另加 8 次 rollout 的实际判断请求；错误请求单列，不静默重发。
- 按模型分别记录 `smoke / stepwise / rollout` 费用、input/output/reasoning/cached tokens、实际提供商和费用来源。估算与实际返回费用/账单分开，未知消费不记成已确认的零。
- **用户已确认本轮独立总预算 $25，不继承旧 $150 或其他任务额度。** 当前指令仅验证接入，不启动全量实验。每次推理前预留费用；接口/截断/解析/预算异常停止并通知，保留可核验断点，不回退 mock、不暗换模型；完成也通知。
- 不包含 SFT/QLoRA/RL、thinking A/B、新 skill 调优、图像消融、其他模型或其他数据；这些必须独立立项，不混入“全量”。

**首次接入检查（2026-10-12，历史状态）：账户鉴权通过，当时图片推理未执行；后续 smoke 结果见本节末尾。**

- 已修复 `.env` 中密钥仅存在于注释、未配置 `OPENROUTER_API_KEY` 的问题；未显示密钥，`.env` 被 Git 忽略且未跟踪。
- 官方只读 `/key`、`/credits` 均返回 HTTP 200；总充值 $25、已用 $0。密钥未设独立额度限制，不能用它代替本地预算保护。
- 9B：`deepinfra/bf16` 声明支持图像模型的 `structured_outputs`、`response_format` 和 reasoning；尚未实测图片响应。
- 27B：当前唯一列出的 BF16 端点为 `novita/bf16`，列出 `response_format` 和 reasoning，但未列出 `structured_outputs`。[Novita 模型页](https://novita.ai/models-console/model-detail/qwen-qwen3.5-27b)声称支持 Structured Output；与 OpenRouter 能力目录不一致，不能据此确认严格 JSON schema 兼容。依据 [OpenRouter 说明](https://openrouter.ai/docs/guides/features/structured-outputs)，须按具体端点核查，不能只看模型名称。
- 因上述兼容性待确认，停止在付费 smoke 之前；**推理请求 0、推理费用 $0**，没有改为 FP8、放宽 schema 或启用 fallback。完整运行器/评分验证尚未全部实现；账户接入成功不等于双模型实验就绪。

### 2026-10-12 后续实施：先 baseline，再优化 harness

- 用户确认顺序：先完成两个模型的 minimal-prompt stepwise + rollout；之后另立 harness 优化版本，冻结后让两个模型使用同一版复测。当前 $25 只属于 baseline（含 smoke），不自动授权后续优化实验。
- 已有 harness：两阶段调度、观测/画图、动作执行、状态检查点、预算与解析保护；已完成过 Astra minimal 四通道。system/skill 分离及 v0/v1、S1–S5、画法 A/B 已实施，但没有稳定改善证据，保留 v0。**这些领域 skill 不加载到当前 minimal baseline。**
- 未完成：Qwen 双模型 live 接入及全量运行器/恢复测试；一对一评分接入结果收集；自动反馈→更新 skill→验证→接受/回退的端到端学习循环。工具自由调度、SFT/RL 不作为本轮已完成内容。
- 先保存 baseline，后续仅在开发数据调 skill；冻结 system/动作接口和优化边界。若需修 controller，独立版本化并在相同修复版上保留 minimal 对照，不能把 controller 与 skill 联合改变的收益全归因于 skill。四通道均已曝光，复测是开发集结果；泛化另需未参与优化的 recording。
- 新入口：`scripts/run/run_openrouter_smoke.py`，默认离线；每模型使用 CH31 step 1（四图）和 CH20 step 37（Phase 2 三图）。复用已冻结 prompt/数值/PNG，逐张验哈希，不打开专家答案文件。仅 API 封装改变；BF16、thinking=true、max_tokens=4000、严格 schema、无重试/fallback；采样参数使用提供商默认，不能声称与 Astra 完全相同。
- dry-run：`zero-shot baseline/runs/openrouter_smoke_dry_20261012_v1/`，4 份请求均已校验；manifest SHA256 `ac9cc852067161cf985ecdefc15164fa501fd6a61e444830fbb9afba9c2ec35d`。不保存 base64 到请求记录。
- smoke 费用上限 $0.50，包含在 $25 内；每次按端点整个上下文容量加 4000 输出上限保守预留，按返回 cost 结算。失败且未知收费时保留预留，不记成免费；无自动续跑。
- 评分核心：`scripts/analysis/curation_one_to_one.py`；预注册规则：`configs/curation/one_to_one_scoring_v1.json`，SHA256 `dc4085582f1e92846afe63405e80beee12b568837bbe98f05f551364de3b79a5`。覆盖拆碎、混群、未匹配噪声、门槛边界、全删除及无效标签测试；不改历史 many-to-one 表格。
- 验证：`python -m pytest tests/test_openrouter_smoke.py tests/test_curation_one_to_one.py tests/test_minimal_prompt_baseline.py tests/test_curation_engineering_v1.py -q` → **76 passed**。这些是离线工程测试，不是模型成绩。
- **付费执行被安全审批在进程启动前拦下：尚缺真实诊断图/数值向 OpenRouter 及下游 DeepInfra/Novita 外发的明确许可。没有推理请求、没有费用，也没有创建 live 运行结果。** 获许可后才做最多四次 smoke；兼容性、截断或解析失败立即停，不弱化协议。smoke 通过并完成全量运行器/评分/恢复验证后才启动 588 个 stepwise 与 8 次 rollout。

### 2026-10-12 已获外发许可后的 smoke 结果（当前停止点）

用户明确允许 CH3/20/30/31 诊断图及数值发往 OpenRouter、DeepInfra、Novita，不发送专家答案/评论；总预算 $25 不变。已执行独立 smoke，没有启动正式 stepwise 或 rollout。

| 模型/端点 | 样本 | 状态 | 返回动作 | Input / Output tokens | 其中 reasoning tokens | 返回费用 USD |
|---|---|---|---|---:|---:|---:|
| Qwen3.5-9B / DeepInfra BF16 | CH31 step 1，Phase 1 四图 | 通过；14.30 s | KEEP | 1964 / 850 | 818 | 0.00032390 |
| Qwen3.5-9B / DeepInfra BF16 | CH20 step 37，Phase 2 三图 | 通过；59.33 s | MERGE | 1615 / 2013 | 1800 | 0.00046345 |
| Qwen3.5-27B / Novita BF16 | CH31 step 1，Phase 1 四图 | 路由 HTTP 404 | 无 | 无 | 无 | 未返回 usage |
| Qwen3.5-27B / Novita BF16 | CH20 step 37，Phase 2 三图 | 未发送 | — | — | — | — |

- 原始记录：`zero-shot baseline/runs/openrouter_smoke_20261012_v1/`（Git 忽略），包含请求哈希、端点目录、原始响应、决策、ledger 与 summary。两次 9B 响应均为 `finish_reason=stop`，通过本地 schema/动作/提供商核验，有 thinking 证据；这证明请求链路可用，不证明动作正确或图像有增量价值。
- 27B 错误为 `No endpoints found that can handle the requested parameters`，`failed_routing_step=Filter by Parameters`；固定 Novita 后有一个端点，但未通过参数过滤。结合目录缺少 `structured_outputs`，严格 schema 是首要兼容性疑点；错误未逐项列出参数，不能声称服务端已确认唯一原因。没有自动放宽 schema、改成 FP8 或切换提供商。
- 3 次 HTTP 推理端点请求、2 次有效响应；两次响应已知费用合计 **$0.00078735**。失败请求保留 **$0.09706752** 未结算预留（不是已花费用），原始账本保守占用合计 $0.09785487。紧接着只读账户接口仅显示 $0.00032390，尚未反映第二次成功调用；因此不拿延迟账单宣称失败请求已确认免费。
- 全量离线输入核验另已通过：294 状态、588 份双模型请求，逐张图片和 payload 哈希一致；CH31/CH3/CH20/CH30=54/97/53/90，Phase 1/2=284/10。配对请求哈希列表摘要 `436c4e13d7860a999f38a19efa2cc7b0572fe24cfa8403c550b913e6a19e9f1f`。没有读取答案用于请求构建，没有额外推理。
- 回归复测 **76 passed**。两个模型尚未同时通过 smoke；全量 OpenRouter 运行器、恢复、评分收集仍待完成。暂停付费调用，先决定如何处理 27B 端点条件；改变精度或输出协议必须明确记录并获用户确认，不能悄悄更改 baseline。
- 后续终态报告采用 **one-to-one 主结果 + 旧 many-to-one 补充结果**，同一份终态离线重算，不增加模型调用；不是 many-to-many。Stepwise accuracy 单独报告。

### 2026-10-12 FP8 获批、接入通过与正式实验启动

用户同意 27B 使用 FP8。明确改为 `qwen/qwen3.5-27b` / `deepinfra/fp8`；9B 保持 `deepinfra/bf16`。不是自动 fallback；没有重复 9B smoke，也没有修改 prompt/图片/数值/schema/thinking/4000 上限。跨模型结果包含精度差异，不能只归因于参数规模；后续 harness 对照须保留各自部署条件。

| 27B FP8 smoke | 动作 | Input / Output tokens | Reasoning tokens | 时间 s | 费用 USD |
|---|---|---:|---:|---:|---:|
| CH31 step 1，Phase 1 | KEEP | 1968 / 955 | 861 | 28.69 | 0.00299468 |
| CH20 step 37，Phase 2 | MERGE | 1618 / 833 | 717 | 24.54 | 0.00258648 |

- 两次均 HTTP 200、正常结束、通过严格本地解析及提供商核验。目录：`runs/openrouter_smoke_27b_fp8_20261012_v2/`。新增费用 $0.00558116，全部成功 smoke 已知费用 $0.00636851；旧失败 $0.09706752 保守预留仍保留。本轮不把 smoke 动作当成准确率成绩。
- **旧失败结论：** Novita BF16 端点被 OpenRouter 的参数过滤拒绝，尚未到模型执行阶段。没有证据指向余额不足、图片损坏或模型动作错误；同一模型的 DeepInfra FP8 端点接受了相同内容和输出要求。精度与结构化输出支持是两件事，不能说“BF16 不支持 JSON”。结合官方端点参数目录，严格 schema 是首要疑点，旧错误未逐项确认，故不宣称已排除所有服务端因素。
- **另查出的接入风险：** Astra 专用运行器的 Responses 封装、token 计数和价格不能直接用于 OpenRouter；现已新增独立入口。4000 tokens 包含 thinking，正式样本仍可能截断；若出现，记录并停止，不擅自加上限。API 默认采样、服务端图像处理、9B BF16/27B FP8 都是对照限制。控制器复查 SPLIT 不执行是既有算法限制，不是此次 404 原因。
- 新入口：`scripts/run/run_openrouter_baseline.py`。默认 dry-run；共用 $25 总额，包含全部 smoke 成功消费和旧失败预留。按模型/stage/dataset 保存调用标签、原始响应、usage/cost、图像及请求哈希、操作前后 checkpoint。拒绝解析失败/截断、重试、fallback、隐式 JSON 修复。
- 恢复：显式 `--resume` 从初态以已保存响应本地重放，并校验请求顺序/内容/响应哈希；已成功响应不再付费调用。存在失败或未知状态的请求则拒绝自动续发，需先核查；不会跳过失败样本。partial 结果不作为完整 accuracy 或终态。
- 一对一主评分、旧多对一补充评分、no-curation 初态及误删专家 spikes 记录已接入新收集流程；同一预测只在本地重复评分。stepwise 分别报告完整分母、覆盖率、逐类指标及错误用例理由。没有专家答案或评论进入模型请求。
- 离线测试：`python -m pytest tests/test_openrouter_baseline.py tests/test_openrouter_smoke.py tests/test_curation_one_to_one.py tests/test_minimal_prompt_baseline.py tests/test_curation_engineering_v1.py -q` → **89 passed**；全量 dry-run **588 请求校验通过**。
- 正式目录：`runs/openrouter_qwen_dual_20261012_v1/`；manifest SHA256 `f8ce7c4d6eb434a5230758ab89d51949f1346bc5e31be7db9aad4a60065668c9`，包含代码快照、数据/配置/评分哈希。执行命令：`python -u scripts/run/run_openrouter_baseline.py --output-root 'zero-shot baseline/runs/openrouter_qwen_dual_20261012_v1' --execute --budget-usd 25`。已启动，先 9B/27B 各 294 个 stepwise，再各 CH31→CH3→CH20→CH30 rollout；**启动不等于完成**，最终状态以 `attempt_*/summary.json` 为准，调用明细位于 `calls/`。

### 2026-10-12：截断核查与用户明确要求的续跑

- v1 在 9B 的第 3 个正式 stepwise（CH31_000003）停止：HTTP 200，但 `finish_reason=length`；输出 4000 tokens，其中 reasoning 3328。前两个响应有效；27B 正式样本及 rollout 尚未开始。不是余额不足，也不作为动作判断错误；截断样本保持失败记录。
- 停止时正式请求已知费用 $0.00171230，连同 smoke 已知费用 $0.00808081；另有旧 404 未结算预留 $0.09706752，保守累计 $0.10514833。预留不是已消费。
- 用户要求继续后，新增离线 `--retry-truncated-from`：仅允许人工确认后对末尾、HTTP 200、有结算费用的 `length` 失败重试一次；不自动重试，不允许越过未知费用/传输失败。新目录继承失败费用并复用成功响应；保留父目录和全部原始失败数据，父子 manifest/请求/响应哈希可追溯。
- 此次不改 prompt、图片、数值、controller、模型/端点、thinking 或 **4000-token 上限**。第二次仍截断则停止，不能反复抽样直到成功；发生过人工重试必须在结果中披露。
- 新目录 `runs/openrouter_qwen_dual_20261012_retry_length_v2/`；manifest SHA256 `e83d8ea3b6560e86a4f29186d2e4e41a3d90a77e2bbe7afd1844cbe4eacff39b`。**93 项离线测试、588 请求预检通过**，随后显式启动 `--execute --budget-usd 25`；不是新增 $25，仍为本轮累计上限。最新状态以新目录 `attempt_*/summary.json` 为准。

### 2026-10-12：8000-token 正式版本（取代 4000 试跑，不覆盖旧记录）

- 4000 版本续跑结果：第 3 样本重试成功（1057 输出 tokens）；第 4 样本再次截断（4000 输出，其中 reasoning 3601）并停止。共 3 个有效预测，27B 正式样本与 rollout 未开始。第 3 样本有人工重试，不能当作一次尝试 baseline。旧费用及记录均保留。
- 用户批准 **8000**（不是 8192）。两个模型统一总输出上限，仍开启 thinking；9B/DeepInfra BF16、27B/DeepInfra FP8，prompt、图像/数值、schema、controller、数据和评分规则不变。不改树图或加入专家答案/评论、skill、示例、反馈。Astra 历史上限为 4000，不能宣称全部推理配置相同。
- 新版从头评测，每模型 294 个 stepwise + 4 个 rollout：**588 个 stepwise、8 个 rollout 尝试**。不复用 4000 版本的成功答案。每样本仅一次调用；同一版本断点恢复只在本地重放保存的有效响应或截断失败，不重新抽样。
- **失败规则预先固定：** HTTP 200、模型/提供商正确、usage 费用已结算的 `length`：stepwise 记录失败且不补动作，继续下一独立样本；rollout 保存 `stopped.npz`，停止该通道、不产生终态成绩，继续下一通道。接口/传输/解析/计费/数据完整性错误仍停止全批，不自动重试或 fallback。8000 版本单次 HTTP 读取超时提高到 600 秒；超时未知费用保留预留，不自动补发。
- **Stepwise 统计：** 报告 planned/attempted/valid/failed/unattempted、有效覆盖率、valid-only accuracy、correct/planned。只有全部样本均已尝试才给完整分母 accuracy；截断计非正确但不填 KEEP/DISCARD 或混入动作混淆矩阵，另列失败类别支持数。Rollout 只评分真正完成者；停止通道单列，不能把其余通道均值冒充全部完成。
- **预算与分账：** 本轮总上限仍 $25；继承旧累计 $0.10630138，其中已知费用 $0.00923386，旧 404 未结算预留 $0.09706752。每次按完整上下文 + 8000 输出保守预留。新费用在 `attempt_*/cost_breakdown.json` 按模型及 stepwise/rollout 分开统计；旧试跑不混入新版成绩，但费用不遗漏。
- **验证与执行：** 103 项离线测试通过（包括截断后继续、通道停止后继续、失败恢复不重发、预算和计费）；588 请求 dry-run 通过。新目录 `runs/openrouter_qwen_dual_20261012_8000_v3/`，manifest SHA256 `54367f5ea62bb0a326210c136fcd764f831d76249eba473ada1d1813da17d97e`。已启动，不等于完成；状态以 `attempt_*/summary.json` 为准，过程中查看 `stepwise_progress.jsonl` 与 `calls/`。结束后生成 `score.json`、`REPORT.md`、费用分账。

```bash
python scripts/run/run_openrouter_baseline.py --output-root 'zero-shot baseline/runs/openrouter_qwen_dual_20261012_8000_v3' --max-output-tokens 8000 --cost-history-from 'zero-shot baseline/runs/openrouter_qwen_dual_20261012_retry_length_v2'
python -u scripts/run/run_openrouter_baseline.py --output-root 'zero-shot baseline/runs/openrouter_qwen_dual_20261012_8000_v3' --execute --budget-usd 25
```

第一条仅用于首次离线创建新目录，不对已有目录重复执行。运行中的配置与代码不可修改。

### 2026-10-12：8000 版本上游 429 停止与显式恢复

- v3 第 16 次请求 HTTP 429：DeepInfra 返回 `engine_overloaded`、`upstream_provider_shared_pool`，未返回 usage。程序保存原始错误并因费用未知停止；不是余额不足、token 截断或本地断连。停止时 9B 有 12 个有效预测、3 个已结算截断；27B 和 rollout 未开始。
- v3 已知调用费用 $0.01014990；本轮累计已知费用 $0.01938376。另保留旧 404 预留 $0.09706752 和本次 429 预留 $0.03015584；累计费用及预留 $0.14660712。未知预留不宣称已消费，也不假定为免费。
- 用户明确要求继续后，使用 `--retry-rate-limited-from` 创建续跑目录：前 15 个结果本地回放，12 个有效结果不重发、3 个截断仍是失败；只重试被 429 拒绝的第 16 个请求。不切换提供商、不改输入/8000/评分，不设自动重试。HTTP 429 基础设施恢复记录单列，不能抹掉原始失败；若再次报错仍停止。
- 恢复路径 `runs/openrouter_qwen_dual_20261012_8000_resume429_v4/`；manifest SHA256 `d031a127dc4d74fd4b3a18ff9b96160cf7d899afdba84841c75c7783505d07c8`。108 项离线测试、588 请求哈希核验通过；恢复前费用核对为 $0.14660712，总上限仍 $25。已启动，最终状态以该目录 `attempt_*/summary.json` 为准。

```bash
python scripts/run/run_openrouter_baseline.py --output-root 'zero-shot baseline/runs/openrouter_qwen_dual_20261012_8000_resume429_v4' --max-output-tokens 8000 --retry-rate-limited-from 'zero-shot baseline/runs/openrouter_qwen_dual_20261012_8000_v3'
python -u scripts/run/run_openrouter_baseline.py --output-root 'zero-shot baseline/runs/openrouter_qwen_dual_20261012_8000_resume429_v4' --execute --budget-usd 25
```

### 2026-10-12：临时过载的有限重试（v5）

- v4 在 CH20 `000019` 再遇 HTTP 429，原始信息为 DeepInfra `engine_overloaded / upstream_provider_shared_pool`，不是余额不足。此前 CH31 54/54、CH3 97/97、CH20 17/53 已取得结果：133 个有效、35 个截断；27B 和 rollout 未开始。
- 修正运行器先检查费用、掩盖 HTTP 错误的问题。用户同意后，仅对明确的上述 429（没有 choices/usage）启用同请求最多 3 次重试，等待至少 30/60/120 秒；遵守可解析的 `Retry-After`，要求等待超过 300 秒则停止。其他接口错误、超时、解析失败不重试，截断处理不变；不改端点、prompt、图像/数值、8000 上限、controller 或评分。
- 新目录 `runs/openrouter_qwen_dual_20261012_8000_retry429_v5/` 复用全部 168 个有效/截断记录，仅从被拒绝的 CH20 `000019` 继续。每个 429 的原始响应和等待记录留在对应 `calls/*/overload_attempts/`；未知费用保留预留，每次重试另行检查预算。不把重试次数算成新评测样本，不重试直到答案正确。
- 累计预算仍 **$25**。恢复前已知费用 **$0.13332886**（含旧试跑/smoke），未知预留 **$0.15737920**，合计预算占用 **$0.29070806**；预留不等于已消费。按模型和 stepwise/rollout 分账，429 HTTP 次数单列。
- **122 项离线测试通过，588 个请求哈希核验通过**，dry-run 未联网。manifest SHA256：`9cc349f59156e1316d501e47ac016bab4ad447f524add3a41423599b70e0bdb0`。这只验证执行机制，不表示模型效果通过；执行状态以新目录的 progress/summary 为准。

```bash
python scripts/run/run_openrouter_baseline.py --output-root 'zero-shot baseline/runs/openrouter_qwen_dual_20261012_8000_retry429_v5' --max-output-tokens 8000 --retry-rate-limited-from 'zero-shot baseline/runs/openrouter_qwen_dual_20261012_8000_resume429_v4' --retry-overload
python -u scripts/run/run_openrouter_baseline.py --output-root 'zero-shot baseline/runs/openrouter_qwen_dual_20261012_8000_retry429_v5' --execute --budget-usd 25
```

第一条只用于离线创建新目录，不对已有目录重复执行。执行时重试策略从冻结 manifest 读取，不在命令行临时改变。

## 8. Claude baseline 复核（2026-10-12）

**结论：可保留为同任务的闭源模型 baseline；平均表现更好，但不能称为可靠或全面优于 Astra。** 本节只读复核已有结果，没有新增推理。原始 [Claude 报告](results/minimal_opus55_high_dual_20261012_v3_full/REPORT.md)与 [Astra 一对一重算](results/minimal_astra_high_dual_20261007_resume150/terminal_scores_20261012/TERMINAL_SCORES.md)保留不改。

- 条件：`claude-opus-5-5`，`high`、adaptive thinking，8000 总输出上限；同四通道、同 minimal prompt、数值定义、图片顺序、动作 schema、controller；不发送专家答案/评论，没有 skill、示例或反馈。Astra 为 `gpt-6-astra/high`、4000 上限。两家的 high、图像处理和采样不能视为等价算力设置。
- 核验：294 组 stepwise 的 prompt、数值文本、schema、初态及 **1,166 张图片哈希**逐项匹配 Astra；共用代码哈希一致。4 个 MAT 哈希与注册记录一致，终态哈希及离线重新计算的初态/终态分数全部一致；**29 项离线测试通过**。
- 执行：294 stepwise + 797 rollout 决策＝**1,091 次**；本次续跑实际新调用 1,071 次，另 20 个 pilot 响应本地复用，费用未重复计入。1,091 个保存响应均正常结束，**0 截断、0 重试**，最大输出 1,334 tokens。

### 同口径比较

Rollout 使用预注册一对一：IoU≥0.5、Hungarian 匹配；全部预测非零 spikes 计入分母，未匹配预测群不能删掉再打分。以下 F1 **不是**旧 many-to-one 数值，也不是逐步 accuracy。

| 通道 | Astra stepwise | Claude stepwise | Astra rollout F1 | Claude rollout F1 | Claude 匹配专家 units | Claude 误删专家 spikes |
|---|---:|---:|---:|---:|---:|---:|
| CH31 | 14/54 | 24/54 | 0.4774 | 0.7169 | 3/3 | 0 |
| CH3 | 37/97 | 55/97 | 0.2497 | 0.4162 | 2/2 | 0 |
| CH20 | 19/53 | 28/53 | 0.3613 | 0.0000 | 0/1 | 17,857 |
| CH30 | 32/90 | 48/90 | 0.2031 | 0.6435 | 1/3 | 40,284 |
| 合计 / 通道宏平均 F1 | 102/294（34.69%） | 155/294（52.72%） | 0.3229 | 0.4442 | 6/9 | 58,141 |

Claude 专家动作召回：DISCARD **37/158＝23.42%**；SPLIT **113/126＝89.68%**；MERGE **5/10＝50%**。这些标签没有 KEEP/NOT_MERGE，且由专家提供操作对象，因此不是完整自主策略准确率。四通道都已被反复查看，不能作为未接触的泛化测试。

### 失败与项目意义

- **CH20：** rollout 第 5 次决策把 cluster 1 整体 DISCARD，删掉 50,729 个参考噪声 spikes，同时删掉 17,857/18,465 个专家 spikes（96.71%）。终态仍有 608 个专家 spikes，但最大候选 IoU 仅 0.0186，达不到匹配门槛，所以一对一 F1＝0；不是“请求失败/没有输出”。
- **CH30：** 第 6 次决策整体 DISCARD 一个含 325,968 spikes 的混合群：删除噪声 285,684，同时丢失 unit 2 的 28,823 和 unit 38 的 11,461 个 spikes；unit 31 的 50,821 个 spikes 保留并完全匹配。F1 提高不代表三个专家 units 都找到了。
- 两次响应理由都把低幅值、混杂群判断为 noise / multi-unit，而不是继续拆分。**已证实的是这两次动作造成终态丢失；模型理由是自述，不足以确定唯一根因。** Astra 主要误保留噪声，Claude 此次同时暴露了“大混合群过早删除”的风险，不能只用平均 F1 选择模型。
- 后续 harness 应把“纯噪声应删”与“混合群含可恢复 unit”分开验证，并同时约束噪声残留和专家 spike 损失；可研究延迟/可撤销删除，但须另立版本、固定开发/评测边界，不能回改 baseline 或用终态答案保护指定通道。

### 费用与协议注意事项

按保存 usage 和该运行登记价格估算：stepwise **$5.570216**，rollout **$15.375860**，合计 **$20.946076**；保守计费/预留 **$23.956375**。包含复用的 pilot 成本，不是服务商账单，也不包含其它独立试跑。

运行器实际实现了“截断自动重试一次”，而原报告末尾写了 `No automatic ... retry`，表述不一致；本次未触发，未影响上述成绩。后续跨模型实验应先统一失败/重试政策，不把重复抽样隐藏成一次尝试。本次仅记录问题，未修改冻结脚本或历史报告。

## 9. 2026-10-13：Qwen 9B 四通道 rollout 对照（本轮结束，8 个通道尝试均截断）

- **只跑 Qwen3.5-9B**，OpenRouter → DeepInfra BF16。顺序：minimal 的 CH31/CH3/CH20/CH30，再按同顺序运行冻结 `system_v1 + skill_v0`。不重新跑 stepwise，不启动 27B，不发送专家答案或评论。两组各一次，共 8 个计划中的完整闭环；截断可能使实际闭环不完整。
- **minimal 不是临时删掉 skill 的新 prompt。** 直接复用 Astra `minimal_astra_high_dual_20261007_v3` 的请求构造、两份 prompt、数值定义、四图/三图观测、初态与 controller；代码/配置核对通过，四通道首请求逐字节哈希与原 Astra 完全一致。完整轨迹会因模型动作而分叉，不声称每一步图片都相同。
- 两组都继承原 `CurationController.run/apply/checkpoint`，不修改自动过滤、4000 小群阈值、递归拆分或 Phase 2 调度。harness 组使用现有 contract，system、skill、数值文本排版一起改变；实际图片和数值计算一致，**这是提示组件整体对照，不是只改变 skill 的单因素消融，也没有运行 continuous learning**。
- 推理参数：thinking 开；`temperature=1.0, top_p=0.95, top_k=20, min_p=0.0, presence_penalty=1.5, repetition_penalty=1.0`，来自 [Qwen 官方通用思考配置](https://huggingface.co/Qwen/Qwen3.5-9B)。端点只读核验支持全部参数，`require_parameters=true`，禁止切换 provider。可减少重复，不保证减少截断；用户明确选择 **4000 总输出 tokens（含 thinking）**，没有采用官方 32768 的长度建议。Astra/high 与 Qwen thinking 不等价，Qwen 采样与服务端实现也不同；不是全套推理设置完全相同。
- 用户确认：`finish_reason=length` 即该通道 **未完成**，保存状态，不执行动作、不补 KEEP、不重试截断，转到下一通道。无终态 P/R/F1，不能填 F1=0。接口 429/502/503/504 无 choices、连接/超时或中断的在途请求最多尝试 4 次（间隔至少 30/60/120 秒）；每次保留实际费用或未知预留。已保存响应只本地回放、不重复付费。余额、解析、配置变化、预算耗尽或恢复次数耗尽停止并报告；不会无限重试。
- 两组合用原 **$25 累计上限**，各组分别记费用。最新旧账继承 **$0.46353701**，包括 v5 `attempt_002` 已有的 10 条 27B stepwise；这些旧记录不是本次启动，不混入本次 9B 指标。剩余可用额度约 $24.536463。已知服务商 cost 与未知预留分开，不将预留称为已消费。
- 评分：一对一 Hungarian、IoU ≥ 0.5、**全部输出**计入 precision 分母为主；历史多对一另列；只评分 completed 终态，未完成单独统计。本次没有新 stepwise accuracy；旧 8000/default-sampling 的 stepwise 不能当成本次 4000/显式采样下的结果。
- **118 项离线测试通过**（含 Astra 输入一致、只允许 9B、预算、截断后转通道、已保存响应恢复、未知费用保留、有限重试）。新入口 `scripts/run/run_qwen9b_rollout_comparison.py`，测试 `tests/test_qwen9b_rollout_comparison.py`；旧脚本、prompt、skill、配置和历史结果不改。
- 新目录：`runs/qwen9b_rollout_comparison_20261013_4000_v1/`。manifest SHA256：`bdc718bb7a07e3863785c5fe4be6e268c1e6e3f7510a2b25783a6b0ed5c573ae`。执行时看 `progress.json`，完成/停止后看 `execution_status.json`、`REPORT.md`、`results.json`、`cost_breakdown.json`；完整响应/重试在 `calls/`，图和状态在 `attempt_*/<arm>/<dataset>/`。所有原始运行产物不上传 Git。
- 已经发送真实请求：minimal CH31 第 1 步有效返回 SPLIT，provider cost $0.00048570；只证明本次参数、图片与响应接口可用，不代表该通道完成或截断率改善。后续进展以运行目录状态为准。

```bash
# 历史 4000 版本的执行命令（当时使用）。该实验已结束；当前入口已升级 8000/6000，不要对旧目录执行或 resume。
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -u scripts/run/run_qwen9b_rollout_comparison.py --output-root 'zero-shot baseline/runs/qwen9b_rollout_comparison_20261013_4000_v1' --execute --budget-usd 25
# 进程确实停止后才可恢复；先核验冻结配置，再本地回放响应，重建调度。
# 上述执行命令加 --resume；锁阻止同一目录的并发执行。
```

### 本轮结果与核验（2026-10-13）

**程序正常退出 `completed_with_failures`，不是 8 条完整 rollout 成功。** 两组各 4 个通道均在 Phase 1 触发 4000-token 截断，完成率均 0/4；没有终态 P/R/F1，也没有本轮 stepwise accuracy。按已确认规则，每次截断保存当前状态并转下一通道，没有补动作、重试截断或加大上限。

| 通道 | minimal：截断步数 | system_v1 + skill_v0：截断步数 | 终态评分 |
|---|---:|---:|---|
| CH31 | 2 | 5 | 两组均未完成，N/A |
| CH3 | 3 | 1 | 两组均未完成，N/A |
| CH20 | 5 | 1 | 两组均未完成，N/A |
| CH30 | 8 | 11 | 两组均未完成，N/A |

| 组别 | 请求数 | 有效动作 | 截断 | 输入 tokens | 输出 tokens（含 thinking） | 服务商报告费用 |
|---|---:|---:|---:|---:|---:|---:|
| minimal | 18 | 14 | 4 | 35,651 | 37,468 | $0.00918530 |
| system_v1 + skill_v0 | 18 | 14 | 4 | 48,630 | 41,531 | $0.01109265 |
| 合计 | 36 | 28 | 8 | 84,281 | 78,999 | **$0.02027795** |

- **失败机制已核实：** 36 次响应均 HTTP 200；8 次失败均 `finish_reason=length`、`completion_tokens=4000`。其中 6 次没有最终正文，2 次仅有不完整 JSON（调用 000010 / 000036）；均没有完整可执行的动作回答。不是网络、余额不足、解析器误删合法 JSON 或 controller 执行动作失败。没有任何重试或本轮未知扣费。
- **结果边界：** 本轮说明“官方通用 thinking 采样 + 4000 输出上限”在这四通道、两种提示组件下均不能稳定完成闭环。不能据此比较两组终态质量、宣称 skill 无效，或将未完成填成 F1=0；两组调用级截断比例都为 4/18，仅描述这些自适应且提前停止的轨迹，不是独立样本准确率。此前 8000/default-sampling 的 stepwise 不与本轮混算。
- **费用：** 新增 $0.02027795（服务商 cost 字段，非最终账单）；加继承旧账 $0.46353701，累计费用及旧预留 **$0.48381496 / $25**。新增费用全部已记录，无新未知预留；没有继续使用剩余预算启动实验。
- **离线复核通过：** 36 请求均为 9B、DeepInfra BF16、指定采样、4000 上限；8 个截断后的 snapshot 哈希均与失败请求之前的状态相同，无伪造 terminal 文件；输入、配置、代码与评分规则哈希保持冻结。核验摘要见运行目录 `postrun_verification.json`，原响应、失败标记、状态、费用均保留。
- **用量字段限制：** 18 个响应的服务商 `reasoning_tokens` 大于 `completion_tokens`，细分字段不自洽；保留原值，不用它精确估计 thinking 占比，也不把 reasoning 再加一次计费。36 次 cost 均与已记录的 input/output tokens × 固定单价一致。保存的 HTTP 等待累计约 61 分钟，不冒充完整端到端耗时。
- 当前已停止，无在途推理。本轮只得到有完整失败记录的闭环尝试，尚未得到可与 Astra/Opus 终态 F1 对比的 Qwen 9B 完整 baseline；后续如改长度或关闭 thinking，必须另立配置，不回改这些失败。

### 独立 thinking budget 接入探测（2026-10-13，已结束）

**未验证出可靠的硬性思考上限，暂不据此恢复四通道 rollout。** OpenRouter 模型目录的 Qwen 9B `reasoning` 仅有 `mandatory=false`，未声明 `supports_max_tokens`；DeepInfra BF16 端点声明支持 `reasoning`，不能由此推断每个子参数都会按硬上限执行。[OpenRouter 参数说明](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)／[DeepInfra 参数说明](https://docs.deepinfra.com/chat/reasoning)。

- 只重用上一轮 minimal CH31 第 2 步的**同一保存状态、四张 PNG、数值、prompt、schema、采样与 DeepInfra BF16 路由**，图片及原请求哈希核验通过。唯一请求差异是新增 `reasoning.max_tokens`；总 `max_tokens=4000`、thinking 开、`require_parameters=true` 不变。两次独立单请求，无重试、无 fallback、不提供专家答案、不执行返回动作，不计入 stepwise／rollout 成绩。
- 为避免依赖服务端不自洽的 reasoning 用量，使用官方 `Qwen/Qwen3.5-9B` tokenizer（revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`）独立统计返回的 reasoning 文本，`add_special_tokens=false`。这是返回文本的重新分词，不冒充服务端内部精确计数；tokenizer 与 `tokenizers==0.22.1` 仅放临时目录，未安装进项目环境、未下载模型权重。

| 请求 thinking 上限 | 独立计数：返回思考文本 | 服务端 reasoning / 总输出 tokens | 响应 | HTTP 耗时 | 费用 |
|---|---:|---:|---|---:|---:|
| 128 | **1,126** | 1,226 / 1,222 | HTTP 200，stop，有效动作 JSON | 148.1 秒 | $0.00038150 |
| 3,000 | 1,481 | 1,557 / 1,560 | HTTP 200，stop，有效动作 JSON | 229.5 秒 | $0.00043220 |

- **判定：** 128 明显没有被当作请求的硬上限执行；不能排除参数被忽略、转成 effort 或受到最低预算限制，不能单凭黑盒结果指定唯一原因。3,000 那次自然生成少于上限，属于“本次未超限，但未证明硬上限生效”，并不证明它能在更长推理时强制转入答案阶段。两次无截断也不能证明相对上一轮改善，因为请求各只有一次、采样随机、用例定向选择。
- **费用单列：** 新增 **$0.00081370**，无未知新预留；继承上一轮及旧账后累计费用／预留 **$0.48462866 / $25**。已停止，不自动增大输出长度、关闭 thinking、切换服务商或启动整批实验。
- 新入口 `scripts/run/probe_qwen_thinking_budget.py`（默认离线、`--execute` 每目录只发一次），测试 `tests/test_qwen_thinking_budget_probe.py`；9 项专属离线测试通过，与相邻运行器合计 47 项通过。结果分别在 `runs/qwen9b_thinking_budget_probe_20261013_128_v1/` 与 `runs/qwen9b_thinking_budget_probe_20261013_3000_v1/`，含请求哈希、原响应、独立计数和费用；第二目录 `REPORT.md` 汇总结论。

### 新配置：8000 总输出 / 请求 6000 thinking（2026-10-13，先离线验证，后授权启动）

- 用户指定后，`scripts/run/run_qwen9b_rollout_comparison.py` 的两组请求统一改为 `max_tokens=8000`、`reasoning={enabled:true, exclude:false, max_tokens:6000}`；仅 Qwen3.5-9B / DeepInfra BF16。**6000 是请求参数，硬上限执行仍未验证，不能宣称保证预留 2000 tokens 给答案。**
- 原 minimal prompt、system_v1/skill_v0、图像和数值、采样、controller、评分不变；controller 的 **4000 spikes** 大小群划分阈值不改。Astra 历史仍是 4000 总输出，不能将新配置写成与 Astra 推理预算相同。
- 总预算仍沿用 $25；每次预留按整个上下文加 **8000 输出**计，$0.03015584。已有费用／预留截至两次探测为 $0.48462866，后续准备正式运行时须完整继承，不从旧的 $0.46353701 重置。
- 失败政策未变：达到总输出上限而截断，停止该通道、保留断点、转下一通道；不补动作、不跳过该群继续同一通道、不自动重试截断。
- manifest 新版本 `qwen9b-rollout-comparison-8000-thinking6000-v2` 记录两个参数及 `thinking_budget_enforcement=unverified`；拒绝用当前入口恢复旧 4000 目录。旧结果、输入、费用、冻结代码快照全部保留，新实验需新目录，不能混用旧成功响应。
- **配置修改时仅做离线验证，没有发送推理请求。** 当时 token 参数拒绝、8000 费用预留、旧 manifest 拒绝恢复等 **131 项回归测试通过**；另用 CH31 已保存真实图像／数值离线组装请求，确认仅总输出和 thinking 预算两处改变，历史 manifest 未变。

#### 同日续跑：用户授权“再跑”后已启动

- 顺序：minimal CH31 → CH3 → CH20 → CH30，再同顺序 `system_v1 + skill_v0`，共 8 个 rollout 尝试，只跑 9B。不重用旧模型答案，不新增 stepwise。
- 补齐只读费用继承：旧运行的实际费用／未知预留 + 4000 轮次 + 两次 thinking 探测，去重核对 **$0.48462866**；新请求每次预留 $0.03015584，仍在原 $25 累计上限内。**136 项离线回归测试通过**，包括旧预留保留、探测不重复计费、历史账变动或仍在运行时拒绝启动。
- 4 个通道的 Astra 原始首请求哈希均核验一致；生成 8 份 Qwen 请求预览。只保证 minimal 的提示词与输入复用，不声称与 Astra 的模型／生成参数一致。
- 新目录：[`runs/qwen9b_rollout_comparison_20261013_8000_thinking6000_v2/`](runs/qwen9b_rollout_comparison_20261013_8000_thinking6000_v2/)。manifest SHA256：`22ebb8365645d43b8d34653754056dd1ab5c871cf53b92af7483616aec0edec8`。代码已冻结为快照；旧目录不修改。
- 启动记录：`attempt_001` 的 minimal CH31 第 1 步已有效返回 SPLIT，费用 $0.00033615，正在请求第 2 步，尚无终态成绩。实时状态看 `progress.json`，停止／结束看 `execution_status.json` 与 `REPORT.md`；响应、费用和两组状态分别保存。**6000 thinking 硬上限仍未证实**，不将请求成功等同于硬限制生效。

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -u scripts/run/run_qwen9b_rollout_comparison.py --output-root 'zero-shot baseline/runs/qwen9b_rollout_comparison_20261013_8000_thinking6000_v2' --execute --budget-usd 25
# 仅在确认进程停止后恢复：同一命令追加 --resume。不要重复启动正在运行的任务。
```

#### 2026-10-13：CH31 第 22 步过载停止与显式恢复

- `attempt_001` 于美东 10:31 停止：minimal CH31 前 21 步有效（SPLIT 8 / KEEP 11 / DISCARD 2），第 22 步连续 4 次 HTTP 429；服务商明确报告 DeepInfra `engine_overloaded / upstream_provider_shared_pool`。没有截断，成功响应最大输出 4,383 tokens；尚无完整通道成绩，其他通道及 harness 未开始。
- 停止时本轮已知费用 $0.01065265，4 次错误因无 cost 字段保留 $0.12062336 未知预留；连同历史费用／预留占用 **$0.61590467 / $25**。未知预留不是已确认扣款。
- 用户“继续重试”后启用独立入口 `scripts/run/retry_qwen9b_transport.py`：只对第 22 步新增最多 4 次尝试（005–008），前 21 步仅本地回放并逐请求核验哈希。其后新动作仍最多 4 次传输尝试；截断仍停止该通道、转下一通道，不补动作。
- 原运行器、manifest、prompt、skill、观测、controller、评分与旧尝试不改；此次重试扩展记在同一目录的 `transport_recovery/round_001/`（授权依据、旧文件哈希、恢复脚本快照），重建轨迹存 `attempt_002/`。不得把恢复进程启动写成已完成 baseline。**145 项离线测试通过**，涵盖无网络检查、成功响应不重付费、旧预留保留、新重试上限、截断不重试、请求变化即停。

```bash
# 已执行本命令开启 round_001；不要在任务运行时再执行。
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -u scripts/run/retry_qwen9b_transport.py --output-root 'zero-shot baseline/runs/qwen9b_rollout_comparison_20261013_8000_thinking6000_v2' --execute
# 同一授权轮中断后恢复：上面命令加 --resume，不追加尝试额度。
```

#### 2026-10-14：CH31 终态结果与 CH3 恢复

- 上一执行轮 `attempt_002` 已完成 minimal CH31：67 次判断、25 次修改操作。一对一 Hungarian / IoU≥0.5、全部预测输出计分：**Precision 0.610861 / Recall 0.772833 / F1 0.682367**；16 个预测群中仅 1 个匹配专家 unit（共 3 个专家 unit）。误删专家 spikes 11,434，保留参考噪声 spikes 24,780；不能将“完成闭环”写成质量合格。
- CH3 前 11 步有效，第 12 步连续四次共享池过载 429，10 月 13 日美东 18:31 停止。该配置累计 78 个有效动作、18 次 HTTP 429、0 次长度截断；CH20/CH30 及 harness 四通道尚未开始。
- 本轮停止时已知费用 $0.03998375，未知预留 $0.54280512；含历史费用／预留 **$1.06741753 / $25**。预留不当作实际扣款。
- 用户 10 月 14 日要求继续后，39 项恢复／运行器离线测试重新通过，启动 `transport_recovery/round_002` / `attempt_003`：只对 CH3 第 12 步增加最多 4 次传输尝试，原 78 个成功动作本地回放；CH31 不重复调用模型。保持 9B、8000/请求6000、原 prompt/controller/评分及顺序，minimal 四通道后再 harness 四通道。最新实际执行位置以 `progress.json` 为准。

#### 2026-10-14：CH3 第 27 步执行失败，当前已停止

- `attempt_003` 于美东 11:34 停止：CH3 的 cluster 332 仅含 1 个初始子群（35 spikes），模型仍返回 SPLIT；controller 报 `No last merge to split`，保留失败前状态。不是截断或接口过载；没有代填 KEEP，也没有把未完成通道记为 F1=0。
- 当前仅 minimal CH31 完成，CH3 未完成；CH20、CH30 及本轮四通道 harness 尚未开始。累计 112 次 HTTP 尝试、94 份格式有效响应（其中最后一份动作不可执行）、18 次 429、0 次长度截断。
- 本轮已知费用 **$0.04836665**、未知费用预留 **$0.54280512**；含历史费用／预留合计 **$1.07580043 / $25**。未知预留不等于实际扣款。
- 现场：`attempt_003/minimal/cM2-e004_004-006_CH3/step_00027/request.json`、`calls/000094/decision.json` 和该通道 `stopped.npz`。这是动作可执行性问题，传输重试不能解决；下一步需先明确不可执行 SPLIT 的处理协议。本次仅归档状态，不改冻结代码、不重发请求。

#### 2026-10-14：范围收窄，仅续跑 minimal（用户授权）

- 用户确认“把 minimal 跑完就 ok”：保留 CH31 终态，将 CH3 第 27 步登记为 `stopped_invalid_action`（未完成、无终态分数），只继续 CH20 → CH30，之后停止。**取消本轮后续四通道 harness，不新增 stepwise 或重跑 CH31/CH3。**
- 新入口 `scripts/run/complete_qwen9b_minimal.py`；只改变批次停止规则：不可执行的叶子群 SPLIT 停止该通道、保留现场后转下一通道。截断政策不变；其他执行错误、解析失败、鉴权／预算／来源校验失败及传输重试用尽仍停止整批。不代填 KEEP，不重新抽取模型答案，不限制／修改模型动作 schema。
- 原 prompt、controller、图像／数值、8000/请求6000、官方采样、provider、评分和旧 manifest 均不改。授权修订、代码快照及原证据哈希另存同目录 `minimal_completion_v1/`；续跑从第 95 个判断开始，原 94 份响应不重复请求。累计费用／未知预留起点 **$1.07580043 / $25**，不清零旧费用。
- **160 项离线测试通过**，包含仅 minimal 调度、禁止 harness、旧响应免费复用、费用保留、不可执行 SPLIT 无代填动作、其他错误仍停止、证据篡改拒绝。真实断点 dry-run 核验通过，API 调用数为 0；本节记录授权与启动准备，不冒充已完成结果。

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -u scripts/run/complete_qwen9b_minimal.py --output-root 'zero-shot baseline/runs/qwen9b_rollout_comparison_20261013_8000_thinking6000_v2' --execute
# 中途退出后的恢复：仅在确认进程停止后加 --resume；不会追加已用尽的传输尝试额度。
# 不再用旧两组入口恢复，以免启动已取消的 harness。
```

- **本次实际续跑结果：已停止，未完成剩余通道。** `attempt_004` 的 CH20 第 1 步（全局判断 95）连续 4 次返回 HTTP 429，均为 DeepInfra `engine_overloaded / upstream_provider_shared_pool`；无模型判断、无动作执行，CH30 尚未开始，harness 仍为 0 次请求。不是输出截断或 CH3 的拆分错误。
- 服务商没有返回这 4 次错误的费用，因此保留新增未知预留 **$0.12062336**，不宣称已扣费或免费；本轮已知费用仍 $0.04836665，未知预留共 $0.66342848，含历史账总占用 **$1.19642379 / $25**。原响应／状态／已完成结果均保留。
- 当前剩余：minimal CH20、CH30；CH3 作为失败尝试归档，没有完整终态。此次有限重试已用尽，普通 `--resume` 不会增加重试额度；需等服务商恢复后另记有界传输恢复轮次，且只能调用 minimal 完成入口，不能恢复旧两组调度。
