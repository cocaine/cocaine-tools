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

from cocaine.asio import engine
from cocaine.tools import actions
from cocaine.tools.actions import CocaineConfigReader
from cocaine.tools.tags import GROUPS_TAGS

__author__ = 'EvgenySafronov <division494@gmail.com>'

GROUP_COLLECTION = 'groups'


class List(actions.List):
    def __init__(self, storage):
        super(List, self).__init__(GROUP_COLLECTION, GROUPS_TAGS, storage)


class View(actions.View):
    def __init__(self, storage, name):
        super(View, self).__init__(storage, GROUP_COLLECTION, name, 'groups')


class Create(actions.Specific):
    def __init__(self, storage, name, content=None):
        super(Create, self).__init__(storage, 'group', name)
        self.content = content

    @engine.asynchronous
    def execute(self):
        if self.content:
            content = CocaineConfigReader.load(self.content, validate=self._validate)
        else:
            content = msgpack.dumps({})
        yield self.storage.write(GROUP_COLLECTION, self.name, content, GROUPS_TAGS)

    def _validate(self, content):
        for app, weight in content.items():
            if not isinstance(weight, (int, long)):
                raise ValueError('all weights must be integer')


class Remove(actions.Specific):
    def __init__(self, storage, name):
        super(Remove, self).__init__(storage, 'group', name)

    def execute(self):
        return self.storage.remove(GROUP_COLLECTION, self.name)


class Refresh(actions.Storage):
    def __init__(self, locator, storage, name):
        super(Refresh, self).__init__(storage)
        self.locator = locator
        self.name = name

    @engine.asynchronous
    def execute(self):
        if not self.name:
            names = yield List(self.storage).execute()
        else:
            names = [self.name]

        for name in names:
            yield self.locator.refresh(name)


class AddApplication(actions.Specific):
    def __init__(self, storage, name, app, weight):
        super(AddApplication, self).__init__(storage, 'group', name)
        self.app = app
        self.weight = int(weight)

    @engine.asynchronous
    def execute(self):
        group = yield self.storage.read(GROUP_COLLECTION, self.name)
        group = msgpack.loads(group)
        group[self.app] = self.weight
        yield self.storage.write(GROUP_COLLECTION, self.name, msgpack.dumps(group), GROUPS_TAGS)


class RemoveApplication(actions.Specific):
    def __init__(self, storage, name, app):
        super(RemoveApplication, self).__init__(storage, 'group', name)
        self.app = app

    @engine.asynchronous
    def execute(self):
        group = yield self.storage.read(GROUP_COLLECTION, self.name)
        group = msgpack.loads(group)
        if self.app in group:
            del group[self.app]
        yield self.storage.write(GROUP_COLLECTION, self.name, msgpack.dumps(group), GROUPS_TAGS)
