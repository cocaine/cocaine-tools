#
# Copyright (c) 2013+ Anton Tyurin <noxiouz@yandex.ru>
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

from __future__ import division

from collections import defaultdict
import fnmatch
import os
import time
import socket

from tornado import gen

from cocaine.decorators import coroutine
from cocaine.tools.error import ToolsError

__author__ = 'Evgeny Safronov <division494@gmail.com>'


class Node(object):
    def __init__(self, node=None):
        self.node = node

    @coroutine
    def execute(self):
        raise NotImplementedError()


class Locate(object):
    def __init__(self, locator, name):
        self.locator = locator
        if not name:
            raise ValueError("option `name` must be specified")
        self.name = name

    @coroutine
    def execute(self):
        channel = yield self.locator.resolve(self.name)
        endpoints, version, api = yield channel.rx.get()
        result = {
            "endpoints": ["%s:%d" % (addr, port) for addr, port in endpoints],
            "version": version,
            "api": dict((num, method[0]) for num, method in api.items())
        }
        raise gen.Return(result)


class Cluster(object):
    def __init__(self, locator, resolve=True):
        self.locator = locator
        self.resolve = resolve

    @coroutine
    def execute(self):
        channel = yield self.locator.cluster()
        result = yield channel.rx.get()
        if self.resolve:
            raise gen.Return(result)

        converted_result = {}
        for uuid, (addr, port) in result.items():
            try:
                host = socket.gethostbyaddr(addr)[0]
                converted_result[uuid] = [host, port]
            except socket.gaierror:
                converted_result[uuid] = [addr, port]

        raise gen.Return(converted_result)


class Routing(object):
    extent = pow(2, 32)

    def __init__(self, locator, name=None):
        self.locator = locator
        self.name = name

    def generate_group(self, body):
        if len(body) == 0:
            return {}
        apps = defaultdict(int)
        # initialize with maximum value
        # from the routing
        prev = body[-1][0] - Routing.extent
        for value, app in body:
            apps[app] += (value - prev)
            prev = value

        output = dict((a, w / Routing.extent) for a, w in apps.items())
        return output

    @coroutine
    def execute(self):
        uid = "%s_%d_%f" % (socket.gethostname(), os.getpid(), time.time())
        channel = yield self.locator.routing(uid, True)
        rings = yield channel.rx.get()
        groups = {}
        if not self.name:
            for name, ring in rings.items():
                groups[name] = self.generate_group(ring)
        elif self.name in rings:
            groups[self.name] = self.generate_group(rings[self.name])
        else:
            raise ToolsError("No such group `%s` in the routing. "
                             "Probably you should refresh the locator" % self.name)

        raise gen.Return(groups)


class NodeInfo(Node):
    def __init__(self, node, locator, name=None, flags=0x1, use_wildcard=False):
        super(NodeInfo, self).__init__(node)
        self.locator = locator
        self._name = name
        self._flags = flags
        self._use_wildcard = use_wildcard

    @coroutine
    def execute(self):
        # name is provided and wildcard is switched off
        # so we use exact match
        if self._name and not self._use_wildcard:
            apps = [self._name]
        else:
            channel = yield self.node.list()
            apps = yield channel.rx.get()
            # wildcard has been already checked
            if self._name:
                apps = fnmatch.filter(apps, self._name)

        result = yield self.info(apps)
        raise gen.Return(result)

    @coroutine
    def info(self, apps):
        infos = {}
        for app in apps:
            info = ''
            try:
                channel = yield self.node.info(app, self._flags)
                info = yield channel.rx.get()
            except Exception as err:
                info = str(err)
            finally:
                infos[app] = info
        result = {
            'apps': infos
        }
        raise gen.Return(result)
