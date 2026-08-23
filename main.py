import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Optional

import requests


# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent

WEBAPP_DIR = ROOT_DIR / "webapp"
WEBAPP_APP_FILE = WEBAPP_DIR / "app.py"
WEBAPP_PYTHON = ROOT_DIR / "webapp_env" / "Scripts" / "python.exe"

COMFY_DIR = Path(r"C:\AI\image\ComfyUI")
COMFY_MAIN_FILE = COMFY_DIR / "main.py"
COMFY_PYTHON = COMFY_DIR / "venv" / "Scripts" / "python.exe"


# ---------------------------------------------------------------------------
# Netzwerk
# ---------------------------------------------------------------------------

HOST = "127.0.0.1"

COMFY_PORT = 8188
WEBAPP_DEFAULT_PORT = 8765
WEBAPP_PORT = WEBAPP_DEFAULT_PORT

COMFY_URL = f"http://{HOST}:{COMFY_PORT}"
WEBAPP_URL = f"http://{HOST}:{WEBAPP_PORT}"

COMFY_HEALTH_URL = f"{COMFY_URL}/system_stats"
WEBAPP_HEALTH_URL = f"{WEBAPP_URL}/api/ui-fields"


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------

COMFY_START_TIMEOUT = 180
WEBAPP_START_TIMEOUT = 30

WEBAPP_RELOAD = False
OPEN_BROWSER = True

# Wichtig:
# Bei True wird ComfyUI NICHT automatisch beendet,
# wenn nur die Web-App beim Start abstürzt.
KEEP_COMFY_RUNNING_ON_WEBAPP_ERROR = True


# ---------------------------------------------------------------------------
# Log-Dateien
# ---------------------------------------------------------------------------

COMFY_LOG_PATH = ROOT_DIR / "comfy_start.log"
WEBAPP_LOG_PATH = ROOT_DIR / "webapp_start.log"


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def validate_file(path: Path, description: str) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"{description} wurde nicht gefunden:\n{path}"
        )

    if not path.is_file():
        raise FileNotFoundError(
            f"{description} ist keine Datei:\n{path}"
        )


def validate_directory(path: Path, description: str) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"{description} wurde nicht gefunden:\n{path}"
        )

    if not path.is_dir():
        raise NotADirectoryError(
            f"{description} ist kein Ordner:\n{path}"
        )


def validate_paths() -> None:
    validate_directory(WEBAPP_DIR, "Web-App-Ordner")
    validate_file(WEBAPP_APP_FILE, "Web-App app.py")
    validate_file(WEBAPP_PYTHON, "Python der Web-App-Umgebung")

    validate_directory(COMFY_DIR, "ComfyUI-Ordner")
    validate_file(COMFY_MAIN_FILE, "ComfyUI main.py")
    validate_file(COMFY_PYTHON, "Python der ComfyUI-Umgebung")


def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_available_port(preferred_port: int, attempts: int = 30) -> int:
    """Return the preferred local port or the next available one."""
    for candidate in range(preferred_port, preferred_port + attempts):
        if not is_port_open(HOST, candidate):
            return candidate

    raise RuntimeError(
        f"Kein freier Web-App-Port im Bereich "
        f"{preferred_port}–{preferred_port + attempts - 1} gefunden."
    )


def configure_webapp_port() -> None:
    """Select a free project-specific port and refresh dependent URLs."""
    global WEBAPP_PORT, WEBAPP_URL, WEBAPP_HEALTH_URL

    selected_port = find_available_port(WEBAPP_DEFAULT_PORT)

    if selected_port != WEBAPP_DEFAULT_PORT:
        print(
            f"Web-App-Port {WEBAPP_DEFAULT_PORT} ist belegt. "
            f"Verwende automatisch Port {selected_port}."
        )

    WEBAPP_PORT = selected_port
    WEBAPP_URL = f"http://{HOST}:{WEBAPP_PORT}"
    WEBAPP_HEALTH_URL = f"{WEBAPP_URL}/api/ui-fields"


def url_responds(
    url: str,
    timeout: float = 2.0,
    require_success: bool = True
) -> bool:
    try:
        response = requests.get(url, timeout=timeout)

        if require_success:
            return response.ok

        return True

    except requests.RequestException:
        return False


def wait_for_url(
    url: str,
    timeout: float,
    process: Optional[subprocess.Popen] = None,
    label: str = "Dienst"
) -> bool:
    started_at = time.monotonic()

    while time.monotonic() - started_at < timeout:
        if process is not None:
            exit_code = process.poll()

            if exit_code is not None:
                raise RuntimeError(
                    f"{label} wurde vorzeitig beendet. "
                    f"Exit-Code: {exit_code}"
                )

        if url_responds(url):
            return True

        time.sleep(1)

    return False


def get_listening_process_id(port: int) -> Optional[int]:
    powershell_command = (
        "$connection = Get-NetTCPConnection "
        f"-LocalPort {port} "
        "-State Listen "
        "-ErrorAction SilentlyContinue | "
        "Select-Object -First 1; "
        "if ($connection) { "
        "$connection.OwningProcess "
        "}"
    )

    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                powershell_command
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None

    output = result.stdout.strip()

    if not output:
        return None

    try:
        return int(output)
    except ValueError:
        return None


def get_process_details(pid: int) -> str:
    powershell_command = (
        f"$process = Get-CimInstance Win32_Process "
        f"-Filter \"ProcessId = {pid}\" "
        "-ErrorAction SilentlyContinue; "
        "if ($process) { "
        "'PID: ' + $process.ProcessId; "
        "'Executable: ' + $process.ExecutablePath; "
        "'CommandLine: ' + $process.CommandLine "
        "}"
    )

    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                powershell_command
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"PID: {pid}\nProzessdetails konnten nicht gelesen werden: {exc}"

    details = result.stdout.strip()

    if not details:
        return f"PID: {pid}\nKeine weiteren Prozessdetails verfügbar."

    return details


def describe_port_conflict(port: int, service_name: str) -> str:
    pid = get_listening_process_id(port)

    if pid is None:
        return (
            f"{service_name}: Port {port} ist bereits belegt.\n"
            "Der zugehörige Prozess konnte nicht ermittelt werden."
        )

    details = get_process_details(pid)

    return (
        f"{service_name}: Port {port} ist bereits belegt.\n\n"
        f"{details}"
    )


def ensure_port_is_free(port: int, service_name: str) -> None:
    if not is_port_open(HOST, port):
        return

    raise RuntimeError(
        describe_port_conflict(port, service_name)
        + "\n\nBeende den alten Prozess oder ändere den Port, "
          "bevor du diesen Launcher erneut startest."
    )


def open_log_file(path: Path):
    return open(
        path,
        mode="w",
        encoding="utf-8",
        errors="replace",
        buffering=1
    )


def print_log_hint() -> None:
    print()
    print("Log-Dateien:")
    print(f"ComfyUI: {COMFY_LOG_PATH}")
    print(f"Web-App: {WEBAPP_LOG_PATH}")
    print()


def start_comfy() -> subprocess.Popen:
    command = [
        str(COMFY_PYTHON),
        str(COMFY_MAIN_FILE),
        "--listen",
        HOST,
        "--port",
        str(COMFY_PORT)
    ]

    print()
    print("Starte ComfyUI")
    print("----------------")
    print("Ordner: ", COMFY_DIR)
    print("Python: ", COMFY_PYTHON)
    print("Log:    ", COMFY_LOG_PATH)
    print("Befehl:", subprocess.list2cmdline(command))
    print()

    log_file = open_log_file(COMFY_LOG_PATH)

    return subprocess.Popen(
        command,
        cwd=str(COMFY_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True
    )


def start_webapp() -> subprocess.Popen:
    command = [
        str(WEBAPP_PYTHON),
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        HOST,
        "--port",
        str(WEBAPP_PORT),
        "--log-level",
        "debug"
    ]

    if WEBAPP_RELOAD:
        command.append("--reload")

    print()
    print("Starte Web-App")
    print("--------------")
    print("Ordner: ", WEBAPP_DIR)
    print("Python: ", WEBAPP_PYTHON)
    print("app.py: ", WEBAPP_APP_FILE)
    print("Log:    ", WEBAPP_LOG_PATH)
    print("Befehl:", subprocess.list2cmdline(command))
    print()

    log_file = open_log_file(WEBAPP_LOG_PATH)

    return subprocess.Popen(
        command,
        cwd=str(WEBAPP_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True
    )


def print_configuration() -> None:
    print("=" * 72)
    print("Employer Branding Prompt Lab Launcher")
    print("=" * 72)
    print()
    print("Launcher-Datei:       ", Path(__file__).resolve())
    print("ROOT_DIR:              ", ROOT_DIR)
    print()
    print("WEBAPP_DIR:            ", WEBAPP_DIR)
    print("WEBAPP_APP_FILE:       ", WEBAPP_APP_FILE)
    print("WEBAPP_PYTHON:         ", WEBAPP_PYTHON)
    print("WEBAPP_URL:            ", WEBAPP_URL)
    print()
    print("COMFY_DIR:             ", COMFY_DIR)
    print("COMFY_MAIN_FILE:       ", COMFY_MAIN_FILE)
    print("COMFY_PYTHON:          ", COMFY_PYTHON)
    print("COMFY_URL:             ", COMFY_URL)
    print()
    print("WEBAPP_RELOAD:         ", WEBAPP_RELOAD)
    print("KEEP_COMFY_RUNNING_ON_WEBAPP_ERROR:", KEEP_COMFY_RUNNING_ON_WEBAPP_ERROR)
    print("=" * 72)


def main() -> int:
    comfy_process: Optional[subprocess.Popen] = None
    webapp_process: Optional[subprocess.Popen] = None

    try:
        configure_webapp_port()
        print_configuration()
        validate_paths()
        print_log_hint()

        ensure_port_is_free(WEBAPP_PORT, "Web-App")

        if url_responds(COMFY_HEALTH_URL):
            print(
                f"ComfyUI läuft bereits unter {COMFY_URL} "
                "und wird wiederverwendet."
            )
        else:
            ensure_port_is_free(COMFY_PORT, "ComfyUI")
            comfy_process = start_comfy()

            print(f"Warte auf ComfyUI: {COMFY_HEALTH_URL}")

            if not wait_for_url(
                url=COMFY_HEALTH_URL,
                timeout=COMFY_START_TIMEOUT,
                process=comfy_process,
                label="ComfyUI"
            ):
                raise RuntimeError(
                    f"ComfyUI antwortet nach {COMFY_START_TIMEOUT} Sekunden "
                    f"noch nicht unter:\n{COMFY_HEALTH_URL}"
                )

            print("ComfyUI ist erreichbar.")

        webapp_process = start_webapp()

        print(f"Warte auf Web-App: {WEBAPP_HEALTH_URL}")

        if not wait_for_url(
            url=WEBAPP_HEALTH_URL,
            timeout=WEBAPP_START_TIMEOUT,
            process=webapp_process,
            label="Web-App"
        ):
            raise RuntimeError(
                f"Die Web-App antwortet nach {WEBAPP_START_TIMEOUT} Sekunden "
                f"noch nicht unter:\n{WEBAPP_HEALTH_URL}"
            )

        print("Web-App ist erreichbar.")

        if OPEN_BROWSER:
            webbrowser.open(WEBAPP_URL)

        print()
        print("=" * 72)
        print("Alles erfolgreich gestartet.")
        print(f"Web-App: {WEBAPP_URL}")
        print(f"ComfyUI: {COMFY_URL}")
        print("=" * 72)
        print()
        print("Dieses Launcher-Fenster kann geschlossen werden.")
        print("ComfyUI und die Web-App laufen im Hintergrund.")
        print_log_hint()

        return 0

    except KeyboardInterrupt:
        print("\nStart wurde abgebrochen.")
        return 130

    except Exception as exc:
        print()
        print("=" * 72)
        print("FEHLER")
        print("=" * 72)
        print(exc)
        print_log_hint()

        if webapp_process is not None and webapp_process.poll() is None:
            print("Beende die neu gestartete Web-App ...")
            webapp_process.terminate()

        if (
            comfy_process is not None
            and comfy_process.poll() is None
            and not KEEP_COMFY_RUNNING_ON_WEBAPP_ERROR
        ):
            print("Beende das neu gestartete ComfyUI ...")
            comfy_process.terminate()
        elif comfy_process is not None and comfy_process.poll() is None:
            print("ComfyUI bleibt weiterlaufen, damit das Fenster/Log nicht verschwindet.")

        print()
        print("Bitte öffne jetzt diese Datei und kopiere den Inhalt hierher:")
        print(WEBAPP_LOG_PATH)
        print()

        return 1


if __name__ == "__main__":
    exit_code = main()

    if exit_code != 0:
        input("Enter drücken, um das Fenster zu schließen ...")

    sys.exit(exit_code)
