"""Detached helper: free port 8877 and start run.py again."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8877
LOG_FILE = ROOT / "logs" / "server.log"


def _win_creationflags(hidden: bool) -> int:
    if sys.platform != "win32":
        return 0
    flags = subprocess.CREATE_NO_WINDOW
    if hidden:
        flags |= subprocess.DETACHED_PROCESS
    return flags


def kill_port(port: int) -> None:
    """Best-effort kill of this app's listener on the given local port.

    Process matching is intentionally constrained to this repo root so restart
    cannot kill unrelated Python apps or clients that happen to use the same port.
    """
    if sys.platform == "win32":
        root = str(ROOT)
        quoted_root = root.replace("'", "''")

        # 1. Kill listeners on the exact local port.
        ps1 = (
            f"$pids = @(); "
            f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ $pids += $_.OwningProcess }}; "
            f"$pids = $pids | Select-Object -Unique; "
            f"foreach($id in $pids) {{ Stop-Process -Id $id -Force -ErrorAction SilentlyContinue -Confirm:$false }}"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps1],
            cwd=ROOT,
            check=False,
            creationflags=_win_creationflags(hidden=True),
        )

        # 2. netstat fallback for listen rows on the exact local endpoint.
        ps2 = (
            f'netstat -ano | findstr ":{port}" | ForEach-Object {{ '
            r'  $parts = ($_ -split "\s+") | Where-Object { $_ }; '
            r'  if ($parts.Count -lt 5 -or $parts[-2] -ne "LISTENING") { return }; '
            rf'  if ($parts[1] -notmatch "^(127\.0\.0\.1|0\.0\.0\.0|\[::1\]|\[::\]):{port}$") {{ return }}; '
            r'  $pid = $parts[-1]; '
            r'  if ($pid -match "^\d+$") { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue -Confirm:$false } '
            r'}}'
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps2],
            cwd=ROOT,
            check=False,
            creationflags=_win_creationflags(hidden=True),
        )

        # 3. Targeted python cleanup, limited to this repo path and app entry points.
        ps3 = (
            r'Get-CimInstance Win32_Process -Filter "Name=\'python.exe\' OR Name=\'pythonw.exe\' OR Name=\'python3.exe\'" '
            rf"| Where-Object {{ $_.CommandLine -like '*{quoted_root}*' -and ($_.CommandLine -like '*run.py*' -or $_.CommandLine -like '*backend.server*' -or $_.CommandLine -like '*uvicorn*backend.server*') }} "
            r'| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue -Confirm:$false }'
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps3],
            cwd=ROOT,
            check=False,
            creationflags=_win_creationflags(hidden=True),
        )
    else:
        subprocess.run(
            ["sh", "-c", f"lsof -ti:{port} | xargs -r kill -9"],
            cwd=ROOT,
            check=False,
        )


def start_server(hidden: bool = True) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_handle = LOG_FILE.open("a", encoding="utf-8")
    log_handle.write(f"\n--- server start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log_handle.flush()

    kwargs: dict = {
        "cwd": ROOT,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
    }
    if hidden:
        kwargs["creationflags"] = _win_creationflags(hidden=True)

    subprocess.Popen([sys.executable, "run.py"], **kwargs)


def _port_in_use(port: int) -> bool:
    """Quick check whether something is still listening on the port."""
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                creationflags=_win_creationflags(hidden=True),
            )
            count = int((out.stdout or "0").strip())
            return count > 0
        except Exception:
            return True  # be conservative
    else:
        try:
            out = subprocess.run(["sh", "-c", f"lsof -ti:{port} | wc -l"], capture_output=True, text=True)
            return int((out.stdout or "0").strip()) > 0
        except Exception:
            return True

def main() -> None:
    time.sleep(0.3)
    kill_port(PORT)

    # Be patient: the old listener can take a moment to release the port on Windows.
    # Re-kill and wait up to ~6 seconds total.
    for i in range(6):
        time.sleep(0.8)
        if not _port_in_use(PORT):
            break
        kill_port(PORT)

    start_server(hidden=True)


if __name__ == "__main__":
    main()
