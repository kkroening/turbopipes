import asyncio
import turbopipes


async def test_aparallel__happy_path():
    max_concurrent = 3
    start_event = asyncio.Event()
    tasks_started = 0
    tasks_completed = []
    task_count = 10

    async def mock_task(index: int) -> int:
        nonlocal tasks_started
        tasks_started += 1
        await start_event.wait()
        tasks_completed.append(index)
        return index * 2

    async def mock_generator():
        for index in range(task_count):
            yield mock_task(index)

    async def collect_results():
        results = []
        async for task in turbopipes.aparallel(mock_generator(), max_concurrent):
            result = await task
            results.append(result)
        return results

    # Run the parallelize function in the background
    collect_task = asyncio.create_task(collect_results())

    # Ensure tasks have started but not completed
    while tasks_started < max_concurrent:
        await asyncio.sleep(0)  # Yield control to the event loop

    assert tasks_started == max_concurrent
    assert len(tasks_completed) == 0

    # Allow tasks to complete
    start_event.set()
    results = await collect_task

    # Verify all tasks completed and returned the expected results
    expected_results = [i * 2 for i in range(task_count)]
    assert sorted(results) == expected_results

    # Verify the correct number of tasks were completed
    assert sorted(tasks_completed) == list(range(task_count))


async def test_aparallel__task_failure_does_not_cancel_peers():
    max_concurrent = 3
    start_event = asyncio.Event()
    tasks_started = 0
    tasks_completed = []
    tasks_failed = []
    task_count = 5  # We'll make task index 2 fail

    class MockError(Exception):
        pass

    async def mock_task(index: int) -> int:
        nonlocal tasks_started
        tasks_started += 1
        await start_event.wait()

        if index == 2:
            tasks_failed.append(index)
            raise MockError(f'Task {index} failed')

        tasks_completed.append(index)
        return index * 2

    async def mock_generator():
        for index in range(task_count):
            yield mock_task(index)

    async def collect_results():
        results = []
        errors = []

        # Consume the pipeline. We expect to get all 5 tasks back.
        async for task in turbopipes.aparallel(mock_generator(), max_concurrent):
            try:
                result = await task
                results.append(result)
            except MockError as e:
                errors.append(str(e))

        return results, errors

    # Run the parallelize function in the background
    collect_task = asyncio.create_task(collect_results())

    # Ensure the first batch of tasks has started (indices 0, 1, 2)
    while tasks_started < max_concurrent:
        await asyncio.sleep(0)  # Yield control to the event loop

    assert tasks_started == max_concurrent
    assert len(tasks_completed) == 0

    # Allow tasks to complete
    start_event.set()
    results, errors = await collect_task

    # Verify the successful tasks completed and returned expected results
    # Indices 0, 1, 3, 4 should succeed. (0*2=0, 1*2=2, 3*2=6, 4*2=8)
    expected_results = [0, 2, 6, 8]
    assert sorted(results) == expected_results

    # Verify the failure was caught and yielded gracefully
    assert len(errors) == 1
    assert errors[0] == 'Task 2 failed'

    # Verify the internal state of the mocks to ensure no tasks were skipped or abruptly
    # cancelled
    assert sorted(tasks_completed) == [0, 1, 3, 4]
    assert tasks_failed == [2]
