import json

import click
import msgpack
from tornado import gen

from cocaine.decorators import coroutine
from cocaine.exceptions import ServiceError
from cocaine.tools.actions import Action

_COLLECTION_TVM = 'tvm'

_KEY_PUBLIC_KEYS = 'public_keys'


class View(Action):
    def __init__(self, storage):
        self._storage = storage

    @coroutine
    def execute(self):
        channel = yield self._storage.read(_COLLECTION_TVM, _KEY_PUBLIC_KEYS)
        keys = yield channel.rx.get()
        keys = msgpack.loads(keys)
        raise gen.Return(keys)


class Update(Action):
    def __init__(self, cid, tvm):
        self._cid = cid
        self._tvm = tvm

    @coroutine
    def execute(self):
        channel = yield self._tvm.download_keys(self._cid)
        keys = yield channel.rx.get()
        raise gen.Return(keys)


class Remove(Action):
    def __init__(self, key, storage):
        self._key = key
        self._storage = storage

    @coroutine
    def execute(self):
        if self._key is None:
            keys = []
        else:
            keys = yield View(self._storage).execute()
            try:
                keys.remove(self._key)
            except ValueError:
                pass
        keys = msgpack.dumps(keys)

        channel = yield self._storage.write(_COLLECTION_TVM, _KEY_PUBLIC_KEYS, keys, [])
        yield channel.rx.get()


class Edit(Action):
    def __init__(self, storage):
        self._storage = storage

    @coroutine
    def execute(self):
        try:
            content = yield View(self._storage).execute()
        except ServiceError:
            content = []

        updated = click.edit(json.dumps(content, indent=4))
        if updated is not None:
            updated = json.loads(updated)
            updated = msgpack.dumps(updated)
            channel = yield self._storage.write(_COLLECTION_TVM, _KEY_PUBLIC_KEYS, updated, [])
            yield channel.rx.get()


class Refresh(Action):
    def __init__(self, tvm):
        self._tvm = tvm

    @coroutine
    def execute(self):
        channel = yield self._tvm.refresh_keyring()
        yield channel.rx.get()
