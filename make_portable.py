"""
Копирует uxplay.exe со ВСЕМИ нужными DLL и плагинами GStreamer в папку uxplay\\ рядом со скриптом,
чтобы airplay_mirror.py работал без установленного MSYS2 (его нужно один раз поставить для сборки).
Запуск:  python make_portable.py
"""
import os
import shutil
import subprocess
import sys

MSYS = r"C:\msys64"
BIN = os.path.join(MSYS, "ucrt64", "bin")
PLUG = os.path.join(MSYS, "ucrt64", "lib", "gstreamer-1.0")
DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uxplay")


def ldd(path):
    """Возвращает пути DLL из ucrt64\\bin, от которых зависит файл."""
    unix = "/" + path.replace(MSYS + "\\", "").replace("\\", "/")
    out = subprocess.run(
        [os.path.join(MSYS, "usr", "bin", "env.exe"), "MSYSTEM=UCRT64",
         "/usr/bin/bash", "-lc", f'ldd "{unix}"'],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    res = []
    for line in out.splitlines():
        if "/ucrt64/" in line and "=>" in line:
            u = line.split("=>")[1].split("(")[0].strip()
            res.append(os.path.join(MSYS, u.lstrip("/").replace("/", "\\")))
    return res


def main():
    exe = os.path.join(BIN, "uxplay.exe")
    if not os.path.isfile(exe):
        sys.exit("Не найден " + exe + " - сначала запустите build_uxplay.bat")
    os.makedirs(os.path.join(DEST, "lib"), exist_ok=True)
    shutil.copy2(exe, DEST)

    dlls = set(ldd(exe))
    plug_dest = os.path.join(DEST, "lib", "gstreamer-1.0")
    shutil.copytree(PLUG, plug_dest, dirs_exist_ok=True)
    for f in os.listdir(plug_dest):
        if f.lower().endswith(".dll"):
            dlls.update(ldd(os.path.join(PLUG, f)))
    for d in dlls:
        if os.path.isfile(d):
            shutil.copy2(d, DEST)
    print(f"Готово: {DEST}  ({len(dlls)} DLL). Теперь airplay_mirror.py использует её.")


if __name__ == "__main__":
    main()
