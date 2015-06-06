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

import base64
import json
import os
import tarfile
import StringIO
import urllib

from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.httputil import HTTPHeaders
from tornado.ioloop import IOLoop
from tornado import gen

from cocaine.tools import log
from cocaine.decorators import coroutine
from cocaine.tools.helpers._unix import AsyncUnixHTTPClient
from cocaine.tools.helpers.dockertemplate import dockerchef, dockerpuppet
from cocaine.tools.helpers import JSONUnpacker

__author__ = 'Evgeny Safronov <division494@gmail.com>'

DEFAULT_TIMEOUT = 3600.0
DEFAULT_URL = 'unix://var/run/docker.sock'
DEFAULT_VERSION = '1.15'
REGISTRY_AUTH_HEADER = 'X-Registry-Auth'
DEFAULT_INDEX_URL = 'https://index.docker.io/v1/'


class DockerException(Exception):
    pass


def expand_registry_url(hostname):
    if hostname.startswith('http:') or hostname.startswith('https:'):
        if '/' not in hostname[9:]:
            hostname += '/v1/'
        return hostname
    return 'http://' + hostname + '/v1/'


def resolve_repository_name(fullname):
    if '://' in fullname:
        raise ValueError('repository name can not contain a scheme ({0})'.format(fullname))

    parts = fullname.split('/', 1)
    if '.' not in parts[0] and ':' not in parts[0] and parts[0] != 'localhost':
        return DEFAULT_INDEX_URL, fullname

    if len(parts) < 2:
        raise ValueError('invalid repository name ({0})'.format(fullname))

    if 'index.docker.io' in parts[0]:
        raise ValueError('invalid repository name, try "{0}" instead'.format(parts[1]))

    return expand_registry_url(parts[0]), parts[1]


class Client(object):
    def __init__(self, url=DEFAULT_URL, version=DEFAULT_VERSION, timeout=DEFAULT_TIMEOUT, io_loop=None):
        self.url = url
        self.version = version
        self.timeout = timeout
        self._io_loop = io_loop or IOLoop.current()
        self.config = {
            'url': url,
            'version': version,
            'timeout': timeout,
            'io_loop': io_loop or IOLoop.current()
        }

    def info(self):
        return Info(**self.config).execute()

    def images(self):
        return Images(**self.config).execute()

    def containers(self):
        return Containers(**self.config).execute()

    def build(self, path, tag=None, quiet=False, streaming=None):
        return Build(path, tag, quiet, streaming, **self.config).execute()

    def pull(self, name, auth, streaming=None):
        return Pull(name, auth, streaming, **self.config).execute()

    def tag(self, name, auth, tag, streaming=None):
        return Tag(name, auth, tag, streaming, **self.config).execute()

    def push(self, name, auth, streaming=None):
        return Push(name, auth, streaming, **self.config).execute()


class Action(object):
    def __init__(self, url, version, timeout=DEFAULT_TIMEOUT, io_loop=None):
        self._unix = url.startswith('unix://')

        self._version = version
        self.timeout = timeout
        self._io_loop = io_loop
        if self._unix:
            # url should mimicry to http://
            # to pass an urlscheme check in _HTTPConnetcion.
            # Overriden Unix resolver'll return proper url and AF.
            self._base_url = "http://unixsocket"
            self._http_client = AsyncUnixHTTPClient(self._io_loop, url)
        else:
            self._base_url = '{0}/v{1}'.format(url, version)
            self._http_client = AsyncHTTPClient(self._io_loop)

    def execute(self):
        raise NotImplementedError

    def _make_url(self, path, query=None):
        if query is not None:
            query = dict((k, v) for k, v, in query.items() if v is not None)
            return '{0}{1}?{2}'.format(self._base_url, path, urllib.urlencode(query))
        else:
            return '{0}{1}'.format(self._base_url, path)


class Info(Action):
    @coroutine
    def execute(self):
        response = yield self._http_client.fetch(self._make_url('/info'),
                                                 allow_ipv6=True)
        raise gen.Return(json.loads(response.body))


class Images(Action):
    @coroutine
    def execute(self):
        response = yield self._http_client.fetch(self._make_url('/images/json'),
                                                 allow_ipv6=True)
        raise gen.Return(json.loads(response.body))


class Containers(Action):
    @coroutine
    def execute(self):
        response = yield self._http_client.fetch(self._make_url('/containers/json'),
                                                 allow_ipv6=True)
        raise gen.Return(json.loads(response.body))


class StreamingAction(Action):
    def __init__(self, auth=None, streaming=None, url=DEFAULT_URL,
                 version=DEFAULT_VERSION, timeout=DEFAULT_TIMEOUT, io_loop=None):
        self.auth = auth or {}
        self._streaming = streaming
        self._jsonunpacker = JSONUnpacker()

        self._lasterr = None
        super(StreamingAction, self).__init__(url, version, timeout, io_loop)

    def _prepare_auth_header_value(self):
        username = self.auth.get('username', 'username')
        password = self.auth.get('password', 'password')
        return base64.b64encode('{0}:{1}'.format(username, password))

    def _handle_message(self, message):
        if "stream" in message:
            log.info(message["stream"].rstrip('\n'))
        elif "error" in message:
            error_msg = message["error"].rstrip('\n')
            self._lasterr = DockerException(error_msg)
            log.error(error_msg)

        if self._streaming is not None:
            self._streaming(message)

    def _on_body(self, data):
        self._jsonunpacker.feed(data)
        for i in self._jsonunpacker:
            self._handle_message(i)


class Build(StreamingAction):
    def __init__(self, path, tag=None, quiet=False, *args, **kwargs):
        super(Build, self).__init__(*args, **kwargs)
        self._path = path
        self._tag = tag
        self._quiet = quiet

    @coroutine
    def execute(self):
        headers = None
        body = ''
        remote = None

        if any(map(self._path.startswith, ['http://', 'https://', 'git://', 'github.com/'])):
            log.info('Remote url detected: "%s"', self._path)
            remote = self._path
        else:
            log.info('Local path detected. Creating archive "%s"... ', self._path)
            headers = {'Content-Type': 'application/tar'}
            body = self.make_env(self._path)
            log.info('OK')

        query = {'t': self._tag,
                 'remote': remote,
                 'q': self._quiet}

        url = self._make_url('/build', query)
        log.info('Building "%s"... ', url)
        request = HTTPRequest(url,
                              method='POST',
                              headers=headers, body=body,
                              request_timeout=self.timeout,
                              allow_ipv6=True,
                              streaming_callback=self._on_body)
        try:
            result = yield self._http_client.fetch(request)
            if self._lasterr is not None:
                raise self._lasterr
            log.info('OK')
        except Exception as err:
            log.error('FAIL - %s', err)
            raise err
        else:
            raise gen.Return(result)

    def _tar(self, path):
        stream = StringIO.StringIO()
        try:
            tar = tarfile.open(mode='w', fileobj=stream)
            tar.add(path, arcname='.')
            return stream.getvalue()
        finally:
            stream.close()

    def make_env(self, path):
        if os.path.exists(os.path.join(path, "Dockerfile")):
            log.info("Dockerfile exists")
            return self._tar(path)
        elif os.path.isdir(os.path.join(path, "cookbooks")):
            log.info("Chef has been detected")
            dockfilecontent = dockerchef.generate(basecontainer="ubuntu:precise",
                                                  cookbooks="cookbooks")
        elif os.path.isdir(os.path.join(path, "puppet")):
            log.info("Puppet has been detected")
            if not os.path.exists(os.path.join(path, "puppet/cocaine.pp")):
                raise ValueError("You have to name your own Puppet manifest 'cocaine.pp'")

            dockfilecontent = dockerpuppet.generate(basecontainer="ubuntu:precise")
        else:
            raise ValueError("Please, create Dockerfile or Puppet manifest or Chef recipe")

        log.info("Generate Dockerfile")
        log.info(dockfilecontent)
        with open("Dockerfile", "w") as dockerfile:
            dockerfile.write(dockfilecontent)
        try:
            return self._tar(path)
        finally:
            os.unlink("Dockerfile")


class Push(StreamingAction):
    def __init__(self, name, *args, **kwargs):
        self.name = name
        super(Push, self).__init__(*args, **kwargs)

    @coroutine
    def execute(self):
        url = self._make_url('/images/{0}/push'.format(self.name))
        registry, name = resolve_repository_name(self.name)

        headers = HTTPHeaders()
        headers.add(REGISTRY_AUTH_HEADER, self._prepare_auth_header_value())
        body = ''
        log.info('Pushing "%s" into "%s"... ', name, registry)
        log.debug('Pushing url: %s', url)
        request = HTTPRequest(url, method='POST',
                              headers=headers,
                              body=body,
                              allow_ipv6=True,
                              request_timeout=self.timeout,
                              streaming_callback=self._on_body)
        try:
            result = yield self._http_client.fetch(request)
            if self._lasterr is not None:
                raise self._lasterr
            log.info('OK')
        except Exception as err:
            log.error('FAIL - %s', err)
            raise err

        raise gen.Return(result)


class Pull(StreamingAction):
    def __init__(self, name, *args, **kwargs):
        self.name = name
        super(Pull, self).__init__(*args, **kwargs)

    @coroutine
    def execute(self):
        url = self._make_url('/images/create', query={"fromImage": self.name})
        registry, name = resolve_repository_name(self.name)

        headers = HTTPHeaders()
        headers.add(REGISTRY_AUTH_HEADER, self._prepare_auth_header_value())
        body = ''
        log.info('Pulling "%s" ... ', name)
        request = HTTPRequest(url, method='POST',
                              headers=headers,
                              body=body,
                              allow_ipv6=True,
                              request_timeout=self.timeout,
                              streaming_callback=self._on_body)
        try:
            result = yield self._http_client.fetch(request)
            if self._lasterr is not None:
                raise self._lasterr
            log.info('OK')
        except Exception as err:
            log.error('FAIL - %s', err)
            raise err

        raise gen.Return(result)


class Tag(StreamingAction):
    def __init__(self, name, tag, *args, **kwargs):
        self.name = name
        self.tag = tag
        super(Tag, self).__init__(*args, **kwargs)

    @coroutine
    def execute(self):
        url = self._make_url('/images/{0}/tag'.format(self.name), query={"repo": self.tag})
        registry, name = resolve_repository_name(self.name)

        headers = HTTPHeaders()
        headers.add(REGISTRY_AUTH_HEADER, self._prepare_auth_header_value())
        body = ''
        log.info('Tagging "%s" with "%s"" ... ', name, self.tag)
        request = HTTPRequest(url, method='POST',
                              headers=headers,
                              body=body,
                              allow_ipv6=True,
                              request_timeout=self.timeout,
                              streaming_callback=self._on_body)
        try:
            result = yield self._http_client.fetch(request)
            if self._lasterr is not None:
                raise self._lasterr
            log.info('OK')
        except Exception as err:
            log.error('FAIL - %s', err)
            raise err

        raise gen.Return(result)
