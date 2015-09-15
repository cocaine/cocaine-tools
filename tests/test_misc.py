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

import json
import os
import tarfile

from cocaine.tools.actions import readArchive
from cocaine.tools.actions import CocaineConfigReader

from cocaine.tools.actions.group import validate_routing_group, GroupWithZeroTotalWeight, MalformedGroup

from cocaine.tools.helpers import JSONUnpacker

from nose import tools


@tools.raises(tarfile.TarError)
def test_read_archive():
    readArchive(os.path.join(os.path.abspath(os.path.dirname(__file__)),
                             "fixtures/simple_app/manifest.json"))


def test_config_reader():
    CocaineConfigReader.load(os.path.join(os.path.abspath(os.path.dirname(__file__)),
                             "fixtures/simple_app/manifest.json"))


def test_json():
    j = JSONUnpacker()
    data = {"A": 1}
    js = json.dumps(data)
    j.feed(js)
    j.feed(js)
    j.feed("A")
    for i in j:
        assert i == data

    assert j.buff == "A"


@tools.raises(GroupWithZeroTotalWeight)
def test_validate_group_empty():
    gr = {}
    validate_routing_group(gr)


@tools.raises(GroupWithZeroTotalWeight)
def test_validate_group_with_zero_total_weight():
    gr = {"A": 0, "B": 0}
    validate_routing_group(gr)


@tools.raises(MalformedGroup)
def test_validate_group_malformed_group_with_float():
    gr = {"A": 9.0, "B": 0}
    validate_routing_group(gr)


@tools.raises(MalformedGroup)
def test_validate_group_malformed_group_with_negative_weight():
    gr = {"A": -1, "B": 1}
    validate_routing_group(gr)


def test_validate_group():
    gr = {"A": 1,
          "B": 99999999999999999999999999}
    validate_routing_group(gr)
