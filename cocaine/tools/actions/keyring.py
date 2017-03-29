from tornado import gen

from cocaine.decorators import coroutine
from cocaine.tools.actions import Action


class Update(Action):
    def __init__(self, cid, tvm):
        self._cid = cid
        self._tvm = tvm

    @coroutine
    def execute(self):
        channel = yield self._tvm.download_keys(self._cid)
        keys = yield channel.rx.get()
        raise gen.Return(keys)
