# 新 Codex 接手说明：Runpod 上的 Qwen9B 与 harness

> **2026-10-26 部署更新，优先于下文旧的 9B 范围：** 用户明确改为使用
> `Qwen/Qwen3.5-27B` 和 `google/gemma-4-E4B-it`，并授权删除本地 9B、下载和
> 准备部署两个新模型；当前是 CPU，暂不使用模型。9B 缓存及其独有共享 blob
> 已删除，历史实验结果保留。两个新模型的固定 revision BF16 权重已下载，
> 全部官方文件校验通过。独立 `.venv-vllm` 已安装 vLLM 0.32.0 / PyTorch
> 2.13.0（CUDA 13.0 build），198 项依赖一致性检查通过；两个模型的 config
> 和 processor 已在离线模式加载验证，**没有加载权重、启动推理或运行实验**。
> 切换 GPU 后仍需检查驱动兼容性与真实加载。见
> [部署说明](docs/LOCAL_MODEL_DEPLOYMENT.md)、`configs/local_models.json`、
> `scripts/deploy/local_models.py` 和 `output/local_model_setup_20261026/`。
> 旧 minimal/harness 的 OpenRouter transport 尚未迁移，本次没有改实验协议。

更新：2026-10-19。本文是给开发助手的交接 prompt，**不是发送给实验模型的 system prompt 或 skill**。先读本文、核对实际文件，再工作；不要仅凭旧聊天或文档标题判断任务已完成。

## 1. 用户现在要做什么

用户不再以 WSL 作为日常工作环境。目标是：Windows 原生 VS Code → Remote-SSH → Runpod 项目，使用 Codex 开发，在 GPU Pod **自部署 Qwen/Qwen3.5-9B** 跑真实数据 curation/harness。

- 当前主线只做真实数据，不恢复模拟路线，不自行扩展到 27B/其他模型、SFT 或 RL。
- 近期四通道范围为 CH3、CH20、CH30、CH31。CH 是电极通道，不是神经元；文件和输出必须使用完整 dataset ID。
- 先完成远程开发环境和本地模型接入验证，再明确/冻结实验协议，之后才进行正式实验。文件迁移不等于部署完成。
- 旧 OpenRouter 9B 任务后期已收缩为 minimal-only；该批次的 harness 组已取消。新的自部署 harness 是新任务，不能直接恢复旧双组运行器。
- 本交接文件不授权新增付费 API 请求、租卡/扩容、覆盖旧结果或删除文件。旧授权文件和旧预算不能当作新任务授权。可以先做只读检查、离线适配和测试。

## 2. 已核验的迁移状态（2026-10-19 快照，开工时再检查）

| 项目 | 状态 |
|---|---|
| 云端项目 | `/workspace/spike-sorting-agent` |
| Git | `main`，`d75c8c94918265dadf2107b3360dabd63212eec2`；迁移时工作区干净，本文是之后新增的未提交文件 |
| 用户仓库 | `https://github.com/DengSHADOW/spike-sorting-agent-research.git` |
| 上游参考 | `https://github.com/jiseshen/spike-sorting-agent`，仅用于溯源，不向其提交 |
| 网络卷 | `spike-sorting-data`，ID `vj1cvih69g`，US-CA-2，100 GB，挂载 `/workspace` |
| 文件 | 126,000 个普通文件、28 个符号链接，31.1316 GiB；迁移时全部 SHA-256/链接目标校验通过，不含之后新增的本文 |
| 旧云端备份 | `/workspace/spike-sorting-agent-backup-20261019-dbdd176`；不要覆盖或删除 |
| 迁移证据 | `output/runpod_migration_20261019/STATUS.md`、`files.json`、`verification.log` |
| 上次剩余空间 | 2026-10-19 追加删除 Gemma 后约 34.63 GiB（37.18 GB）；开工仍须重查，不能直接下载新大模型 |
| Pod | 迁移所用 `4676br22z7acsn` 是临时无 GPU 恢复环境，实测约 0.5 CPU / 488 MiB 内存；不是正常 GPU 推理环境 |

Git 是当前提交的 shallow 副本，没有搬 WSL 中约 18 GiB 的旧 Git 历史。不要无故 fetch 全历史。用户本地原文件仍保留，其他项目 `rl_homework` 未动；迁移完成后另按用户授权清理了部分云端缓存，见下文。

**没有迁移：** `.env`、私钥、简历/个人职业材料、WSL `.venv`、Codex/Claude 登录会话。项目外已有 `/workspace/.venv` 不代表能用于本项目，不要擅自改它。当前任务另外将私人资料与项目凭据备份到 Windows 桌面 `spike-sorting-private-backup-20261019`；不要把它们同步到云端/Git，也不要打印密钥。

2026-10-19 用户随后授权清理云盘：首批删除旧 Qwen3.5-2B/4B 下载缓存和 vLLM 编译缓存，实测释放约 13.16 GiB；同日 14:28（纽约）按用户追加授权删除 `google/gemma-4-E4B-it` 缓存，再释放约 14.92 GiB，剩余约 34.63 GiB（37.18 GB）。记录在 `output/runpod_cleanup_20261019/STATUS.md`。真实数据、全部实验结果/断点、旧云端项目、`.venv`、uv 缓存和其他项目保留。

`/workspace/.cache/huggingface/hub/` 现在仅保留 **Qwen3.5-9B** 模型缓存。9B revision 为 `c202236235762e1c871ad0ccb60c8ee5ba337b9a`：四个权重分片及 snapshot 链接存在，清理前后文件大小/修改时间不变，配置和共享 tokenizer 哈希未变；删除 Gemma 后再次核对 9B 文件大小/修改时间、无断链、tokenizer 哈希不变。**尚未加载模型验证可用性，也未对全部 9B 权重重算哈希**。不要重复下载。编译缓存已清除，后续启动可能需要重新编译；本次清理不授权继续删除其他缓存。

网络卷独立于 GPU；新 GPU Pod 应挂同一卷。停止/更换 Pod 后 SSH IP/端口可能变化，重新从控制台读取。容器中的系统安装、`/root` 下工具/扩展/认证不保证持久化；不要把网络卷文件持久化等同于整个运行环境持久化。

## 3. 先读哪些文件

按需阅读，不必逐张打开十几万个产物：

1. 本文与 `output/runpod_migration_20261019/STATUS.md`。
2. `README.md`、`项目方法与实验状态.md`。
3. `项目流程与实施计划_20261006.md`，尤其是 **2026-10-19 controller 调度审计**。日期晚的追加记录可能更正前面的判断；例如早期“瓶颈不在 controller”不能当作已证实的普遍结论。
4. `zero-shot baseline/README.md`：实验条件、两种评测、历史结果及后续更正。旧双模型计划不覆盖用户最新的“只做 9B/迁往自部署”范围。
5. 最新 9B 状态：`zero-shot baseline/runs/qwen9b_rollout_comparison_20261013_8000_thinking6000_v2/{execution_status.json,REPORT.md,attempt_004/summary.json}`。
6. `实验失败案例分析与归档.md`；需要精确复核调度时读 `output/controller_scheduling_audit_20261019/audit.json`。

## 4. 最近实验事实，避免重复花钱

- Astra 与 Opus minimal：四通道 stepwise 和 full rollout 已完成，有可复核的历史报告；不要自动重跑。当前可比的一对一四通道平均 F1 为 0.3229 / 0.4442，不能与早期 many-to-one 分数混用。
- 最新 9B minimal-only 批次已停止：CH31 完成 67 次判断，一对一 F1 = 0.6823667684；CH3 到第 27 次发生不可执行 leaf SPLIT，未完成、无正式终态分数；CH20 因 HTTP 429 重试耗尽未完成；CH30 尚未开始。该批次 harness 组没有新调用。
- 9B 历史存在其他 token/采样条件的批次，不要与最新结果拼成一个完整实验。最新 8000 总输出 / 请求 6000 thinking 的配置中，**6000 并未验证为硬性上限**，不能保证为动作保留 2000 tokens。
- 27B 另有已运行的历史结果，但当前不继续扩展它。各批次费用可能继承旧费用及未结算预留；分别读账本，不简单求和，不把预留当实际账单，不把历史额度当当前余额。
- CH1 skill_v0 + Astra 的 live 闭环及六个开发通道的免费策略/受限 oracle 已有结果；oracle 读标签、有特权，不是模型成绩。可达 F1=1 不证明观测充分或 controller 没有缺陷。
- skill_v1 与拆分改动 S1–S5 的既有诊断验证没有建立胜出，保留 skill_v0。反馈→修改 skill→验证→接受/回退的完整 continual learning 循环，及成功 SFT/RL 训练，都不能写成已经完成的成果。

## 5. 自部署接入的关键陷阱

**不能只改模型名或设置 `VLLM_BASE_URL` 就启动旧运行器：**

- `scripts/run/run_qwen9b_rollout_comparison.py` / `scripts/run/complete_qwen9b_minimal.py` 仍使用 OpenRouter 专用 transport/计费/路由；其下游 `scripts/run/run_openrouter_smoke.py` 的 `BASE` 指向 OpenRouter。直接执行可能继续向外发图并计费。
- `src/agent/api.py` 有 vLLM 适配，默认地址 `http://localhost:8000/v1`；但 `scripts/run/run_single_channel_qwen35.py` 走 `PureVLMCurationPipeline`、默认 **Qwen3.5-4B / thinking off**，不是已有 minimal/harness 实验的同一套入口。
- `scripts/run/run_qwen35_full_cycle.sh` 含微调流程，不是通用的“部署 Qwen9B”命令。
- 历史 manifest/快照可能含 `/home/shadow_linex/spike-sorting-agent` 绝对路径。先审计，再以运行时路径映射/新配置解决；不要全仓字符串替换、改写历史 manifest 或破坏哈希验证。

应复用的核心与协议：

- controller：`src/pipeline/curation_engineering_v1.py`（`CurationController`）。
- 观测：`src/agent/curation_observation_v1.py`（`raw_member_v1`）。
- minimal 请求：`scripts/run/run_minimal_prompt_baseline.py::build_request`；原文字在 `zero-shot baseline/prompts/{phase1.txt,phase2.txt,SHA256SUMS}`。
- 冻结 skill 组：`src/agent/curation_contract.py`、`configs/curation/{system_v1.txt,skill_v0.json,protocol_v1.json}`。不要把 minimal 定义成随便删掉 skill；应沿用已冻结 minimal prompt。
- 评分：`scripts/analysis/curation_one_to_one.py`、`configs/curation/one_to_one_scoring_v1.json`。
- 离线回归入口参考：`tests/test_minimal_prompt_baseline.py`、`tests/test_curation_engineering_v1.py`、`tests/test_curation_contract.py`、`tests/test_curation_one_to_one.py`、`tests/test_qwen9b_rollout_comparison.py`、`tests/test_qwen9b_minimal_completion.py`、`tests/test_qwen9b_transport_retry.py`。网络与密钥读取应在离线测试中阻断。

## 6. 按顺序做下一步

1. **只读接管。** 检查 Git status/diff/log、运行进程、挂载、磁盘、GPU 与 cgroup 的实际资源限制；不要用宿主机 `free` 总量代替 Pod 可用内存。先给用户短状态说明，不自动接续旧实验或启动收费资源。
2. **Windows 远程开发。** 从 Windows 原生 VS Code Remote-SSH 打开 `/workspace/spike-sorting-agent`；确认终端与 Codex 的 cwd/hostname 都在云端。Codex 扩展应在该 SSH 远端启用；扩展自带可执行程序，只用侧栏不需要另装 CLI，只有需直接在终端运行 `codex` 时才另行配置。不要擅自覆盖 `chatgpt.cliExecutable`。Windows SSH 私钥/账号登录要单独配置，不能假设原 WSL 认证已迁移。
3. **GPU/空间/依赖。** 正式自部署需实际可用 GPU。先检查 9B 缓存、模型 revision/权重、剩余空间与 Python/CUDA；确认需要清理/扩容时先列明确目标与影响。使用本项目独立环境，不无脑安装含模拟路线重依赖的整份依赖，也不影响其他项目。
4. **独立 self-host provider/runner。** 沿用冻结的 controller、观测、动作定义和评分，新增/验证本地模型调用边界，不改历史运行文件。记录模型 revision、dtype/量化、vLLM/依赖版本、chat template、多图/schema支持、sampling、thinking 与输出上限。核对当前工具链支持后再确定启动参数。
5. **离线测试，然后小 smoke。** 先验证 dry-run、图片/请求哈希、答案隔离、合法动作、截断/解析失败停止、预算/耗时限制、检查点恢复与本地端点限制。获得本轮运行范围/成本授权后，做 Phase 1 四图和 Phase 2 三图各一条真实请求，核验请求包含指定图片、服务端接受多图并返回合法动作及 usage。一次成功响应不证明模型有效利用视觉信息；不能只看服务 `ready` 就算验证成功。
6. **先定 controller 版本，再冻结正式对照。** v1 存在复查 SPLIT 不执行、叶子 SPLIT 停通道、逐对比较成本高等已审计问题。v1.1 候选修法在计划里，仅是提案，不能声称已实现。若修改，独立版本化、免费回归/上下限验证，并给 minimal 与 skill 组使用同一 controller；不要只让一组享受修复。
7. **正式实验与收集。** 开跑前清楚告诉用户：四通道/哪个组、prompt/skill/controller版本、模型配置、调用/租卡预算、停止条件。建新输出目录，保存请求与图片哈希、完整响应、事件/前后状态、耗时、错误与恢复记录。独立记录 GPU 时间/费用和任何外部 API 费用；不继承历史授权。
8. **baseline 后再学习。** 冻结可信 baseline 后才做 skill/harness 优化；固定 system prompt、动作/评分与可变范围，按录音块隔离开发/评估。无需先上 SFT/RL，不把只有规则调用的流程描述成已完成自主工具调度或持续学习。

## 7. 数据与评分不可混淆

四通道完整 ID：`cM2-e004_004-006_CH3`、`cM2-e004_011-015_CH20`、`cM2-e008_021-028_CH30`、`cM2-e004_004-006_CH31`。CH3/CH31 共享 recording block；四通道已经反复曝光，不能称 untouched held-out。遵守 protocol 的 development/reserved/quarantined 范围，**不要打开 reserved/quarantined 数据，e007 保留**。

- 输入来自真实 MAT 的波形、时间、初态/聚合树，程序动态生成 Phase 1 四图、Phase 2 三图及数值；不是直接把全部原始 26 GB 图片上传，也不是重新从原始电信号做 spike detection。
- minimal 推理不发送专家答案、专家评论/理由、终态标签、示例、skill、RAG 或 teacher 反馈。专家信息只在隔离的本地参考重建/评分中用。
- stepwise：在专家动作前状态预测动作，比较动作是否相同。既有四通道合格样本为 294；CH20 `s 0` 恢复操作排除预测评分，但用于重建后续状态。无 KEEP/NOT_MERGE 参考样本，不能补造。
- full rollout：从初态执行模型动作至正常结束，再与同一 MAT 的 `curation.assigns` 比较。动作轨迹分叉后不能按第 N 步和专家强行对齐。未完成检查点不是终态，不给它正式 F1，也不冒填 0。
- 主终态口径：共享 spike 行号，一对一 Hungarian / IoU≥0.5、全部输出参与分母；同时报误删专家 spikes、保留参考噪声、群数/匹配率。many-to-one 可补充但分开标注；不能只删掉未匹配群后报高分，也不能把它冒称完整 SpikeInterface 时间匹配实验。

## 8. 长期操作边界

- 不修改原 MAT/专家标注，不覆盖历史 output、付费响应、账本、冻结 prompt/config。未经授权不把不可执行动作改成 KEEP/DISCARD、不回退 mock、不无上限重发。
- 进程停止、失败、完成都要明确告诉用户；不能声称会持续后台监测却没有实际机制。长任务使用可核验的保活/状态文件，停止 Pod 会终止进程，不能靠 tmux 跨关机续跑。
- 不上传数据、模型权重、结果大文件、`.env`、私钥、个人简历到 GitHub；不自动 commit/push，不改用户的 origin/main，不自动新建分支。
- 不删除旧日期研究记录。更新只写项目方法、证据、结论及必要待办，简洁地按日期追加；知识问答、个人简历、操作闲聊不混进研究笔记。
- 当前迁移记录只是过去核验结果。资源、文件、模型支持、价格和运行状态均须当场检查；有冲突先报告证据与影响，不把猜测写成事实。

**给新 Codex 的第一条指令：** 请按本文只读核对当前环境、代码和最近运行状态，列出自部署 Qwen3.5-9B 还缺哪些条件，以及你准备复用/改动哪些入口。先报告，不发外部推理请求、不重跑旧实验、不清理文件或创建收费资源。
