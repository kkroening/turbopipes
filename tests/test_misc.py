import turbopipes


async def test_aiter_chunks():
    async def generate(count):
        for x in range(count):
            yield x

    actual = []
    async for chunk in turbopipes.aiter_chunks(generate(13), 5):
        actual.append(chunk)
    expected = [
        list(range(5)),
        list(range(5, 10)),
        list(range(10, 13)),
    ]
    assert actual == expected
