import json
import os

import click
from cerberus import Validator
from tornado import gen

from cocaine.decorators import coroutine
from cocaine.exceptions import ServiceError

from . import Action


_PREFIX = '/acl'

ERROR_CATEGORY_UNICORN = 16639
ERROR_CODE_NO_NODE = -101

VERSION_NOT_EXISTS = -1


class List(Action):
    def __init__(self, unicorn):
        self._unicorn = unicorn

    @coroutine
    def execute(self):
        path = os.path.join(_PREFIX)
        channel = yield self._unicorn.children_subscribe(path)
        version, services = yield channel.rx.get()
        raise gen.Return(services)


class View(Action):
    def __init__(self, service, unicorn):
        self._service = service
        self._unicorn = unicorn

    @coroutine
    def execute(self):
        raise gen.Return({
            'cids': (yield self._walk('cids')),
            'uids': (yield self._walk('uids')),
        })

    @coroutine
    def _walk(self, ty):
        path = os.path.join(_PREFIX, self._service, ty)

        try:
            channel = yield self._unicorn.children_subscribe(path)
            version, ids = yield channel.rx.get()
        except ServiceError as err:
            if err.category == ERROR_CATEGORY_UNICORN and err.code == ERROR_CODE_NO_NODE:
                raise gen.Return({})
            else:
                raise err

        content = {}
        for id_ in ids:
            channel = yield self._unicorn.get(os.path.join(_PREFIX, self._service, ty, id_))
            value, version = yield channel.rx.get()

            content[id_] = value
        raise gen.Return(content)


class Add(Action):
    def __init__(self, service, event, scope, id_, unicorn):
        self._service = service
        self._event = event
        self._path = os.path.join(_PREFIX, self._service, scope, '{}'.format(id_))
        self._unicorn = unicorn

    @coroutine
    def execute(self):
        channel = yield self._unicorn.get(self._path)
        value, version = yield channel.rx.get()

        if version == VERSION_NOT_EXISTS:
            channel = yield self._unicorn.create(self._path, {
                self._event: {}
            })
        else:
            # Do not override if already exists.
            if self._event not in value:
                value[self._event] = {}
            channel = yield self._unicorn.put(self._path, value, version)

        yield channel.rx.get()


class AddUser(Add):
    def __init__(self, service, event, uid, unicorn):
        super(AddUser, self).__init__(service, event, 'uids', uid, unicorn)


class AddClient(Add):
    def __init__(self, service, event, cid, unicorn):
        super(AddClient, self).__init__(service, event, 'cids', cid, unicorn)


class AddBoth(Action):
    def __init__(self, service, event, cids, uids, unicorn):
        self._service = service
        self._cids = cids
        self._uids = uids
        self._event = event
        self._unicorn = unicorn

    @coroutine
    def execute(self):
        for uid in self._uids:
            yield AddUser(self._service, self._event, uid, self._unicorn).execute()
        for cid in self._cids:
            yield AddClient(self._service, self._event, cid, self._unicorn).execute()


class Remove(Action):
    def __init__(self, service, event, scope, id_, unicorn):
        self._service = service
        self._event = event
        self._path = os.path.join(_PREFIX, self._service, scope, '{}'.format(id_))
        self._unicorn = unicorn

    @coroutine
    def execute(self):
        channel = yield self._unicorn.get(self._path)
        value, version = yield channel.rx.get()

        print(value, version)
        if version != VERSION_NOT_EXISTS:
            value.pop(self._event)

            if len(value) == 0:
                channel = yield self._unicorn.remove(self._path, VERSION_NOT_EXISTS)
            else:
                channel = yield self._unicorn.put(self._path, value, version)

        yield channel.rx.get()


class RemoveUser(Remove):
    def __init__(self, service, event, uid, unicorn):
        super(RemoveUser, self).__init__(service, event, 'uids', uid, unicorn)


class RemoveClient(Remove):
    def __init__(self, service, event, cid, unicorn):
        super(RemoveClient, self).__init__(service, event, 'cids', cid, unicorn)


class Edit(Action):
    SUB_SCHEMA = {
        'type': 'dict',
        'required': True,
        'propertyschema': {
            'type': 'string',
        },
        'valueschema': {
            'type': 'dict',
            'propertyschema': {
                'type': 'string',
            },
            'valueschema': {
                'type': 'dict',
            },
        }
    }

    SCHEMA = {
        'cids': SUB_SCHEMA,
        'uids': SUB_SCHEMA,
    }

    EMPTY_CONTENT = {
        'cids': {},
        'uids': {},
    }

    def __init__(self, service, unicorn):
        self._service = service
        self._unicorn = unicorn
        self._validator = Validator(self.SCHEMA)

    @coroutine
    def execute(self):
        try:
            content = yield View(self._service, self._unicorn).execute()
        except ServiceError as err:
            # Create default empty ACL if not exists.
            if err.category == ERROR_CATEGORY_UNICORN and err.code == ERROR_CODE_NO_NODE:
                content = self.EMPTY_CONTENT
            else:
                raise err

        updated = click.edit(json.dumps(content, indent=4), require_save=False)
        updated = json.loads(updated)
        self._validator.validate(updated)
        if self._validator.errors:
            raise ValueError('failed to validate to ACLs: {}'.format(self._validator.errors))

        for scope in ('uids', 'cids'):
            # Create new records.
            for id_ in set(updated[scope]) - set(content[scope]):
                path = os.path.join(_PREFIX, self._service, scope, '{}'.format(id_))
                channel = yield self._unicorn.create(path, updated[scope][id_])
                yield channel.rx.get()
            # Remove old records.
            for id_ in set(content[scope]) - set(updated[scope]):
                path = os.path.join(_PREFIX, self._service, scope, '{}'.format(id_))
                channel = yield self._unicorn.remove(path, VERSION_NOT_EXISTS)
                yield channel.rx.get()

            # Update non-changed records with changed values.
            for id_ in set(updated[scope]) & set(content[scope]):
                if updated[scope][id_] != content[scope][id_]:
                    path = os.path.join(_PREFIX, self._service, scope, '{}'.format(id_))
                    channel = yield self._unicorn.get(path)
                    value, version = yield channel.rx.get()
                    channel = yield self._unicorn.put(path, updated[scope][id_], version)
                    yield channel.rx.get()
