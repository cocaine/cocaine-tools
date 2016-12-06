from tornado import gen

from cocaine.decorators import coroutine

from . import SecurePlugin


class Promiscuous(SecurePlugin):
    def __init__(self, repo):
        super(Promiscuous, self).__init__(repo)

    def ty(self):
        return ''

    @coroutine
    def fetch_token(self):
        raise gen.Return('')
