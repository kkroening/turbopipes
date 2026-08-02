import asyncio
import contextlib

import turbopipes


async def test_ataskify__yields_completed_tasks():
    async def source():
        for index in range(3):
            await asyncio.sleep(0)
            yield index

    stream = turbopipes.ataskify(source())
    actual = []
    async with contextlib.aclosing(stream):
        async for task in stream:
            # Already complete by the time it's yielded: readiness is observable before
            # the value is, which is what lets a merge order by it.
            assert task.done()
            actual.append(await task)

    assert actual == [0, 1, 2]


async def test_ataskify__source_failure_surfaces_at_the_await():
    # The whole point of the indirection: the failure is the consumer's to handle, and
    # arrives as a task that raises rather than as an exception ending the iteration.
    class MockError(Exception):
        pass

    async def source():
        yield 'first'
        raise MockError('Source failed')

    stream = turbopipes.ataskify(source())
    seen = []
    errors = []
    async with contextlib.aclosing(stream):
        async for task in stream:
            try:
                seen.append(await task)
            except MockError as e:
                errors.append(str(e))

    assert seen == ['first']
    assert errors == ['Source failed']


async def test_ataskify__exhaustion_ends_iteration_rather_than_yielding_a_task():
    # A spent source never comes out as a task carrying `StopAsyncIteration`; exhaustion
    # stays inside the iteration protocol.
    async def source():
        yield 'only'

    stream = turbopipes.ataskify(source())
    async with contextlib.aclosing(stream):
        tasks = [task async for task in stream]

    assert len(tasks) == 1
    assert await tasks[0] == 'only'


async def test_ataskify__backpressure_holds_back_a_fast_source():
    produced = []

    async def firehose():
        index = 0
        while True:
            produced.append(index)
            yield index
            index += 1

    stream = turbopipes.ataskify(firehose())
    consumed = []
    async with contextlib.aclosing(stream):
        async for task in stream:
            consumed.append(await task)
            for _ in range(10):
                await asyncio.sleep(0)
            assert produced == consumed

            if len(consumed) == 3:
                break

    assert consumed == [0, 1, 2]


async def test_ataskify__teardown_closes_a_mid_pull_source():
    # Closing this generator while it's suspended waiting on a pull has to reach the
    # source underneath, which can only be closed once its pull has been cancelled.
    closed = []

    async def quiet():
        try:
            await asyncio.Event().wait()  # nothing ever sets it
            yield 'unreachable'  # pragma: no cover
        finally:
            closed.append('quiet')

    gen = quiet()
    stream = turbopipes.ataskify(gen)
    pull = asyncio.create_task(anext(stream))
    for _ in range(3):
        await asyncio.sleep(0)  # let it reach the source's `await`

    pull.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await pull
    await stream.aclose()

    assert closed == ['quiet']
    assert gen.ag_frame is None


async def test_ataskify__teardown_closes_a_source_parked_at_its_yield():
    closed = []

    async def source():
        try:
            while True:
                yield 'item'
        finally:
            closed.append('source')

    stream = turbopipes.ataskify(source())
    async with contextlib.aclosing(stream):
        async for task in stream:
            await task
            break

    assert closed == ['source']


async def test_ataskify__does_not_settle_a_pull_the_consumer_already_holds():
    # A yielded task belongs to the consumer, who may not have awaited it yet.  Settling
    # it on their behalf would retrieve - and so swallow - a failure they were entitled
    # to see.
    class MockError(Exception):
        pass

    async def source():
        yield 'doomed'
        raise MockError('Source failed')  # pragma: no cover

    stream = turbopipes.ataskify(source())
    held = None
    async with contextlib.aclosing(stream):
        async for task in stream:
            held = task
            break  # walk away holding an un-awaited task

    assert held is not None
    assert await held == 'doomed'


async def test_ataskify__labels_a_mid_pull_cleanup_failure():
    class MockError(Exception):
        pass

    async def quiet():
        try:
            await asyncio.Event().wait()  # nothing ever sets it
            yield 'unreachable'  # pragma: no cover
        finally:
            raise MockError('Cleanup failed')

    reported = []

    def handle_exception(_loop, context):
        reported.append(context)

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(handle_exception)
    try:
        stream = turbopipes.ataskify(quiet(), label='quiet')
        pull = asyncio.create_task(anext(stream))
        for _ in range(3):
            await asyncio.sleep(0)
        pull.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pull
        await stream.aclose()
    finally:
        loop.set_exception_handler(previous_handler)

    assert len(reported) == 1
    assert isinstance(reported[0]['exception'], MockError)
    assert "'quiet'" in reported[0]['message']


async def test_ataskify__reports_without_a_label_when_none_is_given():
    # The keyless case: the failure is still surfaced, it just can't say which source it
    # was.  That is the cost of `ataskify` being the only layer positioned to see it.
    class MockError(Exception):
        pass

    async def quiet():
        try:
            await asyncio.Event().wait()  # nothing ever sets it
            yield 'unreachable'  # pragma: no cover
        finally:
            raise MockError('Cleanup failed')

    reported = []

    def handle_exception(_loop, context):
        reported.append(context)

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(handle_exception)
    try:
        stream = turbopipes.ataskify(quiet())
        pull = asyncio.create_task(anext(stream))
        for _ in range(3):
            await asyncio.sleep(0)
        pull.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pull
        await stream.aclose()
    finally:
        loop.set_exception_handler(previous_handler)

    assert len(reported) == 1
    assert 'a source raised' in reported[0]['message']


async def test_ataskify__never_advanced_leaves_the_source_untouched():
    async def source():
        yield 'unreachable'  # pragma: no cover

    gen = source()
    stream = turbopipes.ataskify(gen)
    await stream.aclose()

    # Ownership begins with iteration, so the source is still the caller's to close.
    assert gen.ag_frame is not None
    await gen.aclose()
