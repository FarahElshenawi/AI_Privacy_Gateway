"""Admin CLI: create an organization and print its API key (shown once; only a hash is stored).

    python -m app.admin create-org "NewCompany Inc."
"""
from __future__ import annotations

import sys

from app.db import create_organization, get_session, init_db


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "create-org" or not argv[1].strip():
        print(__doc__, file=sys.stderr)
        return 2
    session = get_session(init_db())
    try:
        org, key = create_organization(session, argv[1].strip())
    finally:
        session.close()
    print(f"organization id={org.id} name={org.name!r}\n"
          f"ADMIN key (dashboard; keep secret; shown once): {key}\n"
          f"ENROLLMENT key (give to employee machines as CLOUD_ENROLL_KEY; shown once): {org.enroll_key_plain}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
