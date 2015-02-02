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
from cocaine.tools.actions import docker

from tornado.ioloop import IOLoop

from nose.plugins.skip import SkipTest
from nose import tools

io = IOLoop.current()


class TestDocker(object):
    def __init__(self):
        self.storage = Service("storage")
        self.good_path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                      "fixtures/docker_app")
        self.broken_path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                        "fixtures/broken_docker_app")
        self.docker_address = os.getenv("DOCKER_HOST")
        self.registry_address = os.getenv("DOCKER_REGISTRY")
        if not (self.docker_address and self.registry_address):
            raise SkipTest("Can't do it without Docker or Registry")
        self.client = docker.Client(self.docker_address, io_loop=io)

    def test_upload(self):
        name = "test_app"
        uploader = app.DockerUpload(self.storage, self.good_path,
                                    name, "",
                                    self.docker_address, self.registry_address)

        res = io.run_sync(uploader.execute)
        assert res is None

    @tools.raises(docker.DockerException)
    def test_failed_build(self):
        name = "test_app"
        uploader = app.DockerUpload(self.storage, self.broken_path,
                                    name, "",
                                    self.docker_address, self.registry_address)

        res = io.run_sync(uploader.execute)
        assert res is None

    def test_client_info(self):
        res = io.run_sync(self.client.info)
        assert isinstance(res, dict), type(res)

    def test_client_images(self):
        res = io.run_sync(self.client.images)
        assert isinstance(res, list), type(res)

    def test_client_containers(self):
        res = io.run_sync(self.client.containers)
        assert isinstance(res, list), type(res)
