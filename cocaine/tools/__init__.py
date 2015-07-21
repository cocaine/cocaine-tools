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

__author__ = 'EvgenySafronov <division494@gmail.com>'

log = logging.getLogger(__name__)


BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(8)
RESET_SEQ = "\033[0m"
COLOR_SEQ = "\033[1;%dm"
BOLD_SEQ = "\033[1m"


COLORS = {
    'DEBUG': WHITE,
    'INFO': GREEN,
    'WARNING': YELLOW,
    'CRITICAL': YELLOW,
    'ERROR': RED
}


class ColoredFormatter(logging.Formatter):
    def __init__(self, msg, colored=True):
        logging.Formatter.__init__(self, msg)
        self.colored = colored

    def format(self, record):
        levelname = record.levelname
        if self.colored and levelname in COLORS:
            record.msg = COLOR_SEQ % (30 + COLORS[levelname]) + str(record.msg) + RESET_SEQ
        return logging.Formatter.format(self, record)


def interactiveEmit(self, record):  # pragma: no cover
    # Monkey patch Emit function to avoid new lines between records
    try:
        if str(record.msg).endswith('... '):
            fs = '%s'
        else:
            fs = '%s\n'
        msg = self.format(record)
        stream = self.stream
        if not hasattr(logging, '_unicode') or not logging._unicode:  # if no unicode support...
            stream.write(fs % msg)
        else:
            try:
                if isinstance(msg, unicode) and getattr(stream, 'encoding', None):
                    ufs = fs.decode(stream.encoding)
                    try:
                        stream.write(ufs % msg)
                    except UnicodeEncodeError:
                        stream.write((ufs % msg).encode(stream.encoding))
                else:
                    stream.write(fs % msg)
            except UnicodeError:
                stream.write(fs % msg.encode("UTF-8"))
        self.flush()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        self.handleError(record)
