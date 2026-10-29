# 人类反馈优化：ACE＋GEPA Agent

2026-10-29 补充：严格专家重放另有 `--online-learning` 批量正负反馈修订入口，支持 N=1/4/8 对照。它不调用本文的 GEPA 搜索引擎；机制、验收和链式实验见 [批量反馈实验说明](批量正负反馈学习_N1_N4_N8_20261029.md)。

本地开发目录：`spike-sorting-agent-tianmin`。基于 harness-v2；运行期间 controller 固定。

当前主方案：累计 N 条有效反馈 → ACE 式反思与经验整理 → 冻结建议 → GEPA 搜索 skill → 验证 → 在轮次边界加载。GEPA 使用官方 `gepa==0.1.4` 的真实搜索引擎与 instance-Pareto 候选选择。ACE 部分是针对多图 spike curation 自行实现的反思/整理/增量建议流程，**不声称复现 ACE 原始 benchmark，也未引入 ACE 原库**。

`run_learning_agent.py` 是当前主入口。较早的 `run_feedback_optimization.py optimize` 保留作单候选反思对照；它不执行 ACE＋GEPA 搜索。

当前结果衡量同一观测上的动作一致性；合成测试只验证工程闭环。真实 API 兼容性、人工反馈质量、跨数据泛化和自由运行终态收益尚未验证。

## 0. 当前主入口与实验参数

在已有项目运行环境中安装搜索依赖：

```bash
python -m pip install -r requirements-feedback.txt
python scripts/demo/demo_ace_gepa.py --output output/ace_gepa_demo_001
```

Demo 使用真实 GEPA 和模拟模型/反馈，不联网、不读 MAT、不产生模型费用。它完整执行计数、去重、ACE 整理、GEPA 搜索、验收和版本激活；输出中的提升是预设的合成行为，不作为算法效果证据。

| 实验变量 | 设置位置 | 含义 |
|---|---|---|
| 反馈频率 N | `init/configure --feedback-batch-size` | 正整数或 `manual`；累计 N 条唯一有效反馈触发一次 |
| 执行与评估模型 | learning config 的 `execution` | 使用相同模型及推理配置比较旧、新 skill |
| ACE 反思/整理模型 | `reflection` | 阅读学习案例原图、指标、模型判断和专家反馈 |
| GEPA 候选生成模型 | `proposal` | 在冻结的经验建议范围内选择与改写 |
| 搜索预算 | `search.max_proposals`、`max_evaluations` | 搜索迭代上限、执行评估调用上限；与 N 分开 |
| 反思图像批大小 | `reflection_chunk_size` | 1–8 个案例一批，默认 2；每例仍携带完整原图 |
| 搜索随机种子 | `search.seed` | 控制 GEPA 搜索；不等于执行模型的采样种子 |
| 接受条件 | `min_gain`、`max_regressions` | 默认验证准确率严格提高、无新增回归；始终禁止新增误删/误合并 |
| 末尾不足 N 条 | `optimize --force` 或 `--flush-feedback-at-end` | 显式处理剩余反馈；默认保留待处理 |

准备 `role=validation` 的独立选择案例 bundle（格式见下文），再复制 `learning.example.json` 和 API 配置到 `private/` 并填好实际模型。配置里的 profile 路径按 learning 配置所在目录解析；会话保存完整配置快照，不保存密钥值。

```bash
python scripts/run/run_learning_agent.py init \
  --session output/learning_session_001 \
  --validation output/validation_bundle_001 \
  --config private/learning.json --feedback-batch-size 10

python scripts/run/run_learning_agent.py ingest \
  --session output/learning_session_001 --bundle output/learning_bundle_001

python scripts/run/run_learning_agent.py optimize --session output/learning_session_001
python scripts/run/run_learning_agent.py status --session output/learning_session_001
```

`ingest` 只校验、复制和计数，不调用模型。`optimize` 达到 N 时处理最早的 N 条；不足时返回 waiting。离线每次命令最多处理一批；manual 模式须加 `--force`。用 `configure --feedback-batch-size 1/10/50/manual` 调整下一次触发阈值，已有待处理记录保留。这些数字是配置示例，不是已确定的实验组。

接受和拒绝都会把本批标为已处理；失败保留待处理记录并阻止自动重复调用，明确使用 `--retry-failed` 才重试。中断后先运行 `recover --session ...`，它只核对已保存结果，不调用模型；若结果已完成则恢复登记，避免重复优化。

同一个状态/判断对象重复导入不增加计数；pending 反馈修订替换原记录，不能当作新反馈。已被优化消费的反馈发生修订时，首版明确报错，需从修正后的反馈另建会话，避免静默保留基于旧标签的已学习规则。会话内 bundle 和 active skill 使用相对路径，整个会话目录可以一起复制到 RunPod；历史 manifest 保留原始来源路径仅作审计。

## 0.1 在 agent 运行中学习

```bash
python scripts/run/run_local_rollout.py \
  --api-profile private/execution_api.json --max-api-calls 200 \
  --learning-session output/learning_session_001 --review-mode pause \
  --dataset cM2-e004_004-006_CH31 --output-root output/adaptive_rollout_001
```

每轮集中判断并执行完成后，生成 `rollout/reviews/round_XXXX.json` 并暂停。填写其中要审核的行（未审核行保持 `expert_action=null`），按 Enter 后自动导入，达到 N 则优化。`poll` 模式不等待输入，仅在后续轮次边界检查这些文件。默认 pause 适合第一版人工审核流程；尚未自动接入旧网页 UI 的导出格式。

同轮所有请求使用同一个 skill。优化只改变后续判断规则，配对、冲突处理、复核与停止算法不变。轮次边界最多处理 `max_updates_per_boundary` 批（默认 10），剩余批次留到后续边界，防止一个边界出现无限优化。达到运行限额的 stopped 状态不会自动触发完整 dataset 末尾 flush。

实时执行模型 profile 必须与 session 的 `execution.profile` 相同。执行模型、ACE 模型和候选生成模型各自记录调用账本；`--max-api-calls` 限制 agent 本身，session 配置限制每次优化，不是整个实验的统一美元上限。不同执行模型建议使用独立会话。

启动固定基线时不传 `--learning-session`，使用同一份 seed skill。当前 seed 已包含专家/领域规则，不能把这个基线称为完全无专家知识的零样本模型。

## 0.2 ACE 与 GEPA 如何结合、会生成什么

ACE 先分批看学习案例的原图和反馈，生成带来源的反思，再由整理器生成至多 20 条建议：新增、替换（可合并旧规则）或停用。每条建议必须引用本批真实案例，保留适用条件、例外和理由；无充分依据时允许空建议。

GEPA 的候选是“选择哪些建议、如何表达”的计划。未列入 ACE 建议的规则不得被搜索器任意修改，动作接口、图片和评分不可改。条件、例外与来源由程序保留。引擎从未修改的原 skill 开始，在固定预算内进行反思变异与 Pareto 选择；本版不启用 GEPA 的候选合并算子。

接受门槛：选择验证集准确率严格超过 `min_gain`，回归数量不超过 `max_regressions`，学习集整体不退化，两组均无新增误删或误合并。原版本或空修改不自动接受。一次搜索内同一 skill/案例的结果缓存复用，末尾检查不是新的独立测试；推理随机性和多次重复实验仍由实验方案处理。固定验证集会被反复用于选择，不能当作最终未曝光测试。

每个 `session/attempts/update_XXXX/` 保存：

- `manifest.json`、`base_skill.json`：模型、预算、输入修订与来源。
- `ace/reflect_*`、`ace/curate`、`ace/suggestions.json`：反思与冻结经验建议。
- `gepa/`、`search.json`、`proposals/`：真实 GEPA 搜索过程、候选池、谱系和验证分数。
- `evaluations/`、调用账本：逐例输出与实际模型用量。
- `candidate_skill.json`、`result.json`：最终候选与接受/拒绝/失败原因；接受时有带哈希的 `accepted_skill.json`。

候选生成器能看到学习反馈，不能读取验证标签来反思；执行评估请求始终不含专家答案。代码检查相同输入/状态/对象的重复与重叠，但录音块分组仍需实验设计保证。当前评价的是动作一致性和误删/误合并，不含自动理由相似度评分。

## 0.3 测试与当前限制

```bash
python -m pytest -q tests/test_ace_gepa.py tests/test_feedback_optimization.py \
  tests/test_skill_decider_v2.py tests/test_curation_controller_v2_rounds.py \
  tests/test_curation_controller_v2.py tests/test_vllm_provider_v2.py
```

已覆盖真实 GEPA 搜索、多候选验收、反馈去重/修订、失败与中断恢复、验证标签隔离，以及完整 RoundsController 中每轮只使用一个 skill 的离线集成测试。现有历史导出仍需明确适配；controller 整体中断续跑、人工纠正直接应用到当前分群、workflow 自优化及真实模型效果不属于本次已验证内容。

参考机制：[ACE](https://github.com/ace-agent/ace)、[GEPA](https://github.com/gepa-ai/gepa)。以下章节继续介绍公共反馈格式及早期单候选对照入口。

## 1. 运行记录

`SkillDecider` 在每个 `step_*` 中新增：

- `policy_snapshot.json`：当次使用的 contract、skill 和版本哈希。
- `answer.json`：通过输出 schema 检查的模型判断。

现有 question、request 和 PNG 继续保留。当前反馈工具仅接受包含上述快照的新运行；旧 UI 导出和历史 trace 尚未做自动转换，避免猜测字段或状态对应关系。

## 2. 托管 API 与本地 vLLM

原 `--model qwen27b/gemma4` 本地运行方式继续可用。

托管 API 使用单独的配置文件：复制 `configs/controller_v2/hosted_api.example.json` 到机器自己的配置目录，填写精确模型 ID、供应商所需参数，并在环境变量中设置密钥。配置文件只写环境变量名。

示例（需要真实数据和供应商兼容性检查后才运行）：

```bash
python scripts/run/run_local_rollout.py \
  --api-profile private/execution_api.json \
  --max-api-calls 20 --max-decisions 20 \
  --dataset cM2-e004_004-006_CH31 \
  --output-root output/api_smoke_001
```

这是有限步 smoke，可能以 `stopped_limit` 结束，不代表完成 dataset。Hosted provider 支持显式选择 `json_schema` 或 `json_object`，始终在本地校验答案；不自动降级或重试。推理/thinking 参数需根据具体供应商明确填入 `extra_body`，不沿用 vLLM 的 chat template 参数。

调用上限计入失败请求，可用于并行请求。Ledger 记录实际响应的 model/provider/usage（包括供应商返回的费用字段）。缺失费用不当作零；调用数上限不等于美元硬预算。尚未实现托管 API token 总额上限。

真实 dataset 运行仍依赖既有 `output/real_split_manifest_20260903/manifest.json` 和对应 MAT；仓库代码同步不会自动提供数据。当前不自动迁移旧仓库数据。

## 3. 导出并审核反馈

`--run` 指向包含 controller manifest 的 `rollout` 子目录：

```bash
python scripts/run/run_feedback_optimization.py template \
  --run output/api_smoke_001/rollout --output private/reviews.json
```

每条保留的反馈填写 `expert_action`、`expert_reason`、`reviewer`、`evidence_sufficient=true`。确认无争议后保留 `disputed=false`；未审核案例从本批 reviews 列表移除。不要修改模板中的原动作、状态、目标或请求哈希。

顶层 `role` 只能是 `learning` 或 `validation`。`synthetic` 标明反馈是否模拟，不可把模拟标签写成人类反馈。当前一次导入只接受每个决定的一条已解决修订；修订历史由原始导出保留，不自动采用最后一条。

```bash
python scripts/run/run_feedback_optimization.py bundle \
  --run output/api_smoke_001/rollout --feedback private/reviews.json \
  --output output/learning_bundle_001
```

对另一批独立选择案例使用 `role=validation`，生成第二个 bundle。工具冻结请求、观测、图片和标签，并拒绝错配、重复、争议、证据不足或动作接口外的纠正。

第一版自动检查学习和验证之间是否重复了同一输入、状态及判断对象；录音块分组仍需实验设计明确保证。相邻状态和同录音不同通道可能高度相关。这里的 validation 会用于候选选择，不能当作最终测试集，也不应在反复搜索后称为未曝光数据。

## 4. 早期单候选对照入口（不执行 ACE＋GEPA）

```bash
python scripts/run/run_feedback_optimization.py optimize \
  --learning output/learning_bundle_001 \
  --validation output/validation_bundle_001 \
  --provider-profile private/execution_api.json \
  --proposer-profile private/reflection_api.json \
  --version feedback_v2 --max-calls 100 \
  --output output/optimization_001
```

`--proposer-profile` 生成一次完整 skill 规则候选，只读取学习案例，最多 32 例；请先选择小批案例控制图像和上下文开销。也可以用 `--candidate path/to/skill.json` 替代，单独验证人工或外部方法提出的候选。候选必须有新 `version` 和正确的 `parent`。

执行模型分别评估学习集和验证集的旧、新 skill，需要 `2 × (学习案例数 + 验证案例数)` 次判断；反思生成另有最多一次请求。两个 API 的调用上限分别记录。触及上限或异常后保存 failed，首版不支持续跑，不能重复使用相同 output 目录。

本地 vLLM 的 profile 文件格式为：

```json
{"backend": "vllm", "base_url": "http://127.0.0.1:8000", "profile": {}}
```

将 `profile` 填为 `configs/controller_v2/models.json` 中对应模型的完整对象。本地上限按判断次数计算，原 vLLM provider 对截断的单次重试仍然存在。

默认接受条件是验证准确率严格提高、无新增回归、无新增误删，且学习案例总体不退化。`--min-gain` 设置严格超过的最小提升，`--max-regressions` 设置允许的回归数。小样本上的提升仅是候选选择证据；规则是否符合专业知识仍需审查。模型被告知可用动作和证据，但不看到该评估案例的专家标签或上次答案。

输出保留 base、candidate、输入哈希、反馈来源、评估模型配置、逐例结果和 accepted/rejected/failed。接受时额外产生 `accepted_skill.json`。没有自动热更新；下一次运行传入：

```bash
python scripts/run/run_local_rollout.py --model gemma4 \
  --skill output/optimization_001/accepted_skill.json \
  --output-root output/next_rollout_001
```

用旧 skill 路径启动另一次运行即可回到旧策略。Workflow 优化仍在 dataset 完整完成之后单独研究，本版不更改配对、冲突、复核或停止策略。

## 5. 离线验证与远程衔接

使用已安装项目运行依赖的 Python：

```bash
python -m unittest tests.test_feedback_optimization -v
```

测试不调用 API，不需要 MAT，覆盖新 controller 的 5/4/3 图记录、反馈对齐、候选生成、接受/拒绝、回归保护、故障、证据修改、学习验证重叠和标签隔离。

本地修改后同步到 RunPod；远程数据、环境、密钥和结果独立保留。首次同步需先确认远程 SSH 连接信息、项目路径和实际数据 manifest，再准备具体同步命令。
