from cocaine.services import Service
from cocaine.exceptions import ConnectionError, ConnectionRefusedError

from cocaine.tools import actions
from cocaine.tools.actions import app

from nose import tools


@tools.raises(ConnectionRefusedError, ConnectionError)
def test_storage_bad_address():
    st = actions.Storage()
    st.connect(port=10055)


def test_list():
    st = Service("storage")
    result = actions.List("apps", ["app"], st).execute().wait(4)
    assert isinstance(result, (list, tuple)), result


def test_specific():
    st = Service("storage")
    actions.Specific(st, "entity", "name")


@tools.raises(ValueError)
def test_specific_unspecified_name():
    st = Service("storage")
    actions.Specific(st, "entity", "")


def test_isJsonValid():
    valid = "{}"
    invalid = ":dsdll"
    assert actions.isJsonValid(valid)
    assert not actions.isJsonValid(invalid)


@tools.raises(Exception)
def test_view():
    st = Service("storage")
    view = actions.View(st, "profile", "TEST2", "profiles")
    profile = view.execute().wait(1)
    assert profile is not None, profile


class TestAppActions(object):
    def __init__(self):
        self.storage = Service("storage")

    def test_list(self):
        listing = app.List(self.storage).execute().wait(4)
        assert isinstance(listing, (list, tuple))
