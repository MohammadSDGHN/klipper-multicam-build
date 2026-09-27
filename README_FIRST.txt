KLIPPER MULTICAM — CLOUD BUILD
==============================

STOP USING THE LOCAL ANDROID SDK BOOTSTRAPPERS.

Why:
Google's Android Developers page currently documents the Windows Android CLI
installer at:
  https://dl.google.com/android/cli/latest/windows_x86_64/install.cmd

but this PC is receiving Google's own 404 page from that endpoint. This package
avoids the PC's Android SDK/CLI download path entirely.

WHAT THIS BUILDS
----------------
It checks out the GPL-3.0-or-later FreeTracker/AndroidUVC project in GitHub's
Linux build environment, patches MainActivity so up to four USB UVC MJPEG
cameras start automatically when their sessions are ready, builds the APK,
and uploads KlipperMultiCam.apk as a GitHub Actions artifact.

The app exposes separate streams:
  http://PHONE_IP:8080/uvc/camera/0.mjpg
  http://PHONE_IP:8080/uvc/camera/1.mjpg
  http://PHONE_IP:8080/uvc/camera/2.mjpg
  http://PHONE_IP:8080/uvc/camera/3.mjpg

If only two cameras are connected, only camera 0 and 1 start.

HOW TO BUILD — NO GIT / NO ANDROID SDK ON YOUR PC
--------------------------------------------------
1. Sign in to github.com.
2. Create a new EMPTY repository, for example:
     klipper-multicam-build
3. Choose:
     Add file -> Upload files
4. Drag ALL contents of this extracted folder into the upload page.
   Make sure the repository contains:
     .github/workflows/build-apk.yml
     patch_androiduvc.py
     INSTALL_TO_S10.bat
5. Commit the upload.
6. Open the repository's Actions tab.
7. Open "Build Klipper MultiCam APK".
8. If it did not start automatically, click "Run workflow".
9. When the run is green, open it and download:
     KlipperMultiCam-APK
10. Extract that artifact. Put KlipperMultiCam.apk beside INSTALL_TO_S10.bat.
11. Connect to the S10 with ADB and run:
      INSTALL_TO_S10.bat

FIRST CAMERA CONNECTION
-----------------------
Android may ask for USB permission for each webcam the first time. Approve it
and choose the persistent/default option if LineageOS offers one.

KLIPPER / MAINSAIL / FLUIDD
---------------------------
Use each camera's .mjpg URL as the stream URL.

Example phone IP 192.168.0.249:
  http://192.168.0.249:8080/uvc/camera/0.mjpg
  http://192.168.0.249:8080/uvc/camera/1.mjpg

This path does NOT depend on your Windows Android SDK, sdkmanager, Android CLI,
Git, winget, or the Google download route that is returning 404 on your PC.
