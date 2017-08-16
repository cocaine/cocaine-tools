import json


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

        :return: Validated profile object.

        :raise IOError: On any I/O error occurred during load a file from the filesystem.
        :raise ValueError: On content decoding error.
        """
        with open(path) as fh:
            content = fh.read()

        return Profile(json.loads(content))


class ClusterConfiguration(object):
    def upload_mapping(self, name, mapping):
        """Uploads a resource mapping to the configuration service.

        :param name: Mapping name.
        :param mapping: Validated resource mapping.
        """
        raise NotImplementedError()

    def upload_runlist(self, name, runlist):
        """Uploads a runlist to the configuration service.

        :param name: Runlist name.
        :param runlist: Validated runlist.
        """
        raise NotImplementedError()

    def upload_profile(self, name, profile):
        """Uploads a profile to the configuration service.

        :param name: Profile name.
        :param profile: Validated profile.
        """
        raise NotImplementedError()


class UnicornClusterConfiguration(ClusterConfiguration):
    def __init__(self, unicorn):
        self._unicorn = unicorn

    def upload_mapping(self, name, mapping):
        pass

    def upload_runlist(self, name, runlist):
        pass

    def upload_profile(self, name, profile):
        pass
