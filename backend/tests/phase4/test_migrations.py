import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def test_0004_round_trip_preserves_investigations(sandbox, investigation, ai_worker, state):
    await ai_worker.run_once()
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    async with sandbox.engine.begin() as connection:
        report = await connection.scalar(text("SELECT payload FROM investigation_reports"))
        count = await connection.scalar(text("SELECT count(*) FROM log_entries"))

        def down(sync):
            config.attributes["connection"] = sync
            command.downgrade(config, "0003")

        await connection.run_sync(down)
        assert await connection.scalar(text("SELECT payload FROM investigation_reports")) == report
        assert await connection.scalar(text("SELECT count(*) FROM log_entries")) == count

        def up(sync):
            config.attributes["connection"] = sync
            command.upgrade(config, "head")
            command.check(config)

        await connection.run_sync(up)
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
        assert await connection.scalar(text("SELECT payload FROM investigation_reports")) == report
        assert await connection.scalar(text("SELECT count(*) FROM log_entries")) == count
