# Licensing and third-party components

FC27 Tactical Studio's own source code is © 2026 the project owner; see `LICENSE`.

The packaged application includes these third-party components:

| Component | License | Notes |
|---|---|---|
| Qt 6 / PySide6 | LGPL v3 | Shipped as separate DLLs in the one-folder build, so users can replace them as the LGPL requires. |
| FFmpeg (bundled in the PyAV wheels) | LGPL v2.1+ **with GPL parts** | The official PyAV wheels include x264 and x265 encoders, which are GPL. |
| PyAV | BSD 3-Clause | |
| NumPy | BSD 3-Clause | |
| sounddevice + PortAudio | MIT | Sound output. |
| ONNX Runtime | MIT | Runs the AI player-selection model on the CPU. |
| MobileSAM model (ONNX export by Acly) | MIT | Based on Meta's Segment Anything (Apache 2.0). Downloaded at build time by `scripts/fetch_models.py` and verified by SHA-256. |
| Python runtime | PSF License | |

## What this means in practice

- **Personal use, or sharing with friends and teammates:** no restrictions.
- **Publishing or selling the application:** because the bundled FFmpeg contains GPL components, the
  distributed bundle as a whole falls under the GPL. You would need to make the source code available under
  GPL-compatible terms, *or* replace FFmpeg with an LGPL-only build. The app only decodes video, so it does
  not need x264/x265. Before any commercial release, build PyAV against an LGPL FFmpeg; this is part of the
  Milestone 6 checklist.

## Trademarks

EA SPORTS FC™ is a trademark of Electronic Arts Inc. This project is not affiliated with, endorsed by, or
sponsored by Electronic Arts. It analyses video recordings made by the user and does not modify the game.
