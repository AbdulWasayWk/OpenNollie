"""Compact disassembly of extracted PyInstaller code objects (one line per source line).

Must run under the app's Python version (3.11), e.g. the uv-managed interpreter:
  ~/.local/share/uv/python/cpython-3.11.*/bin/python3.11 tools/re/disasm.py vendor-app/pyi_out vendor-app/dis2
"""
import dis, glob, io, marshal, os, sys

SKIP = {"RESUME", "CACHE", "PRECALL", "NOP", "PUSH_NULL", "KW_NAMES", "COPY_FREE_VARS", "MAKE_CELL",
        "RETURN_GENERATOR", "POP_TOP"}
SHORT = {"LOAD_FAST": "", "LOAD_CONST": "", "LOAD_GLOBAL": "", "LOAD_ATTR": ".", "LOAD_METHOD": ".",
         "STORE_FAST": "=>", "STORE_ATTR": "=>.", "STORE_NAME": "=>", "LOAD_DEREF": "", "STORE_DEREF": "=>",
         "CALL": "call", "BINARY_SUBSCR": "[]", "STORE_SUBSCR": "[]=", "BINARY_OP": "op", "COMPARE_OP": "cmp",
         "RETURN_VALUE": "return", "BUILD_LIST": "list", "BUILD_TUPLE": "tuple", "BUILD_MAP": "map",
         "BUILD_CONST_KEY_MAP": "kmap"}


def dump(c, buf):
    args = ", ".join(c.co_varnames[: c.co_argcount + c.co_kwonlyargcount])
    buf.write(f"\n## {c.co_qualname}({args})  @L{c.co_firstlineno}\n")
    line, parts = None, []

    def flush():
        if parts:
            buf.write(f"  L{line}: " + " ".join(parts) + "\n")

    for ins in dis.get_instructions(c):
        if ins.starts_line is not None and ins.starts_line != line:
            flush()
            line, parts = ins.starts_line, []
        if ins.opname in SKIP:
            continue
        jump = ins.opname.startswith(("POP_JUMP", "JUMP", "FOR_ITER", "SEND"))
        label = f"<{ins.offset}>" if ins.is_jump_target else ""
        if ins.opname in SHORT:
            arg = ins.argrepr.replace("NULL|self + ", "").replace("NULL + ", "")
            if ins.opname == "LOAD_CONST":
                v = ins.argval
                arg = f"<code {v.co_qualname}>" if hasattr(v, "co_code") else repr(v)
            prefix = SHORT[ins.opname]
            p = f"{prefix}{arg}" if prefix in ("", ".", "=>", "=>.") else f"{prefix}({arg})"
        else:
            p = ins.opname + (f"@{ins.argval}" if jump else (f"({ins.argrepr})" if ins.argrepr else ""))
        parts.append(label + p)
    flush()
    for k in c.co_consts:
        if hasattr(k, "co_code"):
            dump(k, buf)


src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
for f in sorted(glob.glob(f"{src}/pyz/app.*.code")) + glob.glob(f"{src}/NollieRGB.code"):
    buf = io.StringIO()
    dump(marshal.loads(open(f, "rb").read()), buf)
    open(os.path.join(out, os.path.basename(f).removesuffix(".code") + ".txt"), "w").write(buf.getvalue())
