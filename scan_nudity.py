#!/usr/bin/env python
# NSFW Photo Scanner - https://github.com/n-st-w/nsfw-photo-scanner
# Copyright (C) 2026 n-st-w
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version. This program is distributed WITHOUT ANY WARRANTY; see the
# LICENSE file or <https://www.gnu.org/licenses/>.
"""
scan_nudity.py - flag photos in a folder that likely contain nudity.

Runs fully offline using NudeNet (ONNX detector). Nothing is uploaded.

Usage:
    python scan_nudity.py <folder> [options]

Examples:
    python scan_nudity.py "D:/Photos"
    python scan_nudity.py "D:/Photos" --threshold 0.5 --copy-to "D:/Flagged"
    python scan_nudity.py "D:/Photos" --include-covered --report report.csv

Output: a CSV report (default: nudity_report.csv in the current dir) listing
every image with flagged=yes/no, the highest score, and detected labels.
Optionally copies or moves flagged files to a separate folder (preserving
subfolder structure). Original files are never modified unless --move-to.
"""
import argparse
import csv
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageOps

try:
    from nudenet import NudeDetector
except ImportError:
    sys.exit("nudenet not installed. Run: pip install nudenet pillow")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".gif", ".heic"}

# NudeNet v3 labels considered explicit nudity
EXPOSED = {
    "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "ANUS_EXPOSED",
}
# Partial / suggestive (only counted with --include-covered)
COVERED = {
    "FEMALE_BREAST_COVERED",
    "FEMALE_GENITALIA_COVERED",
    "BUTTOCKS_COVERED",
    "ANUS_COVERED",
}


def iter_images(root: Path, recursive: bool):
    it = root.rglob("*") if recursive else root.glob("*")
    for p in it:
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            yield p


def load_rgb(path: Path, tmp_dir: Path) -> Path:
    """Normalise to an RGB JPEG (handles EXIF rotation, PNG alpha, GIF, HEIC)."""
    if path.suffix.lower() == ".heic":
        try:
            import pillow_heif  # optional
            pillow_heif.register_heif_opener()
        except ImportError:
            raise RuntimeError("HEIC needs: pip install pillow-heif")
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        out = tmp_dir / "current.jpg"
        im.save(out, "JPEG", quality=95)
    return out


def main():
    ap = argparse.ArgumentParser(description="Flag images that likely contain nudity (offline).")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--threshold", type=float, default=0.45,
                    help="Min detection confidence to flag (0-1, default 0.45). Lower = more sensitive.")
    ap.add_argument("--include-covered", action="store_true",
                    help="Also flag covered/partial (e.g. lingerie, swimwear).")
    ap.add_argument("--no-recursive", action="store_true", help="Don't scan subfolders.")
    ap.add_argument("--report", type=Path, default=Path("nudity_report.csv"))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--copy-to", type=Path, help="Copy flagged images here.")
    g.add_argument("--move-to", type=Path, help="Move flagged images here.")
    args = ap.parse_args()

    root = args.folder.resolve()
    if not root.is_dir():
        sys.exit(f"Not a folder: {root}")

    labels = EXPOSED | (COVERED if args.include_covered else set())
    detector = NudeDetector()
    tmp_dir = Path(args.report).resolve().parent / ".scan_tmp"
    tmp_dir.mkdir(exist_ok=True)

    images = sorted(iter_images(root, not args.no_recursive))
    print(f"Scanning {len(images)} images in {root} ...")
    flagged = errors = 0

    with open(args.report, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "flagged", "max_score", "labels", "error"])
        for i, path in enumerate(images, 1):
            try:
                dets = detector.detect(str(load_rgb(path, tmp_dir)))
                hits = [d for d in dets if d["class"] in labels and d["score"] >= args.threshold]
                is_flag = bool(hits)
                top = max((d["score"] for d in hits), default=0.0)
                lab = ";".join(sorted({d["class"] for d in hits}))
                w.writerow([str(path), "yes" if is_flag else "no", f"{top:.3f}", lab, ""])
                if is_flag:
                    flagged += 1
                    dest_root = args.copy_to or args.move_to
                    if dest_root:
                        dest = dest_root / path.relative_to(root)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        (shutil.copy2 if args.copy_to else shutil.move)(str(path), str(dest))
            except Exception as e:
                errors += 1
                w.writerow([str(path), "error", "", "", str(e)])
            if i % 25 == 0 or i == len(images):
                print(f"  {i}/{len(images)}  flagged={flagged}  errors={errors}", flush=True)

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"\nDone. {flagged} flagged, {errors} errors. Report: {Path(args.report).resolve()}")


if __name__ == "__main__":
    main()
