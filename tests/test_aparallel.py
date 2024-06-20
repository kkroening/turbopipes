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
