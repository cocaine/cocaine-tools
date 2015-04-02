#!/usr/bin/env python
#
# Copyright (c) 2013+ Evgeny Safronov <division494@gmail.com>
# Copyright (c) 2011-2014 Other contributors as noted in the AUTHORS file.
#
# This file is part of Cocaine-tools.
#
# Cocaine is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# Cocaine is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

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
            'group': ['groups', ('group',)],
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

        storage = Service('storage', endpoints=[(config['host'], int(config['port']))])
        locateApps().get(timeout=ADEQUATE_TIMEOUT)
    except Exception as err:
        # Hidden log feature :)
        with open(os.devnull, 'w') as fh:
            fh.write(str(err))
