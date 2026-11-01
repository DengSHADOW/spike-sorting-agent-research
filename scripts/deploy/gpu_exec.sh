#!/usr/bin/env bash
# Run one shell command on the GPU Pod through the RunPod SSH proxy and print its output.
# The CPU Pod cannot reach the GPU Pod's exposed TCP port; the proxy needs a PTY and takes the command
# on stdin, so the output is cut out between markers and stripped of terminal escape codes.
#
#   GPU_SSH=<pod-id>-<suffix>@ssh.runpod.io GPU_KEY=~/.ssh/<key> scripts/deploy/gpu_exec.sh 'nvidia-smi'
#
# The command runs in GPU_DIR (default /workspace/spike-sorting-agent, the shared network volume).
# Long jobs must be detached (setsid nohup ... < /dev/null & disown), or they die with the session.
set -u
: "${GPU_SSH:?set GPU_SSH to the proxy login of the pod, shown on the RunPod Connect page}"
KEY=${GPU_KEY:-$HOME/.ssh/id_ed25519}
DIR=${GPU_DIR:-/workspace/spike-sorting-agent}
{ printf 'stty -echo; bind "set enable-bracketed-paste off" 2>/dev/null; export PS1= TERM=dumb; cd %q; echo __BEGIN__\n' "$DIR"
  printf '%s\n' "$*"
  printf 'echo __END__ $?; exit\n'; } |
  ssh -tt -o ConnectTimeout=15 -o LogLevel=ERROR -i "$KEY" "$GPU_SSH" 2>&1 |
  sed -u 's/\x1b\[[0-9;?]*[a-zA-Z]//g; s/\x1b\][^\x07]*\x07//g; s/\r//g' |
  sed -n '/^__BEGIN__$/,/^__END__/p' | grep -v '^__BEGIN__$\|^$'
