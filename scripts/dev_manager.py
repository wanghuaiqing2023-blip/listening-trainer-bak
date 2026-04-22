from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
VENV_DIR = ROOT / ".venv"
VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
RUNTIME_DIR = ROOT / ".runtime"
LOG_DIR = RUNTIME_DIR / "logs"
NPM_EXE = shutil.which("npm.cmd") or shutil.which("npm")
NODE_EXE = shutil.which("node.exe") or shutil.which("node")

BACKEND_PORT = 8000
FRONTEND_PORT = 5173
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
FRONTEND_URL = f"http://127.0.0.1:{FRONTEND_PORT}"

DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    port: int
    health_url: str
    state_file: Path
    stdout_log: Path
    stderr_log: Path
    cwd: Path
    role_marker: str


SERVICES = {
    "backend": ServiceConfig(
        name="backend",
        port=BACKEND_PORT,
        health_url=f"{BACKEND_URL}/health",
        state_file=RUNTIME_DIR / "backend.json",
        stdout_log=LOG_DIR / "backend.out.log",
        stderr_log=LOG_DIR / "backend.err.log",
        cwd=ROOT,
        role_marker="backend.main:app",
    ),
    "frontend": ServiceConfig(
        name="frontend",
        port=FRONTEND_PORT,
        health_url=FRONTEND_URL,
        state_file=RUNTIME_DIR / "frontend.json",
        stdout_log=LOG_DIR / "frontend.out.log",
        stderr_log=LOG_DIR / "frontend.err.log",
        cwd=FRONTEND_DIR,
        role_marker="vite",
    ),
}


def ensure_runtime_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def run_checked(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print(f"[INFO] Running: {' '.join(command)}")
    subprocess.run(command, cwd=cwd, env=env, check=True)


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(service: ServiceConfig) -> dict[str, Any] | None:
    if not service.state_file.exists():
        return None
    try:
        return json.loads(service.state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_state(service: ServiceConfig, state: dict[str, Any]) -> None:
    service.state_file.parent.mkdir(parents=True, exist_ok=True)
    service.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")


def remove_state(service: ServiceConfig) -> None:
    if service.state_file.exists():
        service.state_file.unlink()


def state_recorded_pids(state: dict[str, Any] | None) -> list[int]:
    if not state:
        return []
    pids: list[int] = []
    for key in ("pid", "launcher_pid"):
        value = int(state.get(key, 0) or 0)
        if value > 0 and value not in pids:
            pids.append(value)
    return pids


def reconcile_service_state_with_owner(
    service: ServiceConfig,
    state: dict[str, Any] | None,
    owner_pid: int | None,
) -> dict[str, Any] | None:
    if not state or owner_pid is None:
        return state
    if not process_matches_service(owner_pid, service):
        return state

    recorded_pids = state_recorded_pids(state)
    if owner_pid in recorded_pids:
        return state

    updated_state = dict(state)
    current_pid = int(updated_state.get("pid", 0) or 0)
    launcher_pid = int(updated_state.get("launcher_pid", 0) or 0)
    if current_pid > 0 and current_pid != owner_pid and launcher_pid <= 0:
        updated_state["launcher_pid"] = current_pid
    updated_state["pid"] = owner_pid
    save_state(service, updated_state)
    return updated_state


def normalize_path_text(value: str) -> str:
    return value.replace("\\", "/").lower()


def get_process_info(pid: int) -> dict[str, Any] | None:
    command = (
        "$p = Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\";"
        " if ($p) {{"
        "   [PSCustomObject]@{{"
        "     ProcessId = $p.ProcessId;"
        "     CommandLine = $p.CommandLine;"
        "     Name = $p.Name"
        "   }} | ConvertTo-Json -Compress"
        " }}"
    ).format(pid=pid)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None


def process_matches_service(pid: int, service: ServiceConfig) -> bool:
    info = get_process_info(pid)
    if not info:
        return False
    command_line = normalize_path_text(str(info.get("CommandLine") or ""))
    root_path = normalize_path_text(str(ROOT))
    return service.role_marker.lower() in command_line and root_path in command_line


def wait_for_exit(pid: int, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if get_process_info(pid) is None:
            return True
        time.sleep(0.5)
    return get_process_info(pid) is None


def stop_pid(pid: int) -> bool:
    subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, text=True, check=False)
    if wait_for_exit(pid, timeout_s=8):
        return True

    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, check=False)
    return wait_for_exit(pid, timeout_s=5)


def stop_tracked_service(service: ServiceConfig) -> None:
    state = load_state(service)
    if not state:
        return

    pid_values = state_recorded_pids(state)
    resolved_pid = resolve_service_pid(service, fallback_pid=pid_values[0] if pid_values else None)
    if resolved_pid and resolved_pid not in pid_values:
        pid_values.insert(0, resolved_pid)

    if not pid_values:
        remove_state(service)
        return

    matching_pids = [pid for pid in pid_values if process_matches_service(pid, service)]
    if not matching_pids:
        print(
            f"[WARN] Skipping {service.name}: runtime state exists but no recorded PID still matches this project."
        )
        remove_state(service)
        return

    for pid in matching_pids:
        print(f"[INFO] Stopping tracked {service.name} process (PID {pid})...")
        if stop_pid(pid):
            print(f"[INFO] Stopped {service.name} PID {pid}.")
        else:
            print(f"[WARN] Failed to fully stop {service.name} PID {pid}.")
    remove_state(service)


def get_port_owner_pid(port: int) -> int | None:
    command = (
        "$conn = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue |"
        " Select-Object -First 1 OwningProcess;"
        " if ($conn) {{ $conn.OwningProcess }}"
    ).format(port=port)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout.strip()
    if not output:
        return get_port_owner_pid_from_netstat(port)
    try:
        return int(output)
    except ValueError:
        return get_port_owner_pid_from_netstat(port)


def get_port_owner_pid_from_netstat(port: int) -> int | None:
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None

    port_suffix = f":{port}"
    pattern = re.compile(r"\s+")
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = pattern.split(line)
        if len(parts) < 5:
            continue
        protocol, local_address, _foreign_address, state, pid_text = parts[:5]
        if protocol.upper() != "TCP":
            continue
        if state.upper() != "LISTENING":
            continue
        if not local_address.endswith(port_suffix):
            continue
        try:
            return int(pid_text)
        except ValueError:
            return None
    return None


def find_untracked_project_process(service: ServiceConfig) -> int | None:
    owner_pid = get_port_owner_pid(service.port)
    if owner_pid is None or not process_matches_service(owner_pid, service):
        return None

    recorded_pids = state_recorded_pids(load_state(service))
    if owner_pid in recorded_pids:
        return None
    return owner_pid


def ensure_port_available(service: ServiceConfig) -> None:
    owner_pid = get_port_owner_pid(service.port)
    if owner_pid is None:
        return

    recorded_pids = state_recorded_pids(load_state(service))
    if process_matches_service(owner_pid, service) and owner_pid in recorded_pids:
        raise RuntimeError(
            f"Port {service.port} is still occupied by the recorded {service.name} process (PID {owner_pid}) after cleanup."
        )

    if process_matches_service(owner_pid, service):
        raise RuntimeError(
            f"Port {service.port} is occupied by an untracked {service.name} process from this project (PID {owner_pid}). "
            f"Stop PID {owner_pid} manually, then retry."
        )

    info = get_process_info(owner_pid)
    detail = ""
    if info:
        detail = f" Process name: {info.get('Name')}. Command line: {info.get('CommandLine')}"
    raise RuntimeError(
        f"Port {service.port} is occupied by an unknown process (PID {owner_pid}).{detail}"
    )


def wait_for_http(url: str, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if 200 <= response.status < 500:
                    return True
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    return False


def spawn_process(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
) -> int:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        )
    return process.pid


def ensure_env_file() -> None:
    if not (ROOT / ".env").exists():
        raise RuntimeError("Missing .env. Copy .env.example to .env and fill in the required keys.")


def ensure_npm_available() -> None:
    if NPM_EXE is None:
        raise RuntimeError("npm was not found in PATH. Install Node.js and reopen the terminal.")


def ensure_node_available() -> None:
    if NODE_EXE is None:
        raise RuntimeError("node was not found in PATH. Install Node.js and reopen the terminal.")


def ensure_venv() -> Path:
    if VENV_PYTHON.exists():
        return VENV_PYTHON

    base_python = sys.executable or shutil.which("python")
    if not base_python:
        raise RuntimeError("Python was not found. Install Python before starting the dev services.")

    print("[INFO] Creating Python virtual environment...")
    run_checked([base_python, "-m", "venv", str(VENV_DIR)], cwd=ROOT)
    if not VENV_PYTHON.exists():
        raise RuntimeError("Failed to create the project virtual environment.")
    return VENV_PYTHON


def dependency_stamp(name: str) -> Path:
    return RUNTIME_DIR / f"{name}.stamp"


def stamp_is_stale(stamp: Path, inputs: list[Path]) -> bool:
    if not stamp.exists():
        return True
    stamp_mtime = stamp.stat().st_mtime
    return any(path.exists() and path.stat().st_mtime > stamp_mtime for path in inputs)


def ensure_python_dependencies(venv_python: Path) -> None:
    stamp = dependency_stamp("python-deps")
    requirements = ROOT / "requirements.txt"
    if not stamp_is_stale(stamp, [requirements]):
        return

    print("[INFO] Installing Python dependencies...")
    run_checked([str(venv_python), "-m", "pip", "install", "-r", str(requirements), "--prefer-binary"], cwd=ROOT)
    touch(stamp)


def ensure_frontend_dependencies() -> None:
    stamp = dependency_stamp("frontend-deps")
    package_json = FRONTEND_DIR / "package.json"
    node_modules = FRONTEND_DIR / "node_modules"
    if node_modules.exists() and not stamp_is_stale(stamp, [package_json]):
        return

    print("[INFO] Installing frontend dependencies...")
    run_checked([str(NPM_EXE), "install"], cwd=FRONTEND_DIR)
    touch(stamp)


def ensure_spacy_model(venv_python: Path) -> None:
    probe = subprocess.run(
        [str(venv_python), "-c", "import spacy; spacy.load('en_core_web_sm')"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0:
        return

    print("[INFO] Downloading spaCy model en_core_web_sm...")
    run_checked([str(venv_python), "-m", "spacy", "download", "en_core_web_sm"], cwd=ROOT)


def resolve_service_pid(service: ServiceConfig, fallback_pid: int | None = None) -> int | None:
    candidates: list[int] = []
    if fallback_pid and fallback_pid > 0:
        candidates.append(fallback_pid)

    owner_pid = get_port_owner_pid(service.port)
    if owner_pid and owner_pid not in candidates:
        candidates.append(owner_pid)

    for candidate in candidates:
        if process_matches_service(candidate, service):
            return candidate
    return None


def start_backend(venv_python: Path, reload_enabled: bool = False) -> dict[str, Any]:
    service = SERVICES["backend"]
    command = [
        str(venv_python),
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(service.port),
    ]
    if reload_enabled:
        command.append("--reload")
    env = os.environ.copy()
    pid = spawn_process(command, service.cwd, env, service.stdout_log, service.stderr_log)
    state = {
        "name": service.name,
        "pid": pid,
        "port": service.port,
        "cwd": str(service.cwd),
        "command": command,
        "health_url": service.health_url,
        "stdout_log": str(service.stdout_log),
        "stderr_log": str(service.stderr_log),
        "reload_enabled": reload_enabled,
        "started_at": now_iso(),
    }
    if not wait_for_http(service.health_url, timeout_s=45):
        save_state(service, state)
        raise RuntimeError(f"Backend did not become healthy at {service.health_url}. Check {service.stderr_log}.")
    resolved_pid = resolve_service_pid(service, fallback_pid=pid)
    if resolved_pid:
        state["pid"] = resolved_pid
    if resolved_pid and resolved_pid != pid:
        state["launcher_pid"] = pid
    save_state(service, state)
    return state


def start_frontend() -> dict[str, Any]:
    service = SERVICES["frontend"]
    vite_js = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"
    command = [str(NODE_EXE), str(vite_js), "--host", "127.0.0.1", "--port", str(service.port)]
    env = os.environ.copy()
    env["LISTENING_TRAINER_API_TARGET"] = BACKEND_URL
    pid = spawn_process(command, service.cwd, env, service.stdout_log, service.stderr_log)
    state = {
        "name": service.name,
        "pid": pid,
        "port": service.port,
        "cwd": str(service.cwd),
        "command": command,
        "health_url": service.health_url,
        "stdout_log": str(service.stdout_log),
        "stderr_log": str(service.stderr_log),
        "started_at": now_iso(),
    }
    if not wait_for_http(service.health_url, timeout_s=45):
        save_state(service, state)
        raise RuntimeError(f"Frontend did not become healthy at {service.health_url}. Check {service.stderr_log}.")
    resolved_pid = resolve_service_pid(service, fallback_pid=pid)
    if resolved_pid:
        state["pid"] = resolved_pid
    if resolved_pid and resolved_pid != pid:
        state["launcher_pid"] = pid
    save_state(service, state)
    return state


def command_start(args: argparse.Namespace) -> int:
    ensure_runtime_dirs()
    ensure_env_file()
    ensure_npm_available()
    ensure_node_available()
    venv_python = ensure_venv()
    ensure_python_dependencies(venv_python)
    ensure_frontend_dependencies()
    ensure_spacy_model(venv_python)

    stop_tracked_service(SERVICES["frontend"])
    stop_tracked_service(SERVICES["backend"])

    ensure_port_available(SERVICES["backend"])
    ensure_port_available(SERVICES["frontend"])

    started_services: list[ServiceConfig] = []
    try:
        started_services.append(SERVICES["backend"])
        backend_state = start_backend(venv_python, reload_enabled=args.backend_reload)
        started_services.append(SERVICES["frontend"])
        frontend_state = start_frontend()
    except Exception:
        for service in reversed(started_services):
            stop_tracked_service(service)
        raise

    print("[INFO] Development services are ready.")
    print(f"[INFO] Backend  : {backend_state['health_url']}")
    print(f"[INFO] Frontend : {frontend_state['health_url']}")
    print(f"[INFO] Logs     : {SERVICES['backend'].stdout_log} and {SERVICES['frontend'].stdout_log}")
    return 0


def command_stop(_: argparse.Namespace) -> int:
    ensure_runtime_dirs()
    stop_tracked_service(SERVICES["frontend"])
    stop_tracked_service(SERVICES["backend"])
    for service in (SERVICES["backend"], SERVICES["frontend"]):
        orphan_pid = find_untracked_project_process(service)
        if orphan_pid:
            print(
                f"[WARN] {service.name} still has an untracked project process on port {service.port} (PID {orphan_pid}). "
                f"Stop it manually if you want the port fully released."
            )
    print("[INFO] Stop command completed.")
    return 0


def service_status(service: ServiceConfig) -> dict[str, Any]:
    state = load_state(service)
    owner_pid = get_port_owner_pid(service.port)
    state = reconcile_service_state_with_owner(service, state, owner_pid)
    owner_matches_service = owner_pid is not None and process_matches_service(owner_pid, service)
    recorded_pids = state_recorded_pids(state)
    status = {
        "name": service.name,
        "expected_port": service.port,
        "health_url": service.health_url,
        "tracked": bool(state),
        "running": False,
        "pid": None,
        "port_owner_pid": owner_pid,
        "orphan_pid": None,
        "healthy": False,
        "stdout_log": str(service.stdout_log),
        "stderr_log": str(service.stderr_log),
    }

    if owner_matches_service:
        status["running"] = True
        status["pid"] = owner_pid
        status["healthy"] = wait_for_http(service.health_url, timeout_s=2)
        if owner_pid not in recorded_pids:
            status["orphan_pid"] = owner_pid

    if not state:
        return status

    pid = int(state.get("pid", 0) or 0)
    if status["pid"] is None:
        status["pid"] = pid
    if not status["running"]:
        status["running"] = process_matches_service(pid, service)
    if status["running"] and not status["healthy"]:
        status["healthy"] = wait_for_http(service.health_url, timeout_s=2)
    return status


def print_status(service: ServiceConfig) -> None:
    status = service_status(service)
    print(f"{service.name.upper()}:")
    print(f"  tracked     : {status['tracked']}")
    print(f"  running     : {status['running']}")
    print(f"  pid         : {status['pid']}")
    print(f"  port owner  : {status['port_owner_pid']}")
    print(f"  orphan pid  : {status['orphan_pid']}")
    print(f"  healthy     : {status['healthy']}")
    print(f"  health url  : {status['health_url']}")
    print(f"  stdout log  : {status['stdout_log']}")
    print(f"  stderr log  : {status['stderr_log']}")


def command_status(_: argparse.Namespace) -> int:
    ensure_runtime_dirs()
    print_status(SERVICES["backend"])
    print()
    print_status(SERVICES["frontend"])
    return 0


def command_restart(args: argparse.Namespace) -> int:
    command_stop(args)
    return command_start(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Development process manager for Listening Trainer.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler in (
        ("start", command_start),
        ("stop", command_stop),
        ("restart", command_restart),
        ("status", command_status),
    ):
        subparser = subparsers.add_parser(name)
        if name in {"start", "restart"}:
            subparser.add_argument(
                "--backend-reload",
                action="store_true",
                help="Enable uvicorn auto-reload for the backend. Disabled by default for stability.",
            )
        subparser.set_defaults(handler=handler)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] Command failed with exit code {exc.returncode}: {' '.join(exc.cmd)}", file=sys.stderr)
        return exc.returncode
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
