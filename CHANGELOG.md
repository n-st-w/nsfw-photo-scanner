# Changelog

## Unreleased
- Stopped distributing a pre-built exe, and removed the GitHub Actions workflow that built and attached it. The project is now source-only, so it no longer redistributes the NudeNet model, whose training data was found to contain CSAM (C3P, Oct 2025). Build locally with `build.bat` if you want an exe.
- Added "About the detection model" to the README, disclosing the model's provenance.

## v1.0.0
- First release: GUI scanner and reviewer (`NudityScanner.exe`) plus the command-line `scan_nudity.py`.
- Blurred previews with detection boxes, keyboard review (F / K / U undo / B), live threshold.
- Move confirmed images to a folder or send them to the Recycle Bin; CSV export; auto-saved sessions.
