from collections.abc import AsyncIterator
from typing import TypeVar

_T = TypeVar('_T')


async def aiter_chunks(
    it: AsyncIterator[_T],
    chunk_size: int,
) -> AsyncIterator[list[_T]]:
    """Yields results from an async iterator in chunks of a particular size."""
    chunk = []
    async for item in it:
        chunk.append(item)
        if len(chunk) == chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
