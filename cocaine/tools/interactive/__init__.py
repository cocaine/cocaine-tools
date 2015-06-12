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

import abc
import json
import os
import subprocess
import tempfile

from cocaine.decorators import coroutine
from cocaine.tools.actions import group
from cocaine.tools.actions import profile
from cocaine.tools.actions import runlist
from cocaine.tools.helpers.editor import locate_editor
from cocaine.tools.printer import printer


__author__ = 'Evgeny Safronov <division494@gmail.com>'


class BaseEditor(object):
    __metaclass__ = abc.ABCMeta

    EDITORS = ['vim', 'emacs', 'nano']

    @abc.abstractmethod
    def view(self):
        pass

    @abc.abstractmethod
    def upload(self, data):
        pass

    @coroutine
    def execute(self):
        with printer('Loading "%s"', self.name):
            content = yield self.view()

        with printer('Editing "%s"', self.name):
            with tempfile.NamedTemporaryFile(delete=False) as fh:
                name = fh.name
                fh.write(json.dumps(content, indent=4))

            ec = None

            # locate default editor
            default_editor = locate_editor()
            if default_editor is not None:
                default_editor in self.EDITORS and self.EDITORS.remove(default_editor)
                self.EDITORS.insert(0, default_editor)

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
            yield self.upload(fh.read())
        # Remove temp file
        os.unlink(name)


class RunlistEditor(BaseEditor, runlist.Specific):
    def __init__(self, *args, **kwargs):
        super(RunlistEditor, self).__init__(*args, **kwargs)

    def view(self):
        return runlist.View(self.storage, self.name).execute()

    def upload(self, data):
        return runlist.Upload(self.storage, self.name, data).execute()


class ProfileEditor(BaseEditor, profile.Specific):
    def __init__(self, *args, **kwargs):
        super(ProfileEditor, self).__init__(*args, **kwargs)

    def view(self):
        return profile.View(self.storage, self.name).execute()

    def upload(self, data):
        return profile.Upload(self.storage, self.name, data).execute()


class GroupEditor(BaseEditor, group.Specific):
    def __init__(self, *args, **kwargs):
        super(GroupEditor, self).__init__(*args, **kwargs)

    def view(self):
        return group.View(self.storage, self.name).execute()

    def upload(self, data):
        return group.Create(self.storage, self.name, data).execute()
