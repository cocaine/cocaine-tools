#
# Copyright (c) 2013+ Evgeny Safronov <division494@gmail.com>
# Copyright (c) 2011-2014 Other contributors as noted in the AUTHORS file.
#
# This file is part of Cocaine-tools.
#
# Cocaine is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# Cocaine is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

import socket
from tornado.iostream import IOStream
from tornado.simple_httpclient import _HTTPConnection, SimpleAsyncHTTPClient

__author__ = 'Evgeny Safronov <division494@gmail.com>'


class _UnixHTTPConnection(_HTTPConnection):
    def __init__(self, prefix, io_loop, client, request, release_callback, final_callback, max_buffer_size):
        path = prefix.replace('unix:/', '')
        prefix_id = request.url.index(prefix)
        request.url = 'http://localhost{0}'.format(request.url[prefix_id + len(prefix):])

        class NoneResolver(object):
            def resolve(self, host, port, af, callback):
                io_loop.add_callback(callback, ((socket.AF_UNIX, path),))
        super(_UnixHTTPConnection, self).__init__(io_loop, client, request, release_callback, final_callback,
                                                  max_buffer_size, NoneResolver())
        self.parsed_hostname = prefix

    def _create_stream(self, addrinfo):
        sock = socket.socket(socket.AF_UNIX)
        return IOStream(sock, io_loop=self.io_loop, max_buffer_size=self.max_buffer_size)


class AsyncUnixHTTPClient(SimpleAsyncHTTPClient):
    def __init__(self, io_loop, prefix):
        self._prefix = prefix
        super(AsyncUnixHTTPClient, self).__init__(io_loop)

    def _handle_request(self, request, release_callback, final_callback):
        _UnixHTTPConnection(self._prefix, self.io_loop, self, request, release_callback, final_callback,
                            self.max_buffer_size)
