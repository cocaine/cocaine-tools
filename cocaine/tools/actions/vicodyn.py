from tornado import gen

from cocaine.tools.actions import Action

class Info(Action):
    def __init__(self, vicodyn):
        self._vicodyn = vicodyn

    @gen.coroutine
    def execute(self):
        channel = yield self._vicodyn.info()
        data = yield channel.rx.get()
        raise gen.Return(data)

class Apps(Action):
    def __init__(self, vicodyn, name):
        self._vicodyn = vicodyn
        self._name = name

    @gen.coroutine
    def execute(self):
        channel = yield self._vicodyn.apps(self._name)
        data = yield channel.rx.get()
        raise gen.Return(data)

class Peers(Action):
    def __init__(self, vicodyn, name):
        self._vicodyn = vicodyn
        self._name = name

    @gen.coroutine
    def execute(self):
        channel = yield self._vicodyn.peers(self._name)
        data = yield channel.rx.get()
        raise gen.Return(data)

