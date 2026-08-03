import asyncio
import contextlib

import pytest
import turbopipes


async def test_amerge__merges_in_completion_order():
    gates = {'a': asyncio.Event(), 'b': asyncio.Event()}

    async def source(name):
        for index in range(2):
            await gates[name].wait()
            gates[name].clear()
            yield f'{name}{index}'

    stream = turbopipes.amerge([source(name) for name in gates])
    actual = []

    async def take(name):
        gates[name].set()
        actual.append(await anext(stream))

    async with contextlib.aclosing(stream):
        # 'b' becomes ready first even though 'a' comes first in the sequence, and the
        # merge follows readiness rather than position.
        await take('b')
        await take('a')
        await take('a')
        await take('b')

        assert actual == ['b0', 'a0', 'a1', 'b1']
        assert [item async for item in stream] == []


async def test_amerge__source_failure_brings_down_the_whole_merge():
    # The defining difference from `aselect`: `amerge` is the dumb merge, and a source
    # raising ends the iteration for everyone, exactly as an `async for` over a single
    # failing generator ends it.  A consumer that wants failures isolated per source
    # wraps its sources in `ataskify` first - which is what `aselect` does.
    class MockError(Exception):
        pass

    closed = []

    async def good():
        try:
            while True:
                await asyncio.sleep(0)
                yield 'good'
        finally:
            closed.append('good')

    async def bad():
        try:
            await asyncio.sleep(0)
            raise MockError('Source failed')
            yield  # pragma: no cover
        finally:
            closed.append('bad')

    stream = turbopipes.amerge([good(), bad()])
    seen = []
    with pytest.raises(MockError, match='Source failed'):
        async with contextlib.aclosing(stream):
            async for item in stream:
                seen.append(item)

    # The innocent peer is torn down along with the merge, rather than left running.
    assert sorted(closed) == ['bad', 'good']


async def test_amerge__exhausted_source_does_not_end_merge():
    async def source(name, count):
        for index in range(count):
            await asyncio.sleep(0)
            yield f'{name}{index}'

    stream = turbopipes.amerge([source('short', 1), source('long', 3)])
    async with contextlib.aclosing(stream):
        actual = [item async for item in stream]

    assert sorted(actual) == ['long0', 'long1', 'long2', 'short0']


async def test_amerge__same_pass_completions_follow_round_robin_order():
    gates = {name: asyncio.Event() for name in ('a', 'b', 'c')}

    async def source(name):
        for index in range(2):
            await gates[name].wait()
            gates[name].clear()
            yield f'{name}{index}'

    stream = turbopipes.amerge([source(name) for name in gates])
    actual = []
    async with contextlib.aclosing(stream):
        # Serve 'a' by itself, sending it to the back of the queue behind 'b' and 'c'.
        gates['a'].set()
        actual.append(await anext(stream))

        for gate in gates.values():
            gate.set()
        for _ in range(3):
            actual.append(await anext(stream))

    assert actual == ['a0', 'b0', 'c0', 'a1']


async def test_amerge__backpressure_holds_back_a_fast_source():
    produced = []

    async def firehose():
        index = 0
        while True:
            produced.append(index)
            yield index  # never awaits, so it runs ahead if it's ever allowed to
            index += 1

    stream = turbopipes.amerge([firehose()])
    consumed = []
    async with contextlib.aclosing(stream):
        async for item in stream:
            consumed.append(item)
            for _ in range(10):
                await asyncio.sleep(0)
            assert produced == consumed

            if len(consumed) == 5:
                break

    assert consumed == [0, 1, 2, 3, 4]


async def test_amerge__teardown_cancels_pulls_before_closing_sources():
    closed = []

    async def chatty():
        try:
            while True:
                yield 'chatty'
        finally:
            closed.append('chatty')

    async def quiet(name):
        try:
            await asyncio.Event().wait()  # nothing ever sets it
            yield 'unreachable'  # pragma: no cover
        finally:
            closed.append(name)

    gens = [chatty(), quiet('quiet1'), quiet('quiet2')]
    stream = turbopipes.amerge(gens)
    async with contextlib.aclosing(stream):
        async for _item in stream:
            break  # walk away with both quiet sources mid-pull

    assert sorted(closed) == ['chatty', 'quiet1', 'quiet2']
    assert all(gen.ag_frame is None for gen in gens)


async def test_amerge__cancellation_closes_sources():
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
        stream = turbopipes.amerge([quiet(name) for name in pulling])
        async with contextlib.aclosing(stream):
            async for _item in stream:  # pragma: no cover
                pass

    consume_task = asyncio.create_task(consume())
    for event in pulling.values():
        await event.wait()
    await asyncio.sleep(0)

    consume_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consume_task

    assert sorted(closed) == ['a', 'b']


async def test_amerge__close_propagates_the_last_source_cleanup_failure():
    # A source that fails its *own* `aclose()` reaches whoever closed the merge, having
    # travelled up through five nested `aclosing` scopes - `amerge`'s own, plus one per
    # source inside `atag` and another inside `ataskify`.  Exercised through the full
    # `amerge` -> `atag` -> `ataskify` -> source composition rather than against
    # `aclosing_all` directly, since the layering is what could break it: the failure
    # has to survive being re-raised out of three intermediate generator frames.
    class MockError(Exception):
        pass

    closed = []

    def make_source(name):
        async def source():
            try:
                while True:
                    await asyncio.sleep(0)
                    yield name
            finally:
                closed.append(name)
                raise MockError(f'cleanup {name}')

        return source()

    sources = [make_source('a'), make_source('b')]
    stream = turbopipes.amerge(
        [
            turbopipes.atag(name, turbopipes.ataskify(gen, label=name))
            for name, gen in zip('ab', sources)
        ]
    )

    with pytest.raises(MockError) as excinfo:
        async with contextlib.aclosing(stream):
            async for _key, task in stream:
                await task
                break  # walk away early, leaving both sources parked at their yields

    # Both are closed even though the first close raised; only the last failure raised
    # survives, rather than being chained onto the others.
    assert sorted(closed) == ['a', 'b']
    assert str(excinfo.value) == f'cleanup {closed[-1]}'


async def test_amerge__cancelled_consumer_surfaces_a_source_cleanup_failure():
    # The sharp edge the README warns about before running a merge under a timeout: a
    # source failing its own `aclose()` displaces the `CancelledError` that was doing
    # the unwinding, so the cancelled task reports itself as *not* cancelled and raises
    # the source's exception instead.  Nothing else in the suite pins this.
    class MockError(Exception):
        pass

    consuming = asyncio.Event()

    async def source():
        try:
            yield 'item'
            await asyncio.Event().wait()  # pragma: no cover - cancelled first
        finally:
            raise MockError('cleanup a')

    async def consume():
        stream = turbopipes.amerge(
            [turbopipes.atag('a', turbopipes.ataskify(source(), label='a'))]
        )
        async with contextlib.aclosing(stream):
            async for key, task in stream:
                assert key == 'a'  # the tag survives the chain it rode up
                await task
                consuming.set()
                # Park the consumer with the source idle at its `yield` and no pull in
                # flight, so the cancellation tears down via `aclose()` rather than via
                # a cancelled pull - the two routes differ, and only this one raises.
                await asyncio.Event().wait()

    consume_task = asyncio.create_task(consume())
    await consuming.wait()

    consume_task.cancel()
    with pytest.raises(MockError, match='cleanup a'):
        await consume_task

    # The consumer was cancelled, yet doesn't look it - the source's failure took the
    # place of the `CancelledError`, which is exactly why a `TimeoutError` can go
    # missing around a merge.
    assert not consume_task.cancelled()


async def test_amerge__accepts_any_iterable_of_generators():
    # Taking an `Iterable` rather than a `Sequence` means a generator expression works,
    # which is the shape the composition in `aselect` naturally produces.
    async def source(name):
        yield name

    stream = turbopipes.amerge(source(name) for name in ('a', 'b'))
    async with contextlib.aclosing(stream):
        actual = [item async for item in stream]

    assert sorted(actual) == ['a', 'b']


async def test_amerge__no_sources():
    assert [item async for item in turbopipes.amerge([])] == []
