from tornado import gen


class PluginException(Exception):
    pass


class PluginConfigurationError(PluginException):
    def __init__(self, name, message):
        super(PluginConfigurationError, self).__init__("configuration error for %s: %s" % (name, message))
        self.name = name
        self.message = message


class PluginNoSuchApplication(PluginException):
    pass


class PluginApplicationError(PluginException):
    def __init__(self, category, code, message):
        super(PluginApplicationError, self).__init__("[%d %d] %s" % (category, code, message))
        self.category = category
        self.code = code
        self.message = message


class IPlugin(object):
    @staticmethod
    def name():
        raise NotImplementedError()

    def __init__(self, proxy):
        self.proxy = proxy

    def match(self, request):
        raise NotImplementedError()

    @gen.coroutine
    def process(self, request):
        raise NotImplementedError()
