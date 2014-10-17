#
# Copyright (c) 2013+ Evgeny Safronov <division494@gmail.com>
# Copyright (c) 2013+ Anton Tiurin <noxiouz@yandex.ru>
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

import os

from cocaine.services import Service

from cocaine.tools.actions import app

ENV_DOCKER_ENDPOINT = "TEST_DOCKER_ENDPOINT"
DEFAULT_DOCKER_ENDPOINT = "http://localhost:5432"


# class TestDockerUpload(object):

#     def __init__(self):
#         self.storage = Service("storage")
#         self.path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
#                                  "fixtures/docker_app")
#         self.docker_address = os.getenv(ENV_DOCKER_ENDPOINT) or DEFAULT_DOCKER_ENDPOINT

#     def test_upload(self):
#         name = "test_app"
#         uploader = app.DockerUpload(self.storage, self.path,
#                                     name, "", self.docker_address)

#         res = uploader.execute().wait(30)
#         assert res is None
