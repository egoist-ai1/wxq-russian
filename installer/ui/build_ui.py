# -*- coding: utf-8 -*-
"""Сборка WXQSetupUI.exe одной командой csc (WPF, без внешних зависимостей).
Шрифты (ui/fonts/*.ttf) и растры (ui/assets/*.png) вшиваются в exe как ресурсы.
Если растров нет, они готовятся из ../brand через tools/prep_assets.py.

    python build_ui.py [--out <exe>] [--test-build]

  без параметров: build/WXQSetupUI.exe
  --test-build:   /define:TESTBUILD (сценарий нажатий WXQ_UI_AUTOPILOT и снимки WXQ_UI_SHOTS; только для installer/tests, не раздавать)
Из build_installer.py вызывается функция build(out, test_build).
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CSC = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
WPF = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\WPF"
FONTS = ["Unbounded.ttf", "Onest-Cyr.ttf", "Onest-Lat.ttf"]
ASSETS = ["header.png", "mark.png"]


def build(exe, test_build=False):
    """Собирает WXQSetupUI.exe в путь exe. Исключение RuntimeError с текстом csc при ошибке."""
    os.makedirs(os.path.dirname(os.path.abspath(exe)), exist_ok=True)
    if not all(os.path.exists(os.path.join(HERE, "assets", a)) for a in ASSETS):
        subprocess.check_call([sys.executable, os.path.join(HERE, "tools", "prep_assets.py")])
    cmd = [CSC, "/nologo", "/target:winexe", "/platform:anycpu", "/optimize+", "/codepage:65001", "/out:" + exe,
           "/win32manifest:" + os.path.join(HERE, "WXQSetupUI.manifest"), "/lib:" + WPF]
    if test_build:
        cmd.append("/define:TESTBUILD")
    for r in ["PresentationCore.dll", "PresentationFramework.dll", "WindowsBase.dll", "System.Xaml.dll"]:
        p = os.path.join(WPF, r)
        cmd.append("/r:" + (p if os.path.exists(p) else r))
    cmd.append("/r:System.dll")
    cmd.append("/r:System.Web.Extensions.dll")
    for f in FONTS:
        cmd.append("/resource:%s,fonts.%s" % (os.path.join(HERE, "fonts", f), f))
    for a in ASSETS:
        cmd.append("/resource:%s,assets.%s" % (os.path.join(HERE, "assets", a), a))
    cmd.append(os.path.join(HERE, "WXQSetupUI.cs"))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="cp866", errors="replace")
    if r.returncode:
        raise RuntimeError(r.stdout + r.stderr)
    return r.stdout + r.stderr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(HERE, "build", "WXQSetupUI.exe"))
    parser.add_argument("--test-build", action="store_true")
    args = parser.parse_args()
    try:
        print(build(args.out, args.test_build))
    except RuntimeError as error:
        print(error)
        sys.exit(1)
    print("OK:", args.out)


if __name__ == "__main__":
    main()
