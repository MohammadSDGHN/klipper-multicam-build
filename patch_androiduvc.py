#!/usr/bin/env python3
"""
Klipper MultiCam v3 unified-browser patch for FreeTracker/AndroidUVC.

Adds:
- auto-start USB UVC streams
- browser MJPEG for raw YUYV/NV12 USB webcams
- all Camera2 IDs exposed by Android, not only first rear/front
- H.264 preference for built-in phone camera browser playback
- a single built-in 2x2 Chrome dashboard on port 8080
- bundled mpegts.js (no second Android app)
- USB snapshot URLs and built-in lens switching from the web page
"""

from pathlib import Path
import re
import sys
import urllib.request

repo = Path(sys.argv[1] if len(sys.argv) > 1 else "AndroidUVC")
main_path = repo / "app/src/main/java/net/d7z/net/oss/uvc/MainActivity.kt"
svc_path = repo / "app/src/main/java/net/d7z/net/oss/uvc/UvcStreamingService.kt"
assets_dir = repo / "app/src/main/assets"

for p in (main_path, svc_path):
    if not p.exists():
        raise SystemExit(f"Required source file not found: {p}")

main = main_path.read_text(encoding="utf-8")
svc = svc_path.read_text(encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    out, count = re.subn(pattern, lambda _m: replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: regex anchor not found or ambiguous (count={count})")
    return out


# Bundle mpegts.js into the APK. This remains a single Android application.
assets_dir.mkdir(parents=True, exist_ok=True)
mpegts_path = assets_dir / "mpegts.min.js"
if not mpegts_path.exists() or mpegts_path.stat().st_size < 50_000:
    urls = [
        "https://cdn.jsdelivr.net/npm/mpegts.js@1.8.2/dist/mpegts.min.js",
        "https://unpkg.com/mpegts.js@1.8.2/dist/mpegts.min.js",
    ]
    last_error = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "KlipperMultiCam-GitHubActions/1.0"})
            with urllib.request.urlopen(req, timeout=45) as response:
                data = response.read()
            if len(data) < 50_000:
                raise RuntimeError(f"download too small: {len(data)} bytes")
            mpegts_path.write_bytes(data)
            print(f"Bundled mpegts.js from {url} ({len(data)} bytes)")
            break
        except Exception as exc:
            last_error = exc
    else:
        raise SystemExit(f"Could not download pinned mpegts.js 1.8.2: {last_error}")


# ---------------------------------------------------------------------------
# MainActivity.kt
# ---------------------------------------------------------------------------
main = replace_once(
    main,
    "            mainHandler.postDelayed({ refreshDeviceList() }, 300)",
    """            mainHandler.postDelayed({ refreshDeviceList() }, 300)
            mainHandler.postDelayed({ startAllStreaming() }, 1500)""",
    "MainActivity initial auto-start",
)

main = replace_once(
    main,
    "        service.onSessionCreatedListener = { session ->\n            runOnUiThread {",
    """        service.onSessionCreatedListener = { session ->
            mainHandler.postDelayed({ startAllStreaming() }, 700)
            runOnUiThread {""",
    "MainActivity session-created auto-start",
)

main = replace_once(
    main,
    '        val filterBuiltIn = prefs.getBoolean("filter_builtin_cameras", true)',
    "        val filterBuiltIn = false",
    "MainActivity built-in default",
)

main = replace_once(
    main,
    '        val previewSupported = selectedFormat == "MJPG"',
    '        val previewSupported = selectedFormat in setOf("MJPG", "YUYV", "NV12")',
    "MainActivity raw preview support",
)

main = regex_once(
    main,
    r'''    private fun builtInCameraDisplayName\(session: UvcStreamingService\.BuiltInCameraSession\): String \{
        return when \(session\.facing\) \{
            UvcStreamingService\.BuiltInFacing\.BACK -> getString\(R\.string\.back_camera\)
            UvcStreamingService\.BuiltInFacing\.FRONT -> getString\(R\.string\.front_camera\)
            UvcStreamingService\.BuiltInFacing\.UNKNOWN -> getString\(R\.string\.camera_generic\)
        \}
    \}''',
    '''    private fun builtInCameraDisplayName(session: UvcStreamingService.BuiltInCameraSession): String {
        return session.displayName
    }''',
    "MainActivity built-in lens display names",
)


# ---------------------------------------------------------------------------
# UvcStreamingService.kt imports and frame hub
# ---------------------------------------------------------------------------
svc = replace_once(
    svc,
    "import android.graphics.Rect\nimport android.graphics.SurfaceTexture",
    "import android.graphics.ImageFormat\nimport android.graphics.Rect\nimport android.graphics.SurfaceTexture\nimport android.graphics.YuvImage",
    "Service YUV imports",
)
svc = replace_once(
    svc,
    "import java.io.BufferedReader\nimport java.io.ByteArrayOutputStream",
    "import java.io.BufferedReader\nimport java.io.ByteArrayInputStream\nimport java.io.ByteArrayOutputStream",
    "Service byte stream import",
)

svc = replace_once(
    svc,
    "        fun awaitNextFrame(lastSeenId: Long, timeoutMs: Long): FrameSnapshot? {",
    """        fun latestFrame(): FrameSnapshot? = synchronized(lock) {
            if (frameId <= 0L || jpegBytes.isEmpty()) null else FrameSnapshot(frameId, jpegBytes)
        }

        fun awaitNextFrame(lastSeenId: Long, timeoutMs: Long): FrameSnapshot? {""",
    "MjpegFrameHub latest frame",
)

svc = replace_once(
    svc,
    """        var rawEncoder: UvcRawEncoder? = null
        var selectedResPos: Int = 0""",
    """        var rawEncoder: UvcRawEncoder? = null
        @Volatile
        var lastBrowserJpegAtMs: Long = 0L
        var selectedResPos: Int = 0""",
    "CameraSession browser JPEG timestamp",
)

svc = replace_once(
    svc,
    """                    val frame = session.rawFrameQueue.take()
                    val inputIndex = encoder.dequeueInputBuffer(10_000)""",
    """                    val frame = session.rawFrameQueue.take()
                    maybePublishBrowserJpeg(session, frame)
                    val inputIndex = encoder.dequeueInputBuffer(10_000)""",
    "Raw encoder browser JPEG hook",
)

raw_helpers = r'''
    private fun maybePublishBrowserJpeg(session: CameraSession, frame: UvcRawFrame) {
        val now = SystemClock.elapsedRealtime()
        val intervalMs = 1000L / BROWSER_MJPEG_MAX_FPS.coerceAtLeast(1)
        if (now - session.lastBrowserJpegAtMs < intervalMs) return

        val jpeg = rawFrameToJpeg(frame) ?: return
        session.lastBrowserJpegAtMs = now
        session.frameHub.publish(jpeg)
        onUvcFrameUpdateListener?.invoke(session.fd, jpeg)
    }

    private fun rawFrameToJpeg(frame: UvcRawFrame): ByteArray? {
        if (frame.width <= 0 || frame.height <= 0 || frame.bytes.isEmpty()) return null

        val pair = when (frame.format) {
            "YUYV" -> frame.bytes to ImageFormat.YUY2
            "NV12" -> {
                val ySize = frame.width * frame.height
                if (frame.bytes.size < ySize) return null
                val nv21 = frame.bytes.copyOf()
                var i = ySize
                while (i + 1 < nv21.size) {
                    val u = nv21[i]
                    nv21[i] = nv21[i + 1]
                    nv21[i + 1] = u
                    i += 2
                }
                nv21 to ImageFormat.NV21
            }
            else -> return null
        }

        return runCatching {
            val output = ByteArrayOutputStream()
            val image = YuvImage(pair.first, pair.second, frame.width, frame.height, null)
            if (!image.compressToJpeg(Rect(0, 0, frame.width, frame.height), BROWSER_JPEG_QUALITY, output)) {
                return@runCatching null
            }
            output.toByteArray()
        }.getOrNull()
    }

'''
svc = replace_once(
    svc,
    "    private fun waitForEncodedUvcFrame(session: CameraSession): Boolean {",
    raw_helpers + "    private fun waitForEncodedUvcFrame(session: CameraSession): Boolean {",
    "Raw JPEG helper insertion",
)

svc = replace_once(
    svc,
    '        if (format != "MJPG") session.isPreviewEnabled = false',
    '        if (format == "H264" || format == "HEVC") session.isPreviewEnabled = false',
    "Raw preview enablement",
)
svc = replace_once(
    svc,
    '                "USB Camera $format -> ${codec.label} RTSP"',
    '                "USB Camera $format -> ${codec.label} RTSP + MJPEG HTTP"',
    "Raw encoding label",
)

# Prefer H.264 for browser-oriented transports.
svc = replace_once(
    svc,
    "return listOf(BuiltInVideoCodec.HEVC, BuiltInVideoCodec.H264)",
    "return listOf(BuiltInVideoCodec.H264, BuiltInVideoCodec.HEVC)",
    "UVC encoder preference",
)
svc = svc.replace(
    "return listOf(BuiltInVideoCodec.HEVC, BuiltInVideoCodec.AV1, BuiltInVideoCodec.H264)",
    "return listOf(BuiltInVideoCodec.H264, BuiltInVideoCodec.HEVC, BuiltInVideoCodec.AV1)",
)
svc = replace_once(
    svc,
    "            listOf(true, false).flatMap { highSpeed ->",
    "            listOf(false, true).flatMap { highSpeed ->",
    "Built-in regular profile preference",
)

# Always expose built-in cameras in this dedicated appliance build.
svc = replace_once(
    svc,
    '        filterBuiltInCameras = prefs.getBoolean("filter_builtin_cameras", true)',
    '''        filterBuiltInCameras = false
        prefs.edit { putBoolean("filter_builtin_cameras", false) }''',
    "Service built-in default",
)


# ---------------------------------------------------------------------------
# Enumerate all Camera2 IDs and label rear lenses using focal length ordering.
# ---------------------------------------------------------------------------
discovery_replacement = r'''    private fun discoverBuiltInCameras() {
        if (filterBuiltInCameras) return
        val manager = getSystemService(CameraManager::class.java)
        val discovered = manager.cameraIdList
            .sortedWith(compareBy<String> { it.toIntOrNull() ?: Int.MAX_VALUE }.thenBy { it })
            .mapNotNull { cameraId ->
                val characteristics = manager.getCameraCharacteristics(cameraId)
                val facing = when (characteristics.get(CameraCharacteristics.LENS_FACING)) {
                    CameraCharacteristics.LENS_FACING_BACK -> BuiltInFacing.BACK
                    CameraCharacteristics.LENS_FACING_FRONT -> BuiltInFacing.FRONT
                    else -> BuiltInFacing.UNKNOWN
                }
                if (facing == BuiltInFacing.UNKNOWN) return@mapNotNull null
                val map = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                    ?: return@mapNotNull null
                val profiles = discoverHardwareEncodeProfiles(characteristics, map)
                if (profiles.isEmpty()) return@mapNotNull null
                BuiltInDiscovery(
                    cameraId = cameraId,
                    facing = facing,
                    profiles = profiles,
                    capabilities = builtInCapabilities(characteristics),
                    focalLengthMm = characteristics.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)
                        ?.minOrNull()
                )
            }

        val rear = discovered.filter { it.facing == BuiltInFacing.BACK }
            .sortedBy { it.focalLengthMm ?: Float.MAX_VALUE }
        val front = discovered.filter { it.facing == BuiltInFacing.FRONT }
            .sortedBy { it.focalLengthMm ?: Float.MAX_VALUE }
        val ordered = rear + front

        ordered.forEachIndexed { index, info ->
            val key = "camera$index"
            val position = if (info.facing == BuiltInFacing.BACK) rear.indexOf(info) else front.indexOf(info)
            val groupSize = if (info.facing == BuiltInFacing.BACK) rear.size else front.size
            val displayName = builtInLensDisplayName(info, position, groupSize)

            val newSession = BuiltInCameraSession(
                key = key,
                index = index,
                displayName = displayName,
                cameraId = info.cameraId,
                facing = info.facing,
                profiles = info.profiles,
                capabilities = info.capabilities
            )
            if (builtInSessions.putIfAbsent(key, newSession) == null) {
                restoreBuiltInSettings(newSession)
            }
        }
        log("Built-in cameras discovered: ${builtInSessions.values.sortedBy { it.index }.joinToString { \"${it.key}=${it.displayName}[id=${it.cameraId}]\" }}")
    }

    private fun builtInLensDisplayName(info: BuiltInDiscovery, position: Int, groupSize: Int): String {
        val focal = info.focalLengthMm?.let { String.format(Locale.US, " %.2fmm", it) }.orEmpty()
        return when (info.facing) {
            BuiltInFacing.BACK -> {
                val label = when {
                    groupSize >= 3 && position == 0 -> "Rear Ultra-wide"
                    groupSize >= 3 && position == groupSize - 1 -> "Rear Tele"
                    groupSize >= 3 -> "Rear Wide"
                    groupSize == 2 -> "Rear Lens ${position + 1}"
                    else -> "Rear Camera"
                }
                "$label$focal"
            }
            BuiltInFacing.FRONT -> if (groupSize > 1) "Front Camera ${position + 1}$focal" else "Front Camera$focal"
            BuiltInFacing.UNKNOWN -> "Camera ${position + 1}$focal"
        }
    }

    private data class BuiltInDiscovery(
        val cameraId: String,
        val facing: BuiltInFacing,
        val profiles: List<BuiltInProfile>,
        val capabilities: BuiltInCapabilities,
        val focalLengthMm: Float?
    )
'''
svc = regex_once(
    svc,
    r'''    private fun discoverBuiltInCameras\(\) \{.*?
    private data class BuiltInDiscovery\(
        val cameraId: String,
        val facing: BuiltInFacing,
        val profiles: List<BuiltInProfile>,
        val capabilities: BuiltInCapabilities
    \)
''',
    discovery_replacement,
    "All Camera2 lens discovery",
)

svc = regex_once(
    svc,
    r'''    private fun builtInHttpName\(session: BuiltInCameraSession\): String \{
        return when \(session\.facing\) \{
            BuiltInFacing\.BACK -> "Back Camera"
            BuiltInFacing\.FRONT -> "Front Camera"
            BuiltInFacing\.UNKNOWN -> session\.displayName
        \}
    \}''',
    '''    private fun builtInHttpName(session: BuiltInCameraSession): String = session.displayName''',
    "Built-in HTTP display name",
)


# ---------------------------------------------------------------------------
# HTTP routes and helpers.
# ---------------------------------------------------------------------------
svc = replace_once(
    svc,
    '''                uri == "/" || uri == "/index.html" -> html(rootPage())
                uri == "/uvc" -> html(uvcListPage())''',
    '''                uri == "/" || uri == "/index.html" -> html(rootPage())
                uri == "/static/mpegts.min.js" -> javascriptAsset("mpegts.min.js")
                uri == "/api/builtin/select" -> selectBuiltInFromHttp(session.parameters["key"]?.firstOrNull())
                uri == "/api/builtin/stop" -> stopBuiltInFromHttp()
                uri.startsWith("/snapshot/uvc/") && uri.endsWith(".jpg") -> serveUvcSnapshot(
                    uri.substringAfter("/snapshot/uvc/").removeSuffix(".jpg").toIntOrNull()
                )
                uri == "/uvc" -> html(uvcListPage())''',
    "HTTP dashboard API routes",
)

http_helpers = r'''
    private fun javascriptAsset(name: String): NanoHTTPD.Response {
        return try {
            val text = assets.open(name).bufferedReader(Charsets.UTF_8).use { it.readText() }
            NanoHTTPD.newFixedLengthResponse(NanoHTTPD.Response.Status.OK, "application/javascript; charset=utf-8", text)
        } catch (e: Exception) {
            NanoHTTPD.newFixedLengthResponse(
                NanoHTTPD.Response.Status.NOT_FOUND,
                NanoHTTPD.MIME_PLAINTEXT,
                "Asset unavailable: ${e.message}"
            )
        }
    }

    private fun selectBuiltInFromHttp(key: String?): NanoHTTPD.Response {
        val target = key?.let { builtInSessions[it] } ?: return NanoHTTPD.newFixedLengthResponse(
            NanoHTTPD.Response.Status.NOT_FOUND,
            NanoHTTPD.MIME_PLAINTEXT,
            "Built-in camera not found"
        )
        Thread {
            builtInSessions.values
                .filter { it.key != target.key && it.state != SessionState.IDLE }
                .forEach { stopBuiltInCamera(it.key) }
            if (!target.isStreaming) startBuiltInCamera(target.key)
        }.start()
        return NanoHTTPD.newFixedLengthResponse(
            NanoHTTPD.Response.Status.OK,
            NanoHTTPD.MIME_PLAINTEXT,
            "Starting ${target.displayName}"
        )
    }

    private fun stopBuiltInFromHttp(): NanoHTTPD.Response {
        Thread {
            builtInSessions.values
                .filter { it.state != SessionState.IDLE }
                .forEach { stopBuiltInCamera(it.key) }
        }.start()
        return NanoHTTPD.newFixedLengthResponse(
            NanoHTTPD.Response.Status.OK,
            NanoHTTPD.MIME_PLAINTEXT,
            "Stopping built-in camera"
        )
    }

    private fun serveUvcSnapshot(index: Int?): NanoHTTPD.Response {
        val camera = index?.let { sessionsByIndex[it] } ?: return NanoHTTPD.newFixedLengthResponse(
            NanoHTTPD.Response.Status.NOT_FOUND,
            NanoHTTPD.MIME_PLAINTEXT,
            "USB camera not found"
        )
        val frame = camera.frameHub.latestFrame() ?: return NanoHTTPD.newFixedLengthResponse(
            NanoHTTPD.Response.Status.CONFLICT,
            NanoHTTPD.MIME_PLAINTEXT,
            "No JPEG frame is available yet"
        )
        return NanoHTTPD.newFixedLengthResponse(
            NanoHTTPD.Response.Status.OK,
            "image/jpeg",
            ByteArrayInputStream(frame.bytes),
            frame.bytes.size.toLong()
        ).apply {
            addHeader("Cache-Control", "no-store")
            addHeader("Access-Control-Allow-Origin", "*")
        }
    }

'''
svc = replace_once(
    svc,
    "    private fun serveUvcStream(index: Int?): NanoHTTPD.Response {",
    http_helpers + "    private fun serveUvcStream(index: Int?): NanoHTTPD.Response {",
    "HTTP helper insertion",
)

svc = replace_once(
    svc,
    '''        if (session.streamFormat != "MJPG") return NanoHTTPD.newFixedLengthResponse(
            NanoHTTPD.Response.Status.CONFLICT,
            NanoHTTPD.MIME_PLAINTEXT,
            "Current USB camera stream is not MJPEG"
        )''',
    '''        if (session.streamFormat !in setOf("MJPG", "YUYV", "NV12")) return NanoHTTPD.newFixedLengthResponse(
            NanoHTTPD.Response.Status.CONFLICT,
            NanoHTTPD.MIME_PLAINTEXT,
            "Current USB camera format has no browser MJPEG output"
        )''',
    "serveUvcStream raw formats",
)

svc = replace_once(
    svc,
    '            val links = if (format == "MJPG") {',
    '            val links = if (format in setOf("MJPG", "YUYV", "NV12")) {',
    "UVC list browser links",
)
svc = replace_once(
    svc,
    '        if (currentUvcDisplayFormat(session) != "MJPG") {',
    '        if (currentUvcDisplayFormat(session) !in setOf("MJPG", "YUYV", "NV12")) {',
    "UVC preview raw formats",
)


# ---------------------------------------------------------------------------
# Unified dashboard root page.
# ---------------------------------------------------------------------------
dashboard = r'''    private fun rootPage(): String {
        fun esc(value: String): String = value
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
            .replace("'", "&#39;")

        val usbSessions = sessionsByIndex.keys().toList().sorted().mapNotNull { sessionsByIndex[it] }
        val builtIns = builtInSessions.values.sortedBy { it.index }
        val activeBuiltIn = builtIns.firstOrNull { it.isStreaming }

        val sidebarUsb = usbSessions.joinToString("") { camera ->
            val live = if (camera.isStreaming) "LIVE" else "IDLE"
            val format = currentUvcDisplayFormat(camera)
            val res = camera.supportedFormats.split(";").filter { it.isNotBlank() }
                .getOrNull(camera.selectedResPos)?.substringBefore(':')?.substringAfter('|') ?: ""
            """
            <button class='camera-row' onclick=\"openUrl('/uvc/camera/${camera.index}')\">
              <span><b>USB ${camera.index}</b><small>${esc(camera.device.productName ?: camera.device.deviceName)}</small></span>
              <span class='right'><i class='${if (camera.isStreaming) "dot live" else "dot"}'></i>$live<small>${esc(format)} ${esc(res)}</small></span>
            </button>
            """.trimIndent()
        }

        val sidebarBuiltIn = builtIns.joinToString("") { camera ->
            val live = if (camera.isStreaming) "LIVE" else "READY"
            val profile = camera.actualProfile ?: camera.selectedOrFallbackProfile()
            val res = profile?.let { "${it.size.width}x${it.size.height} @ ${builtInFpsLabel(camera)}" }.orEmpty()
            """
            <button class='camera-row' onclick=\"selectPhoneLens('${esc(camera.key)}')\">
              <span><b>${esc(camera.displayName)}</b><small>Phone Camera2 · id ${esc(camera.cameraId)}</small></span>
              <span class='right'><i class='${if (camera.isStreaming) "dot live" else "dot"}'></i>$live<small>${esc(res)}</small></span>
            </button>
            """.trimIndent()
        }

        val tiles = mutableListOf<String>()
        usbSessions.filter { it.isStreaming }.take(4).forEach { camera ->
            val format = currentUvcDisplayFormat(camera)
            val canMjpeg = format in setOf("MJPG", "YUYV", "NV12")
            val media = if (canMjpeg) {
                "<img crossorigin='anonymous' src='/uvc/camera/${camera.index}.mjpg' alt='USB ${camera.index}'>"
            } else {
                "<div class='no-video'>${esc(format)} is RTSP-only</div>"
            }
            val direct = if (canMjpeg) "/uvc/camera/${camera.index}.mjpg" else ""
            tiles += """
              <section class='tile' id='tile-usb-${camera.index}'>
                <header><b>USB ${camera.index}</b><span>${esc(camera.device.productName ?: "")}</span></header>
                <div class='media'>$media</div>
                <footer>
                  <span>${esc(format)}</span>
                  <button onclick=\"takePhoto('tile-usb-${camera.index}','usb-${camera.index}')\">Photo</button>
                  ${if (direct.isNotBlank()) "<button onclick=\"openUrl('$direct')\">Open</button>" else ""}
                </footer>
              </section>
            """.trimIndent()
        }

        if (activeBuiltIn != null && tiles.size < 4) {
            val profile = activeBuiltIn.actualProfile ?: activeBuiltIn.selectedOrFallbackProfile()
            val streamUrl = "/builtin/${activeBuiltIn.key}.${profile?.streamExtension ?: "ts"}"
            tiles += """
              <section class='tile' id='tile-phone'>
                <header><b>${esc(activeBuiltIn.displayName)}</b><span>Phone camera</span></header>
                <div class='media'><video id='phone-video' muted autoplay playsinline controls></video></div>
                <footer>
                  <span>${esc(profile?.videoCodec?.label ?: activeBuiltIn.actualEncoding)}</span>
                  <button onclick=\"takePhoto('tile-phone','phone')\">Photo</button>
                  <button onclick=\"openUrl('$streamUrl')\">Open TS</button>
                </footer>
              </section>
            """.trimIndent()
        }

        while (tiles.size < 4) {
            tiles += """
              <section class='tile empty'>
                <div class='no-video'>Connect/start another camera</div>
              </section>
            """.trimIndent()
        }

        val phonePlayerScript = if (activeBuiltIn != null) {
            val profile = activeBuiltIn.actualProfile ?: activeBuiltIn.selectedOrFallbackProfile()
            val streamUrl = "/builtin/${activeBuiltIn.key}.${profile?.streamExtension ?: "ts"}"
            "startPhonePlayer('$streamUrl');"
        } else ""

        return """
<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Klipper MultiCam</title>
<script src='/static/mpegts.min.js'></script>
<style>
:root{color-scheme:dark;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
*{box-sizing:border-box}
body{margin:0;background:#0b1015;color:#eef4f8;height:100vh;overflow:hidden}
.layout{display:grid;grid-template-columns:280px 1fr;height:100vh}
.sidebar{background:#111922;border-right:1px solid #263342;padding:14px;overflow:auto}
.brand{font-weight:800;font-size:20px;margin:4px 4px 14px}.brand small{display:block;font-size:11px;font-weight:500;color:#7f93a8;margin-top:3px}
.group{font-size:11px;color:#7f93a8;font-weight:800;letter-spacing:.12em;margin:16px 4px 7px}
.camera-row{width:100%;border:1px solid #263342;background:#151f29;color:#eef4f8;padding:10px;border-radius:10px;margin:5px 0;display:flex;justify-content:space-between;text-align:left;gap:8px;cursor:pointer}
.camera-row:hover{background:#1d2a36}.camera-row span{display:flex;flex-direction:column;gap:2px}.camera-row .right{text-align:right;align-items:flex-end}
.camera-row small{font-size:10px;color:#91a4b8}.dot{width:7px;height:7px;border-radius:50%;background:#58697b;display:inline-block}.dot.live{background:#29d17d;box-shadow:0 0 8px #29d17d88}
.main{display:grid;grid-template-rows:auto 1fr;min-width:0}.toolbar{display:flex;gap:10px;align-items:center;padding:12px 16px;background:#111922;border-bottom:1px solid #263342}
.toolbar h1{font-size:15px;margin:0 auto 0 0}.toolbar button,.tile button{background:#1d2a36;border:1px solid #34465a;color:white;border-radius:8px;padding:7px 10px;cursor:pointer}
.grid{padding:12px;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:10px;min-height:0}
.tile{background:#111922;border:1px solid #263342;border-radius:12px;display:grid;grid-template-rows:auto 1fr auto;overflow:hidden;min-height:0}
.tile header,.tile footer{display:flex;align-items:center;gap:8px;padding:8px 10px;background:#131d27;font-size:12px}.tile header span{color:#8da2b7;margin-left:auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tile footer span{margin-right:auto;color:#8da2b7}
.media{min-height:0;background:black;display:flex;align-items:center;justify-content:center;overflow:hidden}.media img,.media video{width:100%;height:100%;object-fit:contain;background:black}
.no-video{color:#72869a;font-size:13px;text-align:center;padding:20px}.empty{opacity:.55}
@media(max-width:850px){body{overflow:auto}.layout{grid-template-columns:1fr;height:auto}.sidebar{border-right:0;border-bottom:1px solid #263342}.grid{grid-template-columns:1fr;grid-template-rows:repeat(4,320px)}}
</style>
</head>
<body>
<div class='layout'>
  <aside class='sidebar'>
    <div class='brand'>Klipper MultiCam<small>One app · USB + phone lenses · Chrome</small></div>
    <div class='group'>USB CAMERAS</div>
    ${sidebarUsb.ifBlank { "<div class='no-video'>No USB camera sessions</div>" }}
    <div class='group'>PHONE LENSES</div>
    ${sidebarBuiltIn.ifBlank { "<div class='no-video'>No Camera2 lenses exposed</div>" }}
  </aside>
  <main class='main'>
    <div class='toolbar'>
      <h1>Live cameras</h1>
      <button onclick='location.reload()'>Refresh</button>
      <button onclick='stopPhone()'>Stop phone camera</button>
      <button onclick=\"openUrl('/uvc')\">USB details</button>
      <button onclick=\"openUrl('/builtin')\">Phone details</button>
    </div>
    <div class='grid'>${tiles.joinToString("")}</div>
  </main>
</div>
<script>
var phonePlayer=null;
function openUrl(u){window.open(u,'_blank')}
function selectPhoneLens(key){fetch('/api/builtin/select?key='+encodeURIComponent(key),{cache:'no-store'}).then(function(){setTimeout(function(){location.reload()},2200)}).catch(function(e){alert(e)})}
function stopPhone(){fetch('/api/builtin/stop',{cache:'no-store'}).then(function(){setTimeout(function(){location.reload()},800)})}
function startPhonePlayer(url){
  var v=document.getElementById('phone-video'); if(!v){return}
  if(window.mpegts && mpegts.getFeatureList().mseLivePlayback){
    phonePlayer=mpegts.createPlayer({type:'mse',isLive:true,url:url},{enableWorker:true,enableWorkerForMSE:true,liveBufferLatencyChasing:true,liveBufferLatencyMaxLatency:2.0,liveBufferLatencyMinRemain:0.3});
    phonePlayer.attachMediaElement(v); phonePlayer.load(); var p=phonePlayer.play(); if(p&&p.catch){p.catch(function(){})}
  }else{v.outerHTML="<div class='no-video'>This browser does not expose MSE live playback.</div>"}
}
function takePhoto(tileId,name){
  var tile=document.getElementById(tileId); if(!tile){return}
  var media=tile.querySelector('video,img'); if(!media){return}
  var w=media.videoWidth||media.naturalWidth||media.clientWidth; var h=media.videoHeight||media.naturalHeight||media.clientHeight;
  if(!w||!h){alert('No frame available yet');return}
  var c=document.createElement('canvas'); c.width=w; c.height=h; c.getContext('2d').drawImage(media,0,0,w,h);
  c.toBlob(function(blob){if(!blob){return}var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name+'-'+new Date().toISOString().replace(/[:.]/g,'-')+'.jpg';a.click();setTimeout(function(){URL.revokeObjectURL(a.href)},1500)},'image/jpeg',0.92)
}
$phonePlayerScript
</script>
</body>
</html>
        """.trimIndent()
    }
'''
svc = regex_once(
    svc,
    r'''    private fun rootPage\(\): String = page\(
        "Android Camera",
        "<div class='card'><a href='/uvc'>USB cameras</a></div><div class='card'><a href='/builtin'>Built-in cameras</a></div>"
    \)
''',
    dashboard,
    "Unified dashboard root page",
)

# Constants. Use a current stable service constant as the preferred anchor.
const_anchor = "        private const val FIRST_FRAME_TIMEOUT_MS = 1500L"
if const_anchor in svc:
    svc = replace_once(
        svc,
        const_anchor,
        const_anchor + "\n        private const val BROWSER_MJPEG_MAX_FPS = 10\n        private const val BROWSER_JPEG_QUALITY = 78",
        "Browser JPEG constants",
    )
else:
    # Fallback to companion-object insertion if upstream moves that constant.
    match = re.search(r'(    companion object \{.*?)(\n    \}\n\n    private fun)', svc, flags=re.S)
    if not match:
        raise SystemExit("Could not locate service companion object for browser JPEG constants")
    block = match.group(1)
    if "BROWSER_MJPEG_MAX_FPS" not in block:
        block += "\n        private const val BROWSER_MJPEG_MAX_FPS = 10\n        private const val BROWSER_JPEG_QUALITY = 78"
    svc = svc[:match.start()] + block + match.group(2) + svc[match.end():]

main_path.write_text(main, encoding="utf-8")
svc_path.write_text(svc, encoding="utf-8")

print("Patched:", main_path)
print("Patched:", svc_path)
print("Bundled:", mpegts_path)
print("Klipper MultiCam v3 unified dashboard patch applied successfully.")
