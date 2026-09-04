"""
将 RapidDoc 输出目录打包为 MinerU 同构的 ZIP 文件。

MinerU ZIP 结构：
  {filename}.md
  images/
    *.png
    *.jpg

RapidDoc 输出结构：
  {filename}/
    {auto|office}/
      {filename}.md
      images/
        *.png

用法：
  python3 package_rapiddoc_output.py <input_dir> [output_dir]
"""

import os
import re
import sys
import zipfile
from pathlib import Path


def find_md_and_images(base_dir: Path, filename: str) -> tuple[Path | None, Path | None]:
    """在 auto/ 或 office/ 子目录中找到 MD 和 images 目录。"""
    for sub in ["auto", "office"]:
        sub_dir = base_dir / filename / sub
        if not sub_dir.is_dir():
            continue
        md_path = sub_dir / f"{filename}.md"
        images_dir = sub_dir / "images"
        if md_path.exists():
            return md_path, images_dir if images_dir.is_dir() else None
    return None, None


def package_one(file_dir: Path, output_dir: Path) -> Path | None:
    """将单个文件的输出打包为 ZIP，返回 ZIP 路径。"""
    filename = file_dir.name
    md_path, images_dir = find_md_and_images(file_dir.parent, filename)

    if md_path is None:
        print(f"  [SKIP] {filename}: 未找到 MD 文件")
        return None

    md_content = md_path.read_text(encoding="utf-8")
    zip_path = output_dir / f"{filename}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 写入 MD（根目录）
        zf.writestr(f"{filename}.md", md_content)

        # 写入 images/
        if images_dir and images_dir.is_dir():
            for img in sorted(images_dir.iterdir()):
                if img.is_file() and img.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                    zf.write(img, f"images/{img.name}")

    size_kb = zip_path.stat().st_size / 1024
    print(f"  [OK] {filename}.zip ({size_kb:.1f} KB)")
    return zip_path


def main():
    input_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/Users/ming.lv/dev/tmp_rapiddoc_output")
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else input_dir / "zips"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 找到所有子目录（每个子目录对应一个文件）
    file_dirs = sorted([
        d for d in input_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ])

    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")
    print(f"找到 {len(file_dirs)} 个文件待打包\n")

    success = 0
    for file_dir in file_dirs:
        result = package_one(file_dir, output_dir)
        if result:
            success += 1

    print(f"\n完成: {success}/{len(file_dirs)} 个 ZIP 已生成")


if __name__ == "__main__":
    main()
