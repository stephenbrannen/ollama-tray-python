from collections.abc import Iterator
from datetime import datetime

from pytest import fixture
from responses import RequestsMock

from ollama_tray.client import LoadedModel

# The `.test` top-level domain never resolves, so a request that escapes the mock fails fast.
HOST = "http://ollama.test:11434"


def make_model(
    name: str, size_vram: int = 4_700_000_000, expires_at: datetime | None = None
) -> LoadedModel:
    return LoadedModel(name=name, size_vram=size_vram, expires_at=expires_at)


@fixture
def responses() -> Iterator[RequestsMock]:
    with RequestsMock() as mock:
        yield mock
