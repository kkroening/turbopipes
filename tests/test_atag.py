import asyncio
import contextlib

import turbopipes


async def test_atag__attaches_the_key_to_every_item():
    async def source():
        for index in range(3):
            yield index

    stream = turbopipes.atag('src', source())
    async with contextlib.aclosing(stream):
        actual = [item async for item in stream]

    assert actual == [('src', 0), ('src', 1), ('src', 2)]


async def test_atag__is_indifferent_to_what_it_tags():
    # Nothing about `atag` is aselect-specific; tagging a task-yielding generator is
    # just the case that matters most, because it keeps the key readable before the
    # value exists.
    async def source():
        yield 'value'

    stream = turbopipes.atag(7, turbopipes.ataskify(source()))
    async with contextlib.aclosing(stream):
        async for key, task in stream:
            assert key == 7  # ...known before the `await` below, which is the point
            assert await task == 'value'


async def test_atag__closes_the_generator_it_wraps():
    closed = []

    async def source():
        try:
            while True:
                yield 'item'
        finally:
            closed.append('source')

    stream = turbopipes.atag('src', source())
    async with contextlib.aclosing(stream):
        async for _item in stream:
            break

    assert closed == ['source']


async def test_atag__cancellation_reaches_the_generator_it_wraps():
    # `atag` holds no tasks of its own: a cancellation arriving while it waits is
    # delivered into the wrapped generator's body, and whatever cleanup lives there runs
    # on its own account.  That is what lets a chain unwind from one cancellation.
    closed = []

    async def quiet():
        try:
            await asyncio.Event().wait()  # nothing ever sets it
            yield 'unreachable'  # pragma: no cover
        finally:
            closed.append('quiet')

    stream = turbopipes.atag('src', quiet())
    pull = asyncio.create_task(anext(stream))
    for _ in range(3):
        await asyncio.sleep(0)

    pull.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await pull
    await stream.aclose()

    assert closed == ['quiet']


async def test_atag__never_advanced_leaves_the_generator_untouched():
    async def source():
        yield 'unreachable'  # pragma: no cover

    gen = source()
    stream = turbopipes.atag('src', gen)
    await stream.aclose()

    assert gen.ag_frame is not None
    await gen.aclose()


async def test_atag__empty_source():
    async def source():
        return
        yield  # pragma: no cover

    stream = turbopipes.atag('src', source())
    async with contextlib.aclosing(stream):
        assert [item async for item in stream] == []
