# Third-party notices

NSFW Photo Scanner is distributed under AGPL-3.0 (see `LICENSE`). The Windows executable bundles the components below, each under its own licence. Full licence texts ship with each package and are linked here.

| Component | Use | Licence |
|---|---|---|
| [NudeNet](https://github.com/notAI-tech/NudeNet) (incl. `320n.onnx` model) | Nudity detection | AGPL-3.0 per the GitHub repository (the PyPI package metadata says MIT; this project follows the stricter AGPL-3.0). **Provenance warning:** the NudeNet training dataset was found by C3P (Oct 2025) to contain child sexual abuse material. See "About the detection model" in README.md. No dataset images are bundled. |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | Model inference | MIT |
| [OpenCV](https://github.com/opencv/opencv-python) (headless) | Image preprocessing (NudeNet dependency) | Apache-2.0 |
| [NumPy](https://numpy.org/) | Arrays | BSD-3-Clause (plus bundled MIT/Zlib/0BSD/CC0 parts) |
| [Pillow](https://python-pillow.org/) | Image loading, preview | MIT-CMU (HPND) |
| [pillow-heif](https://github.com/bigcat88/pillow_heif) | HEIC support | BSD-3-Clause. Bundles libheif (LGPL-3.0), libde265 (LGPL-3.0) and x265 (GPL-2.0-or-later) |
| [Send2Trash](https://github.com/arsenetar/send2trash) | Recycle Bin support | BSD-3-Clause |
| [Python](https://www.python.org/) / Tkinter / Tcl/Tk | Runtime and GUI | PSF License / Tcl/Tk License (BSD-style) |
| [PyInstaller](https://pyinstaller.org/) bootloader | Packaging the exe | GPL-2.0-or-later with bootloader exception |

The LGPL/GPL components inside pillow-heif are used unmodified. Their source is available from the upstream projects linked above, and the complete source for this application is in this repository.
