"""Standalone production entry point for the V66 API."""
from fastapi import FastAPI
from sqlalchemy import text

from agents.EnnoScholarV66.service import get_service
from db.database import engine
from routers.scholar_v66 import router


app = FastAPI(title="EnnoScholar V66")
app.include_router(router)


@app.get("/health")
def health():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    get_service()
    return {"status": "ok"}
