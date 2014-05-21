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

from cocaine.tools import log
from cocaine.futures import chain
from cocaine.tools.helpers._unix import AsyncUnixHTTPClient
from cocaine.tools.helpers.dockertemplate import dockerchef, dockerpuppet

__author__ = 'Evgeny Safronov <division494@gmail.com>'

DEFAULT_TIMEOUT = 3600.0
DEFAULT_URL = 'unix://var/run/docker.sock'
DEFAULT_VERSION = '1.7'
DEFAULT_INDEX_URL = 'https://index.docker.io/v1/'


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
        self._io_loop = io_loop
        self.config = {
            'url': url,
            'version': version,
            'timeout': timeout,
            'io_loop': io_loop
        }

    def info(self):
        return Info(**self.config).execute()

    def images(self):
        return Images(**self.config).execute()

    def containers(self):
        return Containers(**self.config).execute()

    def build(self, path, tag=None, quiet=False, streaming=None):
        return Build(path, tag, quiet, streaming, **self.config).execute()

    def push(self, name, auth, streaming=None):
        return Push(name, auth, streaming, **self.config).execute()


class Action(object):
    def __init__(self, url, version, timeout=DEFAULT_TIMEOUT, io_loop=None):
        self._unix = url.startswith('unix://')
        self._version = version
        self.timeout = timeout
        self._io_loop = io_loop
        if self._unix:
            self._base_url = url
            self._http_client = AsyncUnixHTTPClient(self._io_loop, url)
        else:
            self._base_url = '{0}/v{1}'.format(url, version)
            self._http_client = AsyncHTTPClient(self._io_loop)

    def execute(self):
        raise NotImplementedError

    def _make_url(self, path, query=None):
        if query is not None:
            query = dict((k, v) for k, v, in query.iteritems() if v is not None)
            return '{0}{1}?{2}'.format(self._base_url, path, urllib.urlencode(query))
        else:
            return '{0}{1}'.format(self._base_url, path)


class Info(Action):
    @chain.source
    def execute(self):
        response = yield self._http_client.fetch(self._make_url('/info'))
        yield response.body


class Images(Action):
    @chain.source
    def execute(self):
        response = yield self._http_client.fetch(self._make_url('/images/json'))
        yield response.body


class Containers(Action):
    @chain.source
    def execute(self):
        response = yield self._http_client.fetch(self._make_url('/containers/json'))
        yield json.loads(response.body)


class Build(Action):
    def __init__(self, path, tag=None, quiet=False, streaming=None,
                 url=DEFAULT_URL, version=DEFAULT_VERSION, timeout=DEFAULT_TIMEOUT, io_loop=None):
        super(Build, self).__init__(url, version, timeout, io_loop)
        self._path = path
        self._tag = tag
        self._quiet = quiet
        self._streaming = streaming
        self._io_loop = io_loop or IOLoop.current()

    @chain.source
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

        query = {'t': self._tag, 'remote': remote, 'q': self._quiet}
        url = self._make_url('/build', query)
        log.info('Building "%s"... ', url)
        request = HTTPRequest(url,
                              method='POST', headers=headers, body=body,
                              request_timeout=self.timeout,
                              streaming_callback=self._streaming)
        try:
            yield self._http_client.fetch(request)
            log.info('OK')
        except Exception as err:
            log.error('FAIL - %s', err)
            raise err

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
        print(dockfilecontent)
        with open("Dockerfile", "w") as dockerfile:
            dockerfile.write(dockfilecontent)
        try:
            return self._tar(path)
        finally:
            os.unlink("Dockerfile")


class Push(Action):
    def __init__(self, name, auth, streaming=None,
                 url=DEFAULT_URL, version=DEFAULT_VERSION, timeout=DEFAULT_TIMEOUT, io_loop=None):
        self.name = name
        self.auth = auth
        self._streaming = streaming
        super(Push, self).__init__(url, version, timeout, io_loop)

    @chain.source
    def execute(self):
        url = self._make_url('/images/{0}/push'.format(self.name))
        registry, name = resolve_repository_name(self.name)

        headers = HTTPHeaders()
        headers.add('X-Registry-Auth', self._prepare_auth_header_value())
        body = ''
        log.info('Pushing "%s" into "%s"... ', name, registry)
        request = HTTPRequest(url, method='POST',
                              headers=headers,
                              body=body,
                              request_timeout=self.timeout,
                              streaming_callback=self._on_body)
        try:
            yield self._http_client.fetch(request)
            log.info('OK')
        except Exception as err:
            log.error('FAIL - %s', err)
            raise err

    def _prepare_auth_header_value(self):
        username = self.auth.get('username', 'username')
        password = self.auth.get('password, password')
        return base64.b64encode('{0}:{1}'.format(username, password))

    def _on_body(self, data):
        parsed = '<undefined>'
        try:
            response = json.loads(data)
        except ValueError:
            parsed = data
        except Exception as err:
            parsed = 'Unknown error: {0}'.format(err)
        else:
            parsed = self._match_first(response, ['status', 'error'], data)
        finally:
            self._streaming(parsed)

    def _match_first(self, dict_, keys, default):
        for key in keys:
            value = dict_.get(key)
            if value is not None:
                return value
        return default
