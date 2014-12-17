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

import itertools
import datetime
import time

from tornado import gen

from cocaine.tools import actions
from cocaine.decorators import coroutine
from cocaine.tools.actions import app

__author__ = 'Evgeny Safronov <division494@gmail.com>'


class List(actions.Storage):
    def __init__(self, storage, name):
        super(List, self).__init__(storage)
        self.name = name
        if not self.name:
            raise ValueError('Please specify crashlog name')

    @coroutine
    def execute(self):
        channel = yield self.storage.find('crashlogs', [self.name])
        listing = yield channel.rx.get()
        raise gen.Return(listing)


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
    @coroutine
    def execute(self):
        channel = yield self.storage.find('crashlogs', [self.name])
        crashlogs = yield channel.rx.get()
        parsedCrashlogs = _parseCrashlogs(crashlogs, timestamp=self.timestamp)
        contents = []
        for crashlog in parsedCrashlogs:
            key = '%s:%s' % (crashlog[0], crashlog[2])
            channel = yield self.storage.read('crashlogs', key)
            content = yield channel.rx.get()
            contents.append(content)
        raise gen.Return(''.join(contents))


class Remove(Specific):
    @coroutine
    def execute(self):
        channel = yield self.storage.find('crashlogs', [self.name])
        crashlogs = yield channel.rx.get()
        parsedCrashlogs = _parseCrashlogs(crashlogs, timestamp=self.timestamp)
        for crashlog in parsedCrashlogs:
            key = '%s:%s' % (crashlog[0], crashlog[2])
            channel = yield self.storage.remove('crashlogs', key)
            yield channel.rx.get()
        raise gen.Return('Done')


class RemoveAll(Remove):
    def __init__(self, storage, name):
        super(RemoveAll, self).__init__(storage, name, timestamp=None)


class Status(actions.Storage):
    @coroutine
    def execute(self):
        applications = yield app.List(self.storage).execute()
        crashed = []
        for application in applications:
            crashlogs = yield List(self.storage, application).execute()
            if crashlogs:
                last = max(_parseCrashlogs(crashlogs), key=lambda (timestamp, time, uuid): timestamp)
                crashed.append((application, last, len(crashlogs)))
        raise gen.Return(crashed)


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

    @coroutine
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
                channel = yield self.storage.find('crashlogs', [app_name])
                crashlogs = yield channel.rx.get()
                result = filter(lambda (ts, uuid): ts < timestamp, filtered(crashlogs))
        elif self.size > 0:
            for app_name in apps:
                channel = yield self.storage.find('crashlogs', [app_name])
                crashlogs = yield channel.rx.get()
                result = itertools.islice(
                    sorted(filtered(crashlogs[0]), key=lambda (ts, uuid): ts, reverse=True), self.size, None)

        for crashlog in result:
            print('removing', '%d:%s' % crashlog)
            channel = yield self.storage.remove('crashlogs', '%d:%s' % crashlog)
            yield channel.rx.get()
        raise gen.Return('Done')
