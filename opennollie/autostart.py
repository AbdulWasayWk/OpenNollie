"""Whether OpenNollie starts at login: the tray icon (XDG autostart) and the background service
(systemd user unit). Both are per-user switches; no root needed."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

AUTOSTART_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"
TRAY_ENTRY = AUTOSTART_DIR / "opennollie-tray.desktop"
SYSTEM_TRAY_ENTRY = Path("/etc/xdg/autostart/opennollie-tray.desktop")      # from the Arch package
SERVICE = "opennollie.service"


def _tray_command() -> str:
    exe = shutil.which("opennollie-tray") or str(Path.home() / ".local" / "bin" / "opennollie-tray")
    return exe if Path(exe).exists() else f"{sys.executable} -m opennollie.gui.tray"


def tray_enabled() -> bool:
    """True if the tray icon starts at login (a user entry, or the package's entry not hidden)."""
    for entry in (TRAY_ENTRY, SYSTEM_TRAY_ENTRY):
        if entry.exists():
            text = entry.read_text()
            return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text
    return False


def set_tray(enabled: bool) -> None:
    """Write the user's autostart entry; Hidden=true also overrides a system-wide entry from the package."""
    AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    TRAY_ENTRY.write_text(
        "[Desktop Entry]\nType=Application\nName=OpenNollie tray\n"
        "Comment=Quick profile switching for the Nollie 16CH RGB controller\n"
        f"Exec={_tray_command()}\nIcon=preferences-desktop-color\nTerminal=false\nNoDisplay=true\n"
        f"Hidden={'false' if enabled else 'true'}\nX-GNOME-Autostart-enabled={'true' if enabled else 'false'}\n")


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=15)


def service_installed() -> bool:
    return "could not be found" not in _systemctl("status", SERVICE).stderr


def service_enabled() -> bool:
    return _systemctl("is-enabled", SERVICE).stdout.strip() == "enabled"


def set_service(enabled: bool) -> None:
    """Enable (and start) or disable (and stop) the background service."""
    r = _systemctl("enable" if enabled else "disable", "--now", SERVICE)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or f"systemctl failed ({r.returncode})")
