#!/usr/bin/env bash
# Keep the Nollie attached to the Windows VM while the official app updates its firmware.
#
# The VM passes the Nollie through by USB ID (3061:4716). During a firmware update the device
# re-enumerates as the bootloader (3061:f037) and then as the new firmware (16d5:2a16); libvirt
# won't pick those up on its own. This watches for them and hot-attaches each one to the running VM.
#
#   tools/vm-follow-nollie.sh [vm-name]      (default: win10; Ctrl-C to stop)
#
# Only attaches devices; never sends anything to the Nollie itself.
set -euo pipefail

VM=${1:-win10}
VIRSH=(virsh -c qemu:///system)
IDS=(3061:f037 16d5:2a16 3061:4716)   # bootloader, new firmware, legacy firmware

attached() {   # attached <vid> <pid>: is a hostdev with this ID in the VM's live config?
    "${VIRSH[@]}" dumpxml "$VM" | tr -d '\n ' |
        grep -qi "<vendorid='0x$1'/><productid='0x$2'/>"
}

[[ $("${VIRSH[@]}" domstate "$VM") == running ]] || { echo "VM '$VM' is not running" >&2; exit 1; }
echo "==> Watching for Nollie IDs ${IDS[*]} and attaching them to '$VM'. Ctrl-C to stop."

while true; do
    for id in "${IDS[@]}"; do
        vid=${id%:*} pid=${id#*:}
        lsusb -d "$id" >/dev/null 2>&1 || continue
        attached "$vid" "$pid" && continue
        xml=$(mktemp --suffix=.xml)
        cat >"$xml" <<EOF
<hostdev mode='subsystem' type='usb' managed='yes'>
  <source><vendor id='0x$vid'/><product id='0x$pid'/></source>
</hostdev>
EOF
        if "${VIRSH[@]}" attach-device "$VM" "$xml" --live >/dev/null; then
            echo "$(date +%T) attached $id to $VM"
        else
            echo "$(date +%T) FAILED to attach $id (will retry)" >&2
        fi
        rm -f "$xml"
    done
    sleep 0.5
done
