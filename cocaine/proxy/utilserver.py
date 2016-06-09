import logging

from tornado import web


class PingHandler(web.RequestHandler):  # pylint: disable=W0223
    def get(self):
        self.write("OK")


class LogLevel(web.RequestHandler):  # pylint: disable=W0223
    def get(self):
        lvl = self.application.logger.getEffectiveLevel()
        self.write(logging.getLevelName(lvl))

    def post(self):
        lvlname = self.get_argument("level")
        lvl = getattr(logging, lvlname.upper(), None)
        if lvl is None:
            self.write("No such level %s" % lvlname)
            return

        for name in ("cocaine.proxy.general", "cocaine.proxy.access", "cocaine.baseservice"):
            logging.getLogger(name).setLevel(lvl)
        self.write("level %s has been set" % logging.getLevelName(lvl))


class InfoHandler(web.RequestHandler):  # pylint: disable=W0223
    def get(self):
        info = self.application.proxy.info()
        self.write(info)


class UtilServer(web.Application):  # pylint: disable=W0223
    def __init__(self, proxy):
        self.proxy = proxy
        self.logger = logging.getLogger("proxy.utilserver")
        handlers = [
            (r"/ping", PingHandler),
            (r"/info", InfoHandler),
            (r"/logger", LogLevel),
        ]
        super(UtilServer, self).__init__(handlers=handlers)

    def log_request(self, handler):
        request_time = 1000.0 * handler.request.request_time()
        self.logger.info("%d %s %.2fms", handler.get_status(),
                         handler._request_summary(), request_time)
