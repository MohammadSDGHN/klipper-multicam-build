#!/usr/bin/env python3
"""
Klipper MultiCam v5 active-web-control patch for FreeTracker/AndroidUVC.

Adds:
- auto-start USB UVC streams
- browser MJPEG for raw YUYV/NV12 USB webcams
- all Camera2 IDs exposed by Android, not only first rear/front
- H.264 preference for built-in phone camera browser playback
- an active 2x2 Chrome dashboard on port 8080 with stream controls
- browser fallback when a raw YUYV/NV12 hardware encoder cannot start
- Camera2 resolution/FPS/zoom/focus/exposure/AWB/torch controls
- bundled mpegts.js (no second Android app)
- USB snapshots and built-in camera switching from the web page
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
svc_original_line_count = len(svc.splitlines())


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
    '        session.isPreviewEnabled = format in setOf("MJPG", "YUYV", "NV12")',
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
# v5 raw USB fallback. If MediaCodec cannot be allocated for another YUYV/NV12
# webcam, keep the USB capture alive and serve software MJPEG to Chrome.
# ---------------------------------------------------------------------------
svc = replace_once(
    svc,
    """        @Volatile
        var lastBrowserJpegAtMs: Long = 0L
        var selectedResPos: Int = 0""",
    """        @Volatile
        var lastBrowserJpegAtMs: Long = 0L
        @Volatile
        var browserRawRunning: Boolean = false
        var browserRawThread: Thread? = null
        var selectedResPos: Int = 0""",
    "Browser-only raw worker fields",
)

raw_worker_helper = r'''
    private fun startRawBrowserWorker(session: CameraSession) {
        if (session.browserRawRunning) return
        session.browserRawRunning = true
        session.browserRawThread = Thread {
            while (session.browserRawRunning && !Thread.currentThread().isInterrupted) {
                try {
                    val frame = session.rawFrameQueue.take()
                    maybePublishBrowserJpeg(session, frame)
                } catch (_: InterruptedException) {
                    break
                } catch (e: Exception) {
                    log("Cam ${session.index} browser JPEG worker: ${e.message}")
                }
            }
        }.apply {
            name = "uvc-browser-jpeg-${session.index}"
            isDaemon = true
            start()
        }
    }

'''
svc = replace_once(
    svc,
    "    private fun waitForEncodedUvcFrame(session: CameraSession): Boolean {",
    raw_worker_helper + "    private fun waitForEncodedUvcFrame(session: CameraSession): Boolean {",
    "Browser-only raw worker helper",
)

svc = replace_once(
    svc,
    "    private fun stopUvcTransport(session: CameraSession) {",
    """    private fun stopUvcTransport(session: CameraSession) {
        session.browserRawRunning = false
        session.browserRawThread?.interrupt()
        session.browserRawThread = null""",
    "Stop browser-only raw worker",
)

raw_block_old = '''            "YUYV", "NV12" -> {
                val codec = preferredUvcEncodeCodec(width, height, fps) ?: return false
                val encoder = UvcRawEncoder(session, width, height, fps, codec)
                if (!encoder.start()) return false
                session.rawEncoder = encoder
                session.actualVideoCodec = codec
                "USB Camera $format -> ${codec.label} RTSP + MJPEG HTTP"
            }'''
raw_block_new = '''            "YUYV", "NV12" -> {
                val codec = preferredUvcEncodeCodec(width, height, fps)
                if (codec != null) {
                    val encoder = UvcRawEncoder(session, width, height, fps, codec)
                    if (encoder.start()) {
                        session.rawEncoder = encoder
                        session.actualVideoCodec = codec
                        "USB Camera $format -> ${codec.label} RTSP + MJPEG HTTP"
                    } else {
                        session.actualVideoCodec = null
                        startRawBrowserWorker(session)
                        log("Cam ${session.index}: MediaCodec unavailable; browser-only MJPEG fallback.")
                        "USB Camera $format -> MJPEG HTTP (browser-only)"
                    }
                } else {
                    session.actualVideoCodec = null
                    startRawBrowserWorker(session)
                    log("Cam ${session.index}: no compatible encoder; browser-only MJPEG fallback.")
                    "USB Camera $format -> MJPEG HTTP (browser-only)"
                }
            }'''
svc = replace_once(svc, raw_block_old, raw_block_new, "Raw USB browser fallback branch")


# ---------------------------------------------------------------------------
# Enumerate every openable Camera2 ID WITHOUT replacing AndroidUVC's original
# discoverBuiltInCameras(). v3 replaced a huge source range and accidentally
# deleted many helper/server/native methods. v4 leaves upstream code intact,
# then rebuilds only the builtInSessions map immediately after normal discovery.
# ---------------------------------------------------------------------------
extra_discovery = r'''
    private data class KlipperBuiltInDiscovery(
        val cameraId: String,
        val facing: BuiltInFacing,
        val profiles: List<BuiltInProfile>,
        val capabilities: BuiltInCapabilities,
        val focalLengthMm: Float?
    )

    private fun discoverKlipperBuiltInCameras() {
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

                KlipperBuiltInDiscovery(
                    cameraId = cameraId,
                    facing = facing,
                    profiles = profiles,
                    capabilities = builtInCapabilities(characteristics),
                    focalLengthMm = characteristics
                        .get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)
                        ?.minOrNull()
                )
            }

        val rear = discovered
            .filter { it.facing == BuiltInFacing.BACK }
            .sortedBy { it.focalLengthMm ?: Float.MAX_VALUE }
        val front = discovered
            .filter { it.facing == BuiltInFacing.FRONT }
            .sortedBy { it.focalLengthMm ?: Float.MAX_VALUE }
        val ordered = rear + front
        if (ordered.isEmpty()) return

        builtInSessions.clear()

        ordered.forEachIndexed { index, info ->
            val position = if (info.facing == BuiltInFacing.BACK) {
                rear.indexOf(info)
            } else {
                front.indexOf(info)
            }
            val groupSize = if (info.facing == BuiltInFacing.BACK) rear.size else front.size
            val focal = info.focalLengthMm?.let { " ${it}mm" }.orEmpty()
            val label = when (info.facing) {
                BuiltInFacing.BACK -> when {
                    groupSize >= 3 && position == 0 -> "Rear Ultra-wide$focal"
                    groupSize >= 3 && position == groupSize - 1 -> "Rear Tele$focal"
                    groupSize >= 3 -> "Rear Wide$focal"
                    groupSize == 2 -> "Rear Lens ${position + 1}$focal"
                    else -> "Rear Camera$focal"
                }
                BuiltInFacing.FRONT -> if (groupSize > 1) {
                    "Front Camera ${position + 1}$focal"
                } else {
                    "Front Camera$focal"
                }
                BuiltInFacing.UNKNOWN -> "Camera ${index + 1}$focal"
            }

            val key = "camera$index"
            val session = BuiltInCameraSession(
                key = key,
                index = index,
                displayName = label,
                cameraId = info.cameraId,
                facing = info.facing,
                profiles = info.profiles,
                capabilities = info.capabilities
            )
            builtInSessions[key] = session
            restoreBuiltInSettings(session)
        }

        val summary = builtInSessions.values
            .sortedBy { it.index }
            .joinToString { item -> item.key + "=" + item.displayName + "[id=" + item.cameraId + "]" }
        log("Klipper built-in cameras: $summary")
    }

'''
svc = replace_once(
    svc,
    "    private fun discoverBuiltInCameras() {",
    extra_discovery + "    private fun discoverBuiltInCameras() {",
    "Safe extra Camera2 discovery insertion",
)

call_pattern = r'(?m)^(\s*)discoverBuiltInCameras\(\)\s*$'
def _add_klipper_discovery(match):
    indent = match.group(1)
    return indent + "discoverBuiltInCameras()\n" + indent + "discoverKlipperBuiltInCameras()"
svc, call_count = re.subn(call_pattern, _add_klipper_discovery, svc, count=1)
if call_count != 1:
    raise SystemExit(f"Camera2 discovery call: expected exactly one call site, found {call_count}")

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
                uri == "/api/uvc/start" -> startUvcFromHttp(
                    session.parameters["index"]?.firstOrNull(),
                    session.parameters["mode"]?.firstOrNull(),
                    session.parameters["fps"]?.firstOrNull()
                )
                uri == "/api/uvc/stop" -> stopUvcFromHttp(session.parameters["index"]?.firstOrNull())
                uri == "/api/builtin/select" -> selectBuiltInFromHttp(session.parameters["key"]?.firstOrNull())
                uri == "/api/builtin/start" -> startBuiltInFromHttp(
                    session.parameters["key"]?.firstOrNull(),
                    session.parameters["width"]?.firstOrNull(),
                    session.parameters["height"]?.firstOrNull(),
                    session.parameters["fps"]?.firstOrNull()
                )
                uri == "/api/builtin/settings" -> updateBuiltInFromHttp(
                    session.parameters["key"]?.firstOrNull(),
                    session.parameters["zoom"]?.firstOrNull(),
                    session.parameters["focus"]?.firstOrNull(),
                    session.parameters["exposure"]?.firstOrNull(),
                    session.parameters["awb"]?.firstOrNull(),
                    session.parameters["torch"]?.firstOrNull()
                )
                uri == "/api/builtin/stop" -> stopBuiltInFromHttp(session.parameters["key"]?.firstOrNull())
                uri.startsWith("/snapshot/uvc/") && uri.endsWith(".jpg") -> serveUvcSnapshot(
                    uri.substringAfter("/snapshot/uvc/").removeSuffix(".jpg").toIntOrNull()
                )
                uri == "/uvc" -> html(uvcListPage())''',
    "HTTP dashboard API routes",
)

http_helpers = r'''
    private data class KlipperUvcOption(
        val label: String,
        val format: String,
        val width: Int,
        val height: Int,
        val fpsValues: List<Int>
    )

    private fun klipperUvcOptions(camera: CameraSession): List<KlipperUvcOption> {
        val result = mutableListOf<KlipperUvcOption>()
        camera.supportedFormats.split(";").forEach { rawEntry ->
            val entry = rawEntry.trim()
            if (entry.isBlank()) return@forEach
            val parts = entry.split(":")
            if (parts.size < 2) return@forEach

            val left = parts[0].trim()
            val leftParts = left.split("|")
            val label = if (leftParts.size >= 2) {
                "${leftParts[0].trim()} ${leftParts[1].trim()}"
            } else {
                "MJPG ${left.trim()}"
            }
            val format = label.substringBefore(" ").trim().ifBlank { "MJPG" }
            val resolution = label.substringAfter(" ", "").trim()
            val width = resolution.substringBefore("x", "").toIntOrNull() ?: return@forEach
            val height = resolution.substringAfter("x", "").toIntOrNull() ?: return@forEach
            val fps = parts[1].split(",").mapNotNull { it.trim().toIntOrNull() }.distinct()

            result += KlipperUvcOption(label, format, width, height, fps)
        }
        return result
    }

    private fun plain(
        text: String,
        status: NanoHTTPD.Response.Status = NanoHTTPD.Response.Status.OK
    ): NanoHTTPD.Response {
        return NanoHTTPD.newFixedLengthResponse(status, NanoHTTPD.MIME_PLAINTEXT, text).apply {
            addHeader("Cache-Control", "no-store")
            addHeader("Access-Control-Allow-Origin", "*")
        }
    }

    private fun startUvcFromHttp(indexText: String?, mode: String?, fpsText: String?): NanoHTTPD.Response {
        val index = indexText?.toIntOrNull() ?: return plain(
            "Missing/invalid USB camera index",
            NanoHTTPD.Response.Status.BAD_REQUEST
        )
        val camera = sessionsByIndex[index] ?: return plain(
            "USB camera $index not found",
            NanoHTTPD.Response.Status.NOT_FOUND
        )

        val options = klipperUvcOptions(camera)
        val selected = mode?.let { wanted -> options.firstOrNull { it.label == wanted } }
            ?: options.getOrNull(camera.selectedResPos)
            ?: options.firstOrNull()
            ?: return plain("No stream modes reported by USB camera $index", NanoHTTPD.Response.Status.CONFLICT)

        val fps = fpsText?.toIntOrNull()
            ?: selected.fpsValues.getOrNull(camera.selectedFpsPos)
            ?: selected.fpsValues.firstOrNull()
            ?: 10

        camera.selectedResPos = options.indexOf(selected).coerceAtLeast(0)
        camera.selectedFpsPos = selected.fpsValues.indexOf(fps).let { if (it < 0) 0 else it }

        val startAction = {
            startStreaming(
                camera.fd,
                selected.width,
                selected.height,
                fps,
                selected.format
            ) { updateStatsAndNotificationAsync() }
        }

        if (camera.isStreaming) {
            stopStreaming(camera.fd) { startAction() }
        } else {
            startAction()
        }
        return plain("Start requested: USB $index ${selected.label} @ ${fps}fps")
    }

    private fun stopUvcFromHttp(indexText: String?): NanoHTTPD.Response {
        val index = indexText?.toIntOrNull() ?: return plain(
            "Missing/invalid USB camera index",
            NanoHTTPD.Response.Status.BAD_REQUEST
        )
        val camera = sessionsByIndex[index] ?: return plain(
            "USB camera $index not found",
            NanoHTTPD.Response.Status.NOT_FOUND
        )
        stopStreaming(camera.fd) { updateStatsAndNotificationAsync() }
        return plain("Stop requested: USB $index")
    }

    private fun startBuiltInFromHttp(
        key: String?,
        widthText: String?,
        heightText: String?,
        fpsText: String?
    ): NanoHTTPD.Response {
        val target = key?.let { builtInSessions[it] } ?: return plain(
            "Built-in camera not found",
            NanoHTTPD.Response.Status.NOT_FOUND
        )

        val current = target.settings
        val width = widthText?.toIntOrNull() ?: current.width
        val height = heightText?.toIntOrNull() ?: current.height
        val fps = fpsText?.toIntOrNull() ?: current.fps
        updateBuiltInSettings(
            target.key,
            current.copy(
                width = width,
                height = height,
                fps = fps,
                fpsRangeLower = fps,
                fpsRangeUpper = fps
            )
        )

        val startTarget = {
            startBuiltInCamera(target.key) { updateStatsAndNotificationAsync() }
        }
        val other = builtInSessions.values.firstOrNull {
            it.key != target.key && it.state != SessionState.IDLE
        }
        when {
            target.state != SessionState.IDLE -> stopBuiltInCamera(target.key) { startTarget() }
            other != null -> stopBuiltInCamera(other.key) { startTarget() }
            else -> startTarget()
        }
        return plain("Start requested: ${target.displayName} ${width}x${height} @ ${fps}fps")
    }

    private fun updateBuiltInFromHttp(
        key: String?,
        zoomText: String?,
        focusText: String?,
        exposureText: String?,
        awbText: String?,
        torchText: String?
    ): NanoHTTPD.Response {
        val target = key?.let { builtInSessions[it] } ?: return plain(
            "Built-in camera not found",
            NanoHTTPD.Response.Status.NOT_FOUND
        )
        val current = target.settings
        val next = current.copy(
            zoomRatio = zoomText?.toFloatOrNull() ?: current.zoomRatio,
            focusDistance = focusText?.toFloatOrNull() ?: current.focusDistance,
            exposureCompensation = exposureText?.toIntOrNull() ?: current.exposureCompensation,
            awbEnabled = awbText?.toBooleanStrictOrNull() ?: current.awbEnabled,
            torchEnabled = torchText?.toBooleanStrictOrNull() ?: current.torchEnabled
        )
        updateBuiltInSettings(target.key, next)
        return plain("Updated ${target.displayName}")
    }

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
        val target = key?.let { builtInSessions[it] } ?: return plain(
            "Built-in camera not found",
            NanoHTTPD.Response.Status.NOT_FOUND
        )
        val current = target.settings
        return startBuiltInFromHttp(
            target.key,
            current.width.toString(),
            current.height.toString(),
            current.fps.toString()
        )
    }

    private fun stopBuiltInFromHttp(key: String? = null): NanoHTTPD.Response {
        val targets = if (key.isNullOrBlank()) {
            builtInSessions.values.filter { it.state != SessionState.IDLE }
        } else {
            listOfNotNull(builtInSessions[key]).filter { it.state != SessionState.IDLE }
        }
        targets.forEach { camera ->
            stopBuiltInCamera(camera.key) { updateStatsAndNotificationAsync() }
        }
        return plain(if (key.isNullOrBlank()) "Stopping built-in camera" else "Stopping $key")
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

        fun builtInWebName(camera: BuiltInCameraSession): String {
            return when (camera.facing) {
                BuiltInFacing.BACK -> if (camera.displayName.startsWith("Rear")) camera.displayName else "Rear phone camera"
                BuiltInFacing.FRONT -> if (camera.displayName.startsWith("Front")) camera.displayName else "Front phone camera"
                BuiltInFacing.UNKNOWN -> camera.displayName
            }
        }

        val usbSessions = sessionsByIndex.keys().toList().sorted().mapNotNull { sessionsByIndex[it] }
        val builtIns = builtInSessions.values.sortedBy { it.index }
        val activeBuiltIn = builtIns.firstOrNull { it.isStreaming }
        val selectedBuiltIn = activeBuiltIn ?: builtIns.firstOrNull()

        val sidebarUsb = usbSessions.joinToString("") { camera ->
            val live = if (camera.isStreaming) "LIVE" else "READY"
            val format = currentUvcDisplayFormat(camera)
            """
            <button class='camera-row' onclick="document.getElementById('tile-usb-${camera.index}')?.scrollIntoView({behavior:'smooth'})">
              <span><b>USB ${camera.index}</b><small>${esc(camera.device.productName ?: camera.device.deviceName)}</small></span>
              <span class='right'><i class='${if (camera.isStreaming) "dot live" else "dot"}'></i>$live<small>${esc(format)}</small></span>
            </button>
            """.trimIndent()
        }

        val sidebarBuiltIn = builtIns.joinToString("") { camera ->
            val live = if (camera.isStreaming) "LIVE" else "READY"
            val profile = camera.actualProfile ?: camera.selectedOrFallbackProfile()
            val res = profile?.let { "${it.size.width}x${it.size.height} @ ${builtInFpsLabel(camera)}" }.orEmpty()
            """
            <button class='camera-row' onclick="choosePhone('${esc(camera.key)}')">
              <span><b>${esc(builtInWebName(camera))}</b><small>Camera2 id ${esc(camera.cameraId)}</small></span>
              <span class='right'><i class='${if (camera.isStreaming) "dot live" else "dot"}'></i>$live<small>${esc(res)}</small></span>
            </button>
            """.trimIndent()
        }

        val tiles = mutableListOf<String>()
        usbSessions.take(3).forEach { camera ->
            val format = currentUvcDisplayFormat(camera)
            val canMjpeg = format in setOf("MJPG", "YUYV", "NV12")
            val options = klipperUvcOptions(camera)
            val selectedOption = options.getOrNull(camera.selectedResPos) ?: options.firstOrNull()
            val selectedFps = selectedOption?.fpsValues?.getOrNull(camera.selectedFpsPos)
                ?: selectedOption?.fpsValues?.firstOrNull()
                ?: 10

            val modeOptions = options.mapIndexed { pos, option ->
                val fpsData = option.fpsValues.joinToString(",")
                "<option value='${esc(option.label)}' data-fps='${esc(fpsData)}' ${if (pos == camera.selectedResPos) "selected" else ""}>${esc(option.label)}</option>"
            }.joinToString("")
            val fpsOptions = (selectedOption?.fpsValues ?: emptyList()).joinToString("") { fps ->
                "<option value='$fps' ${if (fps == selectedFps) "selected" else ""}>$fps fps</option>"
            }

            val media = if (camera.isStreaming && canMjpeg) {
                "<img crossorigin='anonymous' src='/uvc/camera/${camera.index}.mjpg?t=${System.currentTimeMillis()}' alt='USB ${camera.index}'>"
            } else if (camera.isStreaming) {
                "<div class='no-video'>${esc(format)} stream is RTSP-only</div>"
            } else {
                "<div class='no-video'>Stream stopped</div>"
            }

            tiles += """
              <section class='tile' id='tile-usb-${camera.index}'>
                <header><b>USB ${camera.index}</b><span>${esc(camera.device.productName ?: "")}</span></header>
                <div class='media'>$media</div>
                <div class='controls'>
                  <label>Mode<select id='usb-mode-${camera.index}' onchange='refreshUsbFps(${camera.index})'>$modeOptions</select></label>
                  <label>FPS<select id='usb-fps-${camera.index}'>$fpsOptions</select></label>
                  <button class='primary' onclick='usbStart(${camera.index})'>${if (camera.isStreaming) "Restart" else "Start"}</button>
                  <button onclick='usbStop(${camera.index})'>Stop</button>
                </div>
                <footer>
                  <span>${esc(format)} · ${esc(camera.actualEncoding)}</span>
                  <button onclick="takePhoto('tile-usb-${camera.index}','usb-${camera.index}')">Photo</button>
                  ${if (canMjpeg) "<button onclick=\"openUrl('/uvc/camera/${camera.index}.mjpg')\">Open</button>" else ""}
                </footer>
              </section>
            """.trimIndent()
        }

        if (selectedBuiltIn != null) {
            val camera = selectedBuiltIn
            val current = camera.settings
            val profiles = camera.profiles
                .distinctBy { "${it.size.width}x${it.size.height}@${it.fps}" }
                .sortedWith(compareByDescending<BuiltInProfile> { it.size.width * it.size.height }.thenByDescending { it.fps })
            val profileOptions = profiles.joinToString("") { profile ->
                val selected = profile.size.width == current.width && profile.size.height == current.height && profile.fps == current.fps
                "<option value='${profile.size.width},${profile.size.height},${profile.fps}' ${if (selected) "selected" else ""}>${profile.size.width}x${profile.size.height} @ ${profile.fps} fps · ${esc(profile.videoCodec?.label ?: "H.264")}</option>"
            }
            val cameraOptions = builtIns.joinToString("") { item ->
                "<option value='${esc(item.key)}' ${if (item.key == camera.key) "selected" else ""}>${esc(builtInWebName(item))} · id ${esc(item.cameraId)}</option>"
            }
            val streamUrl = if (camera.isStreaming) {
                val profile = camera.actualProfile ?: camera.selectedOrFallbackProfile()
                "/builtin/${camera.key}.${profile?.streamExtension ?: "ts"}"
            } else ""
            val media = if (camera.isStreaming) {
                "<video id='phone-video' muted autoplay playsinline controls></video><div id='phone-error' class='player-error'></div>"
            } else {
                "<div class='no-video'>Phone camera stopped</div>"
            }
            val cap = camera.capabilities
            val maxZoom = cap.maxZoom.coerceAtLeast(1f)
            val maxFocus = cap.maxFocusDistance.coerceAtLeast(0f)

            tiles += """
              <section class='tile phone-tile' id='tile-phone'>
                <header><b>${esc(builtInWebName(camera))}</b><span>Phone Camera2 · id ${esc(camera.cameraId)}</span></header>
                <div class='media'>$media</div>
                <div class='controls phone-controls'>
                  <label>Camera<select id='phone-key' onchange='phoneCameraChanged()'>$cameraOptions</select></label>
                  <label>Resolution / FPS<select id='phone-profile'>$profileOptions</select></label>
                  <button class='primary' onclick='phoneStart()'>${if (camera.isStreaming) "Restart / Apply" else "Start"}</button>
                  <button onclick='phoneStop()'>Stop</button>

                  <label class='slider'>Zoom <span id='zoom-val'>${current.zoomRatio}x</span>
                    <input id='phone-zoom' type='range' min='1' max='$maxZoom' step='0.1' value='${current.zoomRatio}' oninput="document.getElementById('zoom-val').textContent=this.value+'x'" onchange='phoneControls()'>
                  </label>
                  <div class='quick'>
                    <button onclick='setZoom(1)'>1×</button>
                    ${if (maxZoom >= 2f) "<button onclick='setZoom(2)'>2×</button>" else ""}
                    ${if (maxZoom >= 4f) "<button onclick='setZoom(4)'>4×</button>" else ""}
                  </div>

                  ${if (cap.autofocusSupported) """
                  <label class='slider'>Focus <span id='focus-val'>${current.focusDistance}</span>
                    <input id='phone-focus' type='range' min='0' max='$maxFocus' step='0.1' value='${current.focusDistance}' oninput="document.getElementById('focus-val').textContent=this.value" onchange='phoneControls()'>
                  </label>""" else ""}

                  ${if (!cap.exposureCompensationRange.isEmpty()) """
                  <label class='slider'>Exposure <span id='exp-val'>${current.exposureCompensation}</span>
                    <input id='phone-exposure' type='range' min='${cap.exposureCompensationRange.first}' max='${cap.exposureCompensationRange.last}' step='1' value='${current.exposureCompensation}' oninput="document.getElementById('exp-val').textContent=this.value" onchange='phoneControls()'>
                  </label>""" else ""}

                  <label class='check'><input id='phone-awb' type='checkbox' ${if (current.awbEnabled) "checked" else ""} onchange='phoneControls()'> Auto white balance</label>
                  ${if (cap.torchSupported) "<label class='check'><input id='phone-torch' type='checkbox' ${if (current.torchEnabled) "checked" else ""} onchange='phoneControls()'> Torch</label>" else ""}
                </div>
                <footer>
                  <span>${esc(camera.actualEncoding.ifBlank { "Camera2" })}</span>
                  <button onclick="takePhoto('tile-phone','phone')">Photo</button>
                  ${if (streamUrl.isNotBlank()) "<button onclick=\"openUrl('$streamUrl')\">Open TS</button>" else ""}
                </footer>
              </section>
            """.trimIndent()
        }

        while (tiles.size < 4) {
            tiles += """<section class='tile empty'><div class='no-video'>Connect/start another camera</div></section>"""
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
:root{color-scheme:dark;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}*{box-sizing:border-box}
body{margin:0;background:#0b1015;color:#eef4f8;height:100vh;overflow:hidden}.layout{display:grid;grid-template-columns:270px 1fr;height:100vh}
.sidebar{background:#111922;border-right:1px solid #263342;padding:14px;overflow:auto}.brand{font-weight:800;font-size:20px;margin:4px 4px 14px}.brand small{display:block;font-size:11px;font-weight:500;color:#7f93a8;margin-top:3px}
.group{font-size:11px;color:#7f93a8;font-weight:800;letter-spacing:.12em;margin:16px 4px 7px}.camera-row{width:100%;border:1px solid #263342;background:#151f29;color:#eef4f8;padding:10px;border-radius:10px;margin:5px 0;display:flex;justify-content:space-between;text-align:left;gap:8px;cursor:pointer}.camera-row:hover{background:#1d2a36}.camera-row span{display:flex;flex-direction:column;gap:2px}.camera-row .right{text-align:right;align-items:flex-end}.camera-row small{font-size:10px;color:#91a4b8}.dot{width:7px;height:7px;border-radius:50%;background:#58697b;display:inline-block}.dot.live{background:#29d17d;box-shadow:0 0 8px #29d17d88}
.main{display:grid;grid-template-rows:auto 1fr;min-width:0}.toolbar{display:flex;gap:8px;align-items:center;padding:10px 14px;background:#111922;border-bottom:1px solid #263342}.toolbar h1{font-size:15px;margin:0 auto 0 0}.toolbar button,.tile button{background:#1d2a36;border:1px solid #34465a;color:white;border-radius:8px;padding:7px 10px;cursor:pointer}button.primary{background:#0f6654;border-color:#188b72}
.grid{padding:10px;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:10px;min-height:0}.tile{background:#111922;border:1px solid #263342;border-radius:12px;display:grid;grid-template-rows:auto minmax(120px,1fr) auto auto;overflow:hidden;min-height:0}.tile header,.tile footer{display:flex;align-items:center;gap:8px;padding:7px 10px;background:#131d27;font-size:12px}.tile header span{color:#8da2b7;margin-left:auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tile footer span{margin-right:auto;color:#8da2b7;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.media{min-height:0;background:black;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative}.media img,.media video{width:100%;height:100%;object-fit:contain;background:black}.controls{padding:7px;background:#101923;border-top:1px solid #263342;display:grid;grid-template-columns:2fr 1fr auto auto;gap:6px;align-items:end}.controls label{font-size:10px;color:#8da2b7;display:flex;flex-direction:column;gap:3px}.controls select,.controls input[type=range]{width:100%;background:#182430;color:#eef4f8;border:1px solid #34465a;border-radius:6px;padding:5px}.phone-controls{grid-template-columns:1fr 1.4fr auto auto}.phone-controls .slider{grid-column:span 2}.phone-controls .quick{display:flex;gap:4px}.phone-controls .check{flex-direction:row;align-items:center;color:#c9d5df}.no-video{color:#72869a;font-size:13px;text-align:center;padding:20px}.empty{opacity:.55}.player-error{position:absolute;left:8px;right:8px;bottom:8px;background:#541f25dd;color:#ffd9dd;padding:6px;border-radius:6px;font-size:11px;display:none}#action-status{font-size:11px;color:#8da2b7;max-width:320px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media(max-width:900px){body{overflow:auto}.layout{grid-template-columns:1fr;height:auto}.sidebar{border-right:0;border-bottom:1px solid #263342}.grid{grid-template-columns:1fr;grid-template-rows:none}.tile{min-height:430px}.controls,.phone-controls{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<div class='layout'>
  <aside class='sidebar'><div class='brand'>Klipper MultiCam<small>One app · full browser control</small></div><div class='group'>USB CAMERAS</div>${sidebarUsb.ifBlank { "<div class='no-video'>No USB camera sessions</div>" }}<div class='group'>PHONE CAMERAS</div>${sidebarBuiltIn.ifBlank { "<div class='no-video'>No Camera2 cameras exposed</div>" }}</aside>
  <main class='main'><div class='toolbar'><h1>Live cameras</h1><span id='action-status'></span><button onclick='startAllUsb()'>Start USB</button><button onclick='stopAllUsb()'>Stop USB</button><button onclick='location.reload()'>Refresh</button></div><div class='grid'>${tiles.joinToString("")}</div></main>
</div>
<script>
var phonePlayer=null;
function openUrl(u){window.open(u,'_blank')}
function status(t){var e=document.getElementById('action-status');if(e)e.textContent=t||''}
function action(url,delay){status('Working…');return fetch(url,{cache:'no-store'}).then(function(r){return r.text().then(function(t){if(!r.ok)throw new Error(t);return t})}).then(function(t){status(t);if(delay!==0)setTimeout(function(){location.reload()},delay||1300);return t}).catch(function(e){status(e.message);alert(e.message);throw e})}
function refreshUsbFps(i){var m=document.getElementById('usb-mode-'+i),f=document.getElementById('usb-fps-'+i);if(!m||!f)return;var a=(m.options[m.selectedIndex].dataset.fps||'').split(',').filter(Boolean);f.innerHTML='';a.forEach(function(v){var o=document.createElement('option');o.value=v;o.textContent=v+' fps';f.appendChild(o)})}
function usbStart(i){var m=document.getElementById('usb-mode-'+i),f=document.getElementById('usb-fps-'+i);return action('/api/uvc/start?index='+i+'&mode='+encodeURIComponent(m?m.value:'')+'&fps='+encodeURIComponent(f?f.value:''),1800)}
function usbStop(i){return action('/api/uvc/stop?index='+i,1000)}
function startAllUsb(){var ids=[${usbSessions.joinToString(",") { it.index.toString() }}];ids.reduce(function(p,i){return p.then(function(){return fetch('/api/uvc/start?index='+i,{cache:'no-store'})})},Promise.resolve()).then(function(){setTimeout(function(){location.reload()},2200)})}
function stopAllUsb(){var ids=[${usbSessions.joinToString(",") { it.index.toString() }}];ids.forEach(function(i){fetch('/api/uvc/stop?index='+i,{cache:'no-store'})});setTimeout(function(){location.reload()},1200)}
function choosePhone(key){var s=document.getElementById('phone-key');if(s){s.value=key;phoneCameraChanged()}else{action('/api/builtin/select?key='+encodeURIComponent(key),2200)}}
function phoneCameraChanged(){var s=document.getElementById('phone-key');if(s)action('/api/builtin/select?key='+encodeURIComponent(s.value),2200)}
function phoneStart(){var k=document.getElementById('phone-key'),p=document.getElementById('phone-profile');if(!k||!p)return;var a=p.value.split(',');action('/api/builtin/start?key='+encodeURIComponent(k.value)+'&width='+a[0]+'&height='+a[1]+'&fps='+a[2],2500)}
function phoneStop(){var k=document.getElementById('phone-key');action('/api/builtin/stop?key='+encodeURIComponent(k?k.value:''),1200)}
function phoneControls(){var k=document.getElementById('phone-key');if(!k)return;var q=['key='+encodeURIComponent(k.value)];var z=document.getElementById('phone-zoom');if(z)q.push('zoom='+encodeURIComponent(z.value));var f=document.getElementById('phone-focus');if(f)q.push('focus='+encodeURIComponent(f.value));var e=document.getElementById('phone-exposure');if(e)q.push('exposure='+encodeURIComponent(e.value));var a=document.getElementById('phone-awb');if(a)q.push('awb='+a.checked);var t=document.getElementById('phone-torch');if(t)q.push('torch='+t.checked);action('/api/builtin/settings?'+q.join('&'),0)}
function setZoom(v){var z=document.getElementById('phone-zoom');if(!z)return;z.value=Math.min(parseFloat(z.max),v);z.dispatchEvent(new Event('input'));phoneControls()}
function startPhonePlayer(url){var v=document.getElementById('phone-video'),err=document.getElementById('phone-error');if(!v)return;if(window.mpegts&&mpegts.getFeatureList().mseLivePlayback){phonePlayer=mpegts.createPlayer({type:'mpegts',isLive:true,hasAudio:false,hasVideo:true,url:url},{enableWorker:true,enableStashBuffer:false,lazyLoad:false,liveBufferLatencyChasing:true,liveBufferLatencyMaxLatency:1.5,liveBufferLatencyMinRemain:0.15});if(mpegts.Events&&mpegts.Events.ERROR){phonePlayer.on(mpegts.Events.ERROR,function(type,detail,info){if(err){err.style.display='block';err.textContent='Player: '+type+' / '+detail+(info?' · '+String(info):'')}})}phonePlayer.attachMediaElement(v);phonePlayer.load();var p=phonePlayer.play();if(p&&p.catch){p.catch(function(e){if(err){err.style.display='block';err.textContent='Play: '+e.message}})}}else if(err){err.style.display='block';err.textContent='Chrome MSE live playback is unavailable.'}}
function takePhoto(tileId,name){var tile=document.getElementById(tileId);if(!tile)return;var media=tile.querySelector('video,img');if(!media)return;var w=media.videoWidth||media.naturalWidth||media.clientWidth,h=media.videoHeight||media.naturalHeight||media.clientHeight;if(!w||!h){alert('No frame available yet');return}var c=document.createElement('canvas');c.width=w;c.height=h;try{c.getContext('2d').drawImage(media,0,0,w,h)}catch(e){alert('Snapshot failed: '+e.message);return}c.toBlob(function(blob){if(!blob)return;var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name+'-'+new Date().toISOString().replace(/[:.]/g,'-')+'.jpg';a.click();setTimeout(function(){URL.revokeObjectURL(a.href)},1500)},'image/jpeg',0.92)}
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

# Guard against the exact v3 failure mode: a broad regex deleted roughly 1,500
# lines from UvcStreamingService.kt. This patch only inserts/replaces small
# bounded regions, so the patched service must never become shorter.
svc_patched_line_count = len(svc.splitlines())
if svc_patched_line_count < svc_original_line_count:
    raise SystemExit(
        f"Source integrity check failed: service shrank from {svc_original_line_count} "
        f"to {svc_patched_line_count} lines"
    )

if svc.count("private fun discoverBuiltInCameras()") != 1:
    raise SystemExit("Source integrity check failed: discoverBuiltInCameras count changed")
if svc.count("private fun discoverKlipperBuiltInCameras()") != 1:
    raise SystemExit("Source integrity check failed: Klipper discovery method missing/duplicated")

main_path.write_text(main, encoding="utf-8")
svc_path.write_text(svc, encoding="utf-8")

print("Patched:", main_path)
print("Patched:", svc_path)
print("Bundled:", mpegts_path)
print("Klipper MultiCam v6 active web-control patch applied successfully.")
