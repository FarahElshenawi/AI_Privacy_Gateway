# Deploying Doppel to a fleet

Three pieces: the **local backend** on each machine, the **Chrome extension**, and the **cloud backend** (policies, audit, enrollment). This page covers the first two; the cloud backend is in `cloud-backend/README.md`.

## 1. Local backend (per machine)

`installer/` holds one installer per OS. Each one makes a private virtualenv, copies the backend, downloads the Tier 2 model **at a pinned commit** into a local folder, and registers a service that starts at login and restarts on failure. The service listens on `127.0.0.1:8765` only.

| OS | Run | Service |
|---|---|---|
| Windows | `installer\windows\install.ps1` | scheduled task "Doppel backend" (at logon, restarts) |
| macOS | `installer/macos/install.sh` | launchd agent `com.doppel.backend` |
| Linux | `installer/linux/install.sh` | systemd user unit `doppel.service` |

Inputs (environment variables):

- `MODEL_REVISION` (required): the full 40-character commit sha of `fastino/gliner2-privacy-filter-PII-multi` you validated. A branch or tag is refused, so the model cannot change underneath you. At the time of writing the Hub reported `1cb4166094dc58fa8d836429f060d6c95f62b495`; **verify it yourself and run the eval on it before you pin it.**
- `DLP_EXTENSION_IDS`: extra allowed extension IDs (the pinned ID `efcejekkbkbbknfpjgbpkojgnoomggbi` is always allowed).
- `CLOUD_URL`, `CLOUD_ENROLL_KEY`: the device enrolls itself with the enrollment key and then uses its own token.

The model is loaded from disk with `HF_HUB_OFFLINE=1`; nothing is fetched at run time. The vault key goes into the OS credential store (see the local-backend README).

**Updating** is the same command again with a new `MODEL_REVISION` (or none change): it replaces the code and model and restarts the service. Roll out through your software-distribution tool (Intune, Jamf, a configuration manager); there is no self-updater, on purpose, so that nothing on a laptop pulls code from the internet by itself.

**Not included, needs you:** a signed installer package (`.msi`/`.pkg`/`.deb`). Signing needs your code-signing certificate (Windows Authenticode, Apple Developer ID plus notarisation); these scripts are what you would wrap in that package. They have been syntax-checked, not run on Windows or macOS.

## 2. Extension

The extension ID is pinned by the manifest key, so policies and the backend allow-list can name it: `efcejekkbkbbknfpjgbpkojgnoomggbi`.

Force-install and lock protection with Chrome policy (Windows registry / macOS profile / Linux `/etc/opt/chrome/policies/managed/doppel.json`):

```json
{
  "ExtensionSettings": {
    "efcejekkbkbbknfpjgbpkojgnoomggbi": {
      "installation_mode": "force_installed",
      "update_url": "https://YOUR-HOST/doppel/updates.xml"
    }
  },
  "3rdparty": {
    "extensions": {
      "efcejekkbkbbknfpjgbpkojgnoomggbi": { "protectionLocked": true }
    }
  }
}
```

- `protectionLocked: true` removes the user's off-switch and makes Doppel re-attach even if the user cancels Chrome's debugging bar.
- `update_url` points at an update manifest you host (a small XML file naming the `.crx` you build and sign with the key, `doppel-extension.pem`). Keep that key secret; anyone holding it can publish an update that Chrome will accept as Doppel. Without a hosted `update_url`, updates are whatever you push by policy.
- Chrome shows a "debugging" bar while Doppel is attached; that is how it intercepts uploads and cannot be hidden.

## 3. Checks after a rollout

1. `curl http://127.0.0.1:8765/health` on a machine returns `ok` with Tier 2 ready (warm-up can take a minute).
2. The dashboard's **Devices** list shows the machine as online with the expected version.
3. In ChatGPT and Gemini: send a prompt with a name and an IBAN, upload a PDF, a Word and an Excel file. The badge has no "!" and the chat shows the real values (demasked) while the network sees surrogates.
