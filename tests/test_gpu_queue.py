"""Exercise real user credentials and processes without using the GPU."""
import importlib.util
import json
import os
import pathlib
import pwd
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock

SOURCE = pathlib.Path(__file__).resolve().parents[1] / 'scripts/ops/gpu_queue.py'
spec = importlib.util.spec_from_file_location('gpu_queue', SOURCE)
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


@unittest.skipUnless(os.geteuid() == 0, 'Integration checks require root to drop to shadow')
class QueueTests(unittest.TestCase):
    def setUp(self):
        self.user = pwd.getpwnam('shadow')
        self.gid = q.grp.getgrnam(q.GROUP).gr_gid
        self.tmp = tempfile.TemporaryDirectory(prefix='gpuq-check-')
        self.base = pathlib.Path(self.tmp.name)
        self.base.chmod(0o755)
        self.root = self.base / 'queue'
        self.root.mkdir()
        os.chown(self.root, 0, self.gid)
        self.root.chmod(0o2750)
        jobs = self.root / 'jobs'
        jobs.mkdir()
        os.chown(jobs, 0, self.gid)
        jobs.chmod(0o3770)
        (self.root / 'state').mkdir(mode=0o700)
        self.work = self.base / 'work'
        self.work.mkdir()
        os.chown(self.work, self.user.pw_uid, self.user.pw_gid)
        self.work.chmod(0o700)
        self.worker = q.Worker(self.root, probe=lambda: [])
        self.workers = [self.worker]

    def tearDown(self):
        for worker in self.workers:
            for record in worker.ledger['jobs'].values():
                if record.get('pid') and q.process_group_alive(record['pid']):
                    try:
                        os.killpg(record['pid'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if worker.process is not None:
                try:
                    worker.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
            worker.close()
        self.tmp.cleanup()

    def cli(self, *args, check=True):
        return subprocess.run(['runuser', '-u', 'shadow', '--', sys.executable, str(SOURCE),
                               '--root', str(self.root), *args], capture_output=True, text=True, check=check)

    def submit(self, code, **options):
        args = ['submit', '--cwd', str(self.work)]
        for key, value in options.items():
            args.extend(['--' + key.replace('_', '-'), str(value)])
        result = self.cli(*args, '--', sys.executable, '-c', code)
        return result.stdout.strip()

    def until(self, predicate, worker=None, timeout=8):
        worker = worker or self.worker
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            worker.tick()
            if predicate():
                return
            time.sleep(0.03)
        self.fail('Timed out: ' + json.dumps(worker.ledger))

    def terminal(self, job, worker=None):
        worker = worker or self.worker
        self.until(lambda: worker.ledger['jobs'].get(job, {}).get('state') in q.TERMINAL, worker)
        return worker.ledger['jobs'][job]

    def test_fifo_and_unprivileged_execution(self):
        first = self.submit("import os,time,json; print(json.dumps({'uid':os.getuid(),'threads':os.environ['OMP_NUM_THREADS']}),flush=True); time.sleep(.2)")
        second = self.submit("print('second')")
        self.terminal(second)
        a, b = self.worker.ledger['jobs'][first], self.worker.ledger['jobs'][second]
        self.assertEqual(a['state'], 'succeeded')
        self.assertEqual(b['state'], 'succeeded')
        self.assertGreaterEqual(b['started'], a['finished'])
        output = self.cli('logs', first).stdout
        self.assertEqual(json.loads(output)['uid'], self.user.pw_uid)
        self.assertEqual(json.loads(output)['threads'], '4')

    def test_failed_job_does_not_block_next(self):
        first = self.submit('import sys; sys.exit(7)')
        second = self.submit("print('continued')")
        self.terminal(second)
        self.assertEqual(self.worker.ledger['jobs'][first]['exit_code'], 7)
        self.assertEqual(self.worker.ledger['jobs'][second]['state'], 'succeeded')

    def test_queued_cancel_never_executes(self):
        job = self.submit("import pathlib; pathlib.Path('should-not-exist').touch()")
        self.cli('cancel', job)
        self.assertEqual(self.terminal(job)['state'], 'cancelled')
        self.assertFalse((self.work / 'should-not-exist').exists())

    def test_cancel_cleans_up_children_that_ignore_term(self):
        child = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('child-ready',flush=True); time.sleep(30)"
        code = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); time.sleep(30)'
        job = self.submit(code)
        def ready():
            return 'child-ready' in self.cli('logs', job).stdout
        self.until(ready)
        self.cli('cancel', job)
        self.worker.tick()
        time.sleep(.1)
        self.worker.tick()
        record = self.worker.ledger['jobs'][job]
        self.assertEqual(record['state'], 'running')
        self.assertTrue(q.process_group_alive(record['pid']))
        record['terminate_at'] -= 11
        self.assertEqual(self.terminal(job)['state'], 'cancelled')
        self.assertFalse(q.process_group_alive(record['pid']))

    def test_timeout_releases_queue(self):
        job = self.submit('import time; time.sleep(30)', timeout=.1)
        record = self.terminal(job)
        self.assertEqual(record['state'], 'failed')
        self.assertEqual(record['error'], 'timeout')

    def test_worker_restart_adopts_running_job(self):
        first = self.submit('import time; time.sleep(.3)')
        second = self.submit("print('after-restart')")
        self.worker.tick()
        restarted = q.Worker(self.root, probe=lambda: [])
        self.workers.append(restarted)
        self.terminal(second, restarted)
        a, b = restarted.ledger['jobs'][first], restarted.ledger['jobs'][second]
        self.assertEqual(a['state'], 'interrupted')
        self.assertGreaterEqual(b['started'], a['finished'])
        self.assertEqual(b['state'], 'succeeded')

    def test_external_gpu_job_blocks_launch(self):
        job = self.submit("print('after-external')")
        self.worker.probe = lambda: ['12345']
        self.worker.tick()
        self.assertEqual(self.worker.ledger['jobs'][job]['state'], 'queued')
        self.assertEqual(self.worker.message, 'waiting_external_gpu')
        self.worker.probe = lambda: []
        self.assertEqual(self.terminal(job)['state'], 'succeeded')

    def test_symlink_and_foreign_job_are_rejected(self):
        job = self.submit("print('not executed')")
        directory = self.root / 'jobs' / job
        with q.user_files(self.user):
            (directory / 'job.json').unlink()
            (directory / 'job.json').symlink_to('/etc/shadow')
        self.worker.tick()
        self.assertEqual(self.worker.ledger['jobs'][job]['state'], 'rejected')
        foreign = self.root / 'jobs' / uuid.uuid4().hex
        foreign.mkdir(mode=0o700)
        self.assertNotEqual(self.cli('cancel', foreign.name, check=False).returncode, 0)

    def test_cpu_wrapper_limits_threads_and_hides_gpu(self):
        result = self.cli('cpu', '--threads', '2', '--', sys.executable, '-c',
                          "import os,json; print(json.dumps([os.getuid(),os.environ['OMP_NUM_THREADS'],os.environ['CUDA_VISIBLE_DEVICES']]))")
        self.assertEqual(json.loads(result.stdout), [self.user.pw_uid, '2', ''])

    def test_closed_start_gate_never_runs_command(self):
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        try:
            result = subprocess.run([sys.executable, str(SOURCE), '_execute', str(read_fd),
                                     sys.executable, '-c', 'raise RuntimeError("must not execute")'],
                                    pass_fds=(read_fd,), capture_output=True)
        finally:
            os.close(read_fd)
        self.assertEqual(result.returncode, 125)

    def test_administrative_writes_stay_in_pinned_directory(self):
        original = self.base / 'original-queue'
        self.root.rename(original)
        self.root.mkdir()
        destination = self.base / 'untrusted-destination'
        destination.mkdir()
        (self.root / 'state').symlink_to(destination, target_is_directory=True)
        self.worker.save()
        self.assertTrue((original / 'state/ledger.json').is_file())
        self.assertFalse((destination / 'ledger.json').exists())
        self.assertFalse((self.root / 'status.json').exists())
        with self.assertRaises(RuntimeError):
            self.worker.tick()

    def test_two_users_share_queue_but_keep_private_jobs(self):
        try:
            other = pwd.getpwnam('Lv')
        except KeyError:
            self.skipTest('Lv is not provisioned on this host')
        work = self.base / 'lv-work'
        work.mkdir()
        os.chown(work, other.pw_uid, other.pw_gid)
        work.chmod(0o700)
        first = self.submit('import time; time.sleep(.1)')
        base = ['runuser', '-u', 'Lv', '--', sys.executable, str(SOURCE), '--root', str(self.root)]
        result = subprocess.run(base + ['submit', '--cwd', str(work), '--', sys.executable, '-c',
                                        'import os; print(os.getuid())'], check=True, capture_output=True, text=True)
        second = result.stdout.strip()
        directory = self.root / 'jobs' / second
        with q.user_files(other):
            data = json.loads((directory / 'job.json').read_text())
            data['uid'] = 0  # Untrusted metadata must not select the execution user.
            q.atomic_json(directory / 'job.json', data)
        self.assertNotEqual(self.cli('logs', second, check=False).returncode, 0)
        result = subprocess.run(base + ['cancel', first], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.terminal(second)
        a, b = self.worker.ledger['jobs'][first], self.worker.ledger['jobs'][second]
        self.assertGreaterEqual(b['started'], a['finished'])
        output = subprocess.run(base + ['logs', second], check=True, capture_output=True, text=True).stdout
        self.assertEqual(int(output.strip()), other.pw_uid)


class StatusTests(unittest.TestCase):
    def test_transient_nfs_stale_handle_is_retried(self):
        with mock.patch.object(pathlib.Path, 'read_text', side_effect=[OSError(q.errno.ESTALE, 'stale handle'), '{"jobs": []}']):
            self.assertEqual(q.read_status(pathlib.Path('/not-used')), {'jobs': []})

    def test_permanent_permission_error_is_reported(self):
        with mock.patch.object(pathlib.Path, 'read_text', side_effect=PermissionError(q.errno.EACCES, 'denied')):
            with self.assertRaises(PermissionError):
                q.read_status(pathlib.Path('/not-used'))


if __name__ == '__main__':
    unittest.main()
