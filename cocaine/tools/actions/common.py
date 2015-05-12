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

from tornado import gen


from cocaine.decorators import coroutine
from cocaine.services import Service
from cocaine.tools.actions import profile

__author__ = 'Evgeny Safronov <division494@gmail.com>'


class Node(object):
    def __init__(self, node=None):
        self.node = node

    @coroutine
    def execute(self):
        raise NotImplementedError()


class Locate(object):
    def __init__(self, locator, name):
        self.locator = locator
        if not name:
            raise ValueError("option `name` must be specified")
        self.name = name

    @coroutine
    def execute(self):
        channel = yield self.locator.resolve(self.name)
        endpoints, version, api = yield channel.rx.get()
        result = {
            "endpoints": ["%s:%d" % (addr, port) for addr, port in endpoints],
            "version": version,
            "api": dict((num, method[0]) for num, method in api.items())
        }
        raise gen.Return(result)


class Cluster(object):
    def __init__(self, locator):
        self.locator = locator

    @coroutine
    def execute(self):
        ch = yield self.locator.cluster()
        result = yield ch.rx.get()
        raise gen.Return(result)


class NodeInfo(Node):
    def __init__(self, node, locator, storage, name=None, expand=False):
        super(NodeInfo, self).__init__(node)
        self.locator = locator
        self._storage = storage
        self._name = name
        self._expand = expand

    @coroutine
    def execute(self):
        if self._name:
            apps = [self._name]
        else:
            channel = yield self.node.list()
            apps = yield channel.rx.get()
        result = yield self.info(apps)
        raise gen.Return(result)

    @coroutine
    def info(self, apps):
        infos = {}
        for app_ in apps:
            info = ''
            try:
                app = Service(app_, locator=self.locator)
                channel = yield app.info()
                info = yield channel.rx.get()
                if all([self._expand, self._storage is not None, 'profile' in info]):
                    info['profile'] = yield profile.View(self._storage, info['profile']).execute()
            except Exception as err:
                info = str(err)
            finally:
                infos[app_] = info
        result = {
            'apps': infos
        }
        raise gen.Return(result)


# class Call(object):
#     def __init__(self, command, host='localhost', port=10053):
#         if not command:
#             raise ValueError('Please specify service name for getting API or full command to invoke')
#         self.host = host
#         self.port = port
#         self.serviceName, separator, methodWithArguments = command.partition('.')
#         rx = re.compile(r'(.*?)\((.*)\)')
#         match = rx.match(methodWithArguments)
#         if match:
#             self.methodName, self.args = match.groups()
#         else:
#             self.methodName = methodWithArguments

#     @coroutine
#     def execute(self):
#         service = self.getService()
#         response = {
#             'service': self.serviceName,
#         }
#         if not self.methodName:
#             api = service.api
#             response['request'] = 'api'
#             response['response'] = api
#         else:
#             method = self.getMethod(service)
#             args = self.parseArguments()
#             result = yield method(*args)
#             response['request'] = 'invoke'
#             response['response'] = result
#         yield response

#     def get_service(self):
#         try:
#             service = Service(self.serviceName, endpoints=[(self.host, self.port)])
#             return service
#         except Exception as err:
#             raise ServiceCallError(self.serviceName, err)

#     def get_method(self, service):
#         try:
#             method = service.__getattribute__(self.methodName)
#             return method
#         except AttributeError:
#             raise ServiceError(self.serviceName, 'method "{0}" is not found'.format(self.methodName), 1)

#     def parse_arguments(self):
#         if not self.args:
#             return ()

#         try:
#             args = ast.literal_eval(self.args)
#             if not isinstance(args, tuple):
#                 args = (args,)
#             return args
#         except (SyntaxError, ValueError) as err:
#             raise ServiceCallError(self.serviceName, err)
#         except Exception as err:
#             print(err, type(err))
