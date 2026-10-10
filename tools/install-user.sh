#!/usr/bin/env bash
# Install OpenNollie for the current user from this checkout (no root needed):
#   ~/.local/bin/opennollie            CLI wrapper
#   ~/.local/bin/opennollie-gui        GUI wrapper
#   ~/.config/systemd/user/opennollie.service   daemon, started at login
#
#   tools/install-user.sh            install + enable + start the daemon
#   tools/install-user.sh --remove   undo
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
BIN=${XDG_BIN_HOME:-$HOME/.local/bin}
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user

if [[ ${1:-} == --remove ]]; then
    systemctl --user disable --now opennollie.service 2>/dev/null || true
    rm -f "$UNIT_DIR/opennollie.service" "$BIN/opennollie" "$BIN/opennollie-gui"
    systemctl --user daemon-reload
    echo "removed"
    exit 0
fi

mkdir -p "$BIN" "$UNIT_DIR"
for name in opennollie opennollie-gui; do
    module=opennollie; [[ $name == opennollie-gui ]] && module=opennollie.gui
    printf '#!/bin/sh\nPYTHONPATH=%q exec python3 -m %s "$@"\n' "$REPO" "$module" > "$BIN/$name"
    chmod +x "$BIN/$name"
done
sed "s|@REPO@|$REPO|g" "$REPO/packaging/opennollie.service" > "$UNIT_DIR/opennollie.service"
systemctl --user daemon-reload
systemctl --user enable --now opennollie.service
echo "installed: $BIN/opennollie, $BIN/opennollie-gui, opennollie.service (running)"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "note: $BIN is not on your PATH" ;; esac
