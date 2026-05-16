from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config
from .alpaca_client import AlpacaClient
from .api import build_app
from .journal import Journal
from .scheduler import build_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("alpaca-bot")

cfg = config.load()
log.info("starting alpaca-bot mode=%s ceiling=$%.0f", cfg.alpaca_mode, cfg.managed_equity_ceiling_usd)

client = AlpacaClient(cfg)
journal = Journal(cfg.data_dir / "db.sqlite")
scheduler, jobs = build_scheduler(cfg, client, journal)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    log.info("scheduler started with %d jobs", len(scheduler.get_jobs()))
    journal.heartbeat("startup")
    yield
    scheduler.shutdown(wait=False)
    log.info("scheduler stopped")


app = build_app(cfg, client, journal, jobs, lifespan=lifespan)
