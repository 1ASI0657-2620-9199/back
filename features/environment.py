"""Local isolated acceptance tests or an explicitly selected HTTPS demonstration."""

from pathlib import Path
import tempfile
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx

from app.main import create_app


def open_local_client(context):
    context.client = TestClient(create_app(database_path=str(context.database_path)))
    context.client.__enter__()


def before_scenario(context, scenario):
    context.user_id = "bdd-" + str(uuid4())
    context.remote_url = context.config.userdata.get("base_url")
    if context.remote_url:
        if urlsplit(context.remote_url).scheme != "https":
            raise ValueError("Remote acceptance evidence requires HTTPS")
        if "local" in scenario.tags:
            scenario.skip("Local application recreation is not a remote service restart")
            return
        context.client = httpx.Client(base_url=context.remote_url, timeout=60)
    else:
        context.directory = tempfile.TemporaryDirectory(prefix="finanzen-bdd-")
        context.database_path = Path(context.directory.name) / "acceptance.db"
        open_local_client(context)


def after_scenario(context, scenario):
    if getattr(context, "client", None):
        if context.remote_url:
            context.client.close()
        else:
            context.client.__exit__(None, None, None)
    if getattr(context, "directory", None):
        context.directory.cleanup()
