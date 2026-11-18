"""Real process recovery checks, always using an isolated empty database."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import windows_autostart as startup


@unittest.skipUnless(os.name == 'nt', 'Windows task supervisor')
class ScheduledLauncherTests(unittest.TestCase):
    def test_chinese_scheduled_task_path_in_hidden_windows_code_page(self):
        xml = ('<?xml version="1.0" encoding="UTF-16"?>'
               '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
               '<Actions><Exec><WorkingDirectory>' + str(ROOT) + '</WorkingDirectory>'
               '<Arguments>-X utf8 -B windows_autostart.py serve</Arguments>'
               '</Exec></Actions></Task>')
        for encoding in ('utf-8-sig', 'utf-16', 'mbcs'):
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as temp:
                data = Path(temp)
                (data / 'server.stop').write_text('intentional stop')
                responses = [subprocess.CompletedProcess([], 0, stdout=xml.encode(encoding)),
                             subprocess.CompletedProcess([], 0)]
                with patch.object(startup.subprocess, 'run', side_effect=responses) as run:
                    self.assertTrue(startup.start_scheduled_server(data))
                    self.assertEqual('/Run', run.call_args_list[1].args[0][1])
                self.assertFalse((data / 'server.stop').exists())

    def test_other_installation_is_not_started(self):
        xml = (f'<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
               '<Actions><Exec><WorkingDirectory>C:\\OtherInstallation</WorkingDirectory>'
               '<Arguments>python server.py</Arguments></Exec></Actions></Task>')
        with tempfile.TemporaryDirectory() as temp, patch.object(
                startup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout=xml.encode())) as run:
            self.assertFalse(startup.start_scheduled_server(Path(temp)))
            self.assertEqual(1, run.call_count)


@unittest.skipUnless(os.name == 'nt', 'Windows task supervisor')
class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ydz-supervisor-test-')
        self.data = Path(self.temp.name)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.base = f'http://127.0.0.1:{self.port}/'
        self.processes = []
        self.server_pids = set()
        self.log = (self.data / 'test-process.log').open('ab', buffering=0)

    def spawn(self, script, *args):
        process = subprocess.Popen(
            [sys.executable, '-X', 'utf8', '-B', str(ROOT / script), *map(str, args)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=self.log,
            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.processes.append(process)
        return process

    def supervisor(self):
        return self.spawn('windows_autostart.py', 'serve', '--data-dir', self.data,
                          '--port', self.port, '--check-interval', '0.2')

    def wait_ready(self, different_from=None):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            pid = startup.production_pid(self.base, 0.3)
            if pid and pid != different_from:
                self.server_pids.add(pid)
                return pid
            time.sleep(0.1)
        self.fail('Server failed to recover: ' + self.read_logs())

    def read_logs(self):
        return '\n'.join(p.read_text(encoding='utf-8', errors='replace')
                         for p in self.data.glob('*.log'))

    def terminate_server(self, pid):
        # Only terminate the exact PID proven by this test's HTTP endpoint.
        self.assertEqual(pid, startup.production_pid(self.base, 1))
        os.kill(pid, 15)

    def test_configured_listener_ignores_another_service_on_loopback(self):
        configured_host = '127.0.0.2'
        (self.data / 'listen-address.txt').write_text(configured_host, encoding='utf-8')
        self.base = f'http://{configured_host}:{self.port}/'
        with socket.socket() as other_service:
            other_service.bind(('127.0.0.1', self.port))
            other_service.listen()
            supervisor = self.supervisor()
            production_pid = self.wait_ready()
            request = Request(self.base + 'api/health', headers={'Host': 'localhost'})
            with self.assertRaises(HTTPError) as error:
                startup.DIRECT.open(request, timeout=1)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()

            pid_file = self.data / 'server.pid'
            deadline = time.monotonic() + 5
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertTrue(pid_file.exists(), self.read_logs())
            self.assertEqual(int(pid_file.read_text(encoding='ascii')), production_pid)
            stopper = self.spawn('stop.py', '--data-dir', self.data, '--port', self.port)
            self.assertEqual(stopper.wait(timeout=10), 0, self.read_logs())
            self.assertEqual(supervisor.wait(timeout=10), 0, self.read_logs())
            self.assertIsNone(startup.production_pid(self.base, 1))
            with socket.create_connection(('127.0.0.1', self.port), timeout=1):
                pass

    def tearDown(self):
        (self.data / 'server.stop').write_text('test cleanup', encoding='ascii')
        for process in self.processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
        pid = startup.production_pid(self.base, 0.5)
        if pid and startup.recorded_pid(self.data) == pid:
            os.kill(pid, 15)
        self.log.close()
        # Windows may briefly retain file handles while the terminated process exits.
        for attempt in range(30):
            try:
                self.temp.cleanup()
                break
            except PermissionError:
                if attempt == 29:
                    raise
                time.sleep(0.1)

    def test_adopts_existing_server_recovers_without_pidfile_and_honors_stop(self):
        server = self.spawn('server.py', '--host', '127.0.0.1', '--port', self.port,
                            '--data-dir', self.data)
        original = self.wait_ready()
        supervisor = self.supervisor()
        deadline = time.monotonic() + 20
        while not (self.data / 'server.pid').exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue((self.data / 'server.pid').exists(), self.read_logs())
        self.assertIsNone(supervisor.poll(), 'The supervisor must not exit after adoption')
        self.assertEqual(original, startup.production_pid(self.base, 1))
        duplicate = self.supervisor()
        self.assertEqual(0, duplicate.wait(timeout=15), self.read_logs())
        (self.data / 'server.pid').unlink()
        self.terminate_server(original)
        server.wait(timeout=5)
        replacement = self.wait_ready(different_from=original)
        self.assertNotEqual(original, replacement)
        self.assertIsNone(supervisor.poll())
        time.sleep(0.4)
        stopper = self.spawn('stop.py', '--data-dir', self.data, '--port', self.port)
        self.assertEqual(0, stopper.wait(timeout=10), self.read_logs())
        self.assertEqual(0, supervisor.wait(timeout=10), self.read_logs())
        self.assertTrue((self.data / 'server.stop').exists())
        # A periodic scheduler trigger must not undo an intentional stop.
        paused = self.supervisor()
        self.assertEqual(0, paused.wait(timeout=15), self.read_logs())
        self.assertIsNone(startup.production_pid(self.base, 0.5))
        (self.data / 'server.stop').unlink()
        resumed = self.supervisor()
        self.wait_ready()
        self.assertIsNone(resumed.poll())

    def test_new_server_crash_recovery_and_restart_after_guard_exit(self):
        first_guard = self.supervisor()
        original = self.wait_ready()
        self.terminate_server(original)
        replacement = self.wait_ready(different_from=original)
        self.assertIsNone(first_guard.poll())
        first_guard.terminate()
        first_guard.wait(timeout=5)
        # Emulate Task Scheduler launching a replacement guard: no server restart.
        new_guard = self.supervisor()
        time.sleep(3)
        self.assertEqual(replacement, startup.production_pid(self.base, 1))
        self.assertIsNone(new_guard.poll())
        self.terminate_server(replacement)
        self.wait_ready(different_from=replacement)

    def test_foreign_data_directory_is_never_adopted(self):
        other = self.data / 'other'
        self.spawn('server.py', '--host', '127.0.0.1', '--port', self.port,
                   '--data-dir', other)
        original = self.wait_ready()
        guard = self.supervisor()
        self.assertEqual(1, guard.wait(timeout=15), self.read_logs())
        self.assertEqual(original, startup.production_pid(self.base, 1))
        self.assertFalse((self.data / 'server.pid').exists())


if __name__ == '__main__':
    unittest.main()
