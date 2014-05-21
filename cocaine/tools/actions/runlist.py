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

from cocaine.futures import chain
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

    @chain.source
    def execute(self):
        runlist = CocaineConfigReader.load(self.runlist)
        with printer('Uploading "%s"', self.name):
            yield self.storage.write('runlists', self.name, runlist, RUNLISTS_TAGS)


class Create(Specific):
    def execute(self):
        return Upload(self.storage, self.name, '{}').execute()


class Remove(Specific):
    @chain.source
    def execute(self):
        log.info('Removing "%s"... ', self.name)
        yield self.storage.remove('runlists', self.name)
        log.info('OK')


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

    @chain.source
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
            log.debug('Runlist does not exist. Creating new one ...')
            yield Create(self.storage, self.name).execute()
            result['status'] = 'created'

        runlist = yield View(self.storage, name=self.name).execute()
        log.debug('Found runlist: {0}'.format(runlist))
        runlist[self.app] = self.profile
        runlistUploadAction = Upload(self.storage, name=self.name, runlist=runlist)
        yield runlistUploadAction.execute()
        yield result


class RemoveApplication(Specific):
    def __init__(self, storage, name, app):
        super(RemoveApplication, self).__init__(storage, name)
        self.app = app
        if not self.app:
            raise ValueError('Please specify application name')

    @chain.source
    def execute(self):
        result = {
            'runlist': self.name,
            'app': self.app,
            'status': 'successfully removed',
        }

        runlists = yield List(self.storage).execute()
        if self.name not in runlists:
            log.debug('Runlist does not exist.')
            raise ValueError('Runlist {0} is missing.'.format(self.name))

        runlist = yield View(self.storage, name=self.name).execute()
        log.debug('Found runlist: {0}'.format(runlist))
        if runlist.pop(self.app, None) is None:
            result['status'] = 'the application named {0} is not in runlist'.format(self.app)
        else:
            runlistUploadAction = Upload(self.storage, name=self.name, runlist=runlist)
            yield runlistUploadAction.execute()
        yield result
