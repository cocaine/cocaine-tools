import logging
import socket

from tornado import gen

from cocaine.decorators import coroutine
from cocaine.tools.error import ToolsError

from . import SecurePlugin


log = logging.getLogger(__name__)


class TVM(SecurePlugin):
    def __init__(self, repo, client_id, client_secret):
        super(TVM, self).__init__(repo)
        self._client_id = client_id
        self._client_secret = client_secret

        endpoints = socket.getaddrinfo(socket.gethostname(), None)
        if len(endpoints) == 0:
            raise ToolsError('failed to determine local IP address')

        self._ip = endpoints[0][4][0]
        self._tvm = repo.create_service('tvm')

    def ty(self):
        return 'TVM'

    @coroutine
    def fetch_token(self):
        grant_type = 'client_credentials'

        channel = yield self._tvm.ticket_full(self._client_id, self._client_secret, grant_type, {})
        ticket = yield channel.rx.get()
        log.debug('exchanged client secret with TVM ticket')
        raise gen.Return(self._make_header(ticket))

    def _make_header(self, ticket):
        return '{} {}'.format(self.ty(), ticket)
