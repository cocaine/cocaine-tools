#!/usr/bin/env python

__author__ = 'EvgenySafronov <division494@gmail.com>'


def getOption(name, default):
    value = default
    if name in sys.argv:
        index = sys.argv.index(name)
        if index < len(sys.argv) - 1:
            value = sys.argv[index + 1]
            if value == '=' and index + 1 < len(sys.argv) - 2:
                value = sys.argv[index + 2]
    elif name + '=' in sys.argv:
        index = sys.argv.index(name + '=')
        if index < len(sys.argv) - 1:
            value = sys.argv[index + 1]
    return value


if __name__ == '__main__':
    try:
        import sys
        import os
        from cocaine.services import Service
        from cocaine.asio import engine

        ADEQUATE_TIMEOUT = 0.25

        locateItems = {
            'app': ['manifests', ('app', )],
            'profile': ['profiles', ('profile',)],
            'runlist': ['runlists', ('runlist',)],
        }

        config = {
            'locateItem': getOption('--locator_type', 'app'),
            'host': getOption('--host', 'localhost'),
            'port': getOption('--port', '10053')
        }

        @engine.asynchronous
        def locateApps():
            apps = yield storage.find(*locateItems.get(config['locateItem']))
            with open('/tmp/1.txt', 'w') as fh:
                fh.write(' '.join(apps))
            if apps:
                print(' '.join(apps))

        storage = Service('storage', host=config['host'], port=int(config['port']))
        locateApps().get(timeout=ADEQUATE_TIMEOUT)
    except Exception as err:
        # Hidden log feature :)
        # with open(os.devnull, 'w') as fh:
        with open('/tmp/2.txt', 'w') as fh:
            fh.write(str(err))
