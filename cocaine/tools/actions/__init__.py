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

import json
import tarfile

import msgpack
from tornado import gen

from cocaine.decorators import coroutine
from cocaine.tools import log

__author__ = 'Evgeny Safronov <division494@gmail.com>'


def isJsonValid(text):
    try:
        json.loads(text)
        return True
    except ValueError:
        return False


def readArchive(filename):
    if not tarfile.is_tarfile(filename):
        raise tarfile.TarError('File "{0}" is not tar file'.format(filename))
    with open(filename, 'rb') as archive:
        return archive.read()


class CocaineConfigReader:
    @classmethod
    def load(cls, context, validate=lambda ctx: None):
        if isinstance(context, dict):
            log.debug('Content specified directly by dict')
            validate(context)
            return msgpack.dumps(context)

        if isJsonValid(context):
            log.debug('Content specified directly by string')
            content = context
        else:
            log.debug('Loading content from file ...')
            with open(context, 'rb') as fh:
                content = fh.read()
        content = json.loads(content)
        validate(content)
        return msgpack.dumps(content)


class Storage(object):
    def __init__(self, storage=None):
        self.storage = storage

    @coroutine
    def execute(self):  # pragma: no cover
        raise NotImplementedError()


class List(Storage):
    """
    Abstract storage action class which main aim is to provide find list action on 'key' and 'tags'.
    For example if key='manifests' and tags=('apps',) this class will try to find applications list
    """
    def __init__(self, key, tags, storage):
        super(List, self).__init__(storage)
        self.key = key
        self.tags = tags

    @coroutine
    def execute(self):
        channel = yield self.storage.find(self.key, self.tags)
        listing = yield channel.rx.get()
        raise gen.Return(listing)


class Specific(Storage):
    def __init__(self, storage, entity, name):
        super(Specific, self).__init__(storage)
        self.name = name
        if not self.name:
            raise ValueError('Please specify {0} name'.format(entity))


class View(Specific):
    def __init__(self, storage, entity, name, collection):
        super(View, self).__init__(storage, entity, name)
        self.collection = collection

    @coroutine
    def execute(self):
        channel = yield self.storage.read(self.collection, self.name)
        value = yield channel.rx.get()
        raise gen.Return(msgpack.loads(value))
