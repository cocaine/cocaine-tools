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
            path = os.path.join(_PREFIX, self._service, ty, id_)
            channel = yield self._unicorn.children_subscribe(path)
            version, events = yield channel.rx.get()
            for event in events:
                path = os.path.join(_PREFIX, self._service, ty, id_, event)
                channel = yield self._unicorn.get(path)
                data, version = yield channel.rx.get()

                if id_ not in content:
                    content[id_] = {}
                if event not in content[id_]:
                    content[id_][event] = {}
                content[id_][event] = data
        raise gen.Return(content)


class Add(Action):
    def __init__(self, service, event, ty, xid, unicorn):
        self._path = os.path.join(_PREFIX, service, ty, str(xid), event)
        self._unicorn = unicorn

    @coroutine
    def execute(self):
        channel = yield self._unicorn.create(self._path, {})
        yield channel.rx.get()


class AddClient(Add):
    def __init__(self, service, event, xid, unicorn):
        super(AddClient, self).__init__(service, event, 'cids', xid, unicorn)


class AddUser(Add):
    def __init__(self, service, event, xid, unicorn):
        super(AddUser, self).__init__(service, event, 'uids', xid, unicorn)


class Remove(Action):
    def __init__(self, service, event, ty, xid, unicorn):
        self._path = os.path.join(_PREFIX, service, ty, str(xid), event)
        self._unicorn = unicorn

    @coroutine
    def execute(self):
        channel = yield self._unicorn.remove(self._path, -1)
        yield channel.rx.get()


class RemoveClient(Remove):
    def __init__(self, service, event, xid, unicorn):
        super(RemoveClient, self).__init__(service, event, 'cids', xid, unicorn)


class RemoveUser(Remove):
    def __init__(self, service, event, xid, unicorn):
        super(RemoveUser, self).__init__(service, event, 'uids', xid, unicorn)


class Update(Action):
    def __init__(self, service, event, ty, xid, value, unicorn):
        self._path = os.path.join(_PREFIX, service, ty, str(xid), event)
        self._value = value
        self._unicorn = unicorn

    @coroutine
    def execute(self):
        channel = yield self._unicorn.get(self._path)
        value, version = yield channel.rx.get()

        if value != self._value:
            channel = yield self._unicorn.put(self._path, self._value, version)
            yield channel.rx.get()


class UpdateClient(Update):
    def __init__(self, service, event, xid, value, unicorn):
        super(UpdateClient, self).__init__(service, event, 'cids', xid, value, unicorn)


class UpdateUser(Update):
    def __init__(self, service, event, xid, value, unicorn):
        super(UpdateUser, self).__init__(service, event, 'uids', xid, value, unicorn)


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

        actions = [
            ('cids', AddClient, RemoveClient, UpdateClient),
            ('uids', AddUser, RemoveUser, UpdateUser),
        ]

        # Create new keys.
        # Remove old keys.
        # Update non-changed keys with changed values.
        for ty, create, remove, update in actions:
            for i in list(set(updated[ty]) - set(content[ty])):
                for event in updated[ty][i]:
                    yield create(self._service, event, i, self._unicorn).execute()
            for i in list(set(content[ty]) - set(updated[ty])):
                for event in content[ty][i]:
                    yield remove(self._service, event, i, self._unicorn).execute()
            for i in list(set(updated[ty]) & set(content[ty])):
                if updated[ty][i] != content[ty][i]:
                    for event_prev, event in zip(content[ty][i], updated[ty][i]):
                        if event_prev != event:
                            path = os.path.join(_PREFIX, self._service, ty, str(i), event_prev)
                            channel = yield self._unicorn.remove(path, -1)
                            yield channel.rx.get()

                            path = os.path.join(_PREFIX, self._service, ty, str(i), event)
                            channel = yield self._unicorn.create(path, updated[ty][i][event])
                            yield channel.rx.get()
                        else:
                            yield update(self._service, event, i, updated[ty][i][event],
                                         self._unicorn).execute()
