#
#    Copyright (c) 2013+ Evgeny Safronov <division494@gmail.com>
#    Copyright (c) 2011-2013 Other contributors as noted in the AUTHORS file.
#
#    This file is part of Cocaine.
#
#    Cocaine is free software; you can redistribute it and/or modify
#    it under the terms of the GNU Lesser General Public License as published
#    by the Free Software Foundation; either version 3 of the License, or
#    (at your option) any later version.
#
#    Cocaine is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#    GNU Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public License
#    along with this program. If not, see <http://www.gnu.org/licenses/>.
#

import json
import subprocess
import tempfile

from cocaine.futures import chain
from cocaine.tools.printer import printer
from cocaine.tools.actions import runlist

__author__ = 'Evgeny Safronov <division494@gmail.com>'


class Edit(runlist.Specific):
    EDITORS = ['vim', 'emacs', 'nano']

    @chain.source
    def execute(self):
        with printer('Loading "%s"', self.name):
            content = yield runlist.View(self.storage, self.name).execute()

        with printer('Editing "%s"', self.name):
            with tempfile.NamedTemporaryFile(delete=False) as fh:
                name = fh.name
                fh.write(json.dumps(content))

            ec = None
            for editor in self.EDITORS:
                try:
                    ec = subprocess.call([editor, name])
                    break
                except OSError:
                    continue

            if ec is None:
                raise ValueError('cannot open runlist for editing - any of {0} editors not found'.format(self.EDITORS))
            if ec != 0:
                raise ValueError('editing failed with exit code {0}'.format(ec))

        with open(name) as fh:
            yield runlist.Upload(self.storage, self.name, fh.read()).execute()
