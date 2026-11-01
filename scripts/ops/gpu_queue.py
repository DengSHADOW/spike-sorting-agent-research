#!/usr/bin/env python3
"""A shared-volume, multi-user queue for one GPU. Python 3.8+; no dependencies."""
import argparse
import contextlib
import errno
import fcntl
import grp
import json
import math
import os
import pathlib
import pwd
import re
import signal
import stat
import subprocess
import sys
import time
import uuid

DEFAULT_ROOT = '/workspace/.gpu-queue'
GROUP = 'workspace-editors'
JOB_ID = re.compile(r'^[0-9a-f]{32}$')
TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted', 'rejected'}
THREAD_KEYS = ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
               'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'BLIS_NUM_THREADS')


def cpu_threads(requested=None):
    count = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else (os.cpu_count() or 1)
    try:
        quota, period = pathlib.Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota != 'max':
            count = min(count, max(1, int(quota) // int(period)))
    except (OSError, ValueError):
        pass
    return min(count, requested if requested is not None else 4)


def atomic_json(path, value, mode=0o600):
    path = pathlib.Path(path)
    tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_json_at(directory_fd, name, value, mode=0o600):
    """Keep administrative writes on pinned, trusted directory inodes."""
    tmp = '.' + name + '.' + uuid.uuid4().hex
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=directory_fd)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    finally:
        try:
            os.unlink(tmp, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def private_json(path, uid):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != uid or st.st_mode & 0o077:
            raise ValueError('Job manifest must be a regular, owner-only file')
        data = stream.read(131073)
        if len(data) > 131072:
            raise ValueError('Job manifest too large')
        return json.loads(data)


def eligible_user(uid):
    if uid == 0:
        raise ValueError('GPU jobs must run as a regular user')
    user = pwd.getpwuid(uid)
    gid = grp.getgrnam(GROUP).gr_gid
    if gid not in os.getgrouplist(user.pw_name, user.pw_gid):
        raise ValueError('User is not a member of ' + GROUP + ' on the GPU Pod')
    return user


@contextlib.contextmanager
def user_files(user):
    """NFS may deny root access to another user's 0700 directory."""
    if os.geteuid() != 0:
        if os.geteuid() != user.pw_uid:
            raise PermissionError('Wrong user')
        yield
        return
    groups, gid = os.getgroups(), os.getegid()
    os.setgroups(os.getgrouplist(user.pw_name, user.pw_gid))
    os.setegid(user.pw_gid)
    os.seteuid(user.pw_uid)
    try:
        yield
    finally:
        os.seteuid(0)
        os.setegid(gid)
        os.setgroups(groups)


def checked_job_dir(root, job_id, uid=None):
    if not JOB_ID.fullmatch(job_id):
        raise ValueError('Invalid job ID')
    path = pathlib.Path(root) / 'jobs' / job_id
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_mode & 0o077:
        raise ValueError('Job directory must be owner-only and must not be a symlink')
    if uid is not None and st.st_uid != uid:
        raise PermissionError('This job belongs to another user')
    return path, st.st_uid


def validate_manifest(data, job_id):
    if not isinstance(data, dict) or data.get('version') != 1 or data.get('id') != job_id:
        raise ValueError('Invalid job manifest')
    command = data.get('command')
    if (not isinstance(command, list) or not command or len(command) > 1024 or
            not all(isinstance(x, str) and '\x00' not in x for x in command) or not command[0]):
        raise ValueError('Command must be an argument list')
    cwd = data.get('cwd')
    if not isinstance(cwd, str) or not cwd.startswith('/') or '\x00' in cwd:
        raise ValueError('Working directory must be an absolute path')
    timeout = data.get('timeout', 0)
    if not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError('Invalid timeout')
    threads = data.get('threads', 4)
    if not isinstance(threads, int) or threads < 1:
        raise ValueError('Invalid thread limit')
    env = data.get('env', {})
    if (not isinstance(env, dict) or not all(isinstance(k, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k)
            and isinstance(v, str) and '\x00' not in v for k, v in env.items())):
        raise ValueError('Invalid environment')
    return data


def process_identity(pid):
    try:
        text = pathlib.Path('/proc/{}/stat'.format(pid)).read_text()
        fields = text[text.rfind(')') + 2:].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


def process_group_alive(pgid):
    if not pgid:
        return False
    for path in pathlib.Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            text = (path / 'stat').read_text()
            fields = text[text.rfind(')') + 2:].split()
            if fields[0] != 'Z' and int(fields[2]) == pgid:
                return True
        except (OSError, ValueError, IndexError):
            pass
    return False


def gpu_busy():
    result = subprocess.run(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader,nounits'],
                            capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError('nvidia-smi failed: ' + result.stderr.strip()[:300])
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


class Worker:
    def __init__(self, root=DEFAULT_ROOT, probe=gpu_busy):
        self.root = pathlib.Path(root)
        self.probe = probe
        self.root_inode = self.root.lstat().st_ino
        self.jobs_inode = (self.root / 'jobs').lstat().st_ino
        self.state = self.root / 'state' / 'ledger.json'
        self.process = None
        self.message = 'idle'
        self.external_pids = []
        self.stopping = False
        self.boot = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.check_paths()
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        self.root_fd = os.open(str(self.root), flags)
        self.state_fd = os.open('state', flags, dir_fd=self.root_fd)
        root_stat, state_stat = os.fstat(self.root_fd), os.fstat(self.state_fd)
        if (root_stat.st_uid != 0 or root_stat.st_mode & 0o022 or root_stat.st_ino != self.root_inode or
                state_stat.st_uid != 0 or state_stat.st_mode & 0o077):
            self.close()
            raise RuntimeError('Untrusted queue directories')
        try:
            fd = os.open('ledger.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.state_fd)
        except FileNotFoundError:
            self.ledger = {'jobs': {}, 'active': None}
        else:
            with os.fdopen(fd) as stream:
                st = os.fstat(stream.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o077:
                    raise RuntimeError('Untrusted ledger file')
                self.ledger = json.load(stream)

    def close(self):
        for name in ('root_fd', 'state_fd'):
            fd = getattr(self, name, None)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)

    def check_paths(self):
        for path, inode in [(self.root, self.root_inode), (self.root / 'jobs', self.jobs_inode)]:
            st = path.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_ino != inode:
                raise RuntimeError('Queue directory was replaced; refusing to continue')
        if self.root.stat().st_mode & 0o022:
            raise RuntimeError('Queue root must not be group/world writable')
        state = (self.root / 'state').lstat()
        if not stat.S_ISDIR(state.st_mode) or state.st_uid != 0 or state.st_mode & 0o077:
            raise RuntimeError('Queue ledger must be root-only')

    def save(self):
        atomic_json_at(self.state_fd, 'ledger.json', self.ledger)
        public = {'heartbeat': time.time(), 'worker_pid': os.getpid(), 'worker_pod': os.environ.get('RUNPOD_POD_ID'),
                  'message': self.message, 'external_gpu_pids': self.external_pids,
                  'jobs': list(self.ledger['jobs'].values())}
        atomic_json_at(self.root_fd, 'status.json', public, 0o640)

    def discover(self):
        for path in (self.root / 'jobs').iterdir():
            job_id = path.name
            if not JOB_ID.fullmatch(job_id) or job_id in self.ledger['jobs']:
                continue
            record = {'id': job_id, 'state': 'queued', 'submitted': path.lstat().st_mtime,
                      'user': '?', 'uid': None, 'name': '', 'exit_code': None}
            try:
                directory, uid = checked_job_dir(self.root, job_id)
                user = eligible_user(uid)
                record.update(uid=uid, user=user.pw_name)
                with user_files(user):
                    data = validate_manifest(private_json(directory / 'job.json', uid), job_id)
                name = str(data.get('name', ''))[:120]
                record['name'] = ''.join(c if c.isprintable() else '?' for c in name)
            except (OSError, KeyError, ValueError) as error:
                record.update(state='rejected', error=str(error)[:300], finished=time.time())
            self.ledger['jobs'][job_id] = record

    def cancelled(self, record):
        directory, _ = checked_job_dir(self.root, record['id'], record['uid'])
        with user_files(eligible_user(record['uid'])):
            return (directory / 'cancel').exists()

    def finish(self, record, state, code=None, error=None):
        record.update(state=state, exit_code=code, finished=time.time())
        if error:
            record['error'] = str(error)[:500]
        self.ledger['active'] = None
        self.process = None
        self.message = 'idle'

    def launch(self, record):
        directory, _ = checked_job_dir(self.root, record['id'], record['uid'])
        user = eligible_user(record['uid'])
        with user_files(user):
            data = validate_manifest(private_json(directory / 'job.json', user.pw_uid), record['id'])
            log_fd = os.open(str(directory / 'output.log'), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            log_stat = os.fstat(log_fd)
            if log_stat.st_uid != user.pw_uid or not stat.S_ISREG(log_stat.st_mode):
                os.close(log_fd)
                raise ValueError('Invalid output log')
        env = {'PATH': '/usr/local/bin:/usr/bin:/bin:/usr/local/cuda/bin', 'HOME': user.pw_dir,
               'USER': user.pw_name, 'LOGNAME': user.pw_name, 'SHELL': user.pw_shell,
               'LANG': 'C.UTF-8', 'PYTHONUNBUFFERED': '1', 'CUDA_VISIBLE_DEVICES': '0',
               'GPUQ_JOB_ID': record['id']}
        if os.environ.get('RUNPOD_POD_ID'):
            env['RUNPOD_POD_ID'] = os.environ['RUNPOD_POD_ID']
        env.update({key: str(cpu_threads(data.get('threads'))) for key in THREAD_KEYS})
        env.update(data.get('env', {}))
        groups = os.getgrouplist(user.pw_name, user.pw_gid)

        def drop_privileges():
            os.setgroups(groups)
            os.setgid(user.pw_gid)
            os.setuid(user.pw_uid)
            os.umask(0o002)
            # chdir after dropping credentials: private NFS homes are user-only.
            os.chdir(data['cwd'])

        # A small, already unprivileged helper waits on a pipe before exec. Persist
        # its PID before opening that gate, so a worker crash cannot orphan an
        # unrecorded training process or accidentally launch overlapping jobs.
        record.update(state='starting', started=time.time(), timeout=data.get('timeout', 0))
        self.ledger['active'] = record['id']
        self.save()
        gate_read, gate_write = os.pipe()
        try:
            helper = [sys.executable, str(pathlib.Path(__file__).resolve()), '_execute', str(gate_read)] + data['command']
            self.process = subprocess.Popen(helper, stdin=subprocess.DEVNULL, stdout=log_fd,
                                            stderr=subprocess.STDOUT, env=env, start_new_session=True,
                                            preexec_fn=drop_privileges, pass_fds=(gate_read,))
            record.update(state='running', pid=self.process.pid, process_start=process_identity(self.process.pid), boot=self.boot)
            self.message = 'running'
            self.save()
            os.write(gate_write, b'1')
        finally:
            os.close(log_fd)
            os.close(gate_read)
            os.close(gate_write)

    def monitor(self, record):
        if self.process is not None:
            code = self.process.poll()
            alive = code is None or process_group_alive(record.get('pid'))
        else:
            code = None
            alive = (record.get('boot') == self.boot and record.get('process_start') is not None and
                     (process_identity(record.get('pid')) == record.get('process_start') or
                      process_group_alive(record.get('pid'))))
        if not alive:
            if record.get('stop_reason'):
                self.finish(record, 'cancelled' if record['stop_reason'] == 'cancelled' else 'failed', code, record['stop_reason'])
            elif self.process is None:
                self.finish(record, 'interrupted', error='Worker restarted; original exit code is unavailable')
            else:
                self.finish(record, 'succeeded' if code == 0 else 'failed', code)
            return
        try:
            cancel = self.cancelled(record)
        except (OSError, KeyError, ValueError):
            cancel = False
        timeout = record.get('timeout', 0)
        if (cancel or (timeout and time.time() - record['started'] >= timeout)) and not record.get('stop_reason'):
            record['stop_reason'] = 'cancelled' if cancel else 'timeout'
            record['terminate_at'] = time.time()
            try:
                os.killpg(record['pid'], signal.SIGTERM)
            except ProcessLookupError:
                pass
        if record.get('terminate_at') and time.time() - record['terminate_at'] > 10:
            try:
                os.killpg(record['pid'], signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.message = 'running' if not record.get('stop_reason') else 'stopping_job'

    def tick(self):
        self.check_paths()
        self.discover()
        for record in self.ledger['jobs'].values():
            if record['state'] == 'queued':
                try:
                    if self.cancelled(record):
                        record.update(state='cancelled', finished=time.time())
                except (OSError, KeyError, ValueError) as error:
                    record.update(state='rejected', finished=time.time(), error=str(error)[:300])
        active = self.ledger.get('active')
        if active:
            self.monitor(self.ledger['jobs'][active])
        if not self.ledger.get('active') and not self.stopping:
            pending = sorted((r for r in self.ledger['jobs'].values() if r['state'] == 'queued'), key=lambda r: (r['submitted'], r['id']))
            if pending:
                try:
                    self.external_pids = self.probe()
                    if self.external_pids:
                        self.message = 'waiting_external_gpu'
                    else:
                        self.launch(pending[0])
                except (OSError, ValueError, KeyError, subprocess.SubprocessError, RuntimeError) as error:
                    if pending[0]['state'] == 'starting':
                        self.finish(pending[0], 'failed', error=error)
                    else:
                        self.message = 'gpu_probe_error: ' + str(error)[:200]
            else:
                self.message = 'idle'
                self.external_pids = []
        self.save()

    def serve(self, interval=1):
        while not self.stopping or self.ledger.get('active'):
            self.tick()
            time.sleep(interval)


def require_member():
    return eligible_user(os.getuid())


def command_args(args):
    command = args.command
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        raise ValueError('Supply a command after --')
    return command


def submit(root, args):
    user = require_member()
    command = command_args(args)
    env = {}
    for item in args.env:
        if '=' not in item:
            raise ValueError('--env requires NAME=VALUE')
        key, value = item.split('=', 1)
        env[key] = value
    job_id = uuid.uuid4().hex
    data = {'version': 1, 'id': job_id, 'name': args.name, 'command': command,
            'cwd': str(pathlib.Path(args.cwd or os.getcwd()).resolve()), 'threads': args.threads,
            'timeout': args.timeout, 'env': env}
    validate_manifest(data, job_id)
    parent = root / 'jobs'
    tmp = parent / ('.tmp-' + job_id)
    tmp.mkdir(mode=0o700)
    try:
        atomic_json(tmp / 'job.json', data)
        tmp.rename(parent / job_id)
    except BaseException:
        for path in tmp.iterdir():
            path.unlink()
        tmp.rmdir()
        raise
    print(job_id)


def resolve_job(root, prefix):
    if not re.fullmatch(r'[0-9a-f]{8,32}', prefix):
        raise ValueError('Use at least 8 characters of the job ID')
    matches = [p.name for p in (root / 'jobs').iterdir() if JOB_ID.fullmatch(p.name) and p.name.startswith(prefix)]
    if len(matches) != 1:
        raise ValueError('Job ID is missing or ambiguous')
    return checked_job_dir(root, matches[0], os.getuid())[0]


def read_status(root):
    path = root / 'status.json'
    # RunPod's NFS can briefly retain the old handle after an atomic replacement
    # by the other Pod. Reopen the pathname rather than exposing ESTALE to users.
    for attempt in range(6):
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            if attempt == 5:
                return {'heartbeat': 0, 'message': 'worker_not_started', 'jobs': []}
        except OSError as error:
            if error.errno not in (errno.ESTALE, errno.EAGAIN, errno.EIO) or attempt == 5:
                raise
        except json.JSONDecodeError:
            if attempt == 5:
                raise
        time.sleep(.025 * (attempt + 1))


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=DEFAULT_ROOT, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('submit', help='Submit a GPU job from either Pod')
    p.add_argument('--name', default='')
    p.add_argument('--cwd')
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--timeout', type=float, default=0, help='Seconds; 0 means unlimited')
    p.add_argument('--env', action='append', default=[])
    p.add_argument('command', nargs=argparse.REMAINDER)
    p = sub.add_parser('list', aliases=['status'], help='Show queue and worker status')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('logs', help='Read your own job log')
    p.add_argument('job')
    p.add_argument('--follow', '-f', action='store_true')
    p = sub.add_parser('cancel', help='Cancel your own queued/running job')
    p.add_argument('job')
    p = sub.add_parser('wait', help='Wait for your own job; return its exit status')
    p.add_argument('job')
    p.add_argument('--timeout', type=float, default=0)
    p = sub.add_parser('cpu', help='Run locally with a default limit of 4 CPU library threads')
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('command', nargs=argparse.REMAINDER)
    p = sub.add_parser('worker', help=argparse.SUPPRESS)
    p.add_argument('--lock', default='/run/runpod-gpu-queue/worker.lock')
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root)
    # Current root sessions can use the queue safely; GPU commands always run as shadow.
    if os.getuid() == 0 and args.action in ('submit', 'logs', 'cancel', 'wait', 'cpu'):
        command = ['runuser', '-u', 'shadow', '--', sys.executable, str(pathlib.Path(__file__).resolve())]
        command.extend(sys.argv[1:] if argv is None else argv)
        os.execvp(command[0], command)
    if args.action == 'submit':
        submit(root, args)
    elif args.action in ('list', 'status'):
        data = read_status(root)
        if args.json:
            print(json.dumps(data, ensure_ascii=False))
        else:
            print('Worker: {}{}'.format(data['message'], ' (OFFLINE / stale heartbeat)' if time.time() - data['heartbeat'] > 15 else ''))
            if data.get('external_gpu_pids'):
                print('External GPU process IDs: ' + ', '.join(data['external_gpu_pids']))
            print('{:10} {:14} {:13} {:5} {}'.format('JOB', 'USER', 'STATE', 'EXIT', 'NAME / ERROR'))
            for record in sorted(data['jobs'], key=lambda r: (r['submitted'], r['id']))[-100:]:
                print('{:10} {:14} {:13} {:5} {}'.format(record['id'][:8], record['user'], record['state'],
                      str(record.get('exit_code') if record.get('exit_code') is not None else '-'), record.get('error') or record['name']))
            known = {r['id'] for r in data['jobs']}
            for path in (root / 'jobs').iterdir():
                if JOB_ID.fullmatch(path.name) and path.name not in known:
                    print(path.name[:8] + ' pending discovery')
    elif args.action == 'cancel':
        directory = resolve_job(root, args.job)
        fd = os.open(str(directory / 'cancel'), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        print('Cancellation requested: ' + directory.name)
    elif args.action == 'logs':
        directory = resolve_job(root, args.job)
        path = directory / 'output.log'
        if not path.exists() and not args.follow:
            print('No log yet; the job may still be queued.')
            return 0
        with contextlib.ExitStack() as stack:
            stream = None
            while True:
                if stream is None and path.exists():
                    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        os.close(fd)
                        raise ValueError('Invalid output log')
                    stream = stack.enter_context(os.fdopen(fd, errors='replace'))
                text = stream.read() if stream else ''
                if text:
                    print(text, end='', flush=True)
                record = next((r for r in read_status(root)['jobs'] if r['id'] == directory.name), {})
                if not args.follow or record.get('state') in TERMINAL:
                    if args.follow:
                        # Drain output written between the first read and the
                        # worker publishing the terminal state.
                        if stream is None and path.exists():
                            fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                            if not stat.S_ISREG(os.fstat(fd).st_mode):
                                os.close(fd)
                                raise ValueError('Invalid output log')
                            stream = stack.enter_context(os.fdopen(fd, errors='replace'))
                        if stream:
                            print(stream.read(), end='', flush=True)
                    break
                time.sleep(0.5)
    elif args.action == 'wait':
        directory = resolve_job(root, args.job)
        start = time.monotonic()
        while True:
            data = read_status(root)
            record = next((r for r in data['jobs'] if r['id'] == directory.name), None)
            if record and record['state'] in TERMINAL:
                print(json.dumps(record, ensure_ascii=False))
                if record['state'] == 'succeeded':
                    return 0
                code = record.get('exit_code') or 1
                return (128 - code) if code < 0 else code % 256
            if args.timeout and time.monotonic() - start >= args.timeout:
                print('Timed out waiting; job remains queued/running.', file=sys.stderr)
                return 124
            if time.time() - data['heartbeat'] > 15:
                print('Worker is offline or heartbeat is stale.', file=sys.stderr)
                return 2
            time.sleep(0.5)
    elif args.action == 'cpu':
        require_member()
        if args.threads < 1:
            raise ValueError('--threads must be positive')
        env = dict(os.environ)
        env.update({key: str(cpu_threads(args.threads)) for key in THREAD_KEYS})
        env['CUDA_VISIBLE_DEVICES'] = ''
        command = command_args(args)
        os.execvpe(command[0], command, env)
    elif args.action == 'worker':
        if os.getuid() != 0:
            raise PermissionError('Only the administrator can start the worker')
        config = pathlib.Path('/etc/runpod-gpu-queue.json')
        if config.exists():
            expected = json.loads(config.read_text())['gpu_pod']
            if os.environ.get('RUNPOD_POD_ID') != expected:
                raise RuntimeError('The queue worker must run on the GPU Pod')
        os.setgroups(list(set(os.getgroups() + [grp.getgrnam(GROUP).gr_gid])))
        lock = pathlib.Path(args.lock)
        lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with lock.open('a') as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('A GPU queue worker is already running')
            worker = Worker(root)
            def stop(signum, frame):
                # Drain the active job before stopping; queued jobs remain on disk.
                worker.stopping = True
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            try:
                worker.serve()
            finally:
                worker.close()
    return 0


if __name__ == '__main__':
    try:
        if len(sys.argv) > 3 and sys.argv[1] == '_execute':
            gate = int(sys.argv[2])
            ready = os.read(gate, 1)
            os.close(gate)
            if ready != b'1':
                sys.exit(125)
            os.execvpe(sys.argv[3], sys.argv[3:], dict(os.environ))
        sys.exit(cli())
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('gpuq: ' + str(error), file=sys.stderr)
        sys.exit(1)
