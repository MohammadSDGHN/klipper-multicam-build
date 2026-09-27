#!/usr/bin/env python3
from pathlib import Path
import sys

repo = Path(sys.argv[1] if len(sys.argv) > 1 else "AndroidUVC")
main_path = repo / "app/src/main/java/net/d7z/net/oss/uvc/MainActivity.kt"

if not main_path.exists():
    raise SystemExit(f"MainActivity.kt not found: {main_path}")

s = main_path.read_text(encoding="utf-8")

anchor1 = "mainHandler.postDelayed({ refreshDeviceList() }, 300)"
if anchor1 not in s:
    raise SystemExit("Patch anchor 1 not found; upstream MainActivity changed.")
s = s.replace(
    anchor1,
    anchor1 + "\\n            mainHandler.postDelayed({ autoStartReadyUvcSessions() }, 1300)",
    1,
)

anchor2 = "service.onSessionCreatedListener = { session ->"
if anchor2 not in s:
    raise SystemExit("Patch anchor 2 not found; upstream MainActivity changed.")
s = s.replace(
    anchor2,
    anchor2 + "\\n            mainHandler.postDelayed({ autoStartUvcSession(session.index) }, 350)",
    1,
)

anchor3 = "    private fun startAllStreaming() {"
if anchor3 not in s:
    raise SystemExit("Patch anchor 3 not found; upstream MainActivity changed.")

methods = '''
    private fun autoStartReadyUvcSessions() {
        val service = uvcService ?: return
        service.sessionsByIndex.keys.toList().sorted().take(MAX_AUTO_UVC_CAMERAS).forEach { index ->
            autoStartUvcSession(index)
        }
    }

    private fun autoStartUvcSession(index: Int) {
        if (index !in 0 until MAX_AUTO_UVC_CAMERAS) return
        val service = uvcService ?: return
        val session = service.sessionsByIndex[index] ?: return
        if (session.isStreaming || session.state != UvcStreamingService.SessionState.IDLE) return

        val resolutionMap = parseResolutionMap(session.supportedFormats)
        val preferred = resolutionMap.entries.firstOrNull {
            it.key.startsWith("MJPG ") && it.key.contains("640x480")
        } ?: resolutionMap.entries.firstOrNull {
            it.key.startsWith("MJPG ")
        }

        if (preferred == null) {
            logToUI("AUTO: Cam ${session.index} has no MJPEG mode; skipped.")
            return
        }

        val size = parseUvcResolution(preferred.key) ?: (640 to 480)
        val fpsValues = preferred.value.mapNotNull { it.toIntOrNull() }.filter { it > 0 }
        val fps = fpsValues.filter { it <= AUTO_TARGET_FPS }.maxOrNull()
            ?: fpsValues.minOrNull()
            ?: AUTO_TARGET_FPS

        logToUI("AUTO: Starting Cam ${session.index}: MJPG ${size.first}x${size.second}@$fps")
        service.startStreaming(session.fd, size.first, size.second, fps, "MJPG") {
            runOnUiThread { updateUiSnapshot() }
        }
    }

'''
s = s.replace(anchor3, methods + anchor3, 1)

anchor4 = "        private const val LOCAL_IP_CACHE_TTL_MS = 30_000L"
if anchor4 not in s:
    raise SystemExit("Patch anchor 4 not found; upstream MainActivity changed.")
s = s.replace(
    anchor4,
    anchor4
    + "\\n        private const val MAX_AUTO_UVC_CAMERAS = 4"
    + "\\n        private const val AUTO_TARGET_FPS = 15",
    1,
)

main_path.write_text(s, encoding="utf-8")
print("Patched:", main_path)
print("Behavior: app auto-starts up to four UVC MJPEG streams.")
