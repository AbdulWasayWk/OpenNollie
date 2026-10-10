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

# live_entries <vid> <pid>: "alias bus device" for each hostdev with this ID in the VM's live config.
live_entries() {
    "${VIRSH[@]}" dumpxml "$VM" | python3 -c '
import sys, xml.etree.ElementTree as ET
vid, pid = (int(x, 16) for x in sys.argv[1:3])
for h in ET.fromstring(sys.stdin.read()).iter("hostdev"):
    s, al = h.find("source"), h.find("alias")
    v, p, a = s.find("vendor"), s.find("product"), s.find("address")
    if v is None or p is None or int(v.get("id"), 16) != vid or int(p.get("id"), 16) != pid:
        continue
    print(al.get("name") if al is not None else "-",
          a.get("bus", "-") if a is not None else "-", a.get("device", "-") if a is not None else "-")
' "$1" "$2"
}

[[ $("${VIRSH[@]}" domstate "$VM") == running ]] || { echo "VM '$VM' is not running" >&2; exit 1; }
echo "==> Watching for Nollie IDs ${IDS[*]} and attaching them to '$VM'. Ctrl-C to stop."

while true; do
    for id in "${IDS[@]}"; do
        vid=${id%:*} pid=${id#*:}
        dev=$(lsusb -d "$id" 2>/dev/null | awk '{print $2+0, $4+0; exit}') || true
        [[ -n $dev ]] || continue
        read -r bus devnum <<<"$dev"
        current=0
        # The device re-enumerates with a new address after every reset, which leaves earlier
        # entries pointing at a dead address. Keep the matching one and drop the stale ones.
        while read -r alias ebus edev; do
            [[ -z $alias ]] && continue
            if [[ $ebus == "$bus" && $edev == "$devnum" ]]; then
                current=1
            elif [[ $alias != - ]]; then
                "${VIRSH[@]}" detach-device-alias "$VM" "$alias" --live >/dev/null 2>&1 &&
                    echo "$(date +%T) removed stale $id entry ($alias, was $ebus/$edev)"
            fi
        done < <(live_entries "$vid" "$pid")
        (( current )) && continue
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
