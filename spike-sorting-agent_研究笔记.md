# spike-sorting-agent 研究笔记

**最后更新**：2026-08-18

## 项目目标

使用 waveform、ISI、aggregation tree 和质量指标，让 VLM agent 完成 spike sorting 后的 `KEEP`、`SPLIT`、`MERGE`、`DISCARD` 决策。

- **实测数据线**：CH3、CH20、CH30、CH31 MATLAB 数据。
- **模拟数据线**：MEArec + MountainSort5，用于可控 GT、trajectory、adaptation 和跨 setting 实验。

## Proposal 功能状态

| 方向 | 状态 | 当前判断 |
|---|---|---|
| 实测数据 VLM curation | 🟢 已实现 | 已有 GPT-4.1、GPT-5.1、baseline、消融和人工参考结果 |
| MEArec benchmark | 🟡 部分完成 | `setting_001` 已跑通；`setting_002/003` 未完成 |
| Expert-like action trajectory | 🟡 部分完成 | Stage 2 已运行，但 ch_017 存在无进展 SPLIT 问题 |
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

## 2026-08-18 — 最新待办

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

### 最新待办

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

### 最新待办

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

### 最新待办：真实数据线

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

### 最新待办：真实数据线（高 → 低）

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
- 回归测试通过：21 passed（2026-09-15 二次安全复核后）。详细结果与路线见 `REAL_DATA_OPEN_VLM_STATUS_20260914.md`。

### 当前边界

- 现有 1,374 条是专家实际执行的正向编辑日志，没有显式 `KEEP / NOT_MERGE`；可用，但尚不是完整自治 agent decision dataset。
- 默认 train blocks 共 973 条 action-only，人工 rationale 全在 CH30 validation 和 CH3/CH31 final-test 数据中；当前不应为训练 reasoning 而泄漏评测 block。
- 本次未完成开源 VLM 预测：本次受限执行环境无法访问 GPU 和本地 vLLM 服务，且项目 `.venv` 未安装 open-model/训练依赖。启动检查在处理数据前安全停止，没有产生 mock 成绩。

### 最新待办：真实数据线（高 → 低）

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

### 最新待办：真实数据线（高 → 低）

- [ ] 修复 WSL GPU 可见性，或转到 A100 集群；建立独立 open-model 环境并启动本地 VLM 服务。
- [ ] 先在 CH30 跑真实 base Qwen 小规模 smoke，再完成固定状态 action-level baseline；不调 API、不用 mock。
- [ ] 补齐或明确派生 `KEEP / NOT_MERGE`，严格区分 human、derived 和 teacher provenance。
- [ ] 训练 action-only LoRA/SFT student；用 CH30 validation 选模型，最后只在 `cM2-e004_004-006` block 做 final test。
- [ ] 完成自主 pipeline 评测，分开报告 action-level 与 cluster-level，并审计错误 merge/split 和 abstention。
- [ ] baseline 稳定后再做固定 train-memory RAG；偏好数据与 reward 成熟后再考虑 DPO/RL。
