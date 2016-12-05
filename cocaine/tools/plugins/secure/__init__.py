from cocaine.decorators import coroutine


class SecurePlugin(object):
    def __init__(self, repo):
        pass

    def ty(self):
        raise NotImplementedError

    @coroutine
    def fetch_token(self):
        raise NotImplementedError
