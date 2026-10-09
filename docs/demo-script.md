# Demo script (matches what the product really does)

Total: ~8 minutes. Every step below was checked against the code; claims that are *not* true yet are listed at the end so nobody says them out loud.

## Setup (before the audience arrives)
1. Start the local backend (`uvicorn app.main:app --host 127.0.0.1 --port 8765`). Leave `curl 127.0.0.1:8765/health` ready.
2. Load the extension unpacked; open chatgpt.com. Chrome shows "Doppel is debugging this browser".
3. Optional (for step 6): cloud backend running, `CLOUD_URL` / `CLOUD_API_KEY` set on the local backend, dashboard open on the Policies page.

## 1. Degraded coverage, shown honestly (1 min)
Right after starting the backend `/health` says `"tier2":"warming_up"`: the name model is still loading. Say so: *"Names are not covered yet; regexes (cards, emails, keys…) already are. The response says exactly which labels were not checked."* Send a prompt now and point at the popup note. Wait ~30 s until `tier2` is `ready`.

## 2. Text (2 min)
Type: *"Send the contract to Sarah Mitchell, card 4532 8891 2204 7712, mail sarah@acme.com."*
- ChatGPT receives a different name and e-mail (realistic stand-ins), and the card as `[REDACTED:CREDIT_CARD]` (cards are never faked).
- The reply on screen shows the real name and e-mail again (display-only; the card stays redacted).
- Reload the chat: history is restored the same way.

## 3. A file (2 min)
Attach a small text-based PDF containing a name and an IBAN. Show the popup "files masked" counter. The uploaded copy is the masked one.

## 4. Fail closed: blocked request (1 min)
Attach a scanned PDF or a PNG. The upload is **blocked** with a red notice ("image / no extractable text"), not sent. Then stop the backend and send a prompt: also blocked. Say: *"If we can't prove it's safe, it doesn't leave."*

## 5. Policy (1 min)
Dashboard → Policies: change EMAIL from faker to redact. Critical secrets (cards, keys, SSN…) cannot be set to *keep*; show the disabled chip.

## 6. Control plane (1 min, only if the cloud is running)
Local backends pull policies every 5 minutes (`CLOUD_POLICY_PULL_INTERVAL_S` can be lowered for the demo). Audit shows event *types and counts only*; never prompt text.

## Do NOT claim
- Compliance with GDPR / HIPAA / EU AI Act (no assessment has been done).
- That Excel keeps charts, images or pivot tables (they are dropped; sensitive cells are masked).
- OCR or image support (blocked by design).
- Customers or deployments (none yet).
- That real values never reach the page: restored values are written into the page's DOM.
