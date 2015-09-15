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

import msgpack

from cocaine.decorators import coroutine

from cocaine.tools.error import ToolsError
from cocaine.tools import actions
from cocaine.tools.actions import CocaineConfigReader, log
from cocaine.tools.tags import GROUPS_TAGS

__author__ = 'EvgenySafronov <division494@gmail.com>'

GROUP_COLLECTION = 'groups'


class MalformedGroup(ValueError):
    pass


class GroupWithZeroTotalWeight(MalformedGroup):
    pass


def validate_routing_group(group):
    accepted_value_types = (int, long)

    if not group:
        raise GroupWithZeroTotalWeight("routing group must not be empty")

    if not sum(group.values()):
        raise GroupWithZeroTotalWeight("routing group must have non-zero total weight")

    if not all(((isinstance(value, accepted_value_types) and value >= 0)
                for value in group.values())):
        raise MalformedGroup("weight must be positive integer value")


class Specific(actions.Specific):
    def __init__(self, storage, name):
        super(Specific, self).__init__(storage, 'group', name)


class List(actions.List):
    def __init__(self, storage):
        super(List, self).__init__(GROUP_COLLECTION, GROUPS_TAGS, storage)


class View(actions.View):
    def __init__(self, storage, name):
        super(View, self).__init__(storage, GROUP_COLLECTION, name, 'groups')


class Create(actions.Specific):
    def __init__(self, storage, name, content):
        super(Create, self).__init__(storage, 'group', name)
        self.content = CocaineConfigReader.load(content, validate=validate_routing_group)

    @coroutine
    def execute(self):
        channel = yield self.storage.write(GROUP_COLLECTION, self.name, self.content, GROUPS_TAGS)
        yield channel.rx.get()


class Remove(actions.Specific):
    def __init__(self, storage, name):
        super(Remove, self).__init__(storage, 'group', name)

    @coroutine
    def execute(self):
        channel = yield self.storage.remove(GROUP_COLLECTION, self.name)
        yield channel.rx.get()


class Copy(Specific):
    def __init__(self, storage, name, copyname):
        super(Copy, self).__init__(storage, name)
        self.copyname = copyname
        if self.name == self.copyname:
            raise ToolsError("unable to copy an instance to itself")

    @coroutine
    def execute(self):
        log.info('Rename "%s" to "%s"', self.name, self.copyname)
        oldprofile = yield View(self.storage, self.name).execute()
        yield Create(self.storage, self.copyname, oldprofile).execute()


class Rename(Copy):
    @coroutine
    def execute(self):
        yield super(Rename, self).execute()
        yield Remove(self.storage, self.name).execute()


class Refresh(actions.Storage):
    def __init__(self, locator, storage, name):
        super(Refresh, self).__init__(storage)
        self.locator = locator
        self.name = name

    @coroutine
    def execute(self):
        if not self.name:
            names = yield List(self.storage).execute()
        else:
            names = [self.name]

        channel = yield self.locator.refresh(names)
        yield channel.rx.get()


class AddApplication(actions.Specific):
    def __init__(self, storage, name, app, weight):
        super(AddApplication, self).__init__(storage, 'group', name)
        self.app = app
        self.weight = int(weight)

    @coroutine
    def execute(self):
        channel = yield self.storage.read(GROUP_COLLECTION, self.name)
        group = yield channel.rx.get()
        group = msgpack.loads(group)
        group[self.app] = self.weight
        validate_routing_group(group)
        channel = yield self.storage.write(GROUP_COLLECTION, self.name, msgpack.dumps(group), GROUPS_TAGS)
        yield channel.rx.get()


class RemoveApplication(actions.Specific):
    def __init__(self, storage, name, app):
        super(RemoveApplication, self).__init__(storage, 'group', name)
        self.app = app

    @coroutine
    def execute(self):
        channel = yield self.storage.read(GROUP_COLLECTION, self.name)
        group = yield channel.rx.get()
        group = msgpack.loads(group)
        if self.app in group:
            del group[self.app]
        validate_routing_group(group)
        channel = yield self.storage.write(GROUP_COLLECTION, self.name, msgpack.dumps(group), GROUPS_TAGS)
        yield channel.rx.get()
