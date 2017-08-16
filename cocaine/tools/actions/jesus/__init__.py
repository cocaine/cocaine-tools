class Profile(object):
    def __init__(self, name, content):
        pass

    @staticmethod
    def load(name, path):
        pass


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
