# 新同学接入 RunPod：给本机 Agent 的完整 Prompt

请帮我配置本机 VS Code，使我能用自己的独立 Linux 账号连接以下 CPU、GPU 两台 RunPod，并使用现有的共享项目和 GPU 任务队列。请执行实际配置和验证，明确报告哪些步骤已完成、哪些仍需要管理员操作。

## 已知环境

| 项目 | CPU Pod | GPU Pod |
|---|---|---|
| Pod ID | `qmzvskoi8lpqmq` | `z07aj01v9vefp3` |
| 公网 SSH IP | `38.80.152.147` | `38.80.152.148` |
| 公网 SSH 端口 | `34440` | `33152` |
| GPU | 无 | 一张 NVIDIA H200 |
| 共享项目 | `/workspace/spike-sorting-agent` | `/workspace/spike-sorting-agent` |

两台 Pod 挂载同一个 `/workspace` 网络卷，共享文件；CPU Pod 的程序不会因为共享目录而自动使用 GPU。

服务器已有配置：

- 项目共享组：`workspace-editors`，GID `21000`。
- `shadow`：UID `21000`，主组 `shadow-private` / GID `21001`。
- `Lv`：UID `21002`，主组 `Lv-private` / GID `21003`。用户名大小写有区别。
- 已有用户分别使用 `/workspace/.users/<用户名>-cpu`、`/workspace/.users/<用户名>-gpu` 作为个人目录；两边个人目录分开，用户 UID 保持一致。
- `gpuq` 和 `cpurun` 已安装在两台 Pod 上。GPU 只有一套后台任务队列，一次运行一个任务；既有服务应继续使用。
- 操作说明：`/workspace/spike-sorting-agent/docs/GPU_QUEUE.md`。
- 队列源码：`/workspace/spike-sorting-agent/scripts/ops/gpu_queue.py`；运行副本：`/opt/runpod-gpu-queue/gpu_queue.py`。
- 队列共享数据：`/workspace/.gpu-queue`。

这份文件用于新增第三位协作者。请先确认我的新 Linux 用户名；不要复用 `shadow`、`Lv` 的身份或密钥。若我明确说我是既有账号的本人，则核对该账号后配置其现有连接。

## 第一步：准备我自己的本机密钥

1. 检查本机系统，以及 VS Code、SSH 客户端和 Remote - SSH 扩展是否可用。
2. 确认用户名后，检查 `~/.ssh/runpod_<用户名>_ed25519` 和对应 `.pub`。完整密钥对已存在就复用；缺少时创建 Ed25519 密钥，不覆盖已有文件。如果仅存在私钥，可在本机从该私钥导出公钥。
3. Mac / Linux 的 `.ssh` 目录权限为 `700`，私钥为 `600`；Windows 使用适当的本机用户文件权限。
4. 私钥只留在我的电脑上；跨机器只传输 `.pub` 公钥。输出公钥完整一行，供管理员登记。

## 第二步：判断是否具备服务器管理权限

普通用户本机拥有 SSH 密钥，不代表服务器已经允许该密钥登录，也不代表有创建 Linux 用户的权限。

- 如果已有明确授权的管理员连接，使用该连接完成下一步。
- 如果没有管理员权限，先完成本机密钥准备，生成一份“账号开通申请”，包括新用户名、公钥整行、两台 Pod ID、建议 UID / 主组 GID。把申请交给管理员，等待管理员完成两边账号和公钥配置；期间可以准备 SSH 配置，但应把服务器开通标为待完成。
- 不向同学索取或复制现有 root / shadow / Lv 的私钥，不把我的新公钥添加到其他人的账号中。

管理员从 CPU Pod 维护 GPU 时，公网直连可能超时，已有可用的 RunPod 代理入口是：

```text
z07aj01v9vefp3-64412133@ssh.runpod.io
```

该代理需要 SSH PTY；仓库已有 `scripts/deploy/gpu_exec.sh` 说明如何使用。代理仅适用于已授权的管理连接；普通 Linux 用户的 VS Code 连接使用上表的公网 TCP IP、端口和自己的用户名。不要将代理路由名当成普通 Linux 用户名。

## 第三步：由管理员在两台 Pod 上开通同一个新用户

1. 先核对目标机器和现有用户、组。`21000`～`21003` 已用于既有账号 / 组。新用户可从 UID `21004`、独立主组 GID `21005` 开始选择，但必须先检查两台机器都无冲突；冲突时选择另一组同时空闲的编号。已有同名用户的编号若不同，先查明原因，不能覆盖或强行改号。
2. 在两台 Pod 创建相同名字、相同 UID 的普通用户和相同编号的独立主组，加入 `workspace-editors`；不授予 root、sudo 等系统管理权限。用户名建议使用小写字母、数字和下划线。
3. CPU 个人目录为 `/workspace/.users/<用户名>-cpu`，GPU 为 `/workspace/.users/<用户名>-gpu`，登录 shell 使用 `/bin/bash`。由目标用户本人创建各自目录，权限 `700`；通过目标用户身份安装 shell 配置与公钥。该网络卷不支持普通 POSIX ACL，任意用户属主变更也有限制，所以应以目标用户身份创建文件，不重置整个共享卷的权限。
4. 分别建立个人目录下的 `.ssh`（`700`）和 `authorized_keys`（`600`）。按公钥主体去重追加本人的公钥，保留已存在的其他授权；核对文件实际属主。
5. 为新用户设置适合协作的 `umask 0002`，放在 Bash 配置的非交互提前返回语句之前。为其 Git 用户配置添加 `safe.directory=/workspace/spike-sorting-agent`；保留已有 Git 配置，不替用户填写其他人的提交身份。
6. 项目已经分配给 `workspace-editors`，新用户加入组即可获得既有共享权限。`gpuq` 会识别该组中的用户，无需另建队列、重装服务或修改其他人的密钥。
7. 原有恢复脚本 `scripts/ops/restore_workspace_users.py` 目前只登记了 `shadow` 和 `Lv`。由管理员将新用户与编号登记到恢复方案；安装和恢复脚本保持管理员维护权限。

## 第四步：配置我的本机 SSH 和 VS Code

在本机 SSH config 中添加或合并以下两条配置。将所有 `<用户名>` 替换为已开通的真实用户名，保留我现有的其他主机配置。

Mac / Linux 路径：`~/.ssh/config`；Windows 路径：`%USERPROFILE%\.ssh\config`。

```sshconfig
Host runpod-cpu-<用户名>
    HostName 38.80.152.147
    Port 34440
    User <用户名>
    IdentityFile ~/.ssh/runpod_<用户名>_ed25519
    IdentitiesOnly yes

Host runpod-gpu-<用户名>
    HostName 38.80.152.148
    Port 33152
    User <用户名>
    IdentityFile ~/.ssh/runpod_<用户名>_ed25519
    IdentitiesOnly yes
```

保留 SSH 主机密钥校验。连接失败时根据错误排查网络、端口、公钥和用户名；若 Pod 停止或重建过，请管理员核对 RunPod Connect 面板中的最新 IP 和端口。

VS Code 使用 Remote-SSH 分别连接这两个 Host，打开 `/workspace/spike-sorting-agent`。两边独立的个人目录会隔离 VS Code 远程服务与用户配置。

## 第五步：验证 CPU、GPU 和队列

以我的新账号执行并记录实际结果：

1. 分别 SSH 登录两台机器，检查 `id`、`$HOME` 和共享项目访问权限：用户名正确、两边 UID 相同、共享组为 `21000`、个人目录分别对应 CPU / GPU。
2. 在项目目录创建一个带随机后缀的小测试文件，验证读写后删除。不要改动既有项目内容或清理其他人的文件。
3. 两边执行 `gpuq list`，确认后台心跳正常。若队列离线，先由管理员检查；不重复启动第二套 worker。
4. 在 CPU Pod 测试 `cpurun -- python3 -c 'import os; print(os.getuid(), os.environ.get("OMP_NUM_THREADS"), os.environ.get("CUDA_VISIBLE_DEVICES"))'`。它应使用本人 UID，默认数值库线程数为 `4` 或受可用 CPU 限制的更小值，GPU 对该进程隐藏。
5. 从 CPU 或 GPU Pod 的共享项目目录提交一个轻量 GPU 检查任务：

   ```bash
   cd /workspace/spike-sorting-agent
   gpuq submit --name onboarding-check -- nvidia-smi
   gpuq list
   gpuq wait <返回的任务ID> --timeout 120
   gpuq logs <返回的任务ID>
   ```

   若前面有别人的任务在排队，应继续等待或报告等待状态，不取消别人的任务。`wait --timeout` 只是停止等待，不会取消任务。完成后确认任务以本人身份归属，日志显示 H200。
6. 通过可用工具帮助我完成 VS Code 连接；若无法操作 VS Code UI，给出两个已配置的 Host 名和打开项目的简短步骤。

## 我之后的使用约定

- GPU 重任务统一用 `gpuq submit --name <实验名> -- <命令和参数>` 提交；工作目录放在共享卷中，解释器必须在 GPU Pod 可用。
- 查看状态用 `gpuq list`，读自己的日志用 `gpuq logs <ID>`，取消自己的任务用 `gpuq cancel <ID>`。
- CPU 开发、预处理用 `cpurun -- <命令>`，需要时用 `--threads N` 指定数值库线程数。这是程序级协作限制，不是 CPU 硬配额。
- 直接运行 CUDA 仍可绕过队列，因此大家统一走队列。任务中不使用 `setsid`、`nohup` 或自建守护进程逃离队列进程组。
- 每个人使用自己的实验输出目录，例如 `output/<用户名>/<实验名>`；修改相同文件、Git 操作和共享环境安装依赖需要协调。

最后请交付：实际 Linux 用户名和 UID、两台机器的 SSH Host 名、本机公钥路径及公钥、已完成的验证结果、剩余待管理员完成的步骤。私钥内容不进入任何交付文件。
