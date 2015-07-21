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

from tornado import gen


from cocaine.decorators import coroutine

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
    def __init__(self, locator):
        self.locator = locator

    @coroutine
    def execute(self):
        ch = yield self.locator.cluster()
        result = yield ch.rx.get()
        raise gen.Return(result)


class NodeInfo(Node):
    def __init__(self, node, locator, name=None, flags=0x1):
        super(NodeInfo, self).__init__(node)
        self.locator = locator
        self._name = name
        self._flags = flags

    @coroutine
    def execute(self):
        if self._name:
            apps = [self._name]
        else:
            channel = yield self.node.list()
            apps = yield channel.rx.get()
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
