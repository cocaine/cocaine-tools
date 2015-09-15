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

import logging
import json
import os
import time

from cocaine.services import Service, Locator
from cocaine.exceptions import ServiceError

from cocaine.tools import actions
from cocaine.tools.actions import app
from cocaine.tools.actions import common
from cocaine.tools.actions import crashlog
from cocaine.tools.actions import group
from cocaine.tools.actions import profile
from cocaine.tools.actions import runlist
from cocaine.tools.error import ToolsError
from cocaine.tools.helpers._unix import AsyncUnixHTTPClient

from nose import tools
from nose.plugins.skip import SkipTest

from tornado.testing import AsyncHTTPTestCase
from tornado import netutil
from tornado.ioloop import IOLoop

io = IOLoop.current()


log = logging.getLogger("cocaine")
log.setLevel(logging.DEBUG)


def test_list():
    st = Service("storage")
    result = io.run_sync(actions.List("app", ("apps", ), st).execute, timeout=1)
    assert isinstance(result, (list, tuple)), result


def test_specific():
    st = Service("storage")
    actions.Specific(st, "entity", "name")


@tools.raises(ValueError)
def test_specific_unspecified_name():
    st = Service("storage")
    io.run_sync(actions.Specific(st, "entity", ""), timeout=2)


def test_isJsonValid():
    valid = "{}"
    invalid = ":dsdll"
    assert actions.isJsonValid(valid)
    assert not actions.isJsonValid(invalid)


def test_locate():
    locator = Locator()
    res = io.run_sync(common.Locate(locator, "locator").execute, timeout=2)
    assert isinstance(res, dict)
    assert "api" in res
    assert "version" in res
    assert "endpoints" in res


class TestAppActions(object):
    def __init__(self):
        self.storage = Service("storage")
        self.node = Service("node")
        self.locator = Locator()

    @tools.raises(ValueError)
    def test_upload_no_appname(self):
        app.Upload(self.storage, "", "dummy_manifest", None, True)

    @tools.raises(ValueError)
    def test_upload_no_manifest(self):
        app.Upload(self.storage, "appname", "", None, True)

    @tools.raises(ValueError)
    def test_upload_no_manifest_no_package(self):
        app.Upload(self.storage, "appname", "dummy_manifest", None, False)

    @tools.raises(ValueError)
    def test_remove_no_appname(self):
        app.Remove(self.storage, "")

    @tools.raises(ToolsError)
    def test_remove_no_such_app(self):
        io.run_sync(app.Remove(self.storage, "no_such_app_name").execute, timeout=2)

    @tools.raises(ValueError)
    def test_start_no_name(self):
        app.Start(self.node, "", "dummy_profile_name")

    @tools.raises(ValueError)
    def test_start_no_profile(self):
        app.Start(self.node, "dummy_app_name", "")

    @tools.raises(ValueError)
    def test_stop_no_name(self):
        app.Stop(self.node, "")

    @tools.raises(ValueError)
    def test_restart_no_name(self):
        app.Restart(self.node, self.locator, "", "dummy_profile_name")

    @tools.raises(ToolsError)
    def test_restart_no_such_app(self):
        io.run_sync(app.Restart(self.node, self.locator,
                                "no_such_app_name", None).execute, timeout=2)

    @tools.raises(ToolsError)
    def test_check_no_such_app(self):
        io.run_sync(app.Check(self.node, self.storage,
                              self.locator, "no_such_app_name").execute, timeout=2)

    @tools.raises(ValueError)
    def test_check_no_appname(self):
        app.Check(self.node, self.storage, self.locator, "")

    def test_app_a_upload(self):
        name = "random_name"
        manifest = "{\"slave\": \"__init__.py\"}"
        path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                            "fixtures/simple_app/simple_app.tar.gz")
        result = io.run_sync(app.Upload(self.storage, name,
                             manifest, path).execute, timeout=2)

        assert result == "Uploaded successfully", result
        result = io.run_sync(app.View(self.storage, name).execute, timeout=2)
        assert result == json.loads(manifest), result

    def test_app_e_list(self):
        listing = io.run_sync(app.List(self.storage).execute, timeout=2)
        assert isinstance(listing, (list, tuple))

    def test_app_b_start(self):
        name = "random_name"
        io.run_sync(profile.Upload(self.storage, "random_profile", "{}").execute, timeout=2)
        result = io.run_sync(app.Start(self.node, name,
                             "random_profile").execute, timeout=2)
        assert "application `random_name` has been started with profile `random_profile`" == result, result

        result = io.run_sync(app.Check(self.node, self.storage, self.locator, name).execute, timeout=2)
        assert result['state'] == "running"

    def test_app_d_stop(self):
        name = "random_name"
        result = io.run_sync(app.Stop(self.node, name).execute, timeout=2)
        assert "application `random_name` has been stopped" == result, result

    @tools.raises(ToolsError)
    def test_app_d_stop_after_check(self):
        name = "random_name"
        io.run_sync(app.Check(self.node, self.storage, self.locator, name).execute, timeout=2)

    def test_app_c_restart(self):
        name = "random_name"
        profile_name = "random_profile"
        result = io.run_sync(app.Restart(self.node, self.locator,
                                         name, profile_name).execute, timeout=2)

        assert "application `random_name` has been restarted with profile `random_profile`" == result, result

    def test_node_info(self):
        n = common.NodeInfo(self.node, self.locator, self.storage)
        result = io.run_sync(n.execute, timeout=2)
        assert isinstance(result, dict) and "apps" in result, result

    def test_app_f_remove(self):
        result = io.run_sync(app.Remove(self.storage, "random_name").execute, timeout=2)
        assert result == "Removed successfully"


class TestProfileActions(object):
    def __init__(self):
        self.storage = Service("storage")
        self.node = Service("node")

    def test_profile(self):
        name = "dummy_profile_name %d" % time.time()
        copyname = "copy_%s" % name
        renamedname = "move_%s" % name
        dummy_profile = {"aaa": [1, 2, 3]}
        io.run_sync(profile.Upload(self.storage, name, dummy_profile).execute, timeout=2)

        io.run_sync(profile.Copy(self.storage, name, copyname).execute, timeout=2)
        io.run_sync(profile.Rename(self.storage, copyname, renamedname).execute, timeout=2)

        listing = io.run_sync(profile.List(self.storage).execute, timeout=2)
        assert isinstance(listing, (list, tuple)), listing
        assert name in listing
        assert copyname not in listing
        assert renamedname in listing

        pr = io.run_sync(profile.View(self.storage, name).execute, timeout=2)
        assert pr == dummy_profile

        io.run_sync(profile.Remove(self.storage, name).execute, timeout=2)
        try:
            io.run_sync(profile.View(self.storage, name).execute, timeout=2)
        except ServiceError:
            pass
        else:
            raise AssertionError("an exception is expected")

    @tools.raises(ValueError)
    def test_upload_invalid_value(self):
        profile.Upload(self.storage, "dummy", None)

    @tools.raises(ToolsError)
    def test_copy_value_error(self):
        profile.Copy(None, "the_same", "the_same")


class TestRunlistActions(object):
    def __init__(self):
        self.storage = Service("storage")

    def test_runlist(self):
        name = "dummy_runlist %d" % time.time()
        copyname = "copy_%s" % name
        renamedname = "move_%s" % name
        app_name = "test_app"
        profile_name = "test_profile"
        dummy_runlist = {app_name: profile_name}
        io.run_sync(runlist.Upload(self.storage, name, dummy_runlist).execute, timeout=2)

        io.run_sync(runlist.Copy(self.storage, name, copyname).execute, timeout=2)
        io.run_sync(runlist.Rename(self.storage, copyname, renamedname).execute, timeout=2)

        listing = io.run_sync(runlist.List(self.storage).execute, timeout=2)
        assert isinstance(listing, (list, tuple)), listing
        assert name in listing
        assert copyname not in listing
        assert renamedname in listing

        res = io.run_sync(runlist.View(self.storage, name).execute, timeout=2)
        assert isinstance(res, dict), res
        assert res == dummy_runlist, res

        io.run_sync(runlist.Remove(self.storage, name).execute, timeout=2)
        try:
            io.run_sync(runlist.View(self.storage, name).execute, timeout=2)
        except ServiceError:
            pass

        io.run_sync(runlist.Create(self.storage, name).execute, timeout=2)
        res = io.run_sync(runlist.View(self.storage, name).execute, timeout=2)
        assert res == {}, res

        res = io.run_sync(runlist.AddApplication(self.storage, name, app_name, profile_name, force=False).execute, timeout=2)
        assert isinstance(res, dict), res
        assert "added" in res, res
        assert app_name == res["added"]["app"] and profile_name == res["added"]["profile"], res

        res = io.run_sync(runlist.AddApplication(self.storage, "ZZ" + name, app_name, profile_name, force=True).execute, timeout=2)
        assert isinstance(res, dict), res
        assert "added" in res, res
        assert app_name == res["added"]["app"] and profile_name == res["added"]["profile"], res

        missing_name = "ZZZZ" + app_name
        res = io.run_sync(runlist.RemoveApplication(self.storage, name, missing_name).execute, timeout=2)
        assert res['status'] == "the application named %s is not in runlist" % missing_name

        res = io.run_sync(runlist.RemoveApplication(self.storage, name, app_name).execute, timeout=2)
        assert isinstance(res, dict), res

    @tools.raises(ToolsError)
    def test_copy_value_error(self):
        runlist.Copy(None, "the_same", "the_same")

    @tools.raises(ValueError)
    def test_upload_value_error(self):
        runlist.Upload(None, "the_same", None)

    @tools.raises(ValueError)
    def test_add_application_no_appname(self):
        runlist.AddApplication(None, "dummy_name", "", None, force=True)

    @tools.raises(ValueError)
    def test_add_application_no_profile(self):
        runlist.AddApplication(None, "dummy_name", "dummy", None, force=True)

    @tools.raises(ValueError)
    def test_remove_application_no_appname(self):
        runlist.RemoveApplication(None, "dummy_name", "")

    @tools.raises(ToolsError)
    def test_remove_application_no_runlist(self):
        action = runlist.RemoveApplication(self.storage, "dummy_random_name", "appname")
        io.run_sync(action.execute, timeout=2)


class TestGroupActions(object):
    def __init__(self):
        self.storage = Service("storage")
        self.locator = Locator()

    def test_group(self):
        name = "dummy_group %d" % time.time()
        copyname = "copy_%s" % name
        renamedname = "move_%s" % name
        app_name = "test_app"
        weight = 100
        dummy_group = {app_name: weight}
        io.run_sync(group.Create(self.storage, name, dummy_group).execute, timeout=2)

        io.run_sync(group.Copy(self.storage, name, copyname).execute, timeout=2)
        io.run_sync(group.Rename(self.storage, copyname, renamedname).execute, timeout=2)

        listing = io.run_sync(group.List(self.storage).execute, timeout=2)
        assert isinstance(listing, (list, tuple)), listing
        assert name in listing
        assert copyname not in listing
        assert renamedname in listing

        res = io.run_sync(group.View(self.storage, name).execute, timeout=2)
        assert isinstance(res, dict), res
        assert res == dummy_group, res

        io.run_sync(group.Remove(self.storage, name).execute, timeout=2)
        try:
            io.run_sync(group.View(self.storage, name).execute, timeout=2)
        except ServiceError:
            pass

        io.run_sync(group.Create(self.storage, name, '{"A": 1}').execute, timeout=2)
        res = io.run_sync(group.View(self.storage, name).execute, timeout=2)
        assert res == {'A': 1}, res

        res = io.run_sync(group.AddApplication(self.storage, name, app_name, weight).execute, timeout=2)
        assert res is None, res

        res = io.run_sync(group.RemoveApplication(self.storage, name, app_name).execute, timeout=2)
        assert res is None, res

        io.run_sync(group.Refresh(self.locator, self.storage, name).execute, timeout=2)

    def test_refresh(self):
        io.run_sync(group.Refresh(self.locator, self.storage, None).execute, timeout=2)

    @tools.raises(ValueError)
    def test_validation_in_create(self):
        bad_content = {"A": 1.0}
        io.run_sync(group.Create(self.storage, "bad_group", bad_content).execute, timeout=2)

    @tools.raises(ToolsError)
    def test_group_rename_itself(self):
        group.Copy(self.storage, "itself", "itself")


class TestCrashlogsAction(object):
    def __init__(self):
        self.storage = Service("storage")

    def test_crashlog(self):
        listing = io.run_sync(crashlog.List(self.storage, "TEST").execute, timeout=2)
        assert isinstance(listing, (list, tuple)), listing


class HTTPUnixClientTestCase(AsyncHTTPTestCase):
    def setUp(self):
        super(HTTPUnixClientTestCase, self).setUp()
        self.socket_path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                        "test_socket")
        self.http_server = self.get_http_server()
        sock = netutil.bind_unix_socket(self.socket_path)
        self.http_server.add_sockets([sock])

    def get_app(self):
        def handle_request(request):
            message = "You requested %s\n" % request.uri
            request.write("HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" %
                          (len(message), message))
            request.finish()
        return handle_request

    def tearDown(self):
        super(HTTPUnixClientTestCase, self).tearDown()
        try:
            os.remove(self.socket_path)
        except:
            pass

    def test_Client(self):
        http_client = AsyncUnixHTTPClient(self.io_loop, self.socket_path)
        http_client.fetch("http://localhost", self.stop)
        response = self.wait()
        self.assertEqual(200, response.code)
