"""FastAPI app: REST commands in, SSE event stream out.

    uv run uvicorn company_check.api.app:app --reload
"""

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from company_check.config import get_settings
from company_check.db import get_session
from company_check.temporal_client import get_temporal_client

app = FastAPI(title="Live Company Check")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(text("select 1"))
    client = await get_temporal_client()
    await client.service_client.check_health()
    return {"database": "ok", "temporal": "ok"}
