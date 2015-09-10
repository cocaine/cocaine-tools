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

import types

from tornado import gen

from cocaine.decorators import coroutine

from cocaine.tools.error import ToolsError
from cocaine.tools import actions, log
from cocaine.tools.actions import CocaineConfigReader
from cocaine.tools.printer import printer
from cocaine.tools.tags import RUNLISTS_TAGS

__author__ = 'Evgeny Safronov <division494@gmail.com>'


class Specific(actions.Specific):
    def __init__(self, storage, name):
        super(Specific, self).__init__(storage, 'runlist', name)


class List(actions.List):
    def __init__(self, storage):
        super(List, self).__init__('runlists', RUNLISTS_TAGS, storage)


class View(actions.View):
    def __init__(self, storage, name):
        super(View, self).__init__(storage, 'runlist', name, 'runlists')


class Upload(Specific):
    def __init__(self, storage, name, runlist):
        super(Upload, self).__init__(storage, name)
        self.runlist = runlist
        if isinstance(self.runlist, types.DictType):
            return
        elif isinstance(self.runlist, types.StringTypes) and len(self.runlist.strip()) > 0:
            return
        else:
            raise ValueError('Please specify runlist file path')

    @coroutine
    def execute(self):
        runlist = CocaineConfigReader.load(self.runlist)
        with printer('Uploading "%s"', self.name):
            yield self.storage.write('runlists', self.name, runlist, RUNLISTS_TAGS)


class Create(Specific):
    @coroutine
    def execute(self):
        yield Upload(self.storage, self.name, '{}').execute()


class Remove(Specific):
    @coroutine
    def execute(self):
        log.info('Removing "%s"... ', self.name)
        yield self.storage.remove('runlists', self.name)
        log.info('OK')


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
        yield Upload(self.storage, self.copyname, oldprofile).execute()


class Rename(Copy):
    @coroutine
    def execute(self):
        yield super(Rename, self).execute()
        yield Remove(self.storage, self.name).execute()


class AddApplication(Specific):
    def __init__(self, storage, name, app, profile, force=False):
        super(AddApplication, self).__init__(storage, name)
        self.app = app
        self.profile = profile
        self.force = force
        if not self.app:
            raise ValueError('Please specify application name')
        if not self.profile:
            raise ValueError('Please specify profile')

    @coroutine
    def execute(self):
        result = {
            'runlist': self.name,
            'status': 'modified',
            'added': {
                'app': self.app,
                'profile': self.profile,
            }
        }

        runlists = yield List(self.storage).execute()
        if self.force and self.name not in runlists:
            log.debug('Runlist does not exist. Create a new one ...')
            yield Create(self.storage, self.name).execute()
            result['status'] = 'created'

        runlist = yield View(self.storage, name=self.name).execute()
        log.debug('Found runlist: %s', runlist)
        runlist[self.app] = self.profile
        yield Upload(self.storage, name=self.name, runlist=runlist).execute()
        raise gen.Return(result)


class RemoveApplication(Specific):
    def __init__(self, storage, name, app):
        super(RemoveApplication, self).__init__(storage, name)
        self.app = app
        if not self.app:
            raise ValueError('Please specify application name')

    @coroutine
    def execute(self):
        result = {
            'runlist': self.name,
            'app': self.app,
            'status': 'successfully removed',
        }

        runlists = yield List(self.storage).execute()
        if self.name not in runlists:
            log.debug('Runlist does not exist')
            raise ToolsError('Runlist %s is missing', self.name)

        runlist = yield View(self.storage, name=self.name).execute()
        log.debug('Found runlist: %s', runlist)
        if runlist.pop(self.app, None) is None:
            result['status'] = 'the application named {0} is not in runlist'.format(self.app)
        else:
            yield Upload(self.storage, name=self.name, runlist=runlist).execute()
        raise gen.Return(result)
