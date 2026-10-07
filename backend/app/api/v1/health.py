"""Liveness + honest per-dependency reachability.

Qdrant is used from checkpoint 2.D and is checked honestly.
Neo4j is used from checkpoint 2.C and is checked honestly.
"""

import logging

import pymysql
import redis
from fastapi import APIRouter

from app.core.config import Settings, get_settings

logger = logging.getLogger("architectos.health")

router = APIRouter()


def _check_mysql(settings: Settings) -> dict:
    try:
        conn = pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            database=settings.mysql_database,
            connect_timeout=2,
        )
        conn.close()
        return {"status": "up"}
    except Exception as exc:
        logger.warning("mysql health check failed: %s", exc)
        return {"status": "down", "error": str(exc)}


def _check_qdrant(settings: Settings) -> dict:
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, timeout=3)
        client.get_collections()
        return {"status": "up"}
    except Exception as exc:
        logger.warning("qdrant health check failed: %s", exc)
        return {"status": "down", "error": str(exc)}


def _check_neo4j(settings: Settings) -> dict:
    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        driver.verify_connectivity()
        driver.close()
        return {"status": "up"}
    except Exception as exc:
        logger.warning("neo4j health check failed: %s", exc)
        return {"status": "down", "error": str(exc)}


def _check_redis(settings: Settings) -> dict:
    try:
        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            socket_connect_timeout=2,
        )
        client.ping()
        return {"status": "up"}
    except Exception as exc:
        logger.warning("redis health check failed: %s", exc)
        return {"status": "down", "error": str(exc)}


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "dependencies": {
            "mysql": _check_mysql(settings),
            "redis": _check_redis(settings),
            "qdrant": _check_qdrant(settings),
            "neo4j": _check_neo4j(settings),
        },
    }
