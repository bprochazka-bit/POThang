# POThang Receiver (Android)

A native Android app (Kotlin + Jetpack Compose) for receiving against
PurchaseTracker POs from the dock. It talks to the Flask server over HTTP
via the JSON API at `/api/v1` (see `purchasetracker/blueprints/api.py`).

## Features

- **Scan to receive** - the camera reads label / packing-slip text on the
  phone (ML Kit, bundled model, works offline) and the server ranks open PO
  lines by model number, SKU, name and description, tolerating common OCR
  slips (O/0, I/1, S/5, B/8). A confident match pops the "qty received"
  prompt straight away; otherwise pick from the ranked list. Auto look-up
  fires when the camera text settles (toggle "Auto"), or tap **Look up**, or
  type a model number. Torch and pause buttons on the preview.
- **Manual receiving** - open a PO from the home screen, filter lines, tap
  one, enter the qty.
- **New item from photo / screenshot** - take a photo or pick screenshots
  (or *Share* an image to the app from any other app). Text is OCR'd on the
  phone, the server suggests name / model / SKU / vendor / URL / price, and
  any recognized line can be tapped into a field. Images are attached to the
  new item.
- **Received shipments** - receipts grouped by day and PO with a verify tick
  per receipt (same data as the web view at `/pos/receiving/shipments`).
- Works in portrait and landscape: two-pane layouts side by side in
  landscape, stacked in portrait; rotation keeps the camera and any
  in-progress entry.

## Server setup

1. The phone must reach the server, e.g. `http://192.168.1.20:5000`.
2. In `single_user` mode nothing else is needed. For `ldap` or
   `proxy_header`, add a token to `instance/config.py`:

   ```python
   API_TOKENS = {"<long random token>": "dock-phone"}
   ```

   Generate one with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
   Receipts are recorded under the mapped name.
3. Behind an auth proxy (Authentik, oauth2-proxy), let `/api/` and
   `/attachments/` through to Flask unauthenticated by the proxy - the
   bearer token is checked by the app. With `proxy_header`, requests that
   carry the proxy's header still work too.

## Build

Requires the Android SDK (Android Studio, or the command-line tools with
platform 35) and JDK 17.

```
cd android
./gradlew assembleDebug        # app/build/outputs/apk/debug/app-debug.apk
./gradlew installDebug         # onto a connected device
```

Or open the `android/` folder in Android Studio. CI (`.github/workflows/android.yml`)
builds a debug APK on every change under `android/` and uploads it as a
workflow artifact.

minSdk 28 (Android 9), targetSdk 35. Plain `http://` is allowed for LAN
servers (`res/xml/network_security_config.xml`); use https when the server
is reachable from outside the LAN.

## Layout

```
app/src/main/java/com/pothang/receiver/
  MainActivity.kt          entry point, share-intent handling
  data/                    Settings (prefs), ApiClient (OkHttp), models
  ocr/Ocr.kt               ML Kit helpers + CameraX analyzer
  ui/                      Compose screens: Home, Scan, PoLines, NewItem,
                           Received, Settings, ReceiveDialog, components
```
