import logging


class ContextAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {}).update(self.extra)
        return msg, kwargs


class NullLogger(object):
    def __call__(self, *args, **kwargs):
        return self

    def __getattribute__(self, name):
        return self

    def __setattr__(self, name, value):
        pass

    def __delattr__(self, name):
        pass


NULLLOGGER = NullLogger()
