# spike-sorting-agent 研究笔记

**最后更新**：2026-10-07

阅读约定：保留全部日期记录；旧条目的数据与计划按当时条件理解，错误由后续更正条目说明。当前方法和实验索引见 [项目方法与实验状态](项目方法与实验状态.md)；仅文末待办是当前执行清单。

## 项目目标

使用 waveform、ISI、aggregation tree 和质量指标，让 VLM agent 完成 spike sorting 后的 `KEEP`、`SPLIT`、`MERGE`、`DISCARD` 决策。

- **实测数据线**：CH3、CH20、CH30、CH31 MATLAB 数据。
- **模拟数据线**：MEArec + MountainSort5，用于可控 GT、trajectory、adaptation 和跨 setting 实验。

## Proposal 功能状态

| 方向 | 状态 | 当前判断 |
|---|---|---|
| 实测数据 VLM curation | 🟢 已实现 | 已有 GPT-4.1、GPT-5.1、baseline、消融和人工参考结果 |
| MEArec benchmark | 🟡 部分完成 | `setting_001` 已跑通；`setting_002/003` 未完成 |
| Expert-like action trajectory | 🟡 部分完成 | 旧 ch_017 无进展 SPLIT 已修复并重建；旧动作不作有效 GT |
| Teacher-student interaction | 🟡 部分完成 | Stage 3 已做 mock 全量和单通道真实 API 验证 |
| Few-shot adaptation | 🟡 部分完成 | 已生成小规模 dataset，尚无正式训练结果 |
| Memory-augmented curation | 🔵 未来规划 | README 的 long-term goal，尚未形成正式、统一的 curation 方法 |
| RAG 功能 | 🔵 部分状态 | legacy 实测脚本中已有原型；模拟 Stage 3 已接入 memory 生命周期，但尚无正式 no-RAG/RAG 评估 |
| Online RL | 🔵 未来规划 | README long-term goal，当前未实现 |
| Continual learning / heterogeneous labs | 🔵 未来规划 | 当前只有单 setting smoke test，尚无正式结论 |

> 状态按 proposal 的研究目标判断。仓库里存在原型代码或 smoke-test 产物，不等于该研究方向已经正式实现。

---

## 2026-08-11 — 模拟数据 Stage 1–2

### Stage 1：MEArec benchmark

- 完成 `setting_001` 20/20 channel 的模拟、MountainSort5 排序和 overclustering。
- 配置为低噪声、无 drift、无 overlap、strict teacher。
- `setting_002/003` 当时尚未完成。

### Stage 2：GT action trajectory

- 20 个 channel 共生成 234 条 GT action。
- `ch_015` 为 0 step：唯一 cluster 只有 459 spikes，低于 500 阈值。
- `ch_017` 出现重复 SPLIT：无法继续拆分时状态没有变化，但 trajectory 继续循环到 step 上限。
- ch_017 会污染后续 trajectory、adaptation 和 alignment，修复后需要重建下游产物。

---

## 2026-08-18 — 模拟数据 Stage 3–6

### Stage 3：Teacher-student trajectory

- `setting_001` 共运行 84 step；最近全量使用 mock student/teacher。
- mock student 固定输出 `KEEP`，因此总体 accuracy 不能作为模型结果。
- `ch_003` 真实 API 小测试运行 5 step，accuracy=3/5。
- 一个明确错误：模型把 0.55% ISI violation 判断为较低，而 strict teacher 阈值为 0.50%，正确动作应为 SPLIT。

### Stage 4：Few-shot adaptation

- 完成 12 train / 5 eval 的 dataset 构建。
- 只有 8 条训练样本，且全部为 `SPLIT`，多数 feedback 来自 mock teacher。
- 尚未训练模型。

### Stage 5：Alignment

- 当前保留 8 个 channel report，属于 mock smoke test。
- adapter 指定 5 个 eval channel，但 summary 聚合了 8 个旧 report，存在输出混合。

### Stage 6：Sweep

- 已生成 `setting_001` summary，验证了编排流程。
- `setting_002` 尚未有效生成。
- `setting_003` 只生成到 `ch_003`，高噪声条件下检出的 unit 很少。
- 当前不能支持跨 setting 结论。

---

## 2026-08-18 — RAG / Memory 状态

### Legacy 实测数据原型

实测 MATLAB 数据相关脚本中已有：

- continual JSONL memory；
- waveform/feature similarity retrieval；
- Phase 1/2 检索；
- no-RAG/RAG unit comparison；
- Qwen/Gemma backbone 对比脚本。

这些属于已有 RAG 原型，不代表 proposal 中的 Memory-augmented curation 已经形成正式方法或完成效果验证。

### 模拟 Stage 3 接入

当前 Stage 3 已加入 RAG 参数和 trajectory metadata，并产生过 smoke-test 产物：

- 84 条 memory entry；
- 84 个 trajectory step；
- 20 个通道中 19 个出现检索命中；
- 另有 3 条真实 API smoke-test memory。

但集成仍不完整：

- `--rag-overwrite-memory` 只被解析和保存，没有调用 `memory.clear()`；
- 全量验证使用固定输出 `KEEP` 的 mock student；
- 尚无同模型、同数据的正式 no-RAG/RAG 结果；
- 尚未证明 RAG 改善 action accuracy 或最终 sorting quality。

因此当前准确状态是：**legacy RAG 原型存在，模拟 Stage 3 部分接入；Memory-augmented curation 仍属于未来规划。**

---

## 2026-08-18 — ch_017 Stage 2 修复

- 在模拟 `src/actions/` 中增加动作后状态检查，不修改实测数据 pipeline。
- SPLIT 未改变 assignments 时立即停止，不再写入虚假的 canonical action。
- `trajectory_summary.json` 记录 `split_no_progress` 和 blocked action。
- validator 现在能识别旧 ch_017 的 200 条无进展 SPLIT。
- 临时目录复测 ch_017：0 条假 action，正确记录阻塞原因。
- 非回归复测 ch_000：`SPLIT → SPLIT → KEEP`，trajectory 有效。
- 现有 `output/setting_001` 未覆盖；全量 Stage 2–5 重建仍待执行。

---

## 2026-08-18 — Stage 2 重建与 Stage 5 汇总修复

- 旧 Stage 2 actions 已归档到 `output/setting_001/actions_archive_20260818/`。
- 已重建 `setting_001` 20 个通道的 Stage 2 输出：共 34 条有效 canonical actions。
- 19 个通道正常完成；ch_015 正常地以 `no_active_clusters` 结束；ch_017 以 `split_no_progress` 结束且不写入假 action。
- Stage 5 汇总现在只聚合本次指定的 eval channel，并记录实际纳入与缺失的 channel；不再混入旧 report。
- 尚未重建 Stage 3–5，因此现有 trajectory、adaptation 和 alignment 仍是修复前产物。

---

## 2026-08-18 — Stage 3 RAG 覆写语义补全

- 模拟 `TrajectoryRunner` 现在实际执行 `--rag-overwrite-memory`：启动一次 Stage 3 runner 时清空既有 JSONL memory。
- 清空发生在 batch runner 初始化时，而非每个 channel 前；同一批次内前序 channel 的案例仍可供后续 channel 检索。
- 未指定覆写时，既有持久化 memory 会被加载并复用。
- 已在临时目录完成覆写/保留两种回归检查；尚未重新运行 Stage 3，因此现有 RAG trajectory 与 memory 产物未改动。

---

## 2026-08-18 — setting_001 Stage 3–5 隔离 mock 重建

- 隔离输出：`output/stage3to5_mock_rebuild_20260818/`；其中 raw 仅链接到既有模拟数组，`output/setting_001` 未覆盖。
- Stage 3：20 个 channel、84 个 trajectory step；RAG memory 也是 84 条，确认覆写后没有重复累计。ch_015 为预期的 0 step。
- Stage 4：固定 split 为 12 train / 5 eval，仅得到 8 条非 KEEP SFT 样本；未训练 adapter。
- Stage 5：仅评估 ch_000、ch_002、ch_011、ch_014、ch_017；summary 无缺失或旧 report 混入。mock action accuracy 为 0.803，reasoning cosine 为 0.500。
- 这是流程验证，不构成模型、RAG 或 adaptation 效果结论。

---

## 2026-08-18 — ch_017 Stage 3/5 语义对齐与重建更正

- 发现先前 Stage 3/5 会将 ch_017 中不改变 assignments 的 SPLIT 作为有效 step；这与 Stage 2 的 `split_no_progress` 终止语义不一致。
- 现已让 Stage 3 和 Stage 5 共用 Stage 2 的 split 实现，并在动作后检查 assignments：无进展动作不写入 trajectory、RAG memory 或 eval prediction，并立即停止该 channel。
- 已重新覆盖隔离 mock 输出：Stage 3 现为 82 steps / 82 memory entries；ch_017 仅保留一个有效 KEEP，随后以 `split_no_progress` 结束。
- Stage 5 对 ch_017 也只评估该有效 KEEP；5 个 eval channel 的 mock summary 为 action accuracy 0.870、reasoning cosine 0.500。该数值仅因剔除了无效 step 而变化，不是模型提升证据。

---

## 2026-08-18 — 模拟动作覆盖审计

- 隔离 setting_001 的 canonical trajectory 为 KEEP 68、SPLIT 14、MERGE 0、DISCARD 0；12 个 train channel 的 Stage 4 监督样本只有 8 条 SPLIT。
- setting_002 尚未生成 raw；setting_003 只有 4 个 raw 且没有 actions，不能补足类别。
- 已加入可复现 coverage audit，分开报告 canonical 与 Stage 4（按设计排除 KEEP）的监督覆盖。
- 结论：当前数据不能训练或评估四类动作；需新增受控 setting，分别产生可执行的 under-cluster MERGE 与 noise/artifact DISCARD，再做 channel-level train/eval split。

---

## 2026-08-18 — 受控 MERGE/DISCARD 生成机制

- 现有 `artifact.type` 只记录 metadata，未实际注入 artifact；初始聚类也不会主动把同一 GT unit 分成两个 active cluster。这解释了 MERGE/DISCARD 长期缺失。
- 新增仅模拟可用、默认关闭的 controlled perturbations：将干净 cluster 拆为同 GT 的两个 label（产生 MERGE），并加入 GT=0 的非神经噪声 cluster（产生 DISCARD）。
- 新增 `setting_004_action_coverage`；单元测试已验证受控数据的 oracle 顺序为 DISCARD 后 MERGE。
- 启用 drift 的真实 MEArec smoke 首次暴露缺少 `preferred_dir` 参数，已修复；单 channel 重跑仍在进行，尚未产生可报告的真实 coverage 结果。

---

## 2026-08-18 — setting_004 smoke 更正

- 单 channel MEArec smoke 已停止，未生成可用 raw 或 Stage 2 结果。
- 修复 `preferred_dir` 后，当前 MEArec 版本继续要求 drift 参数 `angle_tol`；需先补齐完整 drift 参数再重试。

---

## 2026-08-18 — setting_002 Stage 1–2 隔离 smoke

- 补齐 MEArec 1.11 drift 所需的完整参数 schema 后，`setting_002` 单 channel Stage 1 成功完成：8 个 sorter units、2386 spikes。
- Stage 2 得到 `SPLIT → KEEP`，并以 `all_clusters_keep` 正常结束。
- 输出位于 `output/setting_002_smoke_20260818/`，未覆盖现有 `output/setting_002`；仅验证 drift 兼容性，尚未支持 setting_002 的全量结果或动作覆盖结论。

---

## 2026-08-18 — setting_002 Stage 1–2 断点扩展

- 已完成 ch_000–ch_004 的 Stage 1 与 Stage 2（5/20），均无轨迹验证警告。
- Stage 2 共 12 步：KEEP 5、SPLIT 7、MERGE 0、DISCARD 0；当前 medium-noise + drift 小样本仅覆盖 KEEP/SPLIT。
- 修正 coverage audit：优先读 Stage 3 trajectory；Stage 3 尚未运行时回退读取 Stage 2 canonical actions。此次报告已正确标记 5 个 channel 均来自 Stage 2。
- 这只是 setting_002 的中间审计；MERGE/DISCARD 覆盖仍不能据此下结论，也不改变 setting_004 为备用设计的优先级。

---

## 2026-08-18 — setting_002 MERGE 观察

- ch_005 的 Stage 2 为 `SPLIT × 3 → MERGE → KEEP`；说明现有 medium-noise + drift setting 可自然产生可执行 MERGE，并非只能依赖 setting_004 的受控扰动。
- 当前完成 6/20，canonical 覆盖更新为 KEEP 6、SPLIT 10、MERGE 1、DISCARD 0。
- 因此继续完成 setting_002 并观察 DISCARD；仅当既有 setting 无法产生所需覆盖时，再推进 setting_004。

---

## 2026-08-19 — setting_002 断点扩展

- ch_006 已完成 Stage 1–2：17 个 GT neurons、14 个初始 clusters；轨迹为 `SPLIT × 2 → KEEP`。
- 当前完成 7/20，canonical 覆盖为 KEEP 7、SPLIT 12、MERGE 1、DISCARD 0；仍需继续观察 DISCARD。

---

## 2026-08-19 — setting_002 真实 VLM Stage 3 smoke

- 在 `ch_006` 上以真实 `gpt-4.1` student 运行 no-RAG Stage 3；GT-aware teacher 仅提供反馈。
- 两个有效 decision 的 GT 均为 SPLIT，student 均预测 KEEP，因此 action accuracy 为 0.00；Stage 3 正常完成并落盘。
- 这只验证真实 API 调用、prompt/image 输入和轨迹写入链路；单个 2-step channel 不能作为模型性能结论，也未进行 RAG 或 adaptation 对照。

---

## Proposal 对应的后续方向

### Memory-augmented curation

先统一实测与模拟两条线的 memory schema、retrieval 和评估方法，再做固定 memory、无数据泄漏的 no-RAG/RAG 对照。正式指标需要同时报告 action-level 和最终 cluster-level 结果。

### Online RL

在稳定的 supervised/teacher-feedback baseline、动作空间和 reward 定义完成后再进入。当前不应提前宣称已有 Online RL。

### Continual learning / heterogeneous labs

完成多个 noise、drift、overlap 和 teacher-style setting 后，研究 memory 或 policy 能否跨 setting 迁移。当前单 setting 结果不足以支持该方向结论。

---

## 2026-08-18 — 当时待办（历史）

### 实测数据线

#### 高 → 低

- [ ] 获取 CH3、CH20、CH30、CH31 原始 MATLAB 数据和版本说明。
- [ ] 核对历史实验实际使用的模型、prompt 和配置。
- [ ] 重算统一的 per-channel 与 aggregate 指标，核对 CH31 不一致。
- [ ] 复现实测 GPT-5.1、no-metrics 和 baseline 结果。
- [ ] 验证 legacy RAG 原型，建立正式 no-RAG/RAG baseline。
- [ ] 在 baseline 稳定后推进 proposal 中的 Memory-augmented curation。

### 模拟数据线

#### 已完成

- [x] 修复 ch_017 无进展 SPLIT（2026-08-18）。
- [x] 确认新终止语义并重建 `setting_001` Stage 2（2026-08-18）。
- [x] 限制 Stage 5 汇总到本次 eval channel（2026-08-18）。
- [x] 补全 Stage 3 `rag_overwrite_memory` 的启动覆写语义（2026-08-18）。
- [x] 在隔离输出中以 mock 重建 `setting_001` Stage 3–5（2026-08-18）。
- [x] 对齐 ch_017 的 Stage 2、Stage 3 与 Stage 5 无进展 SPLIT 语义（2026-08-18）。
- [x] 审计 setting_001 的 canonical 与 Stage 4 动作覆盖（2026-08-18）。
- [x] 实现受控 MERGE/DISCARD 初始错误与 setting_004 配置（2026-08-18）。
- [x] 在 `setting_002/ch_006` 完成真实 gpt-4.1 no-RAG Stage 3 smoke（2026-08-19）。

#### 待办：高 → 低

- [x] 补齐 MEArec drift 参数兼容性，并通过 `setting_002` 单-channel Stage 1–2 smoke（2026-08-18）。
- [ ] 完整运行 `setting_002` Stage 1–2（当前 7/20），并审计 channel-level actions 与失败原因。
- [ ] 检查并修复高噪声下 MountainSort5 检出 unit 过少的问题，再完成 `setting_003` Stage 1–2。
- [ ] 在已完成的 setting 上重建 Stage 3–6；先用 mock 验证流程，再决定真实 model 运行范围。
- [ ] 固定 train-memory：仅写入 train channel 的 teacher/GT 案例，在完全隔离的 eval channel 上比较 no-RAG/RAG，并同时报告 action-level 与 cluster-level 指标。
- [ ] 使用真实 student model 做同条件 no-RAG/RAG 对照。
- [ ] 数据修复后再进行 Qwen/Gemma adaptation。
- [ ] Memory baseline 成熟后再设计 Online RL 和跨 setting continual learning。

#### 备用设计（非当前优先级）

- [ ] 完成 `setting_004` 单-channel Stage 1–2 smoke，并确认受控 MERGE/DISCARD coverage；仅在现有 setting 仍无法支持所需动作覆盖时推进。

---

## 2026-08-29 — 主线重定向：本地可训练 student agent

### 核心目标

训练并验证一个可本地/集群运行、权重可更新的 spike-sorting curation student agent。API VLM 降为 teacher、强 baseline 和少量复核工具，不再作为最终每步决策的唯一模型。

主线顺序：

1. 建立版本一致、类别覆盖充分、无泄漏的监督数据；
2. 选择一个开源视觉 backbone，以 LoRA/SFT 训练本地 student；
3. 在未见 setting/recording 上比较 base student、SFT student 与 API baseline；
4. baseline 稳定后做固定 train-memory 的 no-RAG/RAG 对照；
5. SFT、动作空间、reward 和评估稳定后才考虑 Online RL。

Qwen/Gemma 是仓库已有训练脚本支持的候选 baseline，并非 proposal 预先指定的最终模型。最终 backbone 按视觉能力、显存成本、动作指标和跨 setting 泛化结果选择。

### 当前限制

- 模拟 Stage 4 仅有 8 条非 `KEEP` 样本且类别失衡，尚不足以形成正式 SFT 结论。
- 实测 CSV 动作轨迹与 MAT `curation.assigns` 不是统一版本；未确认 provenance 前，CSV 只用于 action-level，MAT 只用于 cluster-level，不混合作为同一次运行的统一 GT。
- RAG 已验证存储、检索和 prompt 注入链路，但尚未证明效果提升。

### 当时待办（历史）

#### 实测数据线：高 → 低

- [ ] 向数据提供者确认 matching canonical set、CSV 是否为完整 log、Excel/CSV 关系及 recording/session 分组。
- [ ] 在版本未统一前，固定 CSV action-level 与 MAT cluster-level 两套独立评估协议。
- [ ] 获得许可后，仅保留小规模 API baseline/teacher 验证；不优先大规模重复调用 API。
- [ ] provenance 无法恢复时，从 MAT 构建明确标记为 derived oracle 的轨迹，或请专家复核小规模干净子集。

#### 模拟与训练线：高 → 低

- [ ] 完成 `setting_002`，修复并完成 `setting_003`，审计 `KEEP/SPLIT/MERGE/DISCARD` 覆盖。
- [ ] 仅在既有 setting 仍缺 MERGE/DISCARD 时启用 `setting_004`。
- [ ] 重建数量充分、类别平衡且按 setting/channel 隔离的 SFT 数据集。
- [ ] 对 Qwen/Gemma 候选做小规模 base inference smoke，选择一条 LoRA/SFT 主路线。
- [ ] 训练第一个本地 student，并在 held-out setting 上同时报告 action-level 与 cluster-level 指标。
- [ ] 本地 SFT baseline 稳定后，再做无泄漏 no-RAG/RAG 对照。
- [ ] 最后再研究 continual learning / online RL。

---

## 2026-09-02 — 无数据提供者修订时的真实数据审计

### 已完成

- 只读审计 17 个 current-format MAT，共 4,469,063 spikes；16 个具有最终 `curation.assigns` 和可执行内部 action log，1 个 CH5 无 curation target。
- 16 个内部 log 共 1,361 条动作：`SPLIT 636 / MERGE 169 / DISCARD 556`，所有动作均可机械执行。
- `Tianmin_Annotated_data` 的 CH3/20/30/31 MAT 与 Jacob 数据包中对应文件的 SHA-256 一致，是重复文件，不重复计入数据量。
- 16 个可评估 MAT 的初始 hierarchy 与人工终态差异明显：initial-vs-final ARI 平均 0.010，范围 -0.141–0.486。

### 四主通道的保守使用协议

| 通道 | 主 action 来源 | 可用步数 | reasoning | 与 MAT 终态关系 |
|---|---|---:|---:|---|
| CH3 | Excel / MAT internal | 97 | 97 | 严格一致，可作为统一 action+cluster GT |
| CH31 | Excel | 54 | 54 | 严格一致，可作为统一 action+cluster GT |
| CH30 | Excel | 90 | 90 | 动作完整可回放，但与 MAT 终态分开使用 |
| CH20 | MAT internal | 54 | 0 | 动作完整可回放，但与 MAT 终态分开使用 |

- CH20 的 Excel/CSV 带 reasoning 轨迹只保留前 43 步；第 44 步引用已消失的 cluster 9，不再继续回放。
- 按上述主来源可得 1,374 条可执行 action label，其中 241 条带人工 reasoning；CH3+CH31 的 151 步可严格联合 action-level 与 cluster-level 评估。
- CH3+CH31 的统一 GT 只含 SPLIT/DISCARD，不含 MERGE；MERGE 监督必须使用其他 MAT 的独立 action-level log，不宣称与终态严格统一。
- 额外 12 个有 curation 的 MAT 提供 1,079 条 action-only 监督和独立最终 cluster target；`not_annotated` 目录名不代表文件内一定无标注。
- 无法获得 session 说明时，暂按路径中的 5 个 recording block 做 leave-one-block-out，并在报告中明确标注这是文件名推定的分组。

审计产物：

- `scripts/analysis/audit_real_mat_internal_actions.py`
- `output/real_mat_internal_action_audit_20260902/summary.json`
- `scripts/analysis/audit_real_action_sources.py`
- `output/real_action_source_comparison_20260902/summary.json`

---

## 2026-09-03 — setting_002 安全断点续跑

### 已完成

- Stage 1 新增 `--channel-id ch_NNN`，单通道断点续跑不再改写 setting 的 `n_channels`。
- MEArec 模板生成默认改为单 worker，可用 `MEAREC_TEMPLATE_N_JOBS` 显式覆写；本次可用内存稳定在约 7.5 GiB，未再出现 WSL 断连。但 extracellular template 仍按 channel seed 重新生成，本通道该步耗时约 2,694 秒，不能当作一次性全局缓存。
- `setting_002/ch_007` Stage 1–2 完成：16 个模拟 neurons，15 个 sorted units，5,106 spikes；canonical trajectory 为 `SPLIT × 2 → KEEP`。
- `setting_002` 现完成 8/20 个通道；Stage 2 canonical coverage 为 `KEEP 8 / SPLIT 14 / MERGE 1 / DISCARD 0`。
- coverage 报告新增 Stage 2 canonical、Stage 3 GT 和当前 effective trajectory 三套独立计数，避免已有 Stage 3 的单通道覆盖 Stage 2 终止 KEEP 而造成误读。
- 本次未调用 API，未修改真实 MAT；两个外部数据目录已加入 `.gitignore`，避免上传新仓库时误提交原始数据。

### 当时待办（历史）

#### 实测数据线：高 → 低

- [ ] 改造真实数据导出器：保留原始动作顺序、MAT/Excel 来源、文件 hash 和 unified/separate target 标记；不将旧 CSV mixed builder 产物宣称为 canonical trajectory。
- [ ] 先在 CH3/CH31 各导出小规模 state-image/action 样本，验证顺序、图像、cluster ID 和终态一致性。
- [ ] 固定基于文件名推定的 5 recording-block split manifest，以 leave-one-block-out 报告主结果和分组敏感性。
- [ ] 分开构建 reasoned 子集与 action-only 全集，训练第一个本地 student baseline；API 仅保留为后续 teacher/baseline。
- [ ] CH5 只用于无标注 inference/人工补标池，不进入监督训练或定量评估。

#### 模拟与训练线：高 → 低

- [ ] 先将 extracellular template pool 改为可复用 artifact，或在可控内存下测试 2-worker；否则剩余 12 个 `setting_002` channel 串行仅模板阶段就可能约需 9 小时。
- [ ] 若共享 template pool 会改变现有 per-channel seed/分布，则必须新建 setting 版本并统一重跑，不与现有 `ch_000–007` 混为同一协议。
- [ ] 优化通过后再按单通道断点续跑 `setting_002`，每个通道后检查 Stage 2 动作和内存。
- [ ] 先修复 `setting_003` 高噪声下检出 unit 过少/为零的问题，再续跑；当前只有 4 个非空 raw 通道且无 Stage 2 actions。
- [ ] `setting_002/003` 完成后统一审计四类动作；仅在 DISCARD 仍缺且实验确实需要受控覆盖时启用 `setting_004`。
- [ ] 真实数据导出协议稳定后，再与模拟样本共同构建按 setting/recording 隔离的 SFT 数据集。

---

## 2026-09-03 — 主线纠正：只推进真实数据

- 模拟数据线即日起暂停，不再续跑 `setting_002/003/004`。
- `setting_002/ch_007` 是本次误沿用旧待办后产生的额外验证产物；暂保留，不继续扩展，也不计入当前真实数据主线。
- 当前主线只使用真实 MAT、Excel/CSV 人工动作和已有真实数据实验产物。

### 当时待办（历史）：真实数据线

- [ ] 改造真实数据导出器，保留动作顺序、来源、文件 hash 和 unified/separate target 标记。
- [ ] 先对 CH3/CH31 做小规模本地 state-image/action 导出 smoke，不调用 API。
- [ ] 固定 5 recording-block split manifest，分开构建 reasoned 子集和 action-only 全集。
- [ ] 数据导出验证通过后，训练并评估第一个本地 student baseline。

### 模拟数据线

- 暂停；除非后续明确重启，不再运行或优化。

---

## 2026-09-03 — 真实数据有序导出与无泄漏划分

### 已完成

- 新增真实数据专用导出器 `scripts/finetune/export_real_unified_actions.py`：从原始 `hierarchy.assigns` 开始，严格保持 Excel 行顺序；不做预过滤、自动 discard、合成 KEEP 或动作重排；缺失 cluster 或无状态变化时直接报错。
- CH3/CH31 各 3 步本地 smoke 通过；随后完成两通道全部 151 步导出：CH3 97、CH31 54，`DISCARD 94 / SPLIT 57`，全部带人工 reasoning。
- 151 步完整回放均与对应 `curation.assigns` 逐元素一致，ARI=1.000；生成 151 条 JSONL 和 604 张状态图，共约 42 MiB。原始 MAT/Excel hash 前后不变，API 调用为 0。
- 新增 `scripts/analysis/build_real_split_manifest.py`，生成基于目录名推定的 5-recording-block leave-one-block-out 清单：17 个 current-format MAT、4,469,063 spikes、1,374 条动作、241 条人工 reasoning。
- CH3 与 CH31 属于同一个推定 recording block，不能互相作为无泄漏 train/eval。

产物：

- `output/real_unified_action_smoke_20260903/`
- `output/real_unified_action_dataset_20260903/`
- `output/real_split_manifest_20260903/manifest.json`

### 当前真实数据使用边界

- **可直接联合使用：** CH3+CH31 共 151 步，可同时用于 action-level 与最终 cluster-level；但两者必须同属一个数据划分。
- **可分开使用：** 其余 14 个有标注 MAT 共 1,223 条动作，其中包含全部 169 条 MERGE；action log 可训练/评估动作，`curation.assigns` 可单独评估终态，不宣称两者构成同一统一真值轨迹。
- **仅无标注使用：** `cM2-e004_001-003_CH5` 无 action/terminal，只用于 inference 或人工补标池。
- **不重复计入：** Tianmin 的四个 MAT 是对应主 MAT 的 hash 重复；`oldFormat` 与 Chronux sampledata 不混入 current-format 人工数据集。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 将同样的严格顺序导出协议扩展到 14 个 separate-target MAT，优先用 CH30 做含 MERGE 的小规模本地 smoke；每条记录明确标记 action 与 terminal 分开。
- [ ] 按 5-block manifest 分别生成 reasoned 子集与 action-only 全集的 train/eval 索引，并审计各 fold 的动作类别覆盖。
- [ ] 选择一个可在本机运行的本地 student，先做 held-out block 小规模离线 inference；不调用 API。
- [ ] inference 格式和显存验证通过后再做 LoRA/SFT，并分别报告 action-level、cluster-level；只有 CH3/CH31 所在 holdout block 可报告统一 action+cluster 结果。
- [ ] CH5 仅在模型稳定后作为无标注 inference/人工补标候选，不纳入定量成绩。

---

## 2026-09-14 — 真实数据全量有序导出与安全运行修复

### 已完成

- 除无标注 `cM2-e004_001-003_CH5` 外，16 个真实 MAT 全部通过只读预检；未调用 API，未修改源 MAT。
- 全量导出 1,374 条按原顺序回放的专家动作：`SPLIT 636 / DISCARD 569 / MERGE 169`；其中 241 条带人工 rationale。
- 产物包含 1,374 条 JSONL 和 5,327 个图像引用，动作步号、状态 hash 链、图像路径和源文件 hash 检查全部通过。
- CH3/CH31 的 151 步统一轨迹仍精确匹配人工终态，ARI=1.000；其他 14 个数据集仍保持 action/terminal separate 标记。
- 真实数据 runner 改用完整 dataset ID；默认关闭未校准的数量 discard；provider 失败不再回退 mock；解析或无法决策时 `ABSTAIN` 并保留 cluster；`NOT_MERGE` 不再导致错误 discard。
- 训练/运行的 Phase 1 输入统一为 waveform、ISI、amplitude、aggregation tree 四图与数值指标；SFT 改为按 recording block 划分，默认 action-only JSON target。
- 回归测试通过：21 passed（2026-09-15 二次安全复核后）。详细结果与路线见 `docs/archive/REAL_DATA_OPEN_VLM_STATUS_20260914.md`。

### 当前边界

- 现有 1,374 条是专家实际执行的正向编辑日志，没有显式 `KEEP / NOT_MERGE`；可用，但尚不是完整自治 agent decision dataset。
- 默认 train blocks 共 973 条 action-only，人工 rationale 全在 CH30 validation 和 CH3/CH31 final-test 数据中；当前不应为训练 reasoning 而泄漏评测 block。
- 本次未完成开源 VLM 预测：本次受限执行环境无法访问 GPU 和本地 vLLM 服务，且项目 `.venv` 未安装 open-model/训练依赖。启动检查在处理数据前安全停止，没有产生 mock 成绩。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 修复 WSL GPU 可见性，或转到 A100 集群；建立独立 open-model 环境并启动本地 VLM 服务。
- [ ] 先在 CH30 跑真实 base Qwen 小规模 smoke，再完成固定状态 action-level baseline；不调 API、不用 mock。
- [ ] 补齐或明确派生 `KEEP / NOT_MERGE`，严格区分 human、derived 和 teacher provenance。
- [ ] 训练 action-only LoRA/SFT student；用 CH30 validation 选模型，最后只在 `cM2-e004_004-006` block 做 final test。
- [ ] 完成自主 pipeline 评测，分开报告 action-level 与 cluster-level，并审计错误 merge/split 和 abstention。
- [ ] baseline 稳定后再做固定 train-memory RAG；偏好数据与 reward 成熟后再考虑 DPO/RL。

---

## 2026-09-15 — 真实数据安全逻辑二次复核

- 新 manifest/Qwen 主线的完整 dataset ID、禁用默认数量过滤、禁止隐式 mock、`ABSTAIN` 保留状态、`NOT_MERGE` 保留独立 cluster、四图输入及 recording-block split 均通过代码与测试复核。
- 修正旧 no-metrics ablation 的残留危险逻辑和过时 prompt；核心与 ablation 现在均在解析失败/无 merge target 时保留 cluster。
- Phase 2 预复核只有明确 `KEEP` 的大 cluster 可作 merge target；`SPLIT/ABSTAIN` 只保留、不接收合并。
- 以实际导出数据复核无泄漏划分：train 973、CH30 validation 90、final-test block 311，recording block 无重叠。
- 旧 `run_all_channels.py` / `run_single_channel.py` 仅保留用于历史复现，仍有显式 500/5000 阈值和 CH-only 路径；不用于新真实数据主线。

### 真实数据体积与 VLM 输入

- Jacob 数据包约 27.85 GB；其中 7,056 个 MATLAB `.fig` 约 25.96 GB，MAT 约 1.90 GB。
- `.fig` 是人工 curation 每步的 MATLAB 中间图形档案；当前 Python/VLM 不读取，只有人工审计、复原原 UI 或未来加入 correlogram 时才需要。
- 当前 VLM/SFT 输入是从 MAT 回放后重生成的 5,327 张 PNG，约 486 MiB；尚未由开源 VLM 实际推理。
- 源 MAT 与 action workbook 必须保留；`.fig` 可外部归档；GitHub 不上传 data、`.fig`、生成 PNG 或任何 `output/`。

### CH30 真实图片 smoke 尝试

- 已尝试启动真实 Qwen/vLLM 路径；本地服务未就绪，且轻量 `.venv` 无 open-model 依赖，因此在模型调用前安全停止，无 mock、无 API、无成绩。
- 主机可识别 RTX 3070 Laptop GPU（8 GiB），但 Qwen3.5-4B BF16 权重约 9.34 GB；本地需验证 4-bit，未量化正式 baseline 优先使用 A100。
- 纠正文档：3-step smoke 使用固定人工状态评测脚本的 `--max-samples 3`；`run_real_manifest.py --mode run` 是完整自主 channel，不能当作 3-step smoke。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 修复 WSL GPU 可见性，或转到 A100 集群；建立独立 open-model 环境并启动本地 VLM 服务。
- [ ] 先在 CH30 跑真实 base Qwen 小规模 smoke，再完成固定状态 action-level baseline；不调 API、不用 mock。
- [ ] 补齐或明确派生 `KEEP / NOT_MERGE`，严格区分 human、derived 和 teacher provenance。
- [ ] 训练 action-only LoRA/SFT student；用 CH30 validation 选模型，最后只在 `cM2-e004_004-006` block 做 final test。
- [ ] 完成自主 pipeline 评测，分开报告 action-level 与 cluster-level，并审计错误 merge/split 和 abstention。
- [ ] baseline 稳定后再做固定 train-memory RAG；偏好数据与 reward 成熟后再考虑 DPO/RL。

---

## 2026-09-20 — 方法定义与历史/当前结果边界

- 新增论文导向的方法与状态文档：`项目方法与实验状态.md`；统一说明研究范围、agent 状态/观察/动作/转移、真实数据 Phase、SFT、指标和外部 benchmark 条件。
- 纠正“真实数据尚未使用 VLM”的过度表述：旧路线已在 CH3/20/30/31 上完成 GPT-4.1、GPT-5.1 和 no-metrics ablation 闭环，并保存 action log、诊断图、final assignments 和 cluster-level reports。
- 旧 API-VLM 结果只作 preliminary/legacy evidence：它们使用 CH-only 路径、500/5,000 自动 discard 阈值、修复前失败语义和旧观察协议，不能替代当前 manifest/Qwen 主路线的正式 baseline。
- 当前安全路线已完成 16 个有标注 MAT 的审计、1,374 条动作回放和 5,327 张图像导出；尚未完成开源 Qwen/Gemma inference、SFT 或修复后 autonomous rollout。
- DISCARD 仍是正式模型动作；关闭的只是未经真实数据校准的自动数量 discard。明确合法的 VLM DISCARD 仍执行，provider/解析/无 merge target 失败则保留状态。
- 当前内部 precision/recall/F1 可用于相同数据协议下的内部比较；外部 SOTA 声明前需加入 SpikeInterface 标准 spike-time/unit matching，并在公开 ground-truth benchmark 上比较。
- 阈值实证复核：569 个人工 DISCARD 中仅 95 个源 cluster 小于 500，另有 8 个小于 500 的 cluster 被人工 MERGE；39 个人工最终 units 中有 3 个小于 5,000。因此 500/5,000 只能作待校准 heuristic/特征，不作真实数据默认硬删除。
- CH30 旧闭环中 GPT-4.1 的 62 个动作与 GPT-5.1 的 22 个动作主要来自决策分岔：前者递归 SPLIT 更多，后者更早 DISCARD；两者最终都只保留 cluster 31（50,821 spikes）。动作少不代表更好，必须结合终态 recall 和错误丢弃评估。
- 方法定位细化为 deterministic harness 管理的 multimodal sequential decision agent；真实主线当前是 human-supervised student policy，API teacher 仍是可选的 train-only 蒸馏/补标来源，不是 human ground truth。
- 公开 benchmark 路线已明确：SpikeForest/SpikeInterface 适合标准终态评估，但需先固定 initial sorter 并为当前 hierarchy-specific SPLIT 接入状态/operator；AECuration 数据更适合单独 DISCARD/noise baseline，不是全动作轨迹。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 在 Runpod A100 建立独立 open-model 环境。
- [ ] 用 base Qwen3.5-4B 完成 CH30 3-sample fixed-state smoke；该步骤只验证多图、vLLM、JSON 和资源，不作为准确率结论。
- [ ] 完成 CH30 全 90 条 expert edit-action baseline，报告 per-action precision/recall/F1、macro-F1、confusion、invalid-output 和 abstention。
- [ ] 在同一小规模协议下比较一个 Gemma 候选，再冻结 SFT backbone。
- [ ] 补齐或明确派生 KEEP/NOT_MERGE，并保留 human/derived/teacher provenance。
- [ ] 训练 action-only LoRA/SFT；CH30 只用于 validation，final-test block 在方案冻结后使用一次。
- [ ] 运行修复后的 autonomous rollout，并用标准化 action-level 与 cluster-level 指标评估。
- [ ] 完成公开 ground-truth benchmark 后才能评估 post-curation SOTA；RAG、DPO/RL 均排在稳定 baseline/SFT 之后。

---

## 2026-09-21 — API teacher、SpikeInterface baseline 与 SpikeAgent 差异

- API teacher 是通过 GPT/Claude API 对 student 动作给反馈或软标签的强模型。项目在模拟 Stage 3 和 `setting_002/ch_006` 两步 API smoke 中用过该机制；当前 runner 未向 teacher 传图，因此它实际是 GT-informed 文本 critic。旧真实 MAT 的 GPT 运行是 API policy 直接决策，不是 teacher 蒸馏。
- 真实主线的 1,374 条动作仍以人工日志为监督；API teacher 后续只作为 train-only 补标/反馈与蒸馏对照，不作为测试真值或长期主系统。
- 项目使用了 SpikeInterface 的 sorting/sorter/评估接口，但尚未使用其阈值或 model-based automated curation。应新增 numeric-only Random Forest/gradient-boosted baseline，检验 VLM 图像输入是否真正增加信息。
- 若仅用 API VLM 看 waveform/ISI 做 Good/Noise 或 merge，会与 SpikeAgent 高度重合。差异方向应集中在：本地可训练 open VLM、专家有序编辑轨迹、显式状态转移、ABSTAIN/安全 DISCARD、recording-block 防泄漏，以及 action-level 与 terminal ground-truth 双层评估。
- 权重更新方法中先做 LoRA/SFT，不先做 RL；但正式 SFT 前必须先完成 base Qwen baseline、numeric-only baseline，并补齐/派生可信的 KEEP/NOT_MERGE 负例。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 在 Runpod A100 完成 base Qwen3.5-4B 的 CH30 3-sample smoke 和 90-sample fixed-state baseline。
- [ ] 在相同 recording-block split 上建立 numeric-only Random Forest/gradient-boosted baseline，并确认所用 quality/template metrics。
- [ ] 小规模比较一个 Gemma base 候选，冻结 student backbone。
- [ ] 补齐或明确派生 KEEP/NOT_MERGE，逐条保留 human/derived/API-teacher provenance。
- [ ] 训练 action-only LoRA/SFT，再做 autonomous rollout；分别报告动作复现、终态质量、错误 DISCARD 和 abstention。
- [ ] 基础结果稳定后再测试 API-teacher 增益与固定 train-memory RAG；DPO/RL 后置。

---

## 2026-09-21 — Runpod H100 真实图片 Qwen smoke

- 已在 Runpod H100 80 GB 上建立可复用的 open-model 环境；代码、Python 环境、模型缓存和实验结果均位于 `/workspace` Network Volume，后续可停掉 H100 并将同一卷挂载到 A100 Pod。
- 已完成 CH30 前 3 条人工动作状态的 base `Qwen/Qwen3.5-4B` fixed-state smoke。每条输入包含 4 张真实诊断图和数值指标；未调用 OpenAI API、未用 mock/RAG、未修改 MAT，也未执行 autonomous state update。
- 三条人工动作均为 `DISCARD`；模型依次预测 `SPLIT / DISCARD / SPLIT`，命中 `1/3`。样本数和类别覆盖都不足，因此该结果只证明真实图片、vLLM、prompt、解析和结果收集链路可运行，不作为模型准确率结论。
- 初步错误模式：base model 容易把 overcluster/ISI 指标解释为需要 `SPLIT`，而没有稳定复现专家的 `DISCARD`。需在 CH30 全 90 条上确认这是否为系统性偏置。
- 三次响应均可解析为合法动作；但在 `enforce_action_only=true` 下仍返回了带 rationale 的 JSON，而非严格的单动作输出。全量 baseline 需同时报告 parsed-valid rate 和 exact-format compliance。
- 运行前发现并修复了 Runpod 环境的可执行路径/CUDA 动态库路径问题；实验归档已校验并下载至 `output/runpod_collected/qwen35_ch30_real_image_smoke_h100_20260921/`。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 在 CH30 全 90 条 fixed-state 样本上运行 base Qwen baseline；报告 per-action precision/recall/F1、macro-F1、confusion、parsed-invalid、abstention、exact-format compliance 和延迟。
- [ ] 在相同 recording-block split 上建立 numeric-only Random Forest/gradient-boosted baseline，检验诊断图和 VLM 的增量价值。
- [ ] 用同一小规模协议比较一个 Gemma base 候选，再冻结 SFT backbone。
- [ ] 补齐或明确派生 KEEP/NOT_MERGE，逐条保留 human/derived/API-teacher provenance。
- [ ] 训练 action-only LoRA/SFT；以 CH30 validation 选模，final-test block 仅在方案冻结后使用一次。
- [ ] 在安全 runner 上执行 autonomous rollout，分别报告动作复现、终态质量、错误 DISCARD 和 abstention。
- [ ] 基础结果稳定后再测试 API-teacher、固定 train-memory RAG 和公开 ground-truth benchmark；DPO/RL 后置。

### 模拟数据线

- 暂停；当前不继续投入运行或扩展。

## 2026-09-21 — CH30 全 90 条 base Qwen fixed-state baseline

- 已在 Runpod H100 80 GB 上完成 CH30 全 90 条 expert edit-action 的首轮 fixed-state baseline；模型为未微调 `Qwen/Qwen3.5-4B`，输入使用真实诊断图和数值特征，未调用 API、mock/RAG，未修改 MAT，也未执行 autonomous state update。
- GT 分布：DISCARD 41、MERGE 6、SPLIT 43；overall accuracy `36/90 = 0.400`，macro-F1 `0.531`。
- DISCARD：precision `0.067`、recall `0.024`、F1 `0.036`；MERGE：`1.000/1.000/1.000`，但仅 6 条；SPLIT：precision `0.475`、recall `0.674`、F1 `0.558`。
- 预测分布：DISCARD 15、MERGE 6、SPLIT 61、KEEP 5、INVALID_ACTION 3。41 个专家 DISCARD 中有 32 个被判为 SPLIT，证实 base model 存在明显 SPLIT 偏置，当前不能直接用于自主 curation。
- 87/90 响应是可解析的完整 JSON；3 条因 64-token 截断成为无效 JSON。严格单动作格式遵从率为 0/90，因为模型全部输出了 JSON+rationale；ABSTAIN 为 0。
- 本轮应视为首轮 diagnostic baseline：数据和推理链路有效，动作偏置结论有效；但正式跨模型/SFT 对比前，需统一 action-only prompt 与 response schema、禁止 rationale，并冻结协议后复跑。
- 已关闭 vLLM、校验云端归档并下载到 `output/runpod_collected/qwen35_ch30_real_image_full90_h100_20260921/`；原始图片、权重和密钥未进入结果包。
- 已完成统一分析，详见 `docs/archive/fixed_state_experiments_20260921_22.md#qwen`；派生 JSON/CSV 位于 `output/action_baseline_analysis/qwen35_ch30_full90_20260921/`。3-sample smoke 是全量前三条且两次 raw response 完全一致，不与 90 条重复计数。
- 同一评估集上的 stage-majority 诊断参照为 49/90（54.4%），高于 Qwen 的 40.0%；同集事后选择的单一 `n_spikes` 阈值可分对 SPLIT/DISCARD 80/84。二者均不能当正式 held-out baseline，但说明 numeric-only baseline 和 shortcut 审计必须先于 SFT。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 修正并冻结 action-only 输出协议：prompt/schema 不冲突、禁止 rationale、消除 64-token 截断；复跑 CH30 90 条正式 base baseline。
- [ ] 在相同 recording-block split 上建立 numeric-only Random Forest/gradient-boosted baseline。
- [ ] 针对 DISCARD 低 recall 和 SPLIT 偏置做图像/数值 ablation，再以同一协议比较 Gemma base 候选。
- [ ] 补齐或明确派生 KEEP/NOT_MERGE，保留 human/derived/API-teacher provenance。
- [ ] 训练 action-only LoRA/SFT；以 CH30 validation 选模，final-test block 仅在方案冻结后使用一次。
- [ ] 在安全 runner 上执行 autonomous rollout，分别报告动作复现、终态质量、错误 DISCARD 和 abstention。
- [ ] 稳定后再进入 API-teacher、固定 train-memory RAG、公开 benchmark；DPO/RL 后置。

### 模拟数据线

- 暂停；当前不继续投入运行或扩展。

---

## 2026-09-21 — `action-only-json-v2` 协议修复

- 发现时间：CH30 3-sample smoke 已出现 JSON+rationale；全 90 条运行进一步确认 90/90 未遵守单 token 指令，且 3 条 rationale 因 64-token 上限截断。
- 根因：导出数据的 prompt 要求 `{"action": ..., "rationale": ...}`，评估器又追加“只输出一个 token、不要 JSON”，形成互相冲突的输出要求；同时首轮未启用 response schema，action-only schema 还允许额外字段。
- 已修复为唯一版本化协议 `action-only-json-v2`：先移除 legacy rationale prompt，再要求只返回 `{"action":"ACTION"}`；schema 设置 `additionalProperties=false`，正式 fixed-state 评测默认启用 response schema。
- vLLM 0.29 使用标准 `response_format=json_schema`；schema 不兼容时直接停止，不静默回退自由文本。legacy 无 schema 模式只能通过显式 `--no-response-schema` 启用。
- 统一分析脚本同步新增 `strict_action_only_json` 指标，避免把新协议的合法 JSON 按旧“单 token”口径误判。
- 本地协议/API/安全测试已通过；尚未重新启动 GPU，真实 vLLM 兼容性需下一次 3-sample smoke 验证。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 在真实 vLLM 上运行 3-sample `action-only-json-v2` smoke；要求 3/3 为只有 action 字段的完整 JSON。
- [ ] smoke 通过后复跑 CH30 90 条正式 base Qwen baseline。
- [ ] 用 train recording 拟合 numeric-only baseline，并在 CH30 validation 一次性评估。
- [ ] 做 numeric-only、images-only、combined ablation，再同协议比较一个 Gemma base 候选。
- [ ] 补齐 KEEP/NOT_MERGE 后再训练 action-only LoRA/SFT；随后才考虑 autonomous rollout。

### 模拟数据线

- 暂停；当前不继续投入运行或扩展。

---

## 2026-09-21 — `action-only-json-v2` 真实 H100 复跑

- Runpod Pod 迁移后已恢复 H100 80 GB、Network Volume、Qwen 权重缓存和 vLLM 0.29 环境。新容器首次启动暴露 `PATH` 缺少虚拟环境 `ninja` 的问题；补齐 `PATH` 后启动成功，未重新下载模型。
- 3-sample smoke 严格 action-only JSON 合规 `3/3`，无 rationale/截断；动作为 `SPLIT/SPLIT/SPLIT`，对三个人工 `DISCARD` 命中 `0/3`。smoke 只证明协议和服务兼容。
- CH30 全 90 条正式 v2 baseline：accuracy `34/90 = 0.378`，macro-F1 `0.530`；90/90 可解析且严格只含 action 字段，无无效或截断输出。
- v2 per-action：DISCARD P/R/F1 `0.059/0.024/0.034`（仅 1/41）；MERGE `1/1/1`（仅 6 个正例，无负例）；SPLIT `0.500/0.628/0.557`（27/43）。预测分布为 SPLIT 54、DISCARD 17、KEEP 13、MERGE 6。
- 与 legacy 首轮相比：格式合规从 0/90 提升至 90/90，准确率从 36/90 变为 34/90；71 条预测相同、19 条改变。这说明协议问题已解决，但 base Qwen 的 DISCARD 决策能力仍不合格，不能进入 autonomous rollout。
- 两个实验包已云端/本地双重 checksum 通过，位于 `output/runpod_collected/qwen35_ch30_action_v2_{smoke,full90}_h100_20260921/`；原始图片、模型权重和密钥未进入归档。vLLM 已关闭。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 只用 train recording 拟合 numeric-only Random Forest/gradient-boosted baseline，在 CH30 validation 上固定评估。
- [ ] 做 numeric-only、images-only、combined ablation，检验 VLM 诊断图的增量价值。
- [ ] 在同一 `action-only-json-v2` 协议下小规模比较一个 Gemma base 候选。
- [ ] 补齐或明确派生 KEEP/NOT_MERGE，保留 human/derived/API-teacher provenance。
- [ ] 训练 action-only LoRA/SFT；只在 fixed-state 安全指标达标后进入 autonomous rollout。

### 模拟数据线

- 暂停；当前不继续投入运行或扩展。

---

## 2026-09-21 — CH30 open-VLM 同协议横向对照

- 已在同一 Runpod H100 80 GB、同一 CH30 90 条 fixed-state 数据和同一 `action-only-json-v2` 协议下完成 Qwen3.5-2B、4B、9B 与 Gemma-4-E4B-it 对照；输入均为真实诊断图和数值指标，未调用 API、mock/RAG，未修改 MAT，也未更新 cluster 状态。
- 四个模型均严格 action-only JSON `90/90`，无解析失败或截断；因此结果差异来自模型决策，不再受输出协议错误干扰。
- Qwen3.5-2B：accuracy `0.067`、macro-F1 `0.333`；Qwen3.5-4B：`0.378/0.530`；Qwen3.5-9B：`0.500/0.570`；Gemma-4-E4B-it：`0.544/0.578`。
- DISCARD 是共同失败点：2B/9B/Gemma 为 `0/41`，4B 仅 `1/41`。Gemma 的准确率恰好等于同集 stage-majority 诊断参照 `49/90`，不能据此证明视觉输入有增量价值。
- 四模型均命中 6/6 MERGE，但 CH30 没有 NOT_MERGE 负例；该结果不能证明完整 merge 判断能力。现有日志也没有人工 KEEP ground truth，因此本轮只属于 expert edit-action baseline。
- 模型扩容从 2B 到 9B 有明显收益，但没有解决 DISCARD；停止继续无目的增加 未做领域微调的模型。第一版 SFT 主候选暂定 Qwen3.5-9B，Gemma 保留为跨架构复核。
- 结果归档已下载并 checksum 通过；统一分析见 `docs/archive/fixed_state_experiments_20260921_22.md#open-vlm`。vLLM 模型进程已关闭，Pod 本身仍需在 Runpod 页面 Stop。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 只用 train recording 拟合 numeric-only Random Forest/gradient-boosted baseline，在 CH30 validation 固定评估。
- [ ] 做 numeric-only、images-only、combined ablation，确认诊断图是否提供超出数值 shortcut 的增量。
- [ ] 补齐或明确派生 KEEP/NOT_MERGE，逐条保留 human/derived/API-teacher provenance，并冻结 SFT 数据版本。
- [ ] 以 Qwen3.5-9B 训练第一版 action-only LoRA/SFT，Gemma-4-E4B-it 仅作必要的跨架构复核；CH30 用于 validation，final-test block 在方案冻结后只用一次。
- [ ] 仅在 fixed-state DISCARD/负例安全指标达标后进入 autonomous rollout；DISCARD 先 quarantine/可回滚，并报告终态质量和 abstention。

### 模拟数据线

- 暂停；当前不继续投入运行或扩展。

---

## 2026-09-21 — Numeric-only recording-block baseline

- 已在本地 CPU 完成 numeric-only baseline；未使用 H100、图片、API、RAG 或 final-test，未修改 MAT/cluster 状态。
- Train 为三个 recording blocks：973 条总动作，其中 split stage 831 条（SPLIT 457、DISCARD 374）；CH30 validation 为 90 条。模型选择只使用 train-only leave-one-recording-block-out CV，无 recording overlap。
- Random Forest 的 train grouped OOF accuracy/macro-F1 为 `0.904/0.903`，高于 HistGradientBoosting 的 `0.889/0.889`，因此在查看 CH30 前锁定 RF 为 primary。
- CH30：RF split-stage `78/84 = 0.929`；overall `84/90 = 0.933`、macro-F1 `0.952`。DISCARD P/R/F1 `1.000/0.854/0.921`，SPLIT `0.878/1.000/0.935`；6 个错误全部为 DISCARD→SPLIT。
- HistGradientBoosting 在 CH30 为 85/90，但 train grouped CV 略低，只作 secondary，不能看完 validation 后事后换主模型。
- merge train 142 条和 CH30 6 条全部为 MERGE，没有 NOT_MERGE/merge-stage DISCARD；6/6 仅来自显式 constant MERGE，不是学得的 merge 泛化能力。
- RF 主要使用 `n_spikes`（importance 0.601）和 `n_overclusters`（0.241）。这证明专家动作与数值指标高度相关，也提示 numeric shortcut；不能由此断言图像无用，因为 RF 有 831 条监督而 base VLM 未做领域微调。
- 完整分析见 `docs/archive/fixed_state_experiments_20260921_22.md#numeric`；脚本为 `scripts/analysis/run_numeric_action_baseline.py`，结果和主模型位于 `output/numeric_action_baseline_20260921/`，checksum 已通过。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 以 numeric RF 的 CH30 `0.933` 为门槛，在相同 train blocks 上设计 images-only 与 images+numeric action-only SFT ablation；final-test 保持未使用。
- [ ] 补齐或明确派生 KEEP/NOT_MERGE，逐条保留 human/derived/API-teacher provenance；缺少负例时只声明 expert edit imitation。
- [ ] 以 Qwen3.5-9B 训练第一版 action-only LoRA/SFT，Gemma-4-E4B-it 仅作必要跨架构复核；CH30 用于 validation。
- [ ] 仅在 fixed-state DISCARD、KEEP/NOT_MERGE 和 abstention 安全指标达标后进入 autonomous rollout；DISCARD 先 quarantine/可回滚。
- [ ] 模型和协议冻结后，final-test block 只评估一次，再决定是否需要 API teacher、RAG、DPO/RL 或公开 benchmark 扩展。

### 模拟数据线

- 暂停；当前不继续投入运行或扩展。

---

## 2026-09-22 — 当前协议 OpenAI API-VLM 对照

- 在同一份当前 CH30 MAT、同一 90 条 fixed-state 人工动作和 `action-only-json-v2` 下完成 GPT-4.1/GPT-5.1 API 对照；输入为真实诊断图和数值指标，预测不执行。
- GPT-4.1：35/90，accuracy `0.389`，macro-F1 `0.575`，DISCARD `0/41`，SPLIT `29/43`，严格 JSON `90/90`。
- GPT-5.1：33/90，accuracy `0.367`，macro-F1 `0.564`，DISCARD `0/41`，SPLIT `27/43`，严格 JSON `90/90`。
- 两模型预测一致 `88/90`；都出现强 KEEP/SPLIT 偏置，没有复现专家 DISCARD。6/6 MERGE 仍只有正例。
- GPT-4.1 使用 221,485 input / 530 output tokens；GPT-5.1 使用 184,225 input / 18,438 output tokens，其中 16,652 为 reasoning tokens。
- 旧 GPT 闭环 F1 与当前 action accuracy 测量对象不同；当前结果说明 API 模型不能未经校准就作为 DISCARD teacher。
- 评测脚本新增逐样本 checkpoint、`--resume`、实际模型版本与 token usage 记录；OpenAI 正式评测默认不再静默弱化 JSON schema。
- 详细记录：`docs/archive/fixed_state_experiments_20260921_22.md#api-vlm`。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 冻结 SFT manifest，在相同 train blocks 上训练 Qwen3.5-9B images-only 与 images+numeric LoRA/SFT。
- [ ] 在 CH30 比较监督式图像增量，重点报告 DISCARD 与 numeric RF `78/84` 基线。
- [ ] 补齐或派生 KEEP/NOT_MERGE，保留 human/derived/teacher provenance。
- [ ] fixed-state 安全指标达标后再做可回滚 DISCARD 的 autonomous rollout；最后只使用一次 final-test。

---

## 2026-09-22 — Prompt 协议复核与 legacy rollout 复现决策

- 确认当前 CH30 fixed-state 对照与旧 GPT rollout 使用不同输入 prompt。当前样本沿用 finetune 导出器的 `gemma4_train_reasoned` 简化 profile；旧 rollout 使用明确的 neuronal morphology、split 和 merge 领域规则。
- 该 profile 在首个可追溯 clean snapshot 中已存在，没有证据表明它是“为提高 Gemma 准确度而经验证的简化”。更准确的定性是：SFT 数据 profile 被直接复用于 fixed-state 评测。
- `action-only` 输出约束保留；它与“删去输入领域规则”是两件事。后者可能导致 未做领域微调的 VLM 的 KEEP/SPLIT 偏置，但没有单变量 prompt ablation 前不定量归因。
- README 的 GPT-5.1 历史 aggregate 为 `P/R/F1=0.7653/0.8327/0.7671`；当前保存终态重评估为 `0.7735/0.8327/0.7718`。差异来自 CH31 的 FP 计数 `20,933` vs `18,065`；重评估不是新模型运行。
- 主线暂时转为 legacy 结果复现：先统一重算已保存终态，再以 CH30 + GPT-5.1 做带旧详细 prompt 和旧数量阈值的完整 rollout；新结果使用独立输出目录，不覆盖旧实验。
- 首次复现误用当前 500-waveform 绘图合约，CH30/211 从历史 `DISCARD` 变为 `SPLIT`，形成超过历史 11 次决策的长递归；在 20 个合法决策后主动停止。精确重放历史 CH30/211 prompt+三图时，同一实际模型版本再次输出 `DISCARD`，证明图像预处理是分叉的主要原因。
- 旧 waveform overlay 为最多约 5,000 条波形，当前合约为 500 条确定性抽样。legacy 复现脚本已改为 5,000 条密度；保留当前安全失败语义，不修改主线 runner。
- 后续完整审计修正：CH30/211 只能证明图像预处理足以改变单个动作；10 个 byte-identical 历史状态仍只有 7/10 一致，故总体差异还包含模型非确定性，不能再称图像是唯一或主要原因。最终结论以下一节为准。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 用同一当前 evaluator 重算 GPT-4.1、GPT-5.1 和 no-metrics 的四通道终态，固定 historical/re-evaluated 口径。
- [ ] 在独立目录运行 CH30 + GPT-5.1 `legacy-detailed-v1` autonomous rollout；开启 500/5,000 历史阈值，限制 API 决策数并保存逐步 checkpoint。
- [ ] 与历史 CH30 `P/R/F1=1.0000/0.5578/0.7162`、终态 1 unit/50,821 spikes 及 11 次 VLM 决策比较。
- [ ] CH30 复现通过后，再决定是否扩到 CH3/20/31；不直接批量付费运行。
- [ ] legacy 复现后再恢复 SFT images-only/images+numeric 主线。

### 模拟数据线

- 暂停；当前不继续投入运行或扩展。

---

## 2026-09-22 — Legacy detailed-prompt 复现结论

> 本节保留当时记录；其中“四通道同协议复现”的归因已被文末“Legacy prompt 归因更正”取代。

- current fixed-state 的简化输入 prompt 不是本轮为 Gemma 临时修改的；它来自已有 `gemma4_train_reasoned` SFT 导出 profile。仓库没有“为 Gemma 简化可提高准确率”的设计记录或实验依据。
- `action-only` 输出与简化输入规则分开处理：前者用于避免不可靠 rationale 和截断，理由成立；后者是否有益尚未做同状态、同图、同模型的 prompt-only A/B。
- Git object `03f68e5` 中的 source 与同 commit 的结果 artifact 不匹配：代码 prompt 比保存 prompt 更新，logger 文件名策略也不同；旧波形图还使用未保存 RNG 的随机 5,000 条抽样。因此无法严格恢复当时 source/RNG，只能以保存的 prompt/PNG 为最强证据。
- CH30 10 个可恢复历史状态的 byte-identical replay：GPT-5.1 动作一致 `7/10 = 70%`。差异为 cluster 1 `DISCARD→SPLIT`、cluster 443 `KEEP→DISCARD`、phase2 455→31 `DISCARD→NOT_MERGE`。
- 首个 artifact-recovered fresh rollout 从 `hierarchy.assigns` 开始，在 Phase 2 达到人为 20-call 成本上限停止：20 次成功 provider call、52,770 total tokens，没有终态。这不是 OpenAI、模型或算法的硬限制。
- 按用户要求将上限提高到 100 后，一次全新 CH30 rollout 在 21 次成功 API 调用后自然结束；使用 53,191 total tokens，实际模型为 `gpt-5.1-2025-11-13`。
- 新终态与历史 CH30 数值完全相同：1 个有效 cluster、50,821 assigned spikes、`P/R/F1=1.0000/0.5578/0.7162`。但 action trajectory 没有复现：新运行对 cluster 1 连续三次 `SPLIT`，对 305 先 `SPLIT`，对 353 经历 `KEEP→ABSTAIN`；额外分支最后被旧 `<5,000` 硬阈值清除。
- 专业结论：成功数值复现了 **CH30 legacy controller 终态**，没有复现 **稳定的 VLM 决策 policy**。不同轨迹之所以到达相同终点，主要是历史 500/5,000 数量阈值消除了额外分支；因此不能把 F1 归因于稳定 prompt/VLM 推理，也不据此声称 SOTA。
- 后续关闭总 VLM 调用数上限，补跑 CH3、CH20、CH31，并与已完成 CH30 统一汇总。保留单状态 3 次 parse retry、provider 失败即停、`ABSTAIN` 和 checkpoint；不覆盖旧结果，不修改 MAT。
- 四通道均自然结束：CH3 `9 calls/23,242 tokens`，CH20 `7/18,456`，CH30 `21/53,191`，CH31 `58/144,781`；共 95 calls/239,670 tokens。
- 新结果：CH3 `0 units, F1=0`；CH20 `0 units, F1=0`；CH30 `1 unit, F1=0.7162`；CH31 `2 units, F1=0.8521`。四通道平均 `P/R/F1=0.4489/0.3688/0.3921`，对比保存历史报告 `0.7735/0.8327/0.7718`；仅 CH30 final assignments 完全一致。
- CH3 暴露空预测评估 bug：0 units 时空 DataFrame 没有 `tp` 列。已修复为 `TP=0, FP=0, FN=全部 GT, P/R/F1=0`，从保存的 final assignments 离线补齐报告，没有重跑 API。
- 四通道结论：历史 GPT-5.1 结果不能作为稳定 policy 复现；旧 500/5,000 阈值在 CH30 使不同轨迹收敛，但在 CH3 清空所有 units。因此旧 aggregate 只作 preliminary legacy evidence，不作 SOTA 或当前主线基线。
- 全部 legacy 复现调查累计 386,369 total tokens；其中四条完整 fresh rollout 为 239,670。统一报告见 `docs/archive/LEGACY_FULL_ROLLOUT_ALL_CHANNELS_20260922.md`，机读汇总位于 `output/legacy_reproduction/full_rollout_summary_20260922/`。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 停止扩跑 legacy GPT controller；回到 leakage-free student 主线。
- [ ] 回到 leakage-free SFT 主线：冻结真实数据 manifest、train/CH30-validation/final-test recording-block 划分和 action provenance。
- [ ] 在相同 train blocks 上训练 Qwen3.5-9B images-only 与 images+numeric action-only LoRA/SFT；以锁定的 numeric RF 为主要门槛。
- [ ] 在 CH30 validation 报告 overall、macro-F1、DISCARD recall、abstention，并明确 merge 仍缺 NOT_MERGE 负例。
- [ ] 只有 fixed-state 安全指标达标后，才做带 checkpoint、调用上限、错误 DISCARD quarantine 的 autonomous rollout。
- [ ] prompt-only A/B 作为独立 ablation：冻结状态、图片 bytes、模型版本和输出协议，只替换 minimal 与 detailed/domain-policy 输入；不与 SFT 主实验混跑。
- [ ] 模型与协议冻结后只使用一次 final-test；之后再决定 RAG/DPO/RL 或公开 benchmark。

### 模拟数据线

- 暂停；当前不继续投入运行或扩展。

---

## 2026-09-22 — Legacy prompt 归因更正

- 复核 JianZhi 初始 commit `03f68e5` 后确认：源码 `src/agent_context.py` 只有一套供所有通道共用的谨慎版 Phase 1/2 prompt，并注明规则来自 CH3/20/30/31 人工 curation sheets。
- CH3、CH20 和 CH31 的 call-suffixed prompt artifact 与该源码逐字节一致；CH30 artifact 以及 CH31 的无 call-suffix 残留文件属于更严格的另一 prompt revision。历史输出目录混有不同运行/版本残留，不代表有意为每个 CH 设计不同 prompt。
- 此前四通道 fresh run 错把 CH30 strict artifact prompt 应用于 CH3/20/30/31。其 `95 calls / 239,670 tokens` 和结果继续保留作审计，但只能称为 cross-channel strict-prompt stress test，不能称为 JianZhi 原 prompt 的四通道复现，也不能据此断言 CH3/CH20 的历史结果不可复现。
- CH30 专项结论仍成立：在 CH30 保存的 strict prompt+PNG 上 byte-identical replay 为 `7/10`；fresh rollout 终态 `1 unit / 50,821 spikes / F1=0.7162` 与历史一致但动作轨迹不同。
- 隔离脚本 `scripts/run/run_legacy_prompt_rollout.py` 已替换为 JianZhi 源码原 prompt，并新增 CH3 Phase 1、CH20 Phase 2 artifact 的精确 SHA-256 回归测试；当前安全失败语义保留，不影响主线 runner。
- Git 证据只支持“JianZhi 提交并维护、根据人工 action sheets 手写/整理的规则 prompt”；仓库没有自动 prompt optimization、agent 自我迭代或学习生成该 prompt 的记录。是否曾借助外部 AI 起草无法从仓库判断。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 暂不再次调用 API；如要复现 legacy，先冻结并审核 JianZhi 原 prompt、图片 bytes/RNG、controller semantics 和每通道 artifact provenance，再由用户明确批准运行范围与预算。
- [ ] 回到 leakage-free SFT 主线：训练 Qwen3.5-9B images-only 与 images+numeric action-only LoRA/SFT，并以 CH30 validation 对比 numeric RF。
- [ ] 补齐或明确派生 KEEP/NOT_MERGE，保持 human/derived/teacher provenance。
- [ ] fixed-state 安全指标达标后再进入带 checkpoint 和 DISCARD quarantine 的 autonomous rollout。

### 模拟数据线

- 暂停；当前不继续投入运行或扩展。

---

## 2026-09-23 — 双-harness scope 记录

- JianZhi 说明项目原始意图可能包含两套 harness，而不是要求所有路线共用一份 prompt。harness 是 prompt、观察、反馈/更新和评估的完整实验框架。
- **Harness A：detailed-rule-prompt。** 给 base VLM 详细领域规则，测试不训练时能达到的 curation 水平；属于强 prompt-only baseline。
- **Harness B：feedback-learning student。** student 从无 spike-curation 专项训练/记忆的预训练模型开始，只给基本任务定义，再利用 teacher feedback、trajectory、RAG/SFT 等逐步获得 skill。
- 没有 human-in-the-loop 时，teacher model 用 oracle/人工参考动作和 reasoning 模拟领域专家反馈；human/GT 才是监督真值，teacher 主要是反馈生成器，不能未经验证充当新 ground truth。
- 两套 harness 可以有不同 prompt；应在每套 harness 内分别冻结 prompt、输入顺序、输出 schema、controller 语义和评估协议。此前“冻结一个主 prompt”更正为“冻结每个 harness 的版本化协议”。
- 当前代码具备 teacher-student trajectory、RAG memory、SFT 数据与训练脚本，但尚未完成真实数据上的逐轮 student adaptation；现阶段主要是离线 trajectory→SFT/RAG→重新评估，不是在线自进化。
- 详细 domain prompt baseline 可归入 Harness A；真实 action-only SFT 主线可归入 Harness B。CH30 strict artifact 仍不能直接认定为 student harness，它更像详细 prompt 的另一历史 revision，具体映射尚未得到确认。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 为 Harness A/B 分别写出并冻结协议清单；明确 prompt version/hash、输入、允许动作、输出 schema、更新机制和评估 split。
- [ ] Harness A 只保留为明确标注的 详细规则提示 baseline，不继续混用不同历史 prompt artifact。
- [ ] Harness B 使用 train recordings 完成 Qwen3.5-9B action-only images-only 与 images+numeric LoRA/SFT，并与 numeric RF 比较。
- [ ] 明确 teacher feedback 的 human/GT provenance；只在 train split 使用 teacher/RAG，不向 validation/final-test 泄漏。
- [ ] fixed-state 安全指标达标后再做 student autonomous rollout；模型与协议冻结后 final-test 只使用一次。

### 模拟数据线

- 暂停；保留其作为 Harness B teacher-feedback/continual-learning 的已有代码基础，当前不扩跑。

---

## 2026-09-23 — GPT-5.1 谨慎版 prompt 四通道 full rollout

- 已用 JianZhi 源码中共用的谨慎版 prompt 重跑 CH3/20/30/31；模型固定为 `gpt-5.1-2025-11-13`，reasoning effort `medium`，不设总调用上限。这是 Harness A 详细规则提示闭环，不是 fixed-state 测试或 SFT student。
- 四通道均自然完成：共 `68` 次 provider calls，`174,606` tokens，按当前公开价格估算 `$0.4861`。

| 通道 | Calls | 最终 units | Assigned spikes | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| CH3 | 14 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 |
| CH20 | 3 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 |
| CH30 | 27 | 1 | 51,447 | 0.9878 | 0.5578 | 0.7130 |
| CH31 | 24 | 2 | 57,230 | 0.6797 | 0.7728 | 0.7233 |
| **四通道均值** | **68 total** | — | **108,677** | **0.4169** | **0.3327** | **0.3591** |

- 历史保存结果均值为 `P/R/F1=0.7735/0.8327/0.7718`；本次 0/4 终态与历史 assignments 完全一致。CH30/31 指标接近历史，但分别多并入 626/266 spikes；CH3/20 最终为空。
- CH3 的 Phase 1 保留了 5 个小 cluster，但全被 legacy `<5,000` 硬阈值删除；CH20 则在首次 SPLIT 后由 VLM 丢弃两个分支，两者失败原因不同。
- CH3/20 首次 prompt 与历史 artifact 逐字节一致，CH31 可核对 artifact 也一致；本次差异不是再次用错 prompt。旧绘图从 waveforms 中未设 seed 随机抽样最多 5,000 条，加上 reasoning model 非严格确定，一个早期动作分叉会改变全部后续状态。
- 结论：该 legacy protocol 不能稳定复现历史 aggregate；本次 `F1=0.3591` 是单次随机闭环观测，不是模型能力的稳定估计，也不是 SOTA 结论。详细记录见 `docs/archive/GPT51_CAUTIOUS_FULL_ROLLOUT_20260923.md`。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 不再扩跑未冻结的 legacy API rollout；若要测稳定性，先固定 sampled spike indices/图片 bytes、prompt hash、controller 语义、模型 snapshot 和 evaluator，再用多 seed 报告方差。
- [ ] 回到 Harness B leakage-free student 主线：在 train recordings 训练 Qwen3.5-9B images-only 与 images+numeric action-only LoRA/SFT，用 CH30 validation 对比锁定的 numeric RF。
- [ ] 补齐或明确派生 KEEP/NOT_MERGE，保持 human/derived/teacher provenance；只在 train split 使用 teacher/RAG。
- [ ] fixed-state 安全指标达标后再做 student autonomous rollout；DISCARD 先 quarantine/可回滚，冻结后 final-test 只使用一次。

### 模拟数据线

- 暂停；当前不扩跑。

---

## 2026-09-23 — GPT-5.1 删除保护版四通道 full rollout

- 在上一轮谨慎版 prompt 不变的前提下，加入 controller 删除保护后重跑 CH3/20/30/31：关闭 `<500` 自动删除和 `<5,000` 最终删除；≥4,000 spikes 的 Phase-1 `DISCARD` 改为 `SPLIT/ABSTAIN`；Phase-2 `DISCARD` 改为 `ABSTAIN`。这是 Harness A 安全机制实验，不是新 prompt、SFT student 或 fixed-state 测试。
- 四通道均完成，共 `1,547 calls / 3,780,384 tokens / 约 $11.85 / 约 5h19m`。CH20 首次连接失败为 0 次成功调用，成功结果保存在 `_retry1`，失败目录保留审计。

| 通道 | Calls | 最终 units | Assigned spikes | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| CH3 | 258 | 55 | 82,700 | 0.2221 | 1.0000 | 0.3634 |
| CH20 | 207 | 38 | 70,354 | 0.2625 | 1.0000 | 0.4158 |
| CH30 | 869 | 50 | 376,317 | 0.2116 | 0.8742 | 0.3408 |
| CH31 | 213 | 31 | 120,026 | 0.4194 | 1.0000 | 0.5909 |
| **四通道均值/总计** | **1,547** | — | **649,397** | **0.2789** | **0.9686** | **0.4277** |

- 汇总 spikes 为 `TP=166,806 / FP=482,591 / FN=11,461`，micro `P/R/F1=0.2569/0.9357/0.4031`。删除保护解决了 CH3/20 空输出，但以大量误保留和低 precision 为代价；不是性能改进。
- 保护共触发 Phase-1 large-DISCARD `95` 次、Phase-2 DISCARD `19` 次；调用数比未保护谨慎版 `68` 增至 `1,547`。CH30 成为主要成本源（869 calls），原因是不断 SPLIT 后产生 60 个小 cluster，并逐一与 13 个有效大 cluster 比较。
- Phase 2 暴露结构性问题：同一个小 cluster 仅因候选 merge target 不同，就会在“有效 neuronal unit”和“应 DISCARD”之间反复改变判断；CH30/344、CH31/79、226 等均出现。cluster validity 不应在每个 pair 中重复判断。
- prompt 中 ISI `0.006` 与百分比表达也被模型解释成不同量纲；数值阈值不应交由自由文本 reasoning 执行。
- 结论：删除保护应保留为可回滚 fail-safe，但“全部保护后继续 legacy pairwise controller”已被本次实验否定，不再扩跑。详细报告：`docs/archive/GPT51_DELETE_PROTECTED_FULL_ROLLOUT_20260923.md`；机读汇总：`output/legacy_reproduction/gpt51_cautious_delete_protected_v2_summary_20260923/`。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 结束 legacy API 扩跑；将本轮结果作为 Harness A 安全失败分析，不再为同协议追加通道或重复单次随机 rollout。
- [ ] 冻结 Harness B 真实数据 manifest、recording-block split、action provenance、四图顺序、数值字段和 action-only schema。
- [ ] 在 train recordings 上训练 Qwen3.5-9B images-only 与 images+numeric LoRA/SFT，在 CH30 validation 与锁定 numeric RF 比较，重点报告 DISCARD recall、macro-F1 和 abstention。
- [ ] 重构 rollout policy：cluster validity 单独判断并缓存；merge 只做候选筛选后的 `MERGE/NOT_MERGE`；删除先进入 `QUARANTINE/ABSTAIN`，保留恢复路径。
- [ ] 补齐或明确派生 KEEP/NOT_MERGE provenance；fixed-state 安全指标达标后才运行 student autonomous rollout，冻结后 final-test 只使用一次。

### 模拟数据线

- 暂停；当前不扩跑。

---

## 2026-09-29 — GPT-6 Astra high 分通道保护实验

- 用户授权以 `gpt-6-astra`、reasoning `high` 重跑四通道 full rollout，总预算 $30；属于 Harness A 领域规则提示实验，不是 SFT 或固定人工状态动作测试。
- CH30/31 保持 9/28 固定上游 controller；CH3/20 采用删除保护，关闭前后数量过滤、拦截大 cluster/Phase-2 删除，并保留无合并目标的 cluster。两组协议不同，不报告混合宏平均作为模型能力提升。
- Prompt、三图和 action+rationale schema 不重写。四 MAT 哈希与 9/28 一致；CH30、CH3 首状态的 prompt、三图哈希和 schema 均与 9/28 对应请求一致。28 项离线测试及两组真实图片离线预检通过。
- 单请求输出预算保留 4,000 tokens（含 reasoning）；请求前计数并预留费用。预算不足、截断或接口失败即停止，不回退 mock，不自动重跑。模型建议与保护后的实际动作分别记录。
- 已启动独立串行批次，顺序 CH30→CH31→CH3→CH20；此处不预报分数。协议见 `docs/archive/GPT6_ASTRA_HIGH_ROLLOUT_20260929.md`，当前状态、结果、费用见 `output/astra_high_channel_guards_budget30_20260929/`。
- SFT 属于 Harness B 的可选学习手段：先用人工示范训练，再研究 teacher 纠正反馈；目前不能声称已完成项目 SFT 或获得训练提升。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 收集本轮已完成通道的 Precision/Recall/F1、实际模型、费用及保护触发次数；失败或预算停止不计终态分数，不自动补跑。
- [ ] 按 controller 条件分别比较 GPT-5.1 medium 与 Astra high；不把模型、reasoning 和保护规则的共同变化归因成单一模型收益。
- [ ] 冻结人工示范 SFT 数据、action-only 输入输出及 recording-block 划分；CH30 为已使用的开发验证集，反复查看过的 CH3/31 不能再宣称未触碰最终测试集。
- [ ] 在完成训练及同协议评测后再记录 SFT 成果；持续学习、teacher feedback 与 RL 分开规划，不预先写成已完成。

### 当时待办（历史）：模拟数据线

- 暂停；本轮不运行、不修改模拟实验。

### 2026-09-29 晚间续跑（UTC 09-30）

- $30 批次在 CH30 合并阶段触发预算保护：588 次成功推理，约 2h56m，标准价估算 $26.61、保守记账 $29.78；第 589 次尚未发送。其它三通道未开始，不存在本轮完整终态分数。
- 经逐个候选核对，CH30 最多剩 17 次合并比较。用户授权将**累计总预算提高至 $70**，不是另加 $70；先续完 CH30，再按原顺序继续 CH31/3/20。
- 新增断点恢复脚本，复用旧响应并检查状态、prompt、随机采样和中断处三图哈希；原 588 次不重新调用、不重复计费。新旧目录分开，模型和协议不变。详情见 `docs/archive/GPT6_ASTRA_HIGH_ROLLOUT_20260929.md` 的续跑记录。
- 失败归档：`实验失败案例分析与归档.md` 记录 ASTRA-001（预算不足中断）和 ASTRA-002（离线响应格式兼容错误）的原因、证据、影响及验证；关键文件副本和完整哈希索引留在本地 `output/failure_archive/`。中断不计模型零分，后续终态误删/误保留另行归因。

#### CH30/31 终态与跨模型归因（同日晚间更新）

- Astra CH30 完成 605 calls，P/R/F1=0.2747/0.4257/0.3339；CH31 完成 235 calls，P/R/F1=0/0/0。CH3 完成 84 次决策后第 85 次遇到 429 停止，CH20 未启动；不能报告完整四通道 baseline。
- 与 9/28 同上游协议 GPT-5.1 medium 对照，CH30 第 6 次、CH31 第 1 次的 prompt、三图和完整状态逐项相同，但 5.1 DISCARD、Astra SPLIT。首次分叉不是图片抽样或 prompt 误用；后续图片可能因轨迹分叉而不同。
- CH30：Astra 救回 5.1 未找回的 26,652 个 TP，却损失其原本正确的 38,694 个，且 FP 从 1,313 增至 102,368。CH31：5.1 保留专家 unit 63 的全部 38,899 spikes；Astra 对同一纯群继续拆分后全部删除，这是主要分差来源。不能仅概括为“Astra 更严格”或只归因无合并对象自动删除。
- 已本地验证两模型终态回放、全部请求图文哈希及关键群成员。任务失败详见 `实验失败案例分析与归档.md` 的 ASTRA-T01/T02/T03；机读对照在 `output/astra_gpt51_causal_audit_20260930/`。新增 API 调用为 0，不改动实验协议。
- 待修方向：校准专家纯群的 SPLIT/KEEP 与形态 DISCARD 边界、统一可恢复删除及 NOT_MERGE 语义；模型与 effort 的独立效应尚未辨明，不凭单次结果宣称普遍能力高低。

### 当时待办（历史）：真实数据线

- [ ] 完成离线恢复校验后继续本轮四通道，累计费用限制 $70；已完成通道单独汇报，未完成不计终态分数。
- [ ] 比较终态 Precision/Recall/F1、动作分布、保护次数和费用，区分 unprotected CH30/31 与 protected CH3/20，不作混合模型能力结论。
- [ ] 整理人工示范 SFT 的数据与划分，保留与 详细规则提示 rollout 不同的实验定位；训练尚未实施。

### 当时待办（历史）：模拟数据线

- 暂停，不扩跑。

---

## 2026-10-06 — 当前目标：闭源模型 + 冻结领域 skill

- 用户明确先完成闭源模型完整 curation baseline，不先推进开源 continual harness 或 SFT。`baseline` 指比较参照，不要求先运行 `src/pipeline/baseline.py`；后者是 VLM + 启发式方法，且参数传递/拆分接口问题尚未修复。
- 据用户转述导师，旧 prompt 针对 Qwen/GPT-4o 的视觉能力加过限制，因此应在开发数据上重新校准领域规则，尤其 SPLIT/KEEP 与形态 DISCARD 边界。正式 baseline 是**先调优、后冻结、再完整评测**，不是永远不改旧 prompt。
- 固定 system、改 skill 仍然会改变完整模型输入；有效性来自规则内容与任务更匹配，不来自文件改名。基线运行时 system/skill 都冻结；未来 skill-learning 才允许按预定规则在训练阶段更新 skill。旧文档“整条 learning curve 的 prompt 必须不变”已修正，不能套用到以 skill 为优化对象的实验。
- Astra CH30/31 的低分是可复核的旧协议结果，不等于 MAT 本身错误；动作日志与终态版本差异、KEEP/NOT_MERGE 缺标等是另一个层次的问题。保留原结果，不改 GT、不根据结果挑通道或改分数。
- 四主通道已多次用于分析，后续调 skill 按开发数据报告；其它 recordings 是否仍隔离需审计，不能宣称旧 final-test block 未被使用。
- 本次只更新 README 与方法文档、保留过去日期记录；未改运行代码/prompt，未补标签，未产生新 API 调用。

### 当时待办（历史）：真实数据线（高 → 低）

- [ ] 锁定闭源 baseline 的数据与执行入口，核查标注/初态对应和可用隔离 recordings；确定全通道一致的 controller、图像/schema、模型参数及预算。
- [ ] 在开发数据上调整实际领域 skill，记录逐条依据；分开记录 skill、controller、绘图变更，不按通道编号设置救援规则。
- [ ] 先做本地请求与执行一致性检查；任何开发 API 验证须另行确认范围/费用，不自动续用原 $70 批次。
- [ ] 开发验证后冻结版本，运行约定范围的完整闭源 baseline；报告终态 P/R/F1、误删/误保留、调用量和费用，并与 no-curation 及旧协议对照。
- [ ] 闭源 baseline 后再规划开源 skill-learning、派生 KEEP/NOT_MERGE 和可选 SFT；不预报训练收益。

### 当时待办（历史）：模拟数据线

- 暂停，不运行、不扩展。

---

## 2026-10-06 — 文档整理与清理边界

- 四份 fixed-state 报告合并、两份教授汇报合并；其他旧实验报告移入 `docs/archive/`。方法文档 §6.0 汇总有效结果，旧计划不再作为当前指令；过去日期记录保留。
- 纠正 Phase 0/NOT_MERGE 描述、测试集未触碰声明、numeric baseline 完成状态及 RAG“验证完成”的边界。明确 system/skill 拆分尚未实现；未修改推理代码或发起 API 实验。
- 仅清理缓存、系统元文件及空分析目录；保留原始数据、模拟大文件/压缩包、付费请求/图片/终态和 Git 历史。旧导出器仍被新导出器依赖，不删除。

---

## 2026-10-06 — system/skill 拆分与候选 v1（止于离线准备）

- 修订 [实施计划](项目流程与实施计划_20261006.md)，完成阶段 0–3。独立固定契约 `configs/curation/system_v1.txt`；v0 原样保留本地规则，v1 修改拆分依据、形态删除边界、ISI 单位及合并语义，不禁止 DISCARD、不写通道答案。旧 live 入口未改。
- 准备 35 个开发案例（16 专家编辑 + 19 终态约束）、70 份新旧 skill 请求预览、241 条开发人工理由；专家标签与 actor 输入分离，不把终态约束伪造成唯一正确动作。结果：`output/curation_skill_preparation_20261006/`。
- 审计确认没有全项目从未曝光的录音块；`cM2-e007_012-017` 仅从本轮起隔离，不能称 pristine held-out。开发范围及历史曝光写入 `protocol_v1.json`。
- 25 项离线测试通过，新增 API 调用 0。候选 skill 尚无性能结论；未改 MAT/历史结果/画图/controller，未跑 SFT/CL。另发现 Phase 2 单群 ISI 重复首个 spike，留到下一阶段单独修正。

## 2026-10-06 — 候选 skill 规则依据补充

- 在 [实施计划 §11](项目流程与实施计划_20261006.md#11-领域规则依据与限制2026-10-06仅补文档)集中补 S1–S5 与证据不足边界：记录外部来源、本地失败证据及不能推出的结论，不另建报告、不将依据发送给模型。
- 明确本地短间隔比例与 SpikeInterface 污染估计不同；2 ms/0.6% 未经本任务校准，形态与拆分判据仍为候选。原 live 小样本返回 0 等一致性问题留待阶段 4，不在本轮修改。
- 仅文档更新；system、skill 和运行代码未变，未调用付费模型，未改变历史结果或底部待办。

## 2026-10-06 — baseline 描述与当前范围更正

- 文档中旧 GPT/Astra 闭环按条件改称“详细规则提示 rollout”；Qwen/Gemma/API 单步实验称“固定状态动作评测”。保留历史日期、数值和实验配置，不把旧实验改称 few-shot。
- `zero-shot baseline` 标签仅用于当前确认的 minimal-prompt 完整闭环：最少任务说明、图例、动作含义和输出格式；不加载旧 system/skill、示范、专家评论或反馈。
- 已保存两阶段提示词、哈希和结果归档规范，见 [新实验目录](<zero-shot baseline/README.md>)。尚未接入运行器、尚无本配置结果。历史配置标识 `minimal-zero-shot-v1` 仅用于追溯，不是这项新完整闭环的结果。

## 2026-10-07 — 补回 stepwise 与完整闭环双评测

- 纠正前一版 minimal-prompt 文档只写 full rollout 的遗漏：**专家状态下的 stepwise 动作一致性与自主 rollout 终态质量都必须评测**，同一套 prompt/模型/观测，在同一份报告中分栏统计，不混成一个 accuracy。
- Stepwise 通过离线专家动作回放重建操作前状态，模型预测不推进下一样本；rollout 用模型动作推进。专家标签只在状态重建/评分侧使用，不给模型答案、评论或反馈。轨迹分叉后不按步骤序号直接比较。
- Stepwise 报准确率、逐类 precision/recall/F1、混淆矩阵与覆盖率；给定对象的判断不等于自主选对对象。没有可靠 KEEP/NOT_MERGE 标签时不补造。Rollout 报 P/R/F1、误删/误保留与群数，并披露 many-to-one 匹配限制。
- 同日后续：双评测入口与评分已接入，67 项离线测试通过；295 条日志的前后状态哈希均匹配。CH20 第 5 步 `s 0` 为 noise 群恢复，超出活跃群 actor 接口，预先排除评分但保留状态回放；实际评分分母 294。MAT v5 读取兼容已补齐，未改原始数据/提示词。294 条请求及图片哈希逐一校验通过，已按授权启动共享独立 $50 的 Astra/high 任务；首批真实响应已返回，不代表完整实验完成。规范、manifest 哈希及最新状态见 [zero-shot baseline](<zero-shot baseline/README.md>)；保留此前日期记录。

## 2026-10-08 — minimal-prompt 双评测完成

- Astra/high；原 minimal prompt、四图/三图观测、controller 与评测器保持冻结。CH30 追加预算续跑新增 529 次请求全部成功，前 1,060 条只本地重放；99 项离线测试、终态和账本核验通过。四通道完整闭环现均已完成，进程退出。
- Stepwise **102/294（34.69%）**；CH31/CH3/CH20/CH30 终态 F1 **0.5581/0.3327/0.3613/0.3183**，简单均值 **0.3926**，初态均值 **0.3536**。不是单一 accuracy 或 SOTA，通道已曝光，many-to-one 匹配对碎片化处罚不足。
- 全通道误删专家 spikes 为 0；主要问题是噪声误保留。CH30 保留噪声 318,201，11,461 个专家 spikes 仍在但与另一 unit 混群，故 Recall=0.8742。CH30 合并比较 1,330 次中 1,280 次 NOT_MERGE；任务结果弱且调度调用量大。
- 最后续跑约 104 分钟，新增标准估算 $17.730030、保守费用 $20.1635625。全实验 stepwise/rollout 标准估算 **$10.425039 / $94.284763**，保守 **$12.0844315 / $107.8665730**，合计 $119.9510045（含旧 HTTP503 未结算预留 $0.2441875），未用满 $150 上限。均非账单，不自动继续消费。
- 记录 KEEP 噪声、同输入 KEEP→SPLIT 翻转、合并前复查不执行 SPLIT 的具体例子，见 [失败归档](实验失败案例分析与归档.md)；条件与结果见 [最终汇总](<zero-shot baseline/README.md>)。不能把差分全归因于 prompt 或模型，不修改本轮结果。

### 历次预算与恢复记录

2026-10-07 21:24 UTC 预算停止：stepwise **102/294（34.69%）**；CH31/CH3/CH20 完整闭环 F1 **0.5581/0.3327/0.3613**。CH20 此次完成 260 次决策，终态保留 53 群/83,746 spikes，噪声误保留 65,281，专家 spikes 误删 0；相比初态 F1=0.3597 仅小幅变化。CH30 成功 1,060 次决策、合并源群完成 52/94，下一次请求预留不足而停止，不能把中途状态当最终结果。检查点哈希核验通过，进程退出，未再发送请求。本次续跑新增 1,266 次推理均成功；历史响应仅本地重放。标准价估算分项：stepwise $10.425039、rollout $76.554733；保守账本分项 $12.0844315 / $87.7030105，合计 $99.787442，含旧 HTTP503 未结算预留 $0.2441875，非官方账单。详见 [汇总报告](<zero-shot baseline/results/minimal_astra_high_dual_20261007_retry503/REPORT.md>)。保留以下当时的续跑记录。

2026-10-07 服务端错误重试：第一次续跑成功至 CH20 第 54 次，第 55 次收到 HTTP 503 后按零自动重试规则停止。累计保守金额 $50.4833335，含 $0.2441875 未结算预留。用户要求再次尝试；恢复前 54 次历史决策的状态与请求核验通过，82 项离线测试通过，已在独立 `retry503` 目录启动。仍使用累计 $100 上限，不把充值视为新增预算，原提示词/模型/控制器不变，费用继续分项。

2026-10-07 预算续跑：首批 stepwise 294/294，CH31 F1=0.5581、CH3 F1=0.3327；CH20 44 次后因预算停止，CH30 闭环未开始。首批保守费用分项：stepwise $12.0844315、rollout $37.694702。用户追加 $50，累计限额 $100；新增续跑入口离线重建 CH20 原模型轨迹与调度（不重复付费），核验后续跑 CH20/CH30。76 项离线测试通过；保留旧结果，不改提示词。

### 最新待办：实测数据线（高 → 低）

- [x] 接入 minimal-prompt stepwise 回放与评分；核对操作前后状态、标签来源、对象及固定分母，67 项离线测试通过。
- [x] 核查两条路径的实际请求不混入旧 system/skill、专家标签、理由或历史对话；冻结模型、观测、controller、评测器和数据哈希。
- [x] 确认 stepwise 与四通道 rollout 的完整执行范围及共享独立 $50 上限；调用量随闭环轨迹变化，不保证预算足够全部完成，不恢复旧批次。
- [x] 归档两项评测全部完成结果与历史停点，分列条件、指标、调用量和费用；四通道完整闭环已完成。
- [x] 确认追加预算与外发许可，核验 CH30 已付费前缀、调度及未发送请求后续跑；保持本轮 prompt/controller 不变。
- [ ] 离线核对专家噪声定义与 KEEP 判断、同输入动作翻转，以及合并前复查 SPLIT 未执行的动作语义；改动须另立版本，不覆盖此次 baseline。
- [ ] 完成本轮后单独评估合并候选筛选/调度效率；不把省调用的改动混进当前结果。
- [ ] 后续再研究 student skill-learning、负例补标和可选 SFT。

### 最新待办：模拟数据线

- 暂停。大型模拟产物约 9 GiB 为后续空间清理候选；不删除唯一归档，不为整理文档重跑模拟。
