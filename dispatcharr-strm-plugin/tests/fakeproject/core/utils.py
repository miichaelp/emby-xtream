import fakeredis

_client = fakeredis.FakeStrictRedis(decode_responses=True)


class RedisClient:
    @staticmethod
    def get_client():
        return _client
