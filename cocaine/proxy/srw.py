from tornado import gen


class SRWException(Exception):
    pass


class SRWConfigurationError(SRWException):
    def __init__(self, name, message):
        super(SRWConfigurationError, self).__init__("configuration error for %s: %s" % (name, message))
        self.name = name
        self.message = message


class SRWNoSuchApplication(SRWException):
    pass


class SRWApplicationError(SRWException):
    def __init__(self, category, code, message):
        super(SRWApplicationError, self).__init__("[%d %d] %s" % (category, code, message))
        self.category = category
        self.code = code
        self.message = message


class ISRWExec(object):
    @staticmethod
    def name():
        raise NotImplementedError()

    def match(self, request):
        raise NotImplementedError()

    @gen.coroutine
    def process(self, request, name, event, timeout):
        raise NotImplementedError()
