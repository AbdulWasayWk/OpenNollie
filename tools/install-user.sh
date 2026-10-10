#!/usr/bin/env bash
# Install OpenNollie for the current user from this checkout (no root needed):
#   ~/.local/bin/opennollie, opennollie-gui, opennollie-tray      commands
#   ~/.config/systemd/user/opennollie.service                     daemon, started at login
#   ~/.local/share/applications/opennollie.desktop                app-menu entry
#   ~/.config/autostart/opennollie-tray.desktop                   tray icon at login
#
#   tools/install-user.sh            install, enable + start the daemon, start the tray
#   tools/install-user.sh --remove   undo
#
# (For a system-wide install use the PKGBUILD in packaging/ instead; don't use both.)
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
BIN=${XDG_BIN_HOME:-$HOME/.local/bin}
CONF=${XDG_CONFIG_HOME:-$HOME/.config}
UNIT_DIR=$CONF/systemd/user
APPS=${XDG_DATA_HOME:-$HOME/.local/share}/applications
AUTOSTART=$CONF/autostart

if [[ ${1:-} == --remove ]]; then
    systemctl --user disable --now opennollie.service 2>/dev/null || true
    pkill -u "$USER" -f "opennollie.gui.tray" 2>/dev/null || true
    rm -f "$UNIT_DIR/opennollie.service" "$BIN"/opennollie{,-gui,-tray} "$APPS/opennollie.desktop" \
          "$AUTOSTART/opennollie-tray.desktop"
    systemctl --user daemon-reload
    echo "removed"
    exit 0
fi

mkdir -p "$BIN" "$UNIT_DIR" "$APPS" "$AUTOSTART"
for name in opennollie opennollie-gui opennollie-tray; do
    case $name in
        opennollie) module=opennollie ;;
        opennollie-gui) module=opennollie.gui ;;
        opennollie-tray) module=opennollie.gui.tray ;;
    esac
    printf '#!/bin/sh\nPYTHONPATH=%q exec python3 -m %s "$@"\n' "$REPO" "$module" > "$BIN/$name"
    chmod +x "$BIN/$name"
done
cat > "$UNIT_DIR/opennollie.service" <<UNIT
[Unit]
Description=OpenNollie: Nollie 16CH RGB controller daemon (from $REPO)

[Service]
Type=simple
Environment=PYTHONPATH=$REPO
ExecStart=/usr/bin/python3 -m opennollie.daemon
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
UNIT
sed "s|^Exec=opennollie-gui|Exec=$BIN/opennollie-gui|" "$REPO/packaging/opennollie.desktop" > "$APPS/opennollie.desktop"
sed "s|^Exec=opennollie-tray|Exec=$BIN/opennollie-tray|" "$REPO/packaging/opennollie-tray.desktop" > "$AUTOSTART/opennollie-tray.desktop"
systemctl --user daemon-reload
systemctl --user enable opennollie.service
systemctl --user restart opennollie.service
if ! pgrep -u "$USER" -f "opennollie.gui.tray" >/dev/null; then
    setsid "$BIN/opennollie-tray" >/dev/null 2>&1 < /dev/null &
fi
echo "installed: $BIN/opennollie{,-gui,-tray}, app-menu entry, tray autostart, opennollie.service (running)"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "note: $BIN is not on your PATH" ;; esac
