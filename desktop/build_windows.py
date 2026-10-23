"""Build a Windows x64 application from a wheel, an app-local Python, and verified Node."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def download(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as output:
        shutil.copyfileobj(response, output)


def build(args: argparse.Namespace) -> Path:
    if sys.platform != "win32":
        raise SystemExit("Run this builder with Windows Python, not WSL Python.")
    if not (args.python_root / "python.exe").is_file() or (args.python_root / "pyvenv.cfg").exists():
        raise SystemExit("--python-root must be a standalone Windows Python 3.12 installation, not a virtualenv.")
    wheel = args.wheel.resolve()
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        version = next(line[9:] for line in archive.read(metadata).decode().splitlines() if line.startswith("Version: "))
    output = args.output.resolve()
    app = output / f"AutoKeeper-{version}-windows-x64"
    runtime = app / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    if not (runtime / "python" / "python.exe").exists():
        shutil.copytree(args.python_root, runtime / "python",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "site-packages", "include", "libs", "tcl"))
    python = runtime / "python" / "python.exe"
    identity = json.loads(subprocess.check_output([str(python), "-I", "-c",
        'import sys,struct,json; print(json.dumps([list(sys.version_info[:2]),struct.calcsize("P")]))'], text=True))
    if identity != [[3, 12], 8]:
        raise SystemExit("The application requires Windows Python 3.12 x64.")
    # App-local CRT files come from Microsoft's signed redistributable, never System32.
    for name in ("msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        if not (args.msvc_runtime / name).is_file():
            raise SystemExit(f"Missing app-local Microsoft runtime: {name}")
    for library in args.msvc_runtime.glob("*.dll"):
        shutil.copy2(library, python.parent / library.name)
    notices = runtime / "licenses" / "msvc"
    notices.mkdir(parents=True, exist_ok=True)
    for notice in args.msvc_runtime.iterdir():
        if notice.is_file() and notice.suffix.lower() != ".dll":
            shutil.copy2(notice, notices / notice.name)
    package = str(wheel) + ("[claude]" if not args.without_claude else "")
    subprocess.run([str(args.uv), "pip", "install", "--python", str(python),
                    "--target", str(python.parent / "Lib" / "site-packages"), "--index-url", "https://pypi.org/simple",
                    "--no-config", "--reinstall-package", "autokeeper", package], check=True)
    node = runtime / "node"
    node_version = subprocess.check_output([str(node / "node.exe"), "--version"], text=True).strip() if (node / "node.exe").exists() else ""
    if node_version != args.node_version:
        shutil.rmtree(node, ignore_errors=True)
        filename = f"node-{args.node_version}-win-x64.zip"
        url = f"https://nodejs.org/dist/{args.node_version}/"
        with tempfile.TemporaryDirectory(prefix="autokeeper-node-") as temporary:
            folder = Path(temporary)
            download(url + "SHASUMS256.txt", folder / "sums")
            expected = next(line.split()[0] for line in (folder / "sums").read_text().splitlines()
                            if line.split()[-1] == filename)
            download(url + filename, folder / filename)
            with (folder / filename).open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise RuntimeError("Node.js archive checksum mismatch")
            with zipfile.ZipFile(folder / filename) as archive:
                archive.extractall(folder / "unpacked")
            shutil.copytree(folder / "unpacked" / filename.removesuffix(".zip"), node)
    bridge = runtime / "bridge"
    bridge.mkdir(exist_ok=True)
    bundled = runtime / "python" / "Lib" / "site-packages" / "autokeeper" / "bundle" / "bridge"
    for name in ("bcdice_bridge.mjs", "package.json", "package-lock.json"):
        shutil.copy2(bundled / name, bridge / name)
    environment = os.environ.copy()
    environment["PATH"] = str(node) + os.pathsep + environment.get("PATH", "")
    # Use the archive's npm directly; npm.cmd would depend on the user's shell configuration.
    subprocess.run([str(node / "node.exe"), str(node / "node_modules" / "npm" / "bin" / "npm-cli.js"),
                    "ci", "--omit=dev", "--no-audit", "--no-fund"], cwd=bridge, env=environment, check=True)
    for name in ("bootstrap.py", "selftest.py"):
        shutil.copy2(HERE / name, runtime / name)
    # A local build path is not useful metadata on another player's computer.
    for direct in (python.parent / "Lib" / "site-packages").glob("autokeeper-*.dist-info/direct_url.json"):
        direct.unlink()
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    subprocess.run([str(compiler), "/nologo", "/target:exe", "/platform:x64", "/utf8output",
                    f"/out:{app / 'AutoKeeper.exe'}", str(HERE / "Launcher.cs")], check=True)
    (app / "使用说明.txt").write_text(
        f"AutoKeeper {version} · Windows 10/11 x64\n\n"
        "完整解压压缩包，双击 AutoKeeper.exe 启动。无需安装 Python、Node.js 或 uv。\n"
        "首次使用请选择模型并填写自己的凭据；联机朋友只需打开房主提供的网址。\n"
        "模型、搜图等联网功能仍需要网络。程序包不含任何账号、密钥或玩家存档。\n\n"
        "存档：%LOCALAPPDATA%\\autokeeper\n设置：%APPDATA%\\autokeeper\n"
        "更新：退出游戏，解压新版并运行。上述用户数据不会随程序替换。\n"
        "请保留 runtime 文件夹。Claude 登录需要自己的账号；本地模型需要本机模型服务。\n\n"
        "命令行：AutoKeeper.exe --version / --self-test / serve / join 地址\n"
        "源代码与许可证：https://github.com/satoshinji2992/autokeeper\n",
        encoding="utf-8-sig")
    shutil.copy2(HERE.parent / "LICENSE", app / "LICENSE")
    # Keep all upstream licence notices in the runtime and installed dist-info directories.
    dependencies = subprocess.check_output([str(python), "-I", "-c",
        'import importlib.metadata,json; print(json.dumps(sorted((d.metadata["Name"],d.version) '
        'for d in importlib.metadata.distributions())))'], text=True)
    (app / "components.json").write_text(json.dumps({"version": version, "node": args.node_version,
        "python": subprocess.check_output([str(python), "--version"], text=True).strip(),
        "packages": json.loads(dependencies)}, ensure_ascii=False, indent=2), encoding="utf-8")
    # Avoid the builder's real config, keys and game data during verification.
    with tempfile.TemporaryDirectory(prefix="autokeeper-desktop-test-") as temporary:
        environment.update(APPDATA=str(Path(temporary) / "config"), LOCALAPPDATA=str(Path(temporary) / "local"))
        environment.pop("AUTOKEEPER_HOME", None)
        subprocess.run([str(app / "AutoKeeper.exe"), "--self-test"], env=environment, cwd=temporary,
                       check=True, timeout=180)
    for folder in app.rglob("__pycache__"):
        shutil.rmtree(folder)
    archive = Path(shutil.make_archive(str(app), "zip", root_dir=output, base_dir=app.name))
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"READY: {archive} ({archive.stat().st_size / 1024**2:.1f} MiB)", flush=True)
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--python-root", type=Path, default=Path(sys.base_prefix))
    parser.add_argument("--uv", type=Path, default=Path(shutil.which("uv") or "uv.exe"))
    parser.add_argument("--msvc-runtime", type=Path, required=True,
                        help="x64 DLLs and licence notices extracted from Microsoft's signed VC redistributable")
    parser.add_argument("--output", type=Path, default=HERE.parent / "dist" / "windows")
    parser.add_argument("--node-version", default="v24.22.1")
    parser.add_argument("--without-claude", action="store_true")
    build(parser.parse_args())


if __name__ == "__main__":
    main()
