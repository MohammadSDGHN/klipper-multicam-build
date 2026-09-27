@echo off
setlocal
cd /d "%~dp0"
title Install Klipper MultiCam to S10

set ADB=C:\adb\platform-tools\adb.exe
if not exist "%ADB%" set ADB=adb.exe

if not exist "KlipperMultiCam.apk" (
  echo.
  echo KlipperMultiCam.apk is not in this folder.
  echo Download the GitHub Actions artifact, extract it,
  echo and put KlipperMultiCam.apk beside this BAT file.
  echo.
  pause
  exit /b 1
)

echo Checking ADB...
"%ADB%" devices -l
echo.
echo Installing APK...
"%ADB%" install -r "KlipperMultiCam.apk"
if errorlevel 1 (
  echo APK install failed.
  pause
  exit /b 1
)

echo.
echo Launching the app...
"%ADB%" shell monkey -p net.d7z.net.oss.uvc -c android.intent.category.LAUNCHER 1

echo.
echo Expected UVC stream URLs after cameras are approved:
echo   http://PHONE_IP:8080/uvc/camera/0.mjpg
echo   http://PHONE_IP:8080/uvc/camera/1.mjpg
echo   http://PHONE_IP:8080/uvc/camera/2.mjpg
echo   http://PHONE_IP:8080/uvc/camera/3.mjpg
echo.
pause
