import json

import click
from tornado.gen import coroutine

from cocaine.tools.actions import Action


class Edit(Action):
    def __init__(self, unicorn, path):
        self._unicorn = unicorn
        self._path = path

    @coroutine
    def execute(self):
        channel = yield self._unicorn.get(self._path)
        content, version = yield channel.rx.get()
        if version == -1:
            content = {}

        updated = click.edit(json.dumps(content, indent=4))
        if updated is not None:
            updated = json.loads(updated)
            if version == -1:
                yield self._unicorn.create(self._path, updated)
            else:
                yield self._unicorn.put(self._path, updated, version)
