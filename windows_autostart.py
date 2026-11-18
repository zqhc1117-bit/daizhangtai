import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener
from deployment import configured_listen_address, local_service_url


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DIRECT = build_opener(ProxyHandler({}))
TASK_NAME = "YDZ-StartServer"


def production_pid(base, timeout, opener=DIRECT):
    try:
        with opener.open(base + "api/health", timeout=timeout) as response:
            health = json.load(response)
    except (OSError, ValueError):
        return None
    if isinstance(health, dict) and health.get("app") == "yidaizhang-local" and health.get("demo") is False:
        pid = health.get("pid")
        return pid if type(pid) is int and pid > 0 else None
    return None


def windows_boot_time_ms():
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = (
        "$boot = (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime;"
        "[DateTimeOffset]::new($boot).ToUnixTimeMilliseconds()"
    )
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, check=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return int(result.stdout.strip())


def archive_preboot_lock(marker, boot_ms):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    delete_and_read_attributes = 0x10000 | 0x80
    open_existing = 3
    handle = create_file(str(marker), delete_and_read_attributes, 0, None, open_existing, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        if ctypes.get_last_error() == 2:  # The existing launcher may have removed it.
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    handle = wintypes.HANDLE(handle)
    try:
        created = wintypes.FILETIME()
        modified = wintypes.FILETIME()
        if not kernel.GetFileTime(handle, ctypes.byref(created), None, ctypes.byref(modified)):
            raise ctypes.WinError(ctypes.get_last_error())
        windows_epoch_offset_ms = 11_644_473_600_000
        for stamp in (created, modified):
            stamp_ms = ((stamp.dwHighDateTime << 32) | stamp.dwLowDateTime) // 10_000
            if stamp_ms - windows_epoch_offset_ms >= boot_ms:
                return None

        archive = marker.with_name(f"{marker.name}.preboot-{time.time_ns()}")
        target = str(archive)

        class RenameInfo(ctypes.Structure):
            _fields_ = [
                ("replace_existing", wintypes.DWORD),
                ("root_directory", wintypes.HANDLE),
                ("name_bytes", wintypes.DWORD),
                ("name", wintypes.WCHAR * (len(target.encode("utf-16-le")) // 2 + 1)),
            ]

        info = RenameInfo(0, None, len(target.encode("utf-16-le")), target)
        file_rename_info = 3
        # Rename the open file so a concurrent launch cannot replace it between the check and the rename.
        if not kernel.SetFileInformationByHandle(handle, file_rename_info, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return archive
    finally:
        kernel.CloseHandle(handle)


def recorded_pid(data):
    try:
        pid = int((data / "server.lock").read_text(encoding="utf-8").strip())
        return pid if pid > 0 else None
    except (OSError, ValueError):
        return None


def start_scheduled_server(data=DATA):
    """Use the OS-owned task, so closing a terminal cannot take down the server."""
    if os.name != "nt":
        return False
    # A copied migration package must not start another installation's task.
    import xml.etree.ElementTree as ET
    flags = subprocess.CREATE_NO_WINDOW
    query = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", TASK_NAME, "/XML"],
        capture_output=True, timeout=15, creationflags=flags,
    )
    if query.returncode:
        return False
    # schtasks advertises UTF-16 even when its pipe uses UTF-8 or the Windows
    # ANSI code page. CREATE_NO_WINDOW changes that code page on Chinese PCs.
    task = None
    encodings = ("utf-16",) if b"\x00" in query.stdout[:80] else ("utf-8-sig", "mbcs")
    for encoding in encodings:
        try:
            task = ET.fromstring(query.stdout.decode(encoding))
            break
        except (ValueError, UnicodeError, ET.ParseError):
            continue
    if task is None:
        return False
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    directory = task.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=ns)
    arguments = task.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns) or ""
    if not directory or Path(directory).resolve() != ROOT or "windows_autostart.py serve" not in arguments:
        return False
    (Path(data) / "server.stop").unlink(missing_ok=True)
    result = subprocess.run(
        ["schtasks.exe", "/Run", "/TN", TASK_NAME],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=15, creationflags=flags,
    )
    if result.returncode:
        raise RuntimeError("无法启动代账后台守护任务，请检查 Windows 任务计划中的 YDZ-StartServer。")
    return True


def supervisor_mutex(data):
    """A system-wide singleton, also across interactive and scheduled sessions."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    name = hashlib.sha256(str(data).lower().encode("utf-8")).hexdigest()[:24]
    handle = kernel.CreateMutexW(None, False, "Global\\YDZSupervisor-" + name)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:  # ERROR_ALREADY_EXISTS
        kernel.CloseHandle(handle)
        return kernel, None
    return kernel, handle


def run_server(data_dir=None, port=8765, interval=3, restart_delay=2, ready_timeout=60):
    """Monitor both adopted and newly started servers until an explicit stop.

    Never kill an alive server just because an HTTP probe is slow: a long write
    or temporary memory pressure must not turn into a watchdog restart loop.
    """
    from server import process_alive
    data = Path(data_dir or DATA).resolve()
    data.mkdir(parents=True, exist_ok=True)
    base = local_service_url(configured_listen_address(data), port)
    kernel, mutex = supervisor_mutex(data)
    if mutex is None:
        return 0
    try:
        with (data / "supervisor.log").open("a", encoding="utf-8", buffering=1) as events, \
                (data / "server.log").open("ab", buffering=0) as output:
            def log(message):
                events.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")

            stop_file = data / "server.stop"
            marker = data / "server.lock"
            pid_file = data / "server.pid"
            boot_ms = windows_boot_time_ms()
            # An intentional stop applies to this boot, not to the next boot.
            if stop_file.exists() and stop_file.stat().st_mtime_ns // 1_000_000 < boot_ms:
                stop_file.unlink(missing_ok=True)
            if not production_pid(base, 1) and marker.exists():
                try:
                    archived = archive_preboot_lock(marker, boot_ms)
                    if archived:
                        log(f"Archived previous boot's lock: {archived.name}")
                except OSError as exc:
                    log(f"Could not inspect old lock: {exc}")
            log(f"Supervisor started (pid={os.getpid()}, port={port}).")
            child = None
            observed = None
            launched_at = 0
            last_warning = 0
            failures = 0
            retry_at = 0
            while True:
                if stop_file.exists():
                    # stop.py owns termination, after verifying the HTTP identity.
                    log("Explicit stop requested; automatic restart is paused until start or reboot.")
                    return 0
                current = production_pid(base, 1)
                locked = recorded_pid(data)
                if current:
                    if current != locked:
                        log("Port belongs to a different data directory; refusing to manage it.")
                        return 1
                    if current != observed:
                        log(f"Monitoring production server pid={current}.")
                    if not pid_file.exists() or pid_file.read_text(encoding="ascii").strip() != str(current):
                        pending = data / "server.pid.pending"
                        pending.write_text(str(current), encoding="ascii")
                        pending.replace(pid_file)
                    observed = current
                    failures = 0
                else:
                    child_alive = child is not None and child.poll() is None
                    owner_alive = locked is not None and process_alive(locked)
                    if child_alive or owner_alive:
                        if time.monotonic() - last_warning >= ready_timeout:
                            log(f"Health check unavailable but server pid={locked or child.pid} is alive; preserving active work.")
                            last_warning = time.monotonic()
                    else:
                        if observed is not None:
                            code = child.poll() if child is not None else "not a child"
                            log(f"Server pid={observed} exited ({code}); restarting automatically.")
                            observed = None
                        if child is not None:
                            code = child.poll()
                            log(f"Child pid={child.pid} exited ({code}).")
                            child = None
                            failures += 1
                            retry_at = max(retry_at, launched_at + min(30, restart_delay * 2 ** min(failures - 1, 4)))
                        if time.monotonic() >= retry_at:
                            try:
                                child = subprocess.Popen(
                                    [sys.executable, "-X", "utf8", "-B", str(ROOT / "server.py"),
                                     "--port", str(port), "--data-dir", str(data)],
                                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
                                )
                                launched_at = time.monotonic()
                                log(f"Started production server pid={child.pid}.")
                            except OSError as exc:
                                log(f"Could not start server: {exc}; retrying in 10 seconds.")
                                retry_at = time.monotonic() + 10
                time.sleep(interval)
    finally:
        kernel.CloseHandle(mutex)


def open_browser():
    deadline = time.monotonic() + 90
    # This launcher runs on the production host; its own browser needs no tunnel.
    address = local_service_url(configured_listen_address(DATA), 8765)
    while time.monotonic() < deadline:
        if production_pid(address, 1):
            break
        time.sleep(2)
    explorer = Path(os.environ["SystemRoot"]) / "explorer.exe"
    subprocess.Popen([str(explorer), address])
    # Let the user's desktop shell hand the URL to their browser.
    time.sleep(3)
    with (DATA / "windows-browser-start.log").open("a", encoding="utf-8") as log:
        log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {address}\n")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("serve", "open"))
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--check-interval", type=float, default=3)
    args = parser.parse_args()
    if args.check_interval <= 0 or not 1 <= args.port <= 65535:
        parser.error("port or check interval is out of range")
    sys.exit(open_browser() if args.action == "open" else
             run_server(args.data_dir, args.port, args.check_interval))
