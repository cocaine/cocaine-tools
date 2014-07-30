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
from tornado.netutil import Resolver
from tornado import gen
from tornado.simple_httpclient import SimpleAsyncHTTPClient

__author__ = 'Evgeny Safronov <division494@gmail.com>'


class UnixResolver(Resolver):
    def initialize(self, resolver, sockpath):
        self.resolver = resolver
        self.sockpath = sockpath.replace('unix:/', '')

    def close(self):
        self.resolver.close()

    @gen.coroutine
    def resolve(self, host, port, *args, **kwargs):
        raise gen.Return([(socket.AF_UNIX, self.sockpath)])


class AsyncUnixHTTPClient(SimpleAsyncHTTPClient):
    def __init__(self, io_loop, prefix):
        self._prefix = prefix
        unix_resolver = UnixResolver(resolver=Resolver(), sockpath=prefix)
        super(AsyncUnixHTTPClient, self).initialize(io_loop, resolver=unix_resolver)
