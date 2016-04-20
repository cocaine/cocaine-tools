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

import os

from tornado import gen

from cocaine.tools.error import ToolsError


class TracingConfigurator(object):
    def __init__(self, configuration_service, path="/zipkin_sampling"):
        self.configuration_service = configuration_service
        self.path = path

    @gen.coroutine
    def execute(self):
        raise NotImplementedError


class TracingConfigRemove(TracingConfigurator):
    def __init__(self, name, configuration_service, path="/zipkin_sampling"):
        super(TracingConfigRemove, self).__init__(configuration_service, path)
        self.name = name

    @gen.coroutine
    def execute(self):
        abs_node_path = os.path.join(self.path, self.name)
        removed = yield (yield self.configuration_service.remove(abs_node_path, 0)).rx.get()
        if not removed:
            raise ToolsError("the value at %s was not removed" % (abs_node_path))


class TracingConfigView(TracingConfigurator):
    def __init__(self, configuration_service, path="/zipkin_sampling", name=None):
        super(TracingConfigView, self).__init__(configuration_service, path)
        self.name = name

    @gen.coroutine
    def execute(self):
        if self.name:
            res = yield self.specific(self.name)
            raise gen.Return(res)
        else:
            listing_channel = yield self.configuration_service.children_subscribe(self.path)
            _, listing = yield listing_channel.rx.get()
            listing_channel.tx.close()
            res = {}
            for node in listing:
                node_res = yield self.specific(node)
                res[node] = node_res
            raise gen.Return(res)

    @gen.coroutine
    def specific(self, name):
        channel = yield self.configuration_service.get(os.path.join(self.path, name))
        version, value = yield channel.rx.get()
        raise gen.Return({"version": version, "value": value})


def convert_tracing_config_value(value):
    try:
        return float(value)
    except ValueError as err:
        raise ToolsError("value %s must be convertable to float: %s" % (value, err))


class TracingConfigStore(TracingConfigurator):
    def __init__(self, name, value, configuration_service, path="/zipkin_sampling"):
        super(TracingConfigStore, self).__init__(configuration_service, path)
        self.name = name
        self.value = convert_tracing_config_value(value)

    @gen.coroutine
    def execute(self):
        abs_node_path = os.path.join(self.path, self.name)
        channel = yield self.configuration_service.create(abs_node_path, self.value)
        created = yield channel.rx.get()
        if not created:
            try:
                version, _ = yield (yield self.configuration_service.get(abs_node_path)).rx.get()
                saved, _ = yield (yield self.configuration_service.put(abs_node_path, self.value, version))
                if not saved:
                    raise ToolsError("the value was not stored to %s" % (abs_node_path))
            except Exception as err:
                raise ToolsError("unable to store value at %s: %s" % (abs_node_path, err))
