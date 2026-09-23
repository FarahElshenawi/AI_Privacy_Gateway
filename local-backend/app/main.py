"""Local Backend entrypoint. Wires /detect, /mask, /demask (app/api/) behind
origin check + per-install token auth (app/security/).
"""
from fastapi import FastAPI

app = FastAPI(title="AI Privacy Gateway — Local Backend")

# TODO(Role 2, Week 1): mount app.api.detect / mask / demask routers here
# TODO(Role 2, Week 1): add origin-check + token-auth middleware (app/security/)
