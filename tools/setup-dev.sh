#!/usr/bin/env bash
# Phase 0 dev-environment setup for OpenNollie. Run as your normal user; it calls sudo.
#
# The usbmon access granted at the end is temporary (it's gone after a reboot or module
# reload), so re-run `tools/setup-dev.sh usbmon` before each capture session.
set -euo pipefail

VID=3061
PID=4716

find_nollie() {
    for d in /sys/bus/usb/devices/*; do
        [[ -f $d/idVendor && $(<"$d/idVendor") == "$VID" && $(<"$d/idProduct") == "$PID" ]] && { echo "$d"; return; }
    done
    echo "Nollie ($VID:$PID) not found on USB" >&2
    exit 1
}

setup_packages() {
    echo "==> Installing packages"
    sudo pacman -S --needed python-hidapi pyside6 wireshark-cli

    echo "==> Adding $USER to the wireshark group (lets dumpcap capture)"
    sudo usermod -aG wireshark "$USER"
}

bind_driver() {
    local dev iface
    dev=$(find_nollie)
    iface="$(basename "$dev"):1.0"
    if [[ -e /sys/bus/usb/devices/$iface/driver ]]; then
        echo "==> $iface already bound to $(basename "$(readlink "/sys/bus/usb/devices/$iface/driver")")"
    else
        echo "==> Binding usbhid to $iface"
        echo "$iface" | sudo tee /sys/bus/usb/drivers/usbhid/bind >/dev/null
    fi
}

usbmon_access() {
    local bus
    bus=$(<"$(find_nollie)/busnum")
    echo "==> Loading usbmon, granting $USER read access to /dev/usbmon$bus (until reboot)"
    sudo modprobe usbmon
    sudo setfacl -m "u:$USER:r" "/dev/usbmon$bus"
}

case "${1:-all}" in
    all)    setup_packages; bind_driver; usbmon_access ;;
    bind)   bind_driver ;;
    usbmon) usbmon_access ;;
    *)      echo "usage: $0 [all|bind|usbmon]" >&2; exit 2 ;;
esac
echo "==> Done"
