"""Cloud Backend entrypoint: policy distribution + audit collection."""
from fastapi import FastAPI

app = FastAPI(title="AI Privacy Gateway — Cloud Backend")
# TODO(Role 5): mount app.api.policy / app.api.audit routers
