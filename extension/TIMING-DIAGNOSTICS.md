# Temporary file-upload timing diagnostics

This build adds temporary `[Doppel Timing]` console logs to help identify slow file uploads. It does not change masking, ordering, timeouts, strict/fail-closed behavior, or backend policy.

## What is logged

Only stage names, elapsed milliseconds, file sizes/counts, response sizes, HTTP status, and retry attempt. No file names, file contents, PII, tokens, or vault mappings are logged by the new timing code.

## Run a manual test

1. Unzip this archive. In `chrome://extensions`, enable Developer mode and choose **Load unpacked**. Select the extracted `extension` folder (the one containing `manifest.json`). If an older copy of Doppel is enabled, avoid running both copies at once.
2. Open ChatGPT. Open the page DevTools Console and filter for `Doppel Timing`.
3. Open `chrome://extensions`, find this diagnostic Doppel instance, and click its **service worker** Inspect link. In that console, filter for `Doppel Timing` too.
4. Attach one representative file that normally feels slow. Note the logs.
5. Repeat with two files attached together. Do not use real sensitive data for a timing test if a synthetic test file will reproduce the slowdown.

## Interpret the stages

- `content.file_array_buffer`, `content.bytes_to_base64`, `content.base64_to_bytes`, `content.construct_file`: browser-side work.
- `content.send_message_round_trip`: end-to-end wait for the service worker, not messaging overhead alone.
- `worker.base64_to_bytes`, `worker.resolve_vault_id`, `worker.mask_file_checked`, `worker.record_file_masked`, `worker.sha256_masked_file`, `worker.bytes_to_base64`: service-worker stages.
- `background.process_file_http_round_trip`: time from issuing the local `/api/process_file` request until response headers arrive. It includes request transfer and backend processing; it does not isolate Tier 1 vs Tier 2.
- `background.process_file_response_body_read`: response-body transfer/read time.
- `content.mask_all_total`: total for the full sequential batch.

The extension archive does not contain the local backend source, so it cannot separately time parsing, Tier 1, Tier 2, and reconstruction inside the backend. If `background.process_file_http_round_trip` dominates, add backend-side stage timers next.

## Revert

This is a separate diagnostic archive. To return to the original build, disable/remove this unpacked diagnostic extension and reload the original extension folder. The instrumentation changes are in:

- `src/content/file-interceptor.js`
- `src/background/backend.js`
- `src/background/chatgpt.js`

Do not copy this diagnostic build over your main working tree unless you intend to keep the instrumentation.
