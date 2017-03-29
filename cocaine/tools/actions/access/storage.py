import click
import msgpack
from tornado import gen

from cocaine.decorators import coroutine
from cocaine.exceptions import ServiceError
from cocaine.tools import log
from cocaine.tools.actions import Action
from cocaine.tools.error import ToolsError

_COLLECTION = '.collection-acls'
_TAGS = ('storage-acls',)


class List(Action):
    def __init__(self, storage):
        self._storage = storage

    @coroutine
    def execute(self):
        channel = yield self._storage.find(_COLLECTION, _TAGS)
        result = yield channel.rx.get()
        raise gen.Return(result)


class View(Action):
    def __init__(self, name, storage):
        self._name = name
        self._storage = storage

    @coroutine
    def execute(self):
        channel = yield self._storage.read(_COLLECTION, self._name)
        result = yield channel.rx.get()
        raise gen.Return(msgpack.loads(result))


class Create(Action):
    def __init__(self, name, storage):
        self._name = name
        self._storage = storage

    @coroutine
    def execute(self):
        try:
            yield View(self._name, self._storage).execute()
        except ServiceError:
            pass
        else:
            raise ToolsError('An ACL for collection "{}" already exists'.format(self._name))

        channel = yield self._storage.write(_COLLECTION, self._name, msgpack.dumps([{}, {}]), _TAGS)
        yield channel.rx.get()


class Edit(Action):
    PERM = {
        '0': 0b00,
        'R': 0b01,
        'W': 0b10,
        'RW': 0b11,
    }

    def __init__(self, name, cids, uids, perm, storage):
        self._name = name
        self._cids = cids
        self._uids = uids
        self._perm = self.PERM.get(perm, 0)
        self._storage = storage

    @coroutine
    def execute(self):
        content = yield View(self._name, self._storage).execute()
        if len(content) != 2:
            raise ToolsError('framing error - ACL should be a tuple of 2 maps')

        cids, uids = content[:]
        for cid in self._cids:
            cids[int(cid)] = self._perm
        for uid in self._uids:
            uids[int(uid)] = self._perm

        content = msgpack.dumps([cids, uids])
        channel = yield self._storage.write(_COLLECTION, self._name, content, _TAGS)
        yield channel.rx.get()


class Remove(Action):
    def __init__(self, name, storage):
        self._name = name
        self._storage = storage

    @coroutine
    def execute(self):
        if self._name is None:
            acls = yield List(self._storage).execute()
        else:
            acls = [self._name]

        log.info('ACL to be removed: %s', acls)

        failed = []
        for acl in acls:
            try:
                channel = yield self._storage.remove(_COLLECTION, acl)
                yield channel.rx.get()
            except ServiceError as err:
                failed.append((acl, err))
            else:
                log.info('ACL %s has been successfully removed', acl)

        if len(failed) > 0:
            click.echo(click.style('Failed to remove the following ACL:', fg='red'))
            for acl, err in failed:
                click.echo(' - "{}": {}'.format(acl, err.reason.lower()))
