# CPU / GPU 多人使用

CPU Pod：`qmzvskoi8lpqmq`；GPU Pod：`z07aj01v9vefp3`，一张 NVIDIA H200。
两边挂载同一个 `/workspace`。`shadow` 的 UID 为 21000，`Lv` 的 UID 为 21002；共享项目组为 `workspace-editors`，GID 21000。Lv 本人的 SSH 公钥已配置到两台 Pod，指纹为 `SHA256:iEyhxswTJvec0sJF7zeBeaHNTFJA2nLYhRHxxMvB+WE`。

新同学接入时，可将 [完整 Agent Prompt](NEW_COLLABORATOR_AGENT_PROMPT.md) 交给其本机 agent；每位协作者使用自己的用户名和密钥。

## 提交和查看任务

在任意一台 Pod 上，以自己的账号进入项目目录后执行：

```bash
cd /workspace/spike-sorting-agent
gpuq submit --name my-experiment -- .venv/bin/python scripts/example.py
```

将示例脚本替换为实际脚本。返回的完整任务 ID 或前 8 位用于查看和取消：

```bash
gpuq list
gpuq logs TASK_ID
gpuq logs TASK_ID --follow
gpuq wait TASK_ID
gpuq cancel TASK_ID
```

任务无论从 CPU 还是 GPU 提交，都会在 GPU Pod 上以提交者对应的 Linux 用户执行。工作目录默认是提交时的目录，因此必须位于两边都能访问的共享卷；解释器和依赖则必须在 GPU Pod 上可用。命令参数以列表传递；需要 shell 语法时显式使用 `bash -lc '...'`。

可指定超时（秒）、CPU 库线程数和任务专用环境变量：

```bash
gpuq submit --name experiment-a --timeout 7200 --threads 4 --env SEED=42 -- .venv/bin/python scripts/example.py
```

每次只启动一个队列任务，按提交目录的时间先后排列。任务在 GPU 后台运行，SSH / VS Code 断开后继续执行。失败或取消后继续下一个任务。取消会先给整个任务进程组发送 SIGTERM，10 秒后仍未退出则发送 SIGKILL。任务应保持在自己的进程组内，避免使用 `setsid`、`nohup` 或自建守护进程。

每个用户只能通过客户端读取、取消自己的任务。队列列表显示所有人的任务摘要；命令参数和日志保存在提交者私有的 0700 目录中。任务以实际提交目录的文件所有者身份执行，manifest 中声明的身份不影响执行权限。root 会话提交的任务自动转为 shadow，GPU 任务不以 root 运行。

## CPU 上开发和预处理

```bash
cpurun -- .venv/bin/python scripts/example.py
cpurun --threads 8 -- .venv/bin/python scripts/example.py
```

`cpurun` 在当前 Pod 本地执行，默认将 OpenMP、MKL、OpenBLAS、NumExpr 等库设为最多 4 个线程，并对该进程隐藏 GPU。GPU 队列任务也默认使用最多 4 个 CPU 库线程。该设置用于减少常见数值计算库的资源争抢，是程序协作约定；它不是 CPU 硬配额，程序自行创建的进程和线程仍需自己控制。

## GPU 占用和边界

队列只保证通过 `gpuq submit` 提交的任务串行。若检测到队列外的 GPU 计算进程，会显示 `waiting_external_gpu` 和进程 ID，等待其退出，不终止现有进程。直接运行 Python / CUDA 的用户仍可绕过队列，因此两人应统一从此入口提交重任务。检查外部任务：

```bash
nvidia-smi
gpuq list
```

这里未启用 MIG，未给用户固定切分显存，也未给 CPU 设置 cgroup 硬配额。共享项目的输出路径仍需各任务区分，例如 `output/shadow/experiment-a` 和 `output/Lv/experiment-b`。修改相同文件、并发 Git 操作和共享环境安装依赖仍需协调。

## 管理、恢复和增加用户

程序的部署副本位于两台 Pod 的 `/opt/runpod-gpu-queue`，只有 root 可修改。共享任务与状态位于 `/workspace/.gpu-queue`。GPU 上的 supervisor 在 worker 意外退出后重启它；worker 会接管记录中的仍在运行的任务，防止同时启动第二个任务。原 worker 丢失的退出码无法恢复，相关任务完成后会标为 `interrupted`。worker 的本地日志位于 `/var/log/runpod-gpu-queue/worker.log`。

root 在 GPU 上可以重新启动 supervisor（已有实例时会拒绝重复启动）：

```bash
/opt/runpod-gpu-queue/start-worker
```

上面的命令在前台运行；需要独立后台运行时使用本文安装器，安装器会创建脱离 SSH 会话的 supervisor。

Pod 重建后系统账号和 `/opt` 下的程序可能需要恢复。若另建 Pod 导致 Pod ID 改变，先更新安装器与账号恢复脚本中的对应 Pod ID。先恢复两台 Pod 的 Linux 用户（相同 UID、GID 和各自个人目录），再在各自 Pod 以 root 审查脚本后执行：

```bash
# CPU / GPU 两边都先恢复账号
python3 /workspace/spike-sorting-agent/scripts/ops/restore_workspace_users.py
# CPU Pod
python3 /workspace/spike-sorting-agent/scripts/ops/install_gpu_queue.py cpu
# GPU Pod
python3 /workspace/spike-sorting-agent/scripts/ops/install_gpu_queue.py gpu
```

共享队列会保留。安装器只在 GPU Pod 启动 worker，平台启动命令未被修改，因此整台 Pod 停止或重建后需确认 `gpuq list` 的心跳正常，必要时重启服务。添加其他人时，在两台 Pod 创建相同 UID 的用户并加入 `workspace-editors`；队列无需重新配置用户清单。

## 验证

以下测试使用临时目录和真实普通用户进程，不占用 GPU：

```bash
python3 -m unittest discover -s tests -p test_gpu_queue.py -v
```

覆盖任务顺序、失败继续、取消及子进程清理、超时、重启接管、外部 GPU 占用、用户隔离、符号链接拒绝和提交身份校验。需要 root 才能执行降权集成测试。
