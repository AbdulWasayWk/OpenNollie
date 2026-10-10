"""Minimal PyInstaller (>=4) CArchive + PYZ extractor. Writes raw marshalled code objects.

usage: pyi_extract.py <exe> <outdir>
"""
import marshal, os, struct, sys, zlib

MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
exe, out = sys.argv[1], sys.argv[2]
data = open(exe, "rb").read()
pos = data.rfind(MAGIC)
magic, pkg_len, toc_off, toc_len, pyver, pylib = struct.unpack("!8sIIII64s", data[pos:pos + 88])
pkg_start = pos + 88 - pkg_len
print(f"cookie@{pos:#x} pkg_start={pkg_start:#x} pyver={pyver} lib={pylib.rstrip(b'\0').decode()}")

toc = data[pkg_start + toc_off: pkg_start + toc_off + toc_len]
entries, i = [], 0
while i < len(toc):
    esize, doff, clen, ulen, cflag, typ = struct.unpack("!IIIIBc", toc[i:i + 18])
    name = toc[i + 18:i + esize].rstrip(b"\0").decode()
    entries.append((name, doff, clen, ulen, cflag, typ.decode()))
    i += esize

os.makedirs(out, exist_ok=True)
for name, doff, clen, ulen, cflag, typ in entries:
    blob = data[pkg_start + doff: pkg_start + doff + clen]
    if cflag:
        blob = zlib.decompress(blob)
    print(f"  [{typ}] {name} ({ulen} B)")
    safe = name.replace("/", "_").replace("\\", "_")
    if typ == "s":          # entry-point script: marshalled code object
        open(os.path.join(out, safe + ".code"), "wb").write(blob)
    elif typ == "z":        # PYZ archive of modules
        assert blob[:4] == b"PYZ\0", blob[:8]
        pyc_magic = blob[4:8]
        (ztoc_off,) = struct.unpack("!i", blob[8:12])
        ztoc = marshal.loads(blob[ztoc_off:])
        if isinstance(ztoc, dict):
            ztoc = list(ztoc.items())
        print(f"    PYZ pyc magic {pyc_magic.hex()}, {len(ztoc)} modules")
        zdir = os.path.join(out, "pyz")
        os.makedirs(zdir, exist_ok=True)
        open(os.path.join(out, "pyc_magic"), "wb").write(pyc_magic)
        for mname, (mtyp, moff, mlen) in ztoc:
            mblob = blob[moff:moff + mlen]
            try:
                mblob = zlib.decompress(mblob)
            except zlib.error:
                print(f"    !! {mname}: not zlib (encrypted?)")
                continue
            suffix = ".pkg.code" if mtyp == 1 else ".code"
            open(os.path.join(zdir, mname + suffix), "wb").write(mblob)
