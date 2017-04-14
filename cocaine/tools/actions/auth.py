import json

import click
from cerberus import Validator

from cocaine.tools.error import ToolsError
from tornado import gen

from cocaine.decorators import coroutine

from . import Action
from .. import actions


COLLECTION_TOKENS = 'tokens'
COLLECTION_GROUPS = 'auth-groups'


class List(actions.List):
    def __init__(self, storage):
        super(List, self).__init__(COLLECTION_GROUPS, ['auth'], storage)


class Create(Action):
    def __init__(self, storage, name, token, force=False):
        self._name = name
        self._token = token
        self._force = force
        self._storage = storage

    @coroutine
    def execute(self):
        if not self._force:
            channel = yield self._storage.find(COLLECTION_GROUPS, ['auth'])
            groups = yield channel.rx.get()
            if self._name in groups:
                raise ToolsError('authorization group already exists')

        channel = yield self._storage.write(COLLECTION_GROUPS, self._name, self._token, ['auth'])
        yield channel.rx.get()


class View(Action):
    def __init__(self, storage, name):
        self._name = name
        self._storage = storage

    @coroutine
    def execute(self):
        channel = yield self._storage.read(COLLECTION_GROUPS, self._name)
        token = yield channel.rx.get()

        channel = yield self._storage.find(COLLECTION_TOKENS, ['auth', 'tokens'])
        members = yield channel.rx.get()

        raise gen.Return({
            'token': token,
            'members': members,
        })


class Edit(Action):
    SCHEMA = {
        'token': {
            'type': 'string',
            'required': True,
            'regex': '^[0-9]+:.+$',
        },
        'members': {
            'type': 'list',
            'required': True,
            'schema': {
                'type': 'string',
            },
        },
    }

    def __init__(self, storage, name):
        self._name = name
        self._storage = storage
        self._validator = Validator(self.SCHEMA)

    @coroutine
    def execute(self):
        content = yield View(self._storage, self._name).execute()
        updated = click.edit(json.dumps(content, indent=4))

        if updated is not None:
            updated = json.loads(updated)
            self._validator.validate(updated)
            if self._validator.errors:
                raise ValueError('failed to validate format: {}'.format(self._validator.errors))

            # Update token only if it was changed.
            if updated['token'] != content['token']:
                yield Create(self._storage, self._name, updated['token'], force=True).execute()
                # Update.
                for member in set(updated['members']) & set(content['members']):
                    yield AddMember(self._storage, self._name, member).execute()

            # Remove excluded members while adding new ones.
            for member in set(updated['members']) - set(content['members']):
                yield AddMember(self._storage, self._name, member).execute()
            for member in set(content['members']) - set(updated['members']):
                yield ExcludeMember(self._storage, self._name, member).execute()


class Remove(Action):
    def __init__(self, storage, name, drop):
        self._name = name
        self._drop = drop
        self._storage = storage

    @coroutine
    def execute(self):
        channel = yield self._storage.remove(COLLECTION_GROUPS, self._name)
        yield channel.rx.get()

        if self._drop:
            channel = yield self._storage.find(COLLECTION_TOKENS, ['auth', 'tokens'])
            members = yield channel.rx.get()

            for member in members:
                yield ExcludeMember(self._storage, self._name, member).execute()


class AddMember(Action):
    def __init__(self, storage, name, service):
        """Adds a member (service) into the specified auth group.

        Args:
            storage: Storage object as a DI.
            name (str): Auth group name.
            service (str): Member to be added into the group.
        """
        self._name = name
        self._service = service
        self._storage = storage

    @coroutine
    def execute(self):
        channel = yield self._storage.read(COLLECTION_GROUPS, self._name)
        token = yield channel.rx.get()

        channel = yield self._storage.write(
            COLLECTION_TOKENS, self._service, token, ['auth', 'tokens', self._name])
        yield channel.rx.get()


class ExcludeMember(Action):
    def __init__(self, storage, name, service):
        """Excludes a member (service) from the specified auth group.

        Args:
            storage: Storage object as a DI.
            name (str): Auth group name.
            service (str): Member to be excluded from the group.
        """
        self._name = name
        self._service = service
        self._storage = storage

    @coroutine
    def execute(self):
        channel = yield self._storage.remove(COLLECTION_TOKENS, self._service)
        yield channel.rx.get()
