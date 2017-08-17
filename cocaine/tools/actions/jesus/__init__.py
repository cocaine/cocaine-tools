import json

from tornado.gen import coroutine

from .. import Action

DEFAULT_PREFIX = '/darkvoice/cfg'


class Mapping(object):
    """A resource mapping.

    Used to map a number of resources consumed by a single slot. For example we can allocate 1 CPU
    slot which equals to 0.5 cores.
    """

    def __init__(self, content):
        if not isinstance(content, dict):
            raise ValueError('Content must be a dict')
        self._content = content

    @property
    def content(self):
        return self._content

    @staticmethod
    def from_file(path):
        """Loads a resource mapping from a file validating its content.

        :param path: Path to the file.
        :raise IOError: On any I/O error occurred during load a file from the filesystem.
        :raise ValueError: On content decoding error.
        :return: Validated resource mapping object.
        """
        return Mapping.from_string(open(path).read())

    @staticmethod
    def from_string(v):
        """Constructs a new resource mapping from its string representation.

        :param v: Valid JSON string with resource mapping content.
        """
        return Mapping(json.loads(v))


class Runlist(object):
    """List of applications that must be run in the cluster.
   """

    def __init__(self, content):
        if not isinstance(content, dict):
            raise ValueError('Content must be a dict')
        self._content = content

    @property
    def content(self):
        return self._content

    @staticmethod
    def from_file(path):
        """Loads a runlist from a file validating its content.

        :param path: Path to the file.
        :raise IOError: On any I/O error occurred during load a file from the filesystem.
        :raise ValueError: On content decoding error.
        :return: Validated runlist object.
        """
        return Runlist.from_string(open(path).read())

    @staticmethod
    def from_string(v):
        """Constructs a new runlist from its string representation.

        :param v: Valid JSON string with runlist content.
        """
        return Runlist(json.loads(v))


class Profile(object):
    """Represents "always valid" profile.
    """

    def __init__(self, content):
        self._content = content

    @property
    def content(self):
        return self._content

    @staticmethod
    def load(path):
        """Loads a profile from a file validating its content.

        :param path: Path to the file.
        :raise IOError: On any I/O error occurred during load a file from the filesystem.
        :raise ValueError: On content decoding error.
        :return: Validated profile object.
        """
        with open(path) as fh:
            content = fh.read()

        return Profile.from_string(content)

    @staticmethod
    def from_string(v):
        """Constructs a new profile from its string representation.

        :param v: Valid JSON string with profile content.
        """
        return Profile(json.loads(v))


class ClusterConfiguration(object):
    @coroutine
    def upload_mapping(self, name, mapping):
        """Uploads a resource mapping to the configuration service.

        :param name: Mapping name.
        :param mapping: Validated resource mapping.
        """
        raise NotImplementedError()

    @coroutine
    def upload_runlist(self, name, runlist):
        """Uploads a runlist to the configuration service.

        :param name: Runlist name.
        :param runlist: Validated runlist.
        """
        raise NotImplementedError()

    @coroutine
    def upload_profile(self, name, profile):
        """Uploads a profile to the configuration service.

        :param name: Profile name.
        :param profile: Validated profile.
        """
        raise NotImplementedError()


class UnicornClusterConfiguration(ClusterConfiguration):
    def __init__(self, unicorn, cluster, prefix=DEFAULT_PREFIX):
        """Constructs a new Unicorn based cluster configuration.

        :param unicorn: Unicorn service.
        :param cluster: Cluster type.
        :param prefix: Optional prefix that is used to uniquely define a namespace where all
            configurations lay.
        """
        self._prefix = prefix
        self._cluster = cluster
        self._unicorn = unicorn

    @coroutine
    def upload_mapping(self, name, mapping):
        path = '{}/{}/mapping/{}'.format(self._prefix, self._cluster, name)

        channel = yield self._unicorn.get(path)
        content, version = yield channel.rx.get()

        if version == -1:
            channel = yield self._unicorn.create(path, mapping.content)
        else:
            channel = yield self._unicorn.put(path, mapping.content, version)

        yield channel.rx.get()

    @coroutine
    def upload_runlist(self, name, runlist):
        path = '{}/{}/runlist/{}'.format(self._prefix, self._cluster, name)

        channel = yield self._unicorn.get(path)
        content, version = yield channel.rx.get()

        if version == -1:
            channel = yield self._unicorn.create(path, runlist.content)
        else:
            channel = yield self._unicorn.put(path, runlist.content, version)

        yield channel.rx.get()

    @coroutine
    def upload_profile(self, name, profile):
        pass


class UploadMapping(Action):
    def __init__(self, name, cluster, unicorn, mapping):
        self._name = name
        self._mapping = mapping

        self._config = UnicornClusterConfiguration(unicorn, cluster)

    @coroutine
    def execute(self):
        yield self._config.upload_mapping(self._name, self._mapping)


class UploadRunlist(Action):
    def __init__(self, name, cluster, unicorn, runlist):
        self._name = name
        self._runlist = runlist

        self._config = UnicornClusterConfiguration(unicorn, cluster)

    @coroutine
    def execute(self):
        yield self._config.upload_runlist(self._name, self._runlist)
