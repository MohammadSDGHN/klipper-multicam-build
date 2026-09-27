#!/usr/bin/env python3
from pathlib import Path
import sys

repo = Path(sys.argv[1] if len(sys.argv) > 1 else "AndroidUVC")
main_path = repo / "app/src/main/java/net/d7z/net/oss/uvc/MainActivity.kt"

if not main_path.exists():
    raise SystemExit(f"MainActivity.kt not found: {main_path}")

s = main_path.read_text(encoding="utf-8")

# Keep this patch deliberately small and use AndroidUVC's OWN existing
# startAllStreaming() implementation instead of duplicating its internal API.
# That makes the patch much less sensitive to private/internal service types.

anchor1 = "mainHandler.postDelayed({ refreshDeviceList() }, 300)"
insert1 = (
    anchor1
    + "\n            // Klipper MultiCam: automatically start all ready UVC sessions."
    + "\n            mainHandler.postDelayed({ startAllStreaming() }, 1500)"
)
if anchor1 not in s:
    raise SystemExit("Patch anchor 1 not found; upstream MainActivity changed.")
s = s.replace(anchor1, insert1, 1)

anchor2 = "service.onSessionCreatedListener = { session ->"
insert2 = (
    anchor2
    + "\n            // Klipper MultiCam: a newly-created USB session may have arrived"
    + "\n            // after the first auto-start pass, so ask the app's own"
    + "\n            // startAllStreaming() routine to start all ready sessions again."
    + "\n            mainHandler.postDelayed({ startAllStreaming() }, 700)"
)
if anchor2 not in s:
    raise SystemExit("Patch anchor 2 not found; upstream MainActivity changed.")
s = s.replace(anchor2, insert2, 1)

# Verify the existing routine we depend on really exists.
anchor3 = "private fun startAllStreaming()"
if anchor3 not in s:
    # Some formatting may include spaces before the brace.
    if "private fun startAllStreaming()" not in s:
        raise SystemExit("Existing startAllStreaming() routine not found.")

main_path.write_text(s, encoding="utf-8")

print("Patched:", main_path)
print("Klipper MultiCam patch v2:")
print(" - reuses AndroidUVC's existing startAllStreaming()")
print(" - auto-starts once after initial device refresh")
print(" - retries after each newly-created UVC session")
print(" - adds no new service/session API references")
