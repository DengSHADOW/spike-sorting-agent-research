# 真实数据与本地 Student 路线状态（2026-09-14）

## 范围与名词

本文只记录真实数据线，不包含模拟 setting 的后续工作。

- `CH` 是 channel，即一次记录中的电极/信号通道编号。相同的 `CH1` 如果来自不同 recording block，就不是同一个数据集。
- 数据集必须用完整 ID 标识，例如 `cM2-e004_004-006_CH3`；不能只写 `CH3`。
- 当前按路径名推定出 5 个 recording block。数据提供者尚未确认 session 信息，因此该分组可用于保守防泄漏，但必须在论文中注明是推定分组。

## 本次实际完成

### 1. 全量预检

对 manifest 中除无标注 CH5 外的 16 个真实 MAT 完成只读预检：

- 成功：16/16；失败：0；
- 检查 MAT 可加载、数组长度一致、最终 `curation.assigns` 存在、源文件 hash 与 manifest 一致；
- 用时约 35.9 秒；每个数据集均保留独立 `preflight.log`；
- 未调用 API，未运行 mock，未修改 MAT。

结果：`output/real_open_vlm_20260914/batch_summary.json`。

### 2. 全量有序动作导出

对同一批 16 个有标注数据集完成全部动作状态导出：

- 1,374 条专家实际执行的动作：`SPLIT 636 / DISCARD 569 / MERGE 169`；
- 241 条动作带人工 rationale，其余 1,133 条为 action-only；
- 生成 1,374 条 JSONL 样本和 5,327 张被引用的状态图，共约 486 MiB；
- 每条样本保留完整数据集 ID、recording block、动作序号、动作前后状态 hash、MAT/Excel 来源与源文件 hash；
- 不做预过滤、自动 discard、合成动作或动作重排；
- 所有动作步号连续，前后状态 hash 可衔接，所有图像存在，所有源文件 hash 前后不变；
- API 调用为 0。

结果：`output/real_manifest_action_dataset_20260914/summary.json` 与 `samples.jsonl`。

### 3. 实际开源 VLM 推理状态

本次没有伪造“模型已跑完”的结果。批量 runner 已在进入数据循环前检查本地 vLLM 服务；当前检查正确停止，原因是：

- 本次受限执行环境中的 `nvidia-smi` 报告 GPU access blocked by the operating system；
- 当前 `.venv` 未安装 `torch / transformers / vllm / unsloth / trl / peft`；
- runner 无法访问 `localhost:8000/v1` 的模型服务；当前也没有在项目环境中配置或启动该服务。

因此，本次完成的是 16 个真实数据集的预检、动作回放和模型输入导出，不是 16 个数据集的开源模型预测。没有回退到 mock，也没有调用 OpenAI API。待 GPU/集群环境就绪后，可以从同一 manifest 直接继续真实推理。

## 各数据集结果

`action↔terminal` 表示动作日志是否能和同一 MAT 的最终人工分群共同视为一条统一真值轨迹。`separate` 的 ARI 只用于量化两个版本的差异，不能据此宣称日志严格产生了该终态。

| 数据集 | 动作数 | rationale | D/M/S | action↔terminal | replay ARI |
|---|---:|---:|---:|---|---:|
| `cM2-e004_004-006_CH3` | 97 | 97 | 60/0/37 | unified | 1.000 |
| `cM2-e004_004-006_CH31` | 54 | 54 | 34/0/20 | unified | 1.000 |
| `cM2-e004_011-015_CH20` | 54 | 0 | 23/4/27 | separate | 0.939 |
| `cM2-e008_021-028_CH30` | 90 | 90 | 41/6/43 | separate | 0.977 |
| `cM2-e004_001-003_CH1` | 99 | 0 | 40/13/46 | separate | 0.894 |
| `cM2-e004_001-003_CH13` | 33 | 0 | 19/2/12 | separate | 0.934 |
| `cM2-e004_001-003_CH16` | 70 | 0 | 35/6/29 | separate | 0.955 |
| `cM2-e004_001-003_CH3` | 97 | 0 | 51/3/43 | separate | 0.837 |
| `cM2-e004_001-003_CH4` | 51 | 0 | 22/9/20 | separate | 0.911 |
| `cM2-e004_004-006_CH1` | 160 | 0 | 60/21/79 | separate | 0.962 |
| `cM2-e004_011-015_CH17` | 60 | 0 | 28/3/29 | separate | 0.949 |
| `cM2-e007_012-017_CH1` | 156 | 0 | 39/40/77 | separate | 0.972 |
| `cM2-e007_012-017_CH2` | 111 | 0 | 34/22/55 | separate | 0.976 |
| `cM2-e007_012-017_CH22` | 94 | 0 | 33/15/46 | separate | 0.973 |
| `cM2-e007_012-017_CH31` | 84 | 0 | 32/10/42 | separate | 0.968 |
| `cM2-e007_012-017_CH7` | 64 | 0 | 18/15/31 | separate | 0.973 |

`cM2-e004_001-003_CH5` 没有动作日志和最终 `curation.assigns`，因此继续排除在训练和定量评测之外；它只能在模型稳定后用于无标注 inference 或人工补标。

## 已修复的问题

| 原问题 | 风险 | 当前处理 |
|---|---|---|
| 只用 `CH` 命名输出 | 不同 recording 的同号通道会互相覆盖 | runner 和导出器改用完整 dataset ID |
| 真实数据默认启用 spike-count discard | 未校准阈值会静默删除真实 cluster | Phase 0/3 默认阈值改为 0，即禁用；仅显式实验才开启 |
| API/provider 失败后自动回退 mock | 会把假结果混进真实实验 | provider 失败直接停止，不再隐式 mock |
| JSON 解析失败默认 `DISCARD` | 格式错误会造成破坏性数据变更 | 返回 `ABSTAIN` 并保持状态 |
| 没有 large merge target 时丢弃 small cluster | “无法判断”被错误变成“噪声” | 保留 cluster 并记录 `ABSTAIN` |
| 所有候选均 `NOT_MERGE` 后丢弃 small cluster | “不同 unit”被错误解释为无效 unit | 保留为独立 cluster 并记录 `KEEP` |
| 训练与运行 Phase 1 输入不一致 | SFT 学到的视觉接口无法复用 | 统一为 waveform、ISI、amplitude、aggregation tree 四图及数值指标 |
| waveform 随机抽样且最多画 5,000 条 | 输入不稳定且真实大 cluster 绘图过慢 | 固定等距抽样，最多 500 条 |
| 旧导出器混合来源并改变状态流程 | 无法说明每个标签来自哪里 | 新导出器严格按原顺序回放并保存 provenance/hash |
| 按 `CH` 划分 train/eval | 同 recording 可能泄漏 | SFT 默认按 recording block 划分 |
| 训练要求 rationale，但大多数数据没有 | 会制造虚假理由或目标不一致 | 第一个 student 默认只输出 `{"action": ...}`；rationale 仅作可选字段 |
| batch runner 长时间捕获全部终端输出 | 大任务难追踪，并额外占用 WSL 内存 | 每个数据集流式写独立日志，汇总只保留路径和末尾 4,000 字符 |

安全逻辑增加了回归测试；2026-09-15 二次复核后为 21 passed。

### 2026-09-15 二次复核结论

- 上表修复在新的 manifest/Qwen 真实数据主线中全部生效。
- 复核发现旧 no-metrics ablation 路径仍保留解析失败和 `NOT_MERGE` 后丢弃 cluster 的旧逻辑；已同步改为 `ABSTAIN`/保留，并修正了与实际执行相矛盾的 prompt。
- Phase 2 大 cluster 预复核只有明确 `KEEP` 才能成为 merge target；若返回 `SPLIT/ABSTAIN`，保留该 cluster 但禁止其他 cluster 合并进去。
- 以实际 1,374 条导出样本重新检查 split：train 973、validation 90、final test 311；recording block 之间无交叉。
- `scripts/run/run_all_channels.py`、`run_single_channel.py` 和历史 ablation runner 仍是旧实验复现入口：它们显式使用 CH-only 数据目录及 500/5000 数量阈值。这些阈值不是新真实数据主线的默认值；新实验必须使用 `run_real_manifest.py`。

## 现有数据到底能训练什么

### 可以直接使用

1. **动作干预监督：** 1,374 条样本可靠记录专家实际执行的 `SPLIT / DISCARD / MERGE`。它们适合训练“何时执行这些编辑”的正例，并可做 teacher-forced action recall/格式正确率评测。
2. **最终分群评测：** 16 个 MAT 都有最终 `curation.assigns`，可对 agent 自主运行后的终态计算 ARI 等 cluster-level 指标。
3. **统一轨迹评测：** CH3 和 CH31 共 151 步能精确回放至对应终态，可同时报告 action-level 与 cluster-level；二者属于同一 recording block，必须一起放在同一个 split。

### 不能直接宣称完整

现有 action log 记录的是“做了哪些编辑”，不是“专家检查过哪些候选以及为什么没编辑”。因此：

- 没有显式 `KEEP` 样本；
- 没有显式 `NOT_MERGE` 样本；
- merge 阶段的 169 条监督全是实际 `MERGE`，无法估计错误合并的 specificity；
- 直接用当前三类数据训练六类决策 agent，容易学成过度 SPLIT/DISCARD/MERGE。

这不是说 1,374 条数据无用，而是它们目前是**正向编辑日志**，还不是完整的 decision dataset。

### rationale 的边界

241 条人工 rationale 全部位于 CH30 以及 CH3/CH31：

- 默认 train blocks：973 条动作、0 条人工 rationale；
- validation CH30：90 条动作、90 条 rationale；
- final test block：311 条动作，其中 CH3/CH31 的 151 条有 rationale。

所以当前最健康的第一步是 action-only SFT。若拿现有 rationale 做训练，就必须重新分配 recording block，并失去当前干净的 rationale validation/test；不能为了“训练 reasoning”而把测试信息泄漏进训练。

## 默认无泄漏划分

| Split | recording block | 数据集数 | spikes | 动作 | D/M/S | 用途 |
|---|---|---:|---:|---:|---:|---|
| Train | `cM2-e004_001-003`, `cM2-e004_011-015`, `cM2-e007_012-017` | 12 | 3,162,514 | 973 | 374/142/457 | action-only SFT |
| Validation | `cM2-e008_021-028` | 1 | 430,937 | 90 | 41/6/43 | CH30 base/SFT 模型选择 |
| Final test | `cM2-e004_004-006` | 3 | 372,558 | 311 | 154/21/136 | 最终一次评测；CH3/CH31 可做统一轨迹指标 |

由于 recording block 是从文件名推定的，数据提供者若能给出真实 session 对应关系，应优先替换该划分。

## 后续主线

### A. 先得到真实 base-model baseline

1. 在能看到 NVIDIA GPU 的 WSL 或 A100 集群建立独立 open-model 环境；不要把大型训练依赖强塞进当前轻量 `.venv`。
2. 启动本地 Qwen3.5-4B 服务后，先跑 CH30 小规模 smoke，确认四图顺序、JSON action、`ABSTAIN`、显存和单步耗时。
3. smoke 通过后，先做固定状态的 teacher-forced inference，再做自主 rollout。两种结果不能混报。
4. 比较 base Qwen 与一个 Gemma 候选后再选 SFT backbone，不因仓库已有脚本就预先认定模型。

### B. 补齐完整 decision dataset

1. 可从最终 active clusters 导出候选 `KEEP`，但必须标为 `derived_terminal_keep`，不能冒充有序专家动作。
2. `NOT_MERGE` 最好由专家对候选 pair 补标；若用最终终态关系或规则构造，只能标为 derived negative，并单独做敏感性实验。
3. API teacher 可以给 train blocks 的候选负例提供蒸馏标签，但它们属于 teacher-generated supervision，不是 human ground truth；人工 validation/test 保持不动。
4. 在 `KEEP / SPLIT / DISCARD` 和 `MERGE / NOT_MERGE / DISCARD` 两个阶段分别审计覆盖和混淆矩阵。

### C. 再做 SFT 与自主 agent 评测

1. 先训练 action-only LoRA/SFT student；当前脚本默认按 recording block 防泄漏。
2. 同时报告 base 与 SFT 的 action recall、macro-F1、非法输出率和 abstention rate。
3. 将 student 放回 pipeline 自主运行，报告终态 ARI、cluster 数偏差、错误 merge/split 数量，并单列规则动作与模型动作。
4. 只有 CH3/CH31 可把动作与终态作为严格统一真值；其他数据的 action-level 和 cluster-level 结果必须分开解释。

### D. RAG、偏好优化与 RL 的顺序

- **RAG：** 在稳定的 base/SFT baseline 后，固定只含 train blocks 的 memory，在完全隔离的 validation/test block 做 no-RAG/RAG 对照。
- **DPO/偏好优化：** 只有拿到 chosen/rejected pair 后才合理，尤其适合补足 merge 候选偏好。
- **RL：** 当前没有稳定 reward、完整负例和可靠 rollout baseline，不应现在做 online RL。若以后做，先从可审计的 offline preference/reward 设计开始。

## 可复现命令

```bash
# 16 个有标注 MAT 的只读预检；默认排除 CH5
uv run python scripts/run/run_real_manifest.py \
  --mode preflight \
  --output-dir output/real_open_vlm_20260914

# 全量有序导出；按数据集断点续跑
uv run python scripts/finetune/export_real_manifest_actions.py \
  --output-dir output/real_manifest_action_dataset_20260914 \
  --resume

# 本地 vLLM 服务真正就绪后，固定人工状态，只跑 CH30 前 3 个样本
uv run python scripts/test/eval_unit_actions_from_dataset.py \
  --input-jsonl output/real_manifest_action_dataset_20260914/samples.jsonl \
  --dataset-root output/real_manifest_action_dataset_20260914 \
  --eval-channels CH30 \
  --provider vllm \
  --model Qwen/Qwen3.5-4B \
  --max-samples 3 \
  --disable-thinking \
  --max-tokens 32 \
  --output-dir output/real_qwen35_ch30_smoke_20260915
```

`run_real_manifest.py --mode run` 会执行完整自主 channel，不带 3-step 上限；它应留到固定状态 baseline 通过之后。

## 新 GitHub 仓库发布边界（2026-09-15 审计）

- 当前 `origin` 仍是 `jiseshen/spike-sorting-agent`；不应 push 或改写该 remote。
- 当前旧 Git 历史约 19 GiB，而已跟踪工作树文件约 248.5 MB，其中约 246.4 MB 是历史 `output/`。现在将 `output/` 加入 `.gitignore` 不会把这些已跟踪历史从旧仓库删掉。
- 新仓库必须从当前工作树生成无旧历史的 clean snapshot，只包含源码、配置、测试和文档。
- 必须排除 `.git/`、`.env*`、`.venv/`、`output/`、`data/`、两个外部真实数据目录、MAT/HDF5/FIG、压缩包和模型权重。`.env.example` 只保留空变量名和本地 endpoint 示例。
- 对当前已跟踪文件的模式扫描未发现 OpenAI/GitHub/Anthropic token 或私钥格式；正式 push 前仍需对 clean snapshot 再扫描一次。
- 项目当前没有 LICENSE，且是合作项目。在 Tianmin Shu/JianZhi Shen 确认公开范围和代码归属前，新仓库应设为 private。

## 26 GB 真实数据中的图像与 VLM 关系

`Jacob Bedke-Annotated_spike_sorting_data_w_chronux/` 实际大小约 27.85 GB：

| 类型 | 数量 | 大小 | 当前用途 |
|---|---:|---:|---|
| MATLAB `.fig` | 7,056 | 25.96 GB | 人工 curation 中间界面/图形档案；当前 Python/VLM 不读取 |
| MAT | 24 | 1.90 GB | 当前主线必需；提供 waveform、spike time、hierarchy、action 和 terminal assignments |
| Excel | 1 | 约 20 KB | CH3/CH30/CH31 等人工 action/rationale 来源 |
| Chronux/辅助代码 | 若干 | 约 1.7 MB | 参考 MATLAB 工具，不是 VLM 训练样本 |

`.fig` 不是当前 VLM 直接输入。它们保存每个 action step 的 waveform、overlaid waveform、histogram、correlogram 和 aggregation tree，因此对“重现原 MATLAB 界面”、人工审计和将来加入 correlogram 观测有备用价值，但不是当前 baseline/SFT 的必需文件。

当前真实 VLM/SFT 数据是从 MAT 回放状态后由 Python 重新生成的 `output/real_manifest_action_dataset_20260914/`：1,374 个 action sample、5,327 张 PNG、约 486 MiB。Phase 1 使用 waveform overlay、ISI histogram、amplitude distribution 和 aggregation tree；merge 阶段使用 small waveform、large waveform 和 merged ISI。

当前结论：

- 25.96 GB `.fig` 可从活跃 workspace 移到外部归档，不影响当前 VLM 和 SFT；如未确认备份，不建议直接删除唯一副本。
- MAT 和 action workbook 要保留，因为它们才是可重建训练样本和评测目标的源数据。
- 已导出的 486 MiB PNG 是即将给 base VLM/SFT 使用的直接输入，但目前尚未被开源 VLM 实际推理；现有成功结果只证明数据回放、图像生成、schema 和无泄漏 split 正确。
- GitHub 新仓库不上传 `.fig`、MAT、PNG dataset 或任何 `output/`；它们均可由本地源数据/脚本重建。

### 2026-09-15 CH30 真实图片 smoke 尝试

- 已按真实 Qwen/vLLM 路径启动检查；未使用 mock、未调用外部 API、未读取或修改 MAT。
- 当前 Codex 沙箱内没有运行中的 vLLM 服务，项目轻量 `.venv` 也没有 `torch/transformers/vllm`，因此在模型调用前安全停止，没有生成成绩。
- Windows/WSL 主机可识别 RTX 3070 Laptop GPU（8 GiB）；Qwen3.5-4B 官方 BF16 权重约 9.34 GB，正式本地运行需验证 4-bit 方案，或优先在 A100 上保持未量化 baseline。
- smoke 应使用上面的固定状态 `--max-samples 3` 命令；完整自主 channel 不是 smoke。

## 最新待办（高 → 低）

- [ ] 修复 WSL GPU 可见性，或迁移到 A100 集群；安装并启动本地 open VLM 服务。
- [ ] 在 CH30 做真实 base Qwen 小规模 smoke，再完成固定状态 action-level baseline；不调用 API、不使用 mock。
- [ ] 补齐或明确派生 `KEEP / NOT_MERGE`，形成完整 decision dataset，并保留 human/derived/teacher provenance。
- [ ] 训练 action-only LoRA/SFT student；以 CH30 validation 选模型，最后只在 `cM2-e004_004-006` block 做一次 final test。
- [ ] 运行自主 pipeline，分开报告 action-level 与 cluster-level，并审计错误 merge/split 和 abstention。
- [ ] baseline 稳定后再做固定 train-memory RAG；偏好数据和 reward 定义成熟后再考虑 DPO/RL。
