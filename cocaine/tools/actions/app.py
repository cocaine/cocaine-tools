#
# Copyright (c) 2013+ Anton Tyurin <noxiouz@yandex.ru>
# Copyright (c) 2013+ Evgeny Safronov <division494@gmail.com>
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
import os
import re
import shutil
import tarfile
import tempfile

import msgpack
from tornado import gen

from cocaine.decorators import coroutine
from cocaine.exceptions import LocatorResolveError
from cocaine.exceptions import ServiceError
from cocaine.tools import actions, log
from cocaine.tools.actions import common, readArchive, CocaineConfigReader, docker
from cocaine.tools.actions.common import NodeInfo
from cocaine.tools.error import ToolsError
from cocaine.tools.installer import PythonModuleInstaller, ModuleInstallError, _locateFile
from cocaine.tools.printer import printer
from cocaine.tools.repository import GitRepositoryDownloader, RepositoryDownloadError
from cocaine.tools.tags import APPS_TAGS

__author__ = 'Evgeny Safronov <division494@gmail.com>'

WRONG_APPLICATION_NAME = 'Application "{0}" is not valid application'

venvFactory = {
    'None': None,
    'P': PythonModuleInstaller,
    'R': None,
    'J': None
}


# class Specific(actions.Specific):
#     def __init__(self, storage, name):
#         super(Specific, self).__init__(storage, 'application', name)


class List(actions.List):
    def __init__(self, storage):
        super(List, self).__init__('manifests', APPS_TAGS, storage)


class View(actions.View):
    def __init__(self, storage, name):
        super(View, self).__init__(storage, 'application', name, 'manifests')


class Upload(actions.Storage):
    """
    Storage action class that tries to upload application into storage asynchronously
    """

    def __init__(self, storage, name, manifest, package=None, manifest_only=False):
        super(Upload, self).__init__(storage)
        self.name = name
        self.manifest = manifest
        if manifest_only:
            self.package = None
        else:
            self.package = package
            if not self.package:
                raise ValueError('Please specify package of the app')

        if not self.name:
            raise ValueError('Please specify name of the app')
        if not self.manifest:
            raise ValueError('Please specify manifest of the app')

    @coroutine
    def execute(self):
        with printer('Loading manifest'):
            manifest = CocaineConfigReader.load(self.manifest)

        #  Not only a manifest is being uploaded,
        #  self.package could be None if manifest_only=True
        if self.package is not None:
            with printer('Reading package "%s"', self.package):
                package = msgpack.dumps(readArchive(self.package))

            with printer('Uploading application "%s"', self.name):
                channel = yield self.storage.write('apps', self.name, package, APPS_TAGS)
                yield channel.rx.get()

        with printer('Uploading manifest'):
            channel = yield self.storage.write('manifests', self.name, manifest, APPS_TAGS)
            yield channel.rx.get()

        raise gen.Return("Uploaded successfully")


class Remove(actions.Storage):
    """
    Storage action class that removes application 'name' from storage
    """

    def __init__(self, storage, name):
        super(Remove, self).__init__(storage)
        self.name = name
        if not self.name:
            raise ValueError('Empty application name')

    @coroutine
    def execute(self):
        with printer('Removing "%s"', self.name):
            apps = yield List(self.storage).execute()
            if self.name not in apps:
                raise ToolsError('application "{0}" does not exist'.format(self.name))
            channel = yield self.storage.remove('manifests', self.name)
            yield channel.rx.get()
            try:
                channel = yield self.storage.remove('apps', self.name)
                yield channel.rx.get()
            except ServiceError:
                log.info('Unable to delete an application source from storage. ',
                         'It\'s okay, if the application is a Docker image')

        raise gen.Return("Removed successfully")


class Start(common.Node):
    def __init__(self, node, name, profile):
        super(Start, self).__init__(node)
        self.name = name
        self.profile = profile
        if not self.name:
            raise ValueError('Please specify application name')
        if not self.profile:
            raise ValueError('Please specify profile name')

    @coroutine
    def execute(self):
        channel = yield self.node.start_app(self.name, self.profile)
        yield channel.rx.get()
        raise gen.Return("application `%s` has been started with profile `%s`" % (self.name,
                                                                                  self.profile))


class Stop(common.Node):
    def __init__(self, node, name):
        super(Stop, self).__init__(node)
        self.name = name
        if not self.name:
            raise ValueError('Please specify application name')

    @coroutine
    def execute(self):
        channel = yield self.node.pause_app(self.name)
        yield channel.rx.get()
        raise gen.Return("application `%s` has been stopped" % self.name)


class Restart(common.Node):
    def __init__(self, node, locator, name, profile):
        super(Restart, self).__init__(node)
        self.locator = locator
        self.name = name
        self.profile = profile
        if not self.name:
            raise ValueError('Please specify application name')

    @coroutine
    def execute(self):
        try:
            if self.profile:
                profile = self.profile
            else:
                info = yield NodeInfo(self.node, self.locator, self.name).execute()
                app_info = info['apps'][self.name]
                if not isinstance(app_info, dict):
                    raise ToolsError('Unable to determine a profile name from info: %s', app_info)
                profile = app_info['profile']['name']
            try:
                yield Stop(self.node, name=self.name).execute()
            except ServiceError as err:  # application is not running
                pass
            yield Start(self.node, name=self.name, profile=profile).execute()
        except KeyError:
            raise ToolsError('Application "{0}" is not running and profile not specified'.format(self.name))
        except ServiceError as err:
            raise ToolsError('Unknown error - {0}'.format(err))

        raise gen.Return("application `%s` has been restarted with profile `%s`" % (self.name, profile))


class Check(common.Node):
    def __init__(self, node, storage, locator, name):
        super(Check, self).__init__(node)
        self.name = name
        self.storage = storage
        self.locator = locator
        if not self.name:
            raise ValueError('Please specify application name')

    @coroutine
    def execute(self):
        log.info('Checking "%s"... ', self.name)
        apps = yield List(self.storage).execute()
        if self.name not in apps:
            raise ToolsError('not available')

        try:
            channel = yield self.node.info(self.name)
            info = yield channel.rx.get()
            log.info(info['state'])
        except (LocatorResolveError, ServiceError):
            raise ToolsError('stopped')
        raise gen.Return(info)


class DockerUpload(actions.Storage):
    def __init__(self, storage, path, name, manifest, address, registry='', on_read=None):
        super(DockerUpload, self).__init__(storage)
        self.path = path or os.path.curdir
        self.name = name or os.path.basename(os.path.abspath(self.path))
        if registry:
            self.fullname = '{0}/{1}'.format(registry, self.name)
        else:
            self.fullname = self.name

        self.manifest = manifest

        self.client = docker.Client(address)

        log.debug('checking Dockerfile')
        if not address:
            raise ValueError('Docker address is not specified')

        if on_read is not None:
            if not callable(on_read):
                raise ValueError("on_read must ne callable")
            self._on_read = on_read

        self._last_message = ''

    @coroutine
    def execute(self):
        log.debug('application name will be: %s', self.fullname)

        if self.manifest:
            manifestPath = self.manifest
        else:
            try:
                manifestPath = _locateFile(self.path, 'manifest.json')
            except IOError:
                log.error("unable to locate manifest.json")
                raise ToolsError("unable to locate manifest.json")

        with printer('Loading manifest'):
            manifest = CocaineConfigReader.load(manifestPath)

        with printer('Uploading manifest'):
            channel = yield self.storage.write('manifests', self.name, manifest, APPS_TAGS)
            yield channel.rx.get()

        try:
            response = yield self.client.build(self.path, tag=self.fullname, streaming=self._on_read)
            if response.code != 200:
                raise ToolsError('building failed with error code {0} {1}'.format(response.code,
                                                                                  response.body))
            response = yield self.client.push(self.fullname, auth={}, streaming=self._on_read)
            if response.code != 200:
                raise ToolsError('pushing failed with error code {0} {1}'.format(response.code,
                                                                                 response.body))
        except Exception as err:
            log.error("Error occurred. %s Erase manifest" % err)
            channel = yield self.storage.remove('manifests', self.name)
            yield channel.rx.get()
            raise err

    def _on_read(self, value):
        if self._last_message != value:
            self._last_message = value
            print(value)


class DockerImport(actions.Storage):
    def __init__(self, storage, path, name, manifest, address, container, registry='', on_read=None):

        print "__init", storage, path, name, manifest, address, container

        super(DockerImport, self).__init__(storage)
        self.path = path or os.path.curdir
        self.name = name or os.path.basename(os.path.abspath(self.path))
        self.container_url = container
        if registry:
            self.fullname = '{0}/{1}'.format(registry, self.name)
        else:
            self.fullname = self.name

        self.manifest = manifest

        self.client = docker.Client(address)

        log.debug('checking Dockerfile')
        if not address:
            raise ValueError('Docker address is not specified')

        if on_read is not None:
            if not callable(on_read):
                raise ValueError("on_read must ne callable")
            self._on_read = on_read

        self._last_message = ''

    @coroutine
    def execute(self):
        log.debug('application name will be: %s', self.fullname)

        if self.manifest:
            manifestPath = self.manifest
        else:
            try:
                manifestPath = _locateFile(self.path, 'manifest.json')
            except IOError:
                log.error("unable to locate manifest.json")
                raise ToolsError("unable to locate manifest.json")

        with printer('Loading manifest'):
            manifest = CocaineConfigReader.load(manifestPath)

        with printer('Uploading manifest'):
            yield self.storage.write('manifests', self.name, manifest, APPS_TAGS)

        try:
            response = yield self.client.pull(self.container_url, {}, streaming=self._on_read)
            if response.code != 200:
                raise ToolsError('building failed with error code {0} {1}'.format(response.code,
                                                                                  response.body))

            response = yield self.client.tag(self.container_url, {}, self.fullname, streaming=self._on_read)
            if response.code != 200 and response.code != 201:
                raise ToolsError('building failed with error code {0} {1}'.format(response.code,
                                                                                  response.body))

            response = yield self.client.push(self.fullname, {}, streaming=self._on_read)
            if response.code != 200:
                raise ToolsError('pushing failed with error code {0} {1}'.format(response.code,
                                                                                 response.body))
        except Exception as err:
            log.error("Error occurred. Erase manifest")
            yield self.storage.remove('manifests', self.name)
            raise err

    def _on_read(self, value):
        if self._last_message != value:
            self._last_message = value
            print(value)


class LocalUpload(actions.Storage):
    def __init__(self, storage, path, name, manifest):
        super(LocalUpload, self).__init__(storage)
        self.path = path or os.path.curdir
        self.name = name
        self.manifest = manifest
        self.virtualEnvironmentType = 'None'
        if not self.name:
            self.name = os.path.basename(os.path.abspath(self.path))
        if not self.name:
            raise ValueError(WRONG_APPLICATION_NAME.format(self.name))

    @coroutine
    def execute(self):
        try:
            repositoryPath = self._createRepository()
            if self.manifest:
                manifestPath = self.manifest
            else:
                manifestPath = _locateFile(self.path, 'manifest.json')
            Installer = venvFactory[self.virtualEnvironmentType]
            if Installer:
                yield self._createVirtualEnvironment(repositoryPath, manifestPath, Installer)
                manifestPath = os.path.join(repositoryPath, 'manifest.json')
            else:
                pass

            packagePath = self._createPackage(repositoryPath)
            yield Upload(self.storage, **{
                'name': self.name,
                'manifest': manifestPath,
                'package': packagePath
            }).execute()
        except (RepositoryDownloadError, ModuleInstallError) as err:
            log.error(err)

    def _createRepository(self):
        with printer('Creating temporary directory') as p:
            repositoryPath = tempfile.mkdtemp()
            p(repositoryPath)

        with printer('Copying "%s" to "%s"', self.path, repositoryPath):
            repositoryPath = os.path.join(repositoryPath, 'repo')
            log.debug('Repository temporary path - "{0}"'.format(repositoryPath))
            shutil.copytree(self.path, repositoryPath)
            return repositoryPath

    @coroutine
    def _createVirtualEnvironment(self, repositoryPath, manifestPath, Installer):
        log.debug('Creating virtual environment "{0}"...'.format(self.virtualEnvironmentType))
        stream = None
        for handler in log.handlers:
            if isinstance(handler, logging.StreamHandler) and hasattr(handler, 'fileno'):
                stream = handler.stream
                break
        installer = Installer(path=repositoryPath, outputPath=repositoryPath, manifestPath=manifestPath, stream=stream)
        installer.install()

    def _createPackage(self, repositoryPath):
        with printer('Creating package'):
            packagePath = os.path.join(repositoryPath, 'package.tar.gz')
            tar = tarfile.open(packagePath, mode='w:gz')
            tar.add(repositoryPath, arcname='')
            tar.close()
            return packagePath


class UploadRemote(actions.Storage):
    def __init__(self, storage, path, name):
        super(UploadRemote, self).__init__(storage)
        self.url = path
        self.name = name
        if not self.url:
            raise ValueError('Please specify repository URL')
        if not self.name:
            rx = re.compile(r'^.*/(?P<name>.*?)(\..*)?$')
            match = rx.match(self.url)
            self.name = match.group('name')

    @coroutine
    def execute(self):
        repositoryPath = tempfile.mkdtemp()
        manifestPath = os.path.join(repositoryPath, 'manifest-start.json')
        packagePath = os.path.join(repositoryPath, 'package.tar.gz')
        self.repositoryDownloader = GitRepositoryDownloader()
        self.moduleInstaller = PythonModuleInstaller(repositoryPath, manifestPath)
        print('Repository path: {0}'.format(repositoryPath))
        try:
            yield self.cloneRepository(repositoryPath)
            yield self.installRepository()
            yield self.createPackage(repositoryPath, packagePath)
            yield Upload(self.storage, **{
                'name': self.name,
                'manifest': manifestPath,
                'package': packagePath
            }).execute()
        except (RepositoryDownloadError, ModuleInstallError) as err:
            print(err)

    @coroutine
    def cloneRepository(self, repositoryPath):
        self.repositoryDownloader.download(self.url, repositoryPath)

    @coroutine
    def installRepository(self):
        self.moduleInstaller.install()

    @coroutine
    def createPackage(self, repositoryPath, packagePath):
        tar = tarfile.open(packagePath, mode='w:gz')
        tar.add(repositoryPath, arcname='')
