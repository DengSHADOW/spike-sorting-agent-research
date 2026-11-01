#!/usr/bin/env python3
"""Install trusted local code and the shared spool; run as root on each Pod."""
import argparse
import grp
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys

GPU_POD = 'z07aj01v9vefp3'
CPU_POD = 'qmzvskoi8lpqmq'


def install(source, role):
    if os.geteuid() != 0:
        raise SystemExit('Run the installer as root')
    expected = GPU_POD if role == 'gpu' else CPU_POD
    if os.environ.get('RUNPOD_POD_ID') != expected:
        raise SystemExit('Unexpected Pod; check the selected role')
    gid = grp.getgrnam('workspace-editors').gr_gid
    os.setgroups(list(set(os.getgroups() + [gid])))
    root = pathlib.Path('/workspace/.gpu-queue')
    for path, mode, group in [(root, 0o2750, gid), (root / 'jobs', 0o3770, gid), (root / 'state', 0o700, 0)]:
        if path.exists() or path.is_symlink():
            st = path.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0:
                raise SystemExit('Untrusted queue path: ' + str(path))
        else:
            path.mkdir(mode=mode)
        if path.stat().st_gid != group:
            os.chown(path, -1, group)
        path.chmod(mode)
    target = pathlib.Path('/opt/runpod-gpu-queue')
    target.mkdir(mode=0o755, exist_ok=True)
    if target.is_symlink() or target.stat().st_uid != 0 or target.stat().st_mode & 0o022:
        raise SystemExit('Untrusted installation directory')
    shutil.copyfile(source, target / 'gpu_queue.py')
    (target / 'gpu_queue.py').chmod(0o755)
    cli = pathlib.Path('/usr/local/bin/gpuq')
    cli_text = '#!/bin/sh\nexec /usr/bin/python3 /opt/runpod-gpu-queue/gpu_queue.py "$@"\n'
    if cli.exists() and cli.read_text() != cli_text:
        raise SystemExit('Existing gpuq command is not managed by this installer')
    cli.write_text(cli_text)
    cli.chmod(0o755)
    cpurun = pathlib.Path('/usr/local/bin/cpurun')
    cpu_text = '#!/bin/sh\nexec /usr/local/bin/gpuq cpu "$@"\n'
    if cpurun.exists() and cpurun.read_text() != cpu_text:
        raise SystemExit('Existing cpurun command is not managed by this installer')
    cpurun.write_text(cpu_text)
    cpurun.chmod(0o755)
    config = pathlib.Path('/etc/runpod-gpu-queue.json')
    config.write_text(json.dumps({'gpu_pod': GPU_POD, 'role': role}) + '\n')
    config.chmod(0o644)
    if role == 'gpu':
        logdir = pathlib.Path('/var/log/runpod-gpu-queue')
        logdir.mkdir(mode=0o700, exist_ok=True)
        start = target / 'start-worker'
        start.write_text('''#!/usr/bin/python3
import fcntl,os,pathlib,signal,subprocess,time
directory=pathlib.Path('/run/runpod-gpu-queue')
directory.mkdir(mode=0o700,exist_ok=True)
lock=(directory/'supervisor.lock').open('a')
try: fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError: raise SystemExit('Queue supervisor is already running')
stopping=False
process=None
def stop(sig,frame):
    global stopping
    stopping=True
    if process is not None and process.poll() is None: process.terminate()
signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)
while not stopping:
    process=subprocess.Popen(['/usr/bin/python3','/opt/runpod-gpu-queue/gpu_queue.py','worker'])
    code=process.wait()
    if not stopping: time.sleep(3)
''')
        start.chmod(0o700)
        with (logdir / 'worker.log').open('ab') as log:
            process = subprocess.Popen([str(start)], stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
        print('Worker start requested; PID:', process.pid)
    print('Installed gpuq and cpurun on', role)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=['cpu', 'gpu'])
    parser.add_argument('--source', default=str(pathlib.Path(__file__).with_name('gpu_queue.py')))
    args = parser.parse_args()
    install(pathlib.Path(args.source), args.role)
