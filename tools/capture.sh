#!/usr/bin/env bash
# Record Nollie USB traffic on the host (works while the device is passed through to the VM).
#
#   tools/capture.sh <name> [seconds]     -> captures/<name>.pcapng
#
# Runs until Ctrl-C, or for [seconds] if given. Needs read access to /dev/usbmonN:
# run `tools/setup-dev.sh usbmon` once per boot.
set -euo pipefail

VID=3061
PID=4716
cd "$(dirname "$0")/.."

name=${1:?usage: $0 <name> [seconds]}
out="captures/$name.pcapng"
[[ -e $out ]] && { echo "$out already exists" >&2; exit 1; }

dev=""
for d in /sys/bus/usb/devices/*; do
    [[ -f $d/idVendor && $(<"$d/idVendor") == "$VID" && $(<"$d/idProduct") == "$PID" ]] && dev=$d
done
[[ -n $dev ]] || { echo "Nollie ($VID:$PID) not found on USB" >&2; exit 1; }
bus=$(<"$dev/busnum")
[[ -r /dev/usbmon$bus ]] || { echo "/dev/usbmon$bus not readable; run tools/setup-dev.sh usbmon" >&2; exit 1; }

# /usr/bin/dumpcap is only executable by the wireshark group. Until that membership is active
# (needs a re-login), use a private copy; plain read access to usbmon is all it needs.
dumpcap=/usr/bin/dumpcap
if [[ ! -x $dumpcap ]]; then
    dumpcap="${XDG_RUNTIME_DIR:-/tmp}/opennollie-dumpcap"
    cp /usr/bin/dumpcap "$dumpcap" && chmod u+x "$dumpcap"
fi

args=(-q -i "usbmon$bus" -w "$out")
[[ -n ${2:-} ]] && args+=(-a "duration:$2")
echo "==> Capturing bus $bus (Nollie is device $(<"$dev/devnum")) to $out. Ctrl-C to stop."
"$dumpcap" "${args[@]}"
