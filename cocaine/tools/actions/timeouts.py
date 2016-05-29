
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

from cocaine.exceptions import ServiceError
from cocaine.tools.error import ToolsError


class TimeoutsConfigurator(object):
    def __init__(self, configuration_service, path="/proxy_apps_timeouts"):
        self.configuration_service = configuration_service
        self.path = path

    @gen.coroutine
    def execute(self):
        raise NotImplementedError


class TimeoutsConfigDrop(TimeoutsConfigurator):
    def __init__(self, configuration_service, name, path="/proxy_apps_timeouts"):
        super(TimeoutsConfigDrop, self).__init__(configuration_service, path)
        self.name = name

    @gen.coroutine
    def execute(self):
        abs_node_path = os.path.join(self.path, self.name)
        removed = yield (yield self.configuration_service.remove(abs_node_path, -1)).rx.get()
        if not removed:
            raise ToolsError("the value at %s was not removed" % (abs_node_path))


class TimeoutsConfigRemove(TimeoutsConfigurator):
    def __init__(self, configuration_service, name, event='', path="/proxy_apps_timeouts"):
        super(TimeoutsConfigRemove, self).__init__(configuration_service, path)
        self.name = name
        self.event = event

    @gen.coroutine
    def execute(self):
        abs_node_path = os.path.join(self.path, self.name)
        try:
            actual, version = yield (yield self.configuration_service.get(abs_node_path)).rx.get()
            if actual is None or self.event not in actual:
                return
            actual.pop(self.event)
            saved, _ = yield (yield self.configuration_service.put(abs_node_path, actual, version)).rx.get()
            if not saved:
                raise ToolsError("the value was not stored to %s" % (abs_node_path))
        except Exception as err:
            raise ToolsError("unable to store value at %s: %s" % (abs_node_path, err))


class TimeoutsConfigView(TimeoutsConfigurator):
    def __init__(self, configuration_service, path="/proxy_apps_timeouts", name=None):
        super(TimeoutsConfigView, self).__init__(configuration_service, path)
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
        value, version = yield channel.rx.get()
        raise gen.Return({"version": version, "value": value})


class TimeoutsConfigStore(TimeoutsConfigurator):
    def __init__(self, configuration_service, name, value, event='', path="/proxy_apps_timeouts"):
        super(TimeoutsConfigStore, self).__init__(configuration_service, path)
        self.name = name
        self.value = value
        self.event = event

    @gen.coroutine
    def execute(self):
        abs_node_path = os.path.join(self.path, self.name)
        try:
            val = {self.event: self.value}
            channel = yield self.configuration_service.create(abs_node_path, val)
            yield channel.rx.get()
            raise gen.Return(val)
        except ServiceError:
            try:
                actual, version = yield (yield self.configuration_service.get(abs_node_path)).rx.get()
                actual[self.event] = self.value
                saved, _ = yield (yield self.configuration_service.put(abs_node_path, actual, version)).rx.get()
                if not saved:
                    raise ToolsError("the value was not stored to %s" % (abs_node_path))
            except Exception as err:
                raise ToolsError("unable to store value at %s: %s" % (abs_node_path, err))
            else:
                raise gen.Return(actual)
