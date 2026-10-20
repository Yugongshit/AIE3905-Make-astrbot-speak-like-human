"""Build a distributable plugin ZIP without local data, credentials or test output."""

import argparse
import hashlib
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    root = Path(__file__).resolve().parents[1]
    version = re.search(r"(?m)^version:\s*v?([\d.]+)", (root / "metadata.yaml").read_text())[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=root / "dist" / f"astrbot_plugin_human_style-{version}.zip"
    )
    output = parser.parse_args().output
    files = [root / name for name in ("main.py", "metadata.yaml", "_conf_schema.json", "README.md")]
    for folder, suffixes in {
        "human_style": {".py"},
        "views": {".html", ".css", ".js"},
        "i18n": {".json"},
        "examples": {".json", ".txt", ".md"},
        "docs": {".md", ".png"},
    }.items():
        files.extend(
            path
            for path in (root / folder).rglob("*")
            if path.is_file() and path.suffix in suffixes
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, Path("astrbot_plugin_human_style") / path.relative_to(root))
    with ZipFile(output) as archive:
        assert archive.testzip() is None
    print(f"Built {output} ({len(files)} files)")
    print(f"SHA256 {hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
