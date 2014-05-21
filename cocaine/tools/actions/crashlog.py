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

import time
from cocaine.asio import engine

from cocaine.futures import chain
import datetime
import itertools
from cocaine.tools import actions
from cocaine.tools.actions import app

__author__ = 'Evgeny Safronov <division494@gmail.com>'


class List(actions.Storage):
    def __init__(self, storage, name):
        super(List, self).__init__(storage)
        self.name = name
        if not self.name:
            raise ValueError('Please specify crashlog name')

    def execute(self):
        return self.storage.find('crashlogs', [self.name])


def _parseCrashlogs(crashlogs, timestamp=None):
    isFilter = lambda x: (x == timestamp if timestamp else True)
    _list = (log.split(':', 1) for log in crashlogs)
    return [(ts, time.ctime(float(ts) / 1000000), name) for ts, name in _list if isFilter(ts)]


class Specific(actions.Storage):
    def __init__(self, storage, name, timestamp=None):
        super(Specific, self).__init__(storage)
        self.name = name
        self.timestamp = timestamp
        if not self.name:
            raise ValueError('Please specify application name')


class View(Specific):
    @chain.source
    def execute(self):
        crashlogs = yield self.storage.find('crashlogs', [self.name])
        parsedCrashlogs = _parseCrashlogs(crashlogs, timestamp=self.timestamp)
        contents = []
        for crashlog in parsedCrashlogs:
            key = '%s:%s' % (crashlog[0], crashlog[2])
            content = yield self.storage.read('crashlogs', key)
            contents.append(content)
        yield ''.join(contents)


class Remove(Specific):
    @chain.source
    def execute(self):
        crashlogs = yield self.storage.find('crashlogs', [self.name])
        parsedCrashlogs = _parseCrashlogs(crashlogs, timestamp=self.timestamp)
        for crashlog in parsedCrashlogs:
            key = '%s:%s' % (crashlog[0], crashlog[2])
            yield self.storage.remove('crashlogs', key)
        yield 'Done'


class RemoveAll(Remove):
    def __init__(self, storage, name):
        super(RemoveAll, self).__init__(storage, name, timestamp=None)


class Status(actions.Storage):
    @engine.asynchronous
    def execute(self):
        applications = yield app.List(self.storage).execute()
        crashed = []
        for application in applications:
            crashlogs = yield List(self.storage, application).execute()
            if crashlogs:
                last = max(_parseCrashlogs(crashlogs), key=lambda (timestamp, time, uuid): timestamp)
                crashed.append((application, last, len(crashlogs)))
        yield crashed


def splitted(collection, sep=None, maxsplit=None):
    for item in collection:
        yield item.split(sep, maxsplit)


def filtered(crashlogs):
    for (ts, uuid) in splitted(crashlogs, ':', 1):
        yield int(ts), uuid


class Clean(Specific):
    def __init__(self, storage, name, size, timestamp=None):
        super(Clean, self).__init__(storage, name, timestamp)
        self.size = int(size)

    @engine.asynchronous
    def execute(self):
        if not self.name:
            apps = yield app.List(self.storage).execute()
        else:
            apps = [self.name]

        result = []
        if self.timestamp:
            try:
                dt = datetime.datetime.strptime(self.timestamp, '%Y-%m-%dT%H:%M:%S')
                timestamp = int(time.mktime(dt.timetuple())) * 1000000 + dt.microsecond
            except ValueError:
                timestamp = int(self.timestamp)

            for app_name in apps:
                crashlogs = yield self.storage.find('crashlogs', [app_name])
                result = filter(lambda (ts, uuid): ts < timestamp, filtered(crashlogs))
        elif self.size > 0:
            for app_name in apps:
                crashlogs = yield self.storage.find('crashlogs', [app_name])
                result = itertools.islice(
                    sorted(filtered(crashlogs), key=lambda (ts, uuid): ts, reverse=True), self.size, None)

        for crashlog in result:
            print('removing', '%d:%s' % crashlog)
            yield self.storage.remove('crashlogs', '%d:%s' % crashlog)
        yield 'Done'
