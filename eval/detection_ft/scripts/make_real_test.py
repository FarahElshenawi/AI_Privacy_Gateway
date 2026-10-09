#!/usr/bin/env python3
"""Turn a plain text file of hand-marked prompts into eval/real_test.jsonl (the repo's eval format).

Input: one prompt per line. Wrap each sensitive part as [[LABEL|text]]. Lines with no markers are harmless text.
Blank lines and lines starting with # are skipped.   Use \\n inside a line for a line break.

    python eval/detection_ft/scripts/make_real_test.py real_prompts.txt eval/real_test.jsonl

Example line:   Hi, I'm [[PERSON|Mona Fahmy]], my pass is [[PASSWORD|Tr0ub4dor&3xyz]]
"""
import json
import re
import sys

TIER2 = {"PERSON", "DATE_OF_BIRTH", "ADDRESS", "LOCATION", "USERNAME", "GOVERNMENT_ID", "PASSPORT_NUMBER",
         "DRIVERS_LICENSE_NUMBER", "SENSITIVE_DATE", "SECRET", "PASSWORD", "ORGANIZATION"}
TIER1 = {"EMAIL", "PHONE_NUMBER", "CREDIT_CARD", "IBAN", "US_SSN", "API_KEY", "IP_ADDRESS", "AUTH_TOKEN"}
MARK = re.compile(r"\[\[([A-Za-z_]+)\|(.+?)\]\]")


def convert(line):
    line = line.replace("\\n", "\n")
    text, spans, pos = "", [], 0
    for m in MARK.finditer(line):
        text += line[pos:m.start()]
        label, val = m.group(1).upper(), m.group(2)
        if label not in TIER2 | TIER1:
            raise ValueError(f"unknown label {label!r}")
        spans.append({"start": len(text), "end": len(text) + len(val), "label": label,
                      "tier": 2 if label in TIER2 else 1})
        text += val
        pos = m.end()
    return text + line[pos:], spans


def main(src, dst):
    n = bad = 0
    with open(src, encoding="utf-8") as f, open(dst, "w", encoding="utf-8") as out:
        for i, raw in enumerate(f, 1):
            raw = raw.rstrip("\n")
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            try:
                text, spans = convert(raw)
            except ValueError as e:
                print(f"line {i}: {e} - skipped")
                bad += 1
                continue
            n += 1
            out.write(json.dumps({"id": f"real-{n:04d}", "text": text, "tags": ["real"], "spans": spans},
                                 ensure_ascii=False) + "\n")
    print(f"wrote {n} cases to {dst} ({bad} skipped)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
