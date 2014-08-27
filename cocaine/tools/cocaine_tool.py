#!/usr/bin/env python
#
# Copyright (c) 2013+ Anton Tyurin <noxiouz@yandex.ru>
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

import logging
import errno

from cocaine.tools import printer
from cocaine.tools.dispatcher import d


printer.ENABLE_OUTPUT = True


__author__ = 'EvgenySafronov <division494@gmail.com>'


__doc__ = '''Provides helpful tools for management, viewing, uploading and other actions with cocaine applications
and services'''


log = logging.getLogger('cocaine.tools')


def main():
    try:
        d.dispatch()
    except KeyboardInterrupt:
        log.error('Terminated by user')
        exit(errno.EINTR)
    except Exception as err:
        log.error('Unknown error occurred - %s', err)
        exit(128)

if __name__ == '__main__':
    main()
