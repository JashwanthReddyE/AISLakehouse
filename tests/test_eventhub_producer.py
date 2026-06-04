from ingestion.eventhub_producer import EventHubFrameProducer, extract_mmsi


def test_extract_mmsi_present():
    assert extract_mmsi('{"MetaData":{"MMSI":563012345}}') == "563012345"


def test_extract_mmsi_missing_or_bad():
    assert extract_mmsi('{"MetaData":{}}') is None
    assert extract_mmsi("not json") is None
    assert extract_mmsi("[1,2,3]") is None


class FakeClient:
    """Captures send_batch calls so we can assert grouping/partition keys."""

    def __init__(self):
        self.batches = []  # list of (partition_key, [body, ...])
        self.closed = False

    async def send_batch(self, events, partition_key=None):
        self.batches.append((partition_key, [e.body_as_str() for e in events]))

    async def close(self):
        self.closed = True


async def test_flush_groups_by_mmsi_partition_key():
    client = FakeClient()
    producer = EventHubFrameProducer(client, max_buffer=100, max_wait_s=999)

    await producer.handle('{"MetaData":{"MMSI":111}}', "t1")
    await producer.handle('{"MetaData":{"MMSI":222}}', "t2")
    await producer.handle('{"MetaData":{"MMSI":111}}', "t3")
    await producer.handle('{"MetaData":{}}', "t4")  # no MMSI -> partition_key None
    await producer.flush()

    by_key = {pk: bodies for pk, bodies in client.batches}
    assert len(by_key["111"]) == 2
    assert len(by_key["222"]) == 1
    assert None in by_key  # frame without MMSI sent with no partition key


async def test_handle_autoflushes_on_buffer_full():
    client = FakeClient()
    producer = EventHubFrameProducer(client, max_buffer=2, max_wait_s=999)

    await producer.handle('{"MetaData":{"MMSI":111}}', "t1")
    assert client.batches == []  # not full yet
    await producer.handle('{"MetaData":{"MMSI":111}}', "t2")
    assert client.batches  # buffer hit max_buffer -> flushed


async def test_close_drains_and_closes_client():
    client = FakeClient()
    producer = EventHubFrameProducer(client, max_buffer=100, max_wait_s=999)
    await producer.handle('{"MetaData":{"MMSI":111}}', "t1")
    await producer.close()
    assert client.closed
    assert client.batches  # buffered frame was drained on close
