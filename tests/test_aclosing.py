import asyncio
import contextlib

import pytest
import turbopipes


async def test_aclosing_all__closes_every_generator():
    closed = []

    async def source(name):
        try:
            yield name
        finally:
            closed.append(name)

    gens = [source(name) for name in ('a', 'b', 'c')]
    for gen in gens:
        await anext(gen)  # park each at its `yield`, so closing has something to run

    async with turbopipes.aclosing_all(gens):
        pass

    assert sorted(closed) == ['a', 'b', 'c']


async def test_aclosing_all__closes_the_rest_after_one_close_raises():
    # Every generator is closed regardless, and one failure propagates once the rest
    # have been dealt with.  Nested `aclosing` blocks behave the same way, so this pins
    # `aclosing_all`'s own contract rather than a contrast with them; what it has over
    # nesting is dynamic arity, which no assertion here can express.  A sequential
    # `for gen in gens: await gen.aclose()` is what this rules out.
    class MockError(Exception):
        pass

    closed = []

    async def source(name):
        try:
            yield name
        finally:
            closed.append(name)

    async def bad():
        try:
            yield 'bad'
        finally:
            raise MockError('Cleanup failed')

    gens = [source('a'), bad(), source('b')]
    for gen in gens:
        await anext(gen)  # park each at its `yield`, so closing has something to do

    with pytest.raises(MockError):
        async with turbopipes.aclosing_all(gens):
            pass

    assert sorted(closed) == ['a', 'b']


async def test_aclosing_all__never_advanced_generators_are_still_closed():
    # `aclose()` on a generator that was never started is a no-op rather than an error,
    # so bulk-closing a mixture of started and unstarted sources is safe.
    async def source():
        yield 'unreachable'  # pragma: no cover

    gen = source()
    async with turbopipes.aclosing_all([gen]):
        pass

    assert gen.ag_frame is None


async def test_asettle__cancels_in_flight_tasks():
    cancelled = []

    async def block(name):
        try:
            await asyncio.Event().wait()  # nothing ever sets it
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    tasks = [asyncio.create_task(block(name)) for name in ('a', 'b')]
    for _ in range(3):
        await asyncio.sleep(0)  # let them reach their `await`

    await turbopipes.asettle(tasks)

    assert sorted(cancelled) == ['a', 'b']
    assert all(task.done() for task in tasks)


async def test_asettle__retrieves_completed_task_outcomes():
    # A task that finished before the settling reached it is awaited rather than
    # cancelled, so that whatever it holds is retrieved.  An unretrieved exception here
    # would surface later as an "exception was never retrieved" report from the loop.
    class MockError(Exception):
        pass

    async def fails():
        raise MockError('Task failed')

    task = asyncio.create_task(fails())
    for _ in range(3):
        await asyncio.sleep(0)
    assert task.done()

    await turbopipes.asettle([task])  # absorbed, not raised

    assert isinstance(task.exception(), MockError)


async def test_asettle__reports_a_cleanup_failure_with_its_label():
    class MockError(Exception):
        pass

    async def block():
        try:
            await asyncio.Event().wait()  # nothing ever sets it
        finally:
            raise MockError('Cleanup failed')

    reported = []

    def handle_exception(_loop, context):
        reported.append(context)

    task = asyncio.create_task(block())
    for _ in range(3):
        await asyncio.sleep(0)

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(handle_exception)
    try:
        await turbopipes.asettle([task], label='quiet')
    finally:
        loop.set_exception_handler(previous_handler)

    assert len(reported) == 1
    assert isinstance(reported[0]['exception'], MockError)
    assert "'quiet'" in reported[0]['message']


async def test_asettle__does_not_report_a_plain_cancellation():
    async def block():
        await asyncio.Event().wait()  # nothing ever sets it

    reported = []

    def handle_exception(_loop, context):
        reported.append(context)

    task = asyncio.create_task(block())
    for _ in range(3):
        await asyncio.sleep(0)

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(handle_exception)
    try:
        await turbopipes.asettle([task])
    finally:
        loop.set_exception_handler(previous_handler)

    assert reported == []


async def test_asettle__does_not_report_an_already_completed_failure():
    # The counterpart: a task that had already failed on its own account carries an
    # ordinary failure that somebody walked away from, not a cleanup failure, and
    # reporting it as one would be a false log line.
    class MockError(Exception):
        pass

    async def fails():
        raise MockError('Task failed')

    reported = []

    def handle_exception(_loop, context):
        reported.append(context)

    task = asyncio.create_task(fails())
    for _ in range(3):
        await asyncio.sleep(0)

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(handle_exception)
    try:
        await turbopipes.asettle([task], label='flaky')
    finally:
        loop.set_exception_handler(previous_handler)

    assert reported == []


async def test_asettle__no_tasks():
    await turbopipes.asettle([])


async def test_aclosing_all__and_asettle__compose_in_the_documented_order():
    # The arrangement the two are meant to be used in: settling happens inside the
    # block, so it runs *before* the closes.  Nesting them the other way leaves the
    # mid-pull source unclosable, which is the mistake the pairing exists to prevent.
    closed = []

    async def quiet():
        try:
            await asyncio.Event().wait()  # nothing ever sets it
            yield 'unreachable'  # pragma: no cover
        finally:
            closed.append('quiet')

    gen = quiet()
    pull = asyncio.create_task(anext(gen))
    for _ in range(3):
        await asyncio.sleep(0)  # let the pull reach the source's `await`

    async with turbopipes.aclosing_all([gen]):
        try:
            pass
        finally:
            await turbopipes.asettle([pull])

    assert closed == ['quiet']
    assert gen.ag_frame is None


async def test_aclosing_all__without_settling_cannot_close_a_mid_pull_source():
    # The negative of the test above, pinned so that the ordering requirement is a
    # property of the code rather than a claim in a docstring.
    async def quiet():
        await asyncio.Event().wait()  # nothing ever sets it
        yield 'unreachable'  # pragma: no cover

    gen = quiet()
    pull = asyncio.create_task(anext(gen))
    for _ in range(3):
        await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match='already running'):
        async with turbopipes.aclosing_all([gen]):
            pass

    pull.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await pull
