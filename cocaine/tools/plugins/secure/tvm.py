import logging
import socket

from tornado import gen

from cocaine.decorators import coroutine
from . import SecurePlugin


log = logging.getLogger(__name__)


class TVM(SecurePlugin):
    def __init__(self, repo, oauth):
        super(TVM, self).__init__(repo)
        self._oauth = oauth
        self._ip = socket.gethostbyname('localhost')
        self._tvm = repo.create_service('tvm')

    def ty(self):
        return 'TVM'

    @coroutine
    def fetch_token(self):
        channel = yield self._tvm.ticket('oauth', {
            'type': 'oauth',
            'userip': self._ip,
            'oauth_token': self._oauth,
        })
        ticket = yield channel.rx.get()
        log.debug('exchanged OAUTH token with TVM ticket')
        raise gen.Return(self._make_header(ticket))

    def _make_header(self, ticket):
        return '{} {}'.format(self.ty(), ticket)

