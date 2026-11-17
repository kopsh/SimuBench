"""Assemble the portable Windows bundle from a built wheel, on any OS (#1188).

Usage (scripts/build-windows.sh runs it): ``python scripts/windows_bundle.py dist/simubench-*.whl``.

The bundle is a folder, zipped as ``dist/SimuBench-<version>-win64.zip``::

    SimuBench-<version>/
      SimuBench.exe          double-click: Web Bench + MCP in one service, browser opens (`simubench launch`)
      tools/simubench.exe    the full command line (`simubench validate ...`, `simubench mcp` for stdio)
      python/                the official embeddable CPython for Windows, with SimuBench in Lib/site-packages
      使用说明.txt

Nothing is installed on the user's PC and nothing is downloaded when it runs (offline, D17). Built on
Linux: the Windows wheels come from the package index by uv's cross-platform install pinned to
uv.lock, the interpreter is the embeddable zip from python.org (or ``SIMUBENCH_PYTHON_EMBED``: a URL
or a local file, for an internal mirror), and the two ``.exe`` files are the script launcher pip
itself writes on Windows (distlib's ``t64.exe``) with a shebang relative to the launcher's folder and
a ``__main__.py`` appended as a zip.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

#: The interpreter inside the bundle: the CI tests run on 3.12.
PYTHON_VERSION = "3.12.10"
PY_TAG = "312"
EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
#: SHA-256 of that zip: a copy from an internal mirror or share must be the same file. Changing
#: PYTHON_VERSION changes this too (sha256sum of the new python.org file).
EMBED_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"

ROOT = Path(__file__).resolve().parents[1]

LAUNCH_MAIN = "import sys\nfrom simubench.cli import main\nsys.exit(main(['launch', *sys.argv[1:]]))\n"
CLI_MAIN = "import sys\nfrom simubench.cli import main\nsys.exit(main())\n"

README = """SimuBench {version}（Windows 便携版）

启动
  双击 SimuBench.exe。浏览器自动打开 SimuBench 界面；同时启动 MCP 服务，供 AI 助手连接。
  黑色窗口里是服务的地址与日志；关闭这个窗口即停止服务。再次双击时若已在运行，只打开界面。
  不需要安装 Python，也不需要联网。整个文件夹可以放在任何位置（路径建议不含中文以外的特殊符号）。

测试台文件
  默认放在 文档\\SimuBench（第一次启动时创建）。另选文件夹：在命令行里运行
    SimuBench.exe --workspace D:\\我的测试台

连接外部 AI（MCP）
  最简单：在打开的页面右上角点「连接外部 AI（MCP）」，复制地址或配置即可。
  服务启动后 MCP 地址是 http://127.0.0.1:8765/mcp （端口被占用时用下一个空闲端口，以窗口里显示的为准）。
  在支持 HTTP MCP 的客户端里加入：
    {{"mcpServers": {{"simubench": {{"type": "http", "url": "http://127.0.0.1:8765/mcp"}}}}}}
  只支持 stdio 的客户端：命令填本文件夹下 tools\\simubench.exe，参数填 mcp --workspace <测试台文件夹>

脚本连接仪表
  界面与窗口里列出每台仪表的 SCPI / HiSLIP 地址，NI-VISA、Keysight IO Libraries、pyvisa 可直接连接。
  默认端口：SCPI 从 5025 起（每台仪表加 1），HiSLIP 4880。默认端口被别的程序占用时，自动改用空闲端口，
  界面会提示，脚本按界面上列出的地址连接。要固定端口：给 SimuBench.exe 的快捷方式「目标」末尾加参数，例如
    SimuBench.exe --scpi-base-port 6000 --hislip-port 4890
  （指定了端口而它被占用时会报错，不会自动换。）

命令行
  tools\\simubench.exe 是完整的命令行（validate、doctor、replay ……），例如：
    tools\\simubench.exe doctor
"""


def _run(cmd: list[str], **kw: object) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)  # type: ignore[call-overload]


def check_embed(data: bytes, src: str) -> bytes:
    digest = hashlib.sha256(data).hexdigest()
    if digest != EMBED_SHA256:
        raise SystemExit(
            f"{src}: sha256 {digest}, expected {EMBED_SHA256} "
            f"(python-{PYTHON_VERSION}-embed-amd64.zip from python.org)"
        )
    return data


def _embed_zip(cache: Path) -> bytes:
    """The embeddable CPython zip: SIMUBENCH_PYTHON_EMBED (a local file or a URL, for a network
    without python.org), else the copy in the cache folder, else python.org; always checksummed."""
    src = os.environ.get("SIMUBENCH_PYTHON_EMBED") or EMBED_URL
    if Path(src).is_file():
        return check_embed(Path(src).read_bytes(), src)
    cached = cache / Path(src).name
    if not cached.is_file():
        print(f"+ download {src}", flush=True)
        cache.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(src, timeout=120) as r:
            data = check_embed(r.read(), src)
        cached.write_bytes(data)
    return check_embed(cached.read_bytes(), str(cached))


def _launcher() -> bytes:
    """distlib's console launcher (the one pip writes as ``<script>.exe`` on Windows)."""
    try:
        import distlib  # noqa: PLC0415
    except ImportError:
        from pip._vendor import distlib  # type: ignore[no-redef]  # noqa: PLC0415
    return (Path(distlib.__file__).parent / "t64.exe").read_bytes()


def _exe(launcher: bytes, python_rel: str, main_py: str) -> bytes:
    """A launcher running ``<launcher_dir>\\<python_rel>`` on the zip appended to itself."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("__main__.py", main_py)
    shebang = f"#!<launcher_dir>\\{python_rel}\n".encode()
    return launcher + shebang + buf.getvalue()


def _version(wheel: Path) -> str:
    m = re.match(r"simubench-([^-]+)-", wheel.name)
    if not m:
        raise SystemExit(f"not a simubench wheel: {wheel}")
    return m.group(1)


def build(wheel: Path, out_dir: Path, cache: Path) -> Path:
    version = _version(wheel)
    name = f"SimuBench-{version}"
    stage = cache / "stage"
    shutil.rmtree(stage, ignore_errors=True)
    app = stage / name
    py = app / "python"
    py.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(_embed_zip(cache))) as z:
        z.extractall(py)
    # ._pth: the stdlib zip, the interpreter folder, our site-packages, and `import site` so that
    # .pth files there run (pywin32's DLL path).
    pth = py / f"python{PY_TAG}._pth"
    if not pth.is_file():
        raise SystemExit(f"{pth.name} missing: the embeddable zip is not Python 3.{PY_TAG[1:]}")
    pth.write_text(f"python{PY_TAG}.zip\n.\nLib\\site-packages\nimport site\n", encoding="utf-8")

    site = py / "Lib" / "site-packages"
    req = cache / "requirements.txt"
    _run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--extra",
            "mcp",
            "--no-emit-project",
            "--no-hashes",
            "-o",
            str(req),
        ],
        cwd=ROOT,
    )
    target = [
        "--target",
        str(site),
        "--python-platform",
        "x86_64-pc-windows-msvc",
        "--python-version",
        "3.12",
    ]
    _run(["uv", "pip", "install", "--no-deps", "--only-binary", ":all:", *target, "-r", str(req)], cwd=ROOT)
    _run(["uv", "pip", "install", "--no-deps", *target, str(wheel)], cwd=ROOT)
    for junk in site.glob("**/__pycache__"):
        shutil.rmtree(junk, ignore_errors=True)
    bad = [p.name for p in site.glob("*.dist-info") if p.name.startswith(("uvloop",))]
    if bad:
        raise SystemExit(f"non-Windows packages in the bundle: {bad}")

    launcher = _launcher()
    (app / "SimuBench.exe").write_bytes(_exe(launcher, "python\\python.exe", LAUNCH_MAIN))
    (app / "tools").mkdir()
    (app / "tools" / "simubench.exe").write_bytes(_exe(launcher, "..\\python\\python.exe", CLI_MAIN))
    (app / "使用说明.txt").write_text(
        README.format(version=version).replace("\n", "\r\n"), encoding="utf-8-sig"
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}-win64.zip"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(app.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(stage).as_posix())
    shutil.rmtree(stage, ignore_errors=True)
    print(f"{out} ({out.stat().st_size / 1e6:.0f} MB)")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("wheel", type=Path)
    p.add_argument("--out", type=Path, default=ROOT / "dist")
    p.add_argument("--cache", type=Path, default=ROOT / "build" / "windows")
    args = p.parse_args(argv)
    build(args.wheel.resolve(), args.out.resolve(), args.cache.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
