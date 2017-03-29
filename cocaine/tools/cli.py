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

import errno
import json

import click
import msgpack
import time

from tornado.ioloop import IOLoop

from cocaine.exceptions import ChokeEvent, CocaineError
from cocaine.decorators import coroutine
from cocaine.tools import log, interactive
from cocaine.tools.actions import common, app, auth, profile, runlist, crashlog, group, \
    tracing, timeouts, logs, keyring
from cocaine.tools.actions.access import storage, event
from cocaine.tools.error import ToolsError

__author__ = 'EvgenySafronov <division494@gmail.com>'


class ToolHandler(object):
    def __init__(self, action):
        self._Action = action

    @coroutine
    def execute(self, **config):
        try:
            action = self._Action(**config)
            result = yield action.execute()
            self._processResult(result)
        except IOError as err:
            log.error(err)
            exit(128)
        except (ChokeEvent, StopIteration):
            pass
        except (CocaineError, ToolsError) as err:
            click.secho(str(err).capitalize(), fg='red')
            exit(128)
        except ValueError as err:
            log.error(err)
            exit(errno.EINVAL)
        except Exception as err:
            log.exception(err)
            exit(128)

    def _processResult(self, result):
        pass


class JsonToolHandler(ToolHandler):
    def _processResult(self, result):
        print(json.dumps(result, sort_keys=True, indent=4))


class PrintToolHandler(ToolHandler):
    def _processResult(self, result):
        click.echo(result.capitalize())


class CrashlogStatusToolHandler(ToolHandler):
    FORMAT_HEADER = '{0:^20} {1:^10} {2:^26} {3:^36}'
    HEADER = FORMAT_HEADER.format('Application', 'Total', 'Last', 'UUID')
    FORMAT_CONTENT = '{0:<20}|{1:^10}|{2:^26}|{3:^38}'

    def _processResult(self, result):
        if not result:
            print('There are no applications with crashlogs')

        log.info(self.HEADER)
        for appname, (timestamp, time_, uuid), total in sorted(result,
                                                               key=lambda (app, (timestamp, time_, uuid), total): timestamp):
            print(self.FORMAT_CONTENT.format(appname, total, time, uuid))


class CrashlogListToolHandler(ToolHandler):
    FORMAT_HEADER = '{0:^20} {1:^26} {2:^36}'
    HEADER = FORMAT_HEADER.format('Timestamp', 'Time', 'UUID')

    def _processResult(self, result):
        if not result:
            log.info('Crashlog list is empty')
            return

        log.info(self.HEADER)
        for timestamp, caltime, uuid in sorted(crashlog._parseCrashlogs(result), key=lambda (ts, caltime, uuid): ts):
            print(self.FORMAT_HEADER.format(timestamp, caltime, uuid))


class CrashlogViewToolHandler(ToolHandler):
    def _processResult(self, result):
        print('\n'.join(msgpack.loads(result)))


class CallActionCli(ToolHandler):
    def _processResult(self, result):
        requestType = result['request']
        response = result['response']
        if requestType == 'api':
            log.info('Service provides following API:')
            log.info('\n'.join(' - {0}'.format(method) for method in response))
        elif requestType == 'invoke':
            print(response)


NG_ACTIONS = {
    'cluster': JsonToolHandler(common.Cluster),
    'info': JsonToolHandler(common.NodeInfo),
    'metrics': JsonToolHandler(common.RuntimeMetrics),
    'locate': JsonToolHandler(common.Locate),
    'routing': JsonToolHandler(common.Routing),

    'app:check': ToolHandler(app.Check),
    'app:list': JsonToolHandler(app.List),
    'app:view': JsonToolHandler(app.View),
    'app:remove': ToolHandler(app.Remove),
    'app:upload': ToolHandler(app.LocalUpload),
    'app:upload-docker': ToolHandler(app.DockerUpload),
    'app:import-docker': ToolHandler(app.DockerImport),
    'app:upload-manual': ToolHandler(app.Upload),
    'app:start': PrintToolHandler(app.Start),
    'app:stop': PrintToolHandler(app.Stop),
    'app:restart': PrintToolHandler(app.Restart),

    'profile:copy': ToolHandler(profile.Copy),
    'profile:edit': ToolHandler(interactive.ProfileEditor),
    'profile:list': JsonToolHandler(profile.List),
    'profile:remove': ToolHandler(profile.Remove),
    'profile:rename': ToolHandler(profile.Rename),
    'profile:upload': ToolHandler(profile.Upload),
    'profile:view': JsonToolHandler(profile.View),

    'runlist:add-app': JsonToolHandler(runlist.AddApplication),
    'runlist:create': ToolHandler(runlist.Create),
    'runlist:copy': ToolHandler(runlist.Copy),
    'runlist:edit': ToolHandler(interactive.RunlistEditor),
    'runlist:list': JsonToolHandler(runlist.List),
    'runlist:remove': ToolHandler(runlist.Remove),
    'runlist:remove-app': JsonToolHandler(runlist.RemoveApplication),
    'runlist:rename': ToolHandler(runlist.Rename),
    'runlist:upload': ToolHandler(runlist.Upload),
    'runlist:view': JsonToolHandler(runlist.View),

    'group:app:add': ToolHandler(group.AddApplication),
    'group:app:remove': ToolHandler(group.RemoveApplication),
    'group:create': ToolHandler(group.Create),
    'group:copy': ToolHandler(group.Copy),
    'group:edit': ToolHandler(interactive.GroupEditor),
    'group:list': JsonToolHandler(group.List),
    'group:remove': ToolHandler(group.Remove),
    'group:rename': ToolHandler(group.Rename),
    'group:refresh': ToolHandler(group.Refresh),
    'group:view': JsonToolHandler(group.View),

    'crashlog:status': CrashlogStatusToolHandler(crashlog.Status),
    'crashlog:list': CrashlogListToolHandler(crashlog.List),
    'crashlog:view': CrashlogViewToolHandler(crashlog.View),
    'crashlog:remove': ToolHandler(crashlog.Remove),
    'crashlog:removeall': ToolHandler(crashlog.RemoveAll),
    'crashlog:clean': ToolHandler(crashlog.Clean),
    'crashlog:cleanwhen': ToolHandler(crashlog.CleanRange),

    'tracing:view': JsonToolHandler(tracing.TracingConfigView),
    'tracing:store': ToolHandler(tracing.TracingConfigStore),
    'tracing:remove': ToolHandler(tracing.TracingConfigRemove),

    'timeouts:view': JsonToolHandler(timeouts.TimeoutsConfigView),
    'timeouts:store': JsonToolHandler(timeouts.TimeoutsConfigStore),
    'timeouts:remove': ToolHandler(timeouts.TimeoutsConfigRemove),
    'timeouts:drop': ToolHandler(timeouts.TimeoutsConfigDrop),

    'logging:list_loggers': JsonToolHandler(logs.LoggingConfigListLoggers),
    'logging:set_filter': JsonToolHandler(logs.LoggingConfigSetFilter),
    'logging:remove_filter': JsonToolHandler(logs.LoggingConfigRemoveFilter),
    'logging:list_filters': JsonToolHandler(logs.LoggingConfigListFilters),
    'logging:set_cluster_filter': JsonToolHandler(logs.LoggingConfigSetClusterFilter),

    'auth:group:list': JsonToolHandler(auth.List),
    'auth:group:create': ToolHandler(auth.Create),
    'auth:group:view': JsonToolHandler(auth.View),
    'auth:group:edit': ToolHandler(auth.Edit),
    'auth:group:remove': ToolHandler(auth.Remove),

    'auth:group:members:add': ToolHandler(auth.AddMember),
    'auth:group:members:exclude': ToolHandler(auth.ExcludeMember),

    'access:storage:list': JsonToolHandler(storage.List),
    'access:storage:view': JsonToolHandler(storage.View),
    'access:storage:create': ToolHandler(storage.Create),
    'access:storage:edit': ToolHandler(storage.Edit),
    'access:storage:rm': ToolHandler(storage.Remove),

    'access:list': JsonToolHandler(event.List),
    'access:view': JsonToolHandler(event.View),
    'access:add': ToolHandler(event.AddBoth),
    'access:edit': ToolHandler(event.Edit),

    'keyring:update': JsonToolHandler(keyring.Update),
}


class Executor(object):
    """
    This class represents abstract action executor for specified service 'serviceName' and actions pool
    """
    def __init__(self, timeout=None):
        self.timeout = timeout
        self._loop = None

    @property
    def loop(self):
        """Lazy event loop initialization"""
        if not self._loop:
            self._loop = IOLoop.current()
            return self._loop
        return self._loop

    def execute_action(self, action_name, **options):
        """Execute action with specified options.

        Tries to create action from its name and invokes it.

        :param action_name: action name.
        :param options: various action configuration.
        """
        assert action_name in NG_ACTIONS, 'wrong action - {0}'.format(action_name)

        action = NG_ACTIONS[action_name]
        self.loop.run_sync(lambda: action.execute(**options), timeout=self.timeout)
