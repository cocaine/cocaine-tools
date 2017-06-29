from cocaine.decorators import coroutine
from cocaine.tools.actions import Action


class EditBase(Action):
    PERM = {
        '0': 0b00,
        'R': 0b01,
        'W': 0b10,
        'RW': 0b11,
    }

    def __init__(self, tp, name, cids, uids, perm, unicat):
        self._tp = tp
        self._name = name
        self._cids = map(int, cids)
        self._uids = map(int, uids)
        self._perm = self.PERM.get(perm, 0)
        self._unicat = unicat

    @coroutine
    def op(self):
        pass

    @coroutine
    def execute(self):
        yield self.op()


class Grant(EditBase):

    def __init__(self, *args, **kwargs):
        super(Grant, self).__init__(*args, **kwargs)

    @coroutine
    def op(self):
        channel = yield self._unicat.grant(
            [(self._tp, '', self._name)], self._cids, self._uids, self._perm)
        yield channel.rx.get()


class Revoke(EditBase):

    def __init__(self, *args, **kwargs):
        super(Revoke, self).__init__(*args, **kwargs)

    @coroutine
    def op(self):
        channel = yield self._unicat.revoke(
            [(self._tp, '', self._name)], self._cids, self._uids, self._perm)
        yield channel.rx.get()
