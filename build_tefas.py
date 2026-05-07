"""
TEFAS React uygulamasını build edip tr-data-dashboard/tefas_build/ klasörüne kopyalar.

Kullanım (tr-data-dashboard klasöründen):
  python build_tefas.py

Gereksinim: Node.js ve npm kurulu olmalı.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(HERE, "..", "tefas-flow", "frontend")
BUILD_SRC    = os.path.join(FRONTEND_DIR, "dist")
BUILD_DEST   = os.path.join(HERE, "tefas_build")


def run(cmd: str, cwd: str):
    print(f"\n>>> {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd)
    if result.returncode != 0:
        print(f"HATA: '{cmd}' başarısız oldu (exit code {result.returncode})")
        sys.exit(1)


def main():
    # 1. Frontend bağımlılıklarını yükle
    if not os.path.isdir(os.path.join(FRONTEND_DIR, "node_modules")):
        run("npm install", cwd=FRONTEND_DIR)

    # 2. Production build al
    run("npm run build", cwd=FRONTEND_DIR)

    # 3. Eski build klasörünü temizle
    if os.path.isdir(BUILD_DEST):
        print(f"\n>>> Eski build temizleniyor: {BUILD_DEST}")
        shutil.rmtree(BUILD_DEST)

    # 4. dist/ → tefas_build/ kopyala
    print(f"\n>>> Kopyalaniyor: {BUILD_SRC} -> {BUILD_DEST}")
    shutil.copytree(BUILD_SRC, BUILD_DEST)

    print(f"\nBuild tamamlandi! Klasor: {BUILD_DEST}")
    print("  Sonraki adım: tr-data-dashboard içeriğini PythonAnywhere'e yükle.")


if __name__ == "__main__":
    main()
