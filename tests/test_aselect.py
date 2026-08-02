import asyncio
import contextlib
import turbopipes


async def test_aselect__merges_in_completion_order():
    gates = {'a': asyncio.Event(), 'b': asyncio.Event()}

    async def source(name):
        for index in range(2):
            await gates[name].wait()
            gates[name].clear()
            yield f'{name}{index}'

    stream = turbopipes.aselect({name: source(name) for name in gates})
    actual = []

    async def take(name):
        gates[name].set()
        key, task = await anext(stream)
        actual.append((key, await task))

    async with contextlib.aclosing(stream):
        # 'b' becomes ready first even though 'a' is registered first, and the merge
        # follows readiness rather than registration.
        await take('b')
        await take('a')
        await take('a')
        await take('b')

        assert actual == [
            ('b', 'b0'),
            ('a', 'a0'),
            ('a', 'a1'),
            ('b', 'b1'),
        ]

        # Both sources are spent, so the merge itself ends.
        assert [key async for key, _task in stream] == []


async def test_aselect__same_pass_completions_follow_mapping_order():
    async def source(name):
        yield name

    keys = ['c', 'a', 'b']
    stream = turbopipes.aselect({name: source(name) for name in keys})
    async with contextlib.aclosing(stream):
        actual = [key async for key, _task in stream]

    assert actual == keys


async def test_aselect__exhausted_source_does_not_end_merge():
    async def source(name, count):
        for index in range(count):
            await asyncio.sleep(0)
            yield f'{name}{index}'

    stream = turbopipes.aselect(
        {
            'short': source('short', 1),
            'long': source('long', 4),
        }
    )
    actual = []
    async with contextlib.aclosing(stream):
        async for key, task in stream:
            actual.append((key, await task))

    # 'short' drops out silently after its single item, and the merge keeps going until
    # 'long' is spent too.
    assert sorted(actual) == [
        ('long', 'long0'),
        ('long', 'long1'),
        ('long', 'long2'),
        ('long', 'long3'),
        ('short', 'short0'),
    ]


async def test_aselect__source_failure_does_not_cancel_peers():
    class MockError(Exception):
        pass

    async def good(count):
        for index in range(count):
            await asyncio.sleep(0)
            yield index * 2

    async def bad():
        await asyncio.sleep(0)
        raise MockError('Source failed')
        yield  # pragma: no cover

    stream = turbopipes.aselect({'good': good(4), 'bad': bad()})
    results = []
    errors = []
    async with contextlib.aclosing(stream):
        async for key, task in stream:
            try:
                results.append((key, await task))
            except MockError as e:
                errors.append((key, str(e)))

    assert results == [('good', 0), ('good', 2), ('good', 4), ('good', 6)]
    assert errors == [('bad', 'Source failed')]


async def test_aselect__backpressure_holds_back_a_fast_source():
    produced = []

    async def firehose():
        index = 0
        while True:
            produced.append(index)
            yield index  # never awaits, so it runs ahead if it's ever allowed to
            index += 1

    stream = turbopipes.aselect({'hose': firehose()})
    consumed = []
    async with contextlib.aclosing(stream):
        async for _key, task in stream:
            consumed.append(await task)

            # Dawdle, giving the event loop ample opportunity to let the source get
            # ahead.  It can't: its next pull isn't armed until this loop comes back
            # around for another item.
            for _ in range(10):
                await asyncio.sleep(0)
            assert produced == consumed

            if len(consumed) == 5:
                break

    assert consumed == [0, 1, 2, 3, 4]


async def test_aselect__teardown_cancels_pulls_before_closing_sources():
    # This is the case that makes `aclosing` alone insufficient.  Both quiet sources are
    # suspended at an `await` *inside their own bodies* when the consumer walks away, so
    # `ag_running` is set on them; closing them before cancelling their in-flight pulls
    # raises `RuntimeError: aclose(): asynchronous generator is already running` out of
    # the cleanup path, masking the cancellation and leaking whatever hadn't been closed
    # yet.
    closed = []
    pulling = {name: asyncio.Event() for name in ('quiet1', 'quiet2')}

    async def chatty():
        try:
            index = 0
            while True:
                yield f'chatty{index}'
                index += 1
        finally:
            closed.append('chatty')

    async def quiet(name):
        try:
            pulling[name].set()
            await asyncio.Event().wait()  # nothing ever sets it
            yield 'unreachable'  # pragma: no cover
        finally:
            closed.append(name)

    gens = {'chatty': chatty(), 'quiet1': quiet('quiet1'), 'quiet2': quiet('quiet2')}
    stream = turbopipes.aselect(gens)
    actual = []
    async with contextlib.aclosing(stream):
        async for key, task in stream:
            actual.append((key, await task))
            if len(actual) == 2:
                break  # walk away with both quiet sources mid-pull

    assert actual == [('chatty', 'chatty0'), ('chatty', 'chatty1')]
    assert all(event.is_set() for event in pulling.values())
    assert sorted(closed) == ['chatty', 'quiet1', 'quiet2']
    assert all(gen.ag_frame is None for gen in gens.values())


async def test_aselect__teardown_closes_a_source_parked_at_its_yield():
    # The other half of the teardown: a source whose item was just handed to the
    # consumer has no in-flight pull to cancel, and is only cleaned up by `aclose()`.
    closed = []

    async def source(name):
        try:
            index = 0
            while True:
                yield f'{name}{index}'
                index += 1
        finally:
            closed.append(name)

    stream = turbopipes.aselect({'only': source('only')})
    async with contextlib.aclosing(stream):
        async for _key, task in stream:
            await task
            break

    assert closed == ['only']


async def test_aselect__cancellation_closes_sources():
    closed = []
    pulling = {name: asyncio.Event() for name in ('a', 'b')}

    async def quiet(name):
        try:
            pulling[name].set()
            await asyncio.Event().wait()  # nothing ever sets it
            yield 'unreachable'  # pragma: no cover
        finally:
            closed.append(name)

    async def consume():
        stream = turbopipes.aselect({name: quiet(name) for name in pulling})
        async with contextlib.aclosing(stream):
            async for _key, _task in stream:  # pragma: no cover
                pass

    consume_task = asyncio.create_task(consume())
    for event in pulling.values():
        await event.wait()
    await asyncio.sleep(0)

    consume_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consume_task

    assert sorted(closed) == ['a', 'b']


async def test_aselect__no_sources():
    actual = [item async for item in turbopipes.aselect({})]
    assert actual == []
