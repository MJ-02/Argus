"""FastAPI dependency factories for database sessions and the Neo4j driver."""
from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Request
from neo4j import AsyncDriver
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.session_factory() as session:
        yield session


async def get_neo4j(request: Request) -> AsyncDriver:
    return request.app.state.neo4j_driver
