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

from __future__ import print_function

import contextlib
import sys

from cocaine.tools import log


__author__ = 'EvgenySafronov <division494@gmail.com>'


ENABLE_OUTPUT = False


class Color:
    RESET = '\033[0m'
    OFFSET = 30
    BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = ['\033[1;%dm' % (OFFSET + id_) for id_ in range(8)]


class Result(object):
    def __init__(self):
        self.value = ''

    def set(self, msg, *args):
        self.value = ' - {0}'.format('{0}{1}{2}'.format(Color.WHITE, msg % args, Color.RESET))

    def __str__(self):
        return str(self.value)


def _print(status, message, color, suffix):
    status = '{0:^6}'.format(status)
    formatted = '[{0}{1}{2}] {3}{4}'.format(color, status, Color.RESET, message, suffix)
    if ENABLE_OUTPUT:
        sys.stdout.write(formatted)
        sys.stdout.flush()
    else:
        log.debug(formatted)


def print_start(message):
    _print('', message, Color.WHITE, '\r')


def print_success(message):
    _print('OK', message, Color.GREEN, '\n')


def print_error(message):
    _print('FAIL', message, Color.RED, '\n')


@contextlib.contextmanager
def printer(message, *args):
    result = Result()
    message = message % args

    try:
        print_start(message)
        yield result.set
        print_success('{0}{1}'.format(message, result))
    except Exception:
        print_error('{0}{1}'.format(message, result))
        raise
