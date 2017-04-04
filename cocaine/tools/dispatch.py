# coding=utf-8

import contextlib
import copy
import logging
import os

import click
from click import Choice
import collections
import six
import yaml
from tornado import gen
from tornado.util import import_object
from cerberus import Validator

from cocaine.tools.cli import Executor

from cocaine.decorators import coroutine
from cocaine.exceptions import CocaineError
from cocaine.services import Locator, Service
from .plugins.secure.promiscuous import Promiscuous
from .version import __version__


CONFIG_GLOB = '/etc/cocaine/.cocaine/tools.yml'
CONFIG_USER = '~/.cocaine/tools.yml'
CONFIG_PATHS = [CONFIG_GLOB, CONFIG_USER]

DEFAULT_LOCATOR_HOST = 'localhost'
DEFAULT_LOCATOR_PORT = 10053

log = logging.getLogger('cocaine.tools')


def _print_experimental_warning():
    click.echo('')
    click.echo('THIS COMMAND IS EXPERIMENTAL. DO NOT DEPEND ON IT IN YOUR SCRIPTS')
    click.echo('')


class SecureServiceError(CocaineError):
    pass


def set_verbosity(ctx, param, value):
    levels = [
        logging.NOTSET,
        logging.ERROR,
        logging.WARN,
        logging.INFO,
        logging.DEBUG
    ]

    loggers = ['cocaine.tools']

    # Enable cocaine-framework logging if there are more than maximum verbosity requested.
    if value > 4:
        value = 4
        loggers.append('cocaine')

    level = levels[value]

    formatter = logging.Formatter('%(levelname)-1.1s, %(asctime)s: %(message)s')

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.setLevel(level)
    for name in loggers:
        lg = logging.getLogger(name)
        lg.propagate = False
        lg.addHandler(handler)
        lg.setLevel(level)
    return value


_global_options = [
    click.option('-v', count=True, callback=set_verbosity, help='Enable additional output.'),
    click.option('--host', metavar='', help='Locator hostname.'),
    click.option('--port', metavar='', help='Locator port.'),
    click.option('--timeout', metavar='', default=20, help='Operation timeout.'),
]


@contextlib.contextmanager
def watch_progress(fmt, *args):
    message = (fmt % args).capitalize()
    try:
        click.echo('[ ] {}\033[1A'.format(message))
        yield
    except Exception:
        click.echo('[{}] {}'.format(click.style('✘', fg='red'), message))
        raise
    else:
        click.echo('[{}] {}'.format(click.style('✔', fg='green'), message))


def with_options(func):
    for option in reversed(_global_options):
        func = option(func)
    return func


class SecureService(object):
    def __init__(self, secure, wrapped):
        self._secure = secure
        self._wrapped = wrapped

    @coroutine
    def connect(self, traceid=None):
        yield self._wrapped.connect(traceid)

    def disconnect(self):
        return self._wrapped.disconnect()

    def __getattr__(self, name):
        @coroutine
        def wrapper(*args, **kwargs):
            try:
                kwargs['authorization'] = yield self._secure.fetch_token()
            except Exception as err:
                raise SecureServiceError('failed to fetch secure token: {}'.format(err))
            raise gen.Return((yield getattr(self._wrapped, name)(*args, **kwargs)))
        return wrapper


class ServiceFactory(object):
    def create_service(self, name):
        raise NotImplementedError

    def create_secure_service(self, name):
        raise NotImplementedError


class PooledServiceFactory(ServiceFactory):
    def __init__(self, endpoints):
        self._secure = None
        self._endpoints = endpoints
        self._cache = {}

    @property
    def secure(self):
        return self._secure

    @secure.setter
    def secure(self, value):
        self._secure = value

    def create_service(self, name):
        if name not in self._cache:
            if name == 'locator':
                service = Locator(endpoints=self._endpoints)
            else:
                service = Service(name, endpoints=self._endpoints)
            self._cache[name] = service
        return self._cache[name]

    def create_secure_service(self, name):
        return SecureService(self._secure, self.create_service(name))


class Configurator(object):
    SCHEMA = {
        'locator': {
            'type': 'dict',
            'schema': {
                'host': {
                    'type': 'string',
                },
                'port': {
                    'type': 'integer',
                    'min': 0,
                    'max': 65535,
                }
            }
        },
        'secure': {
            'type': 'dict',
            'schema': {
                'mod': {
                    'type': 'string',
                    'allowed': ['TVM'],
                },
                'client_id': {
                    'type': 'integer',
                },
                'client_secret': {
                    'type': 'string',
                }
            }
        }
    }

    def __init__(self):
        self._config = {}
        self._validator = Validator(self.SCHEMA)

    @property
    def config(self):
        return self._config

    def update(self, paths=None):
        if paths is None:
            paths = CONFIG_PATHS

        log.debug('scanning configs at %s', paths)
        configs = []
        for path in paths:
            if os.path.exists(os.path.expanduser(path)):
                content = yaml.safe_load(open(os.path.expanduser(path)).read())
                if content is None:
                    log.debug('config %s is empty, skipping', path)
                    continue
                configs.append((path, content))
                log.debug('successfully read config from %s\n%s', path, content)
            else:
                log.debug('config %s was not found, skipping', path)

        self._config.clear()
        if len(configs) == 0:
            log.info('no config found - use default values')
        else:
            used = []
            for filename, config in configs:
                used.append(filename)
                self._config = Configurator._merge_dicts(self._config, config)
            log.info('loaded config(s) from %s', used)
        self._validate()

    @staticmethod
    def _merge_dicts(src, d):
        key = None
        try:
            if src is None or isinstance(src, (six.string_types, float, six.integer_types)):
                # Border case for first run or if `src` is a primitive.
                src = d
            elif isinstance(src, list):
                # Lists can be only appended.
                if isinstance(d, list):
                    src.extend(d)
                else:
                    src.append(d)
            elif isinstance(src, dict):
                # Dicts must be merged.
                if isinstance(d, dict):
                    for key in d:
                        if key in src:
                            src[key] = Configurator._merge_dicts(src[key], d[key])
                        else:
                            src[key] = d[key]
                else:
                    raise RuntimeError('Cannot merge non-dict "%s" into dict "%s"' % (d, src))
            else:
                raise RuntimeError('NOT IMPLEMENTED "%s" into "%s"' % (d, src))
        except TypeError as e:
            raise RuntimeError(
                'TypeError "%s" in key "%s" when merging "%s" into "%s"' % (e, key, d, src))
        return src

    def _validate(self):
        self._validator.validate(self._config)

        if self._validator.errors:
            raise ValueError('failed to validate configuration file: {}'.format(self._validator.errors))


class PluginLoader(object):
    def __init__(self):
        self._secure = Promiscuous(None)

    def load(self, config, repo):
        self._load_secure(config, repo)

    def _load_secure(self, config, repo):
        if 'secure' in config:
            ty = config['secure'].get('mod')
            if ty is None:
                return
            kwargs = copy.deepcopy(config['secure'])
            kwargs.pop('mod')

            if not ty.startswith('cocaine.tools.plugins.secure'):
                ty = 'cocaine.tools.plugins.secure.' + ty.lower() + '.' + ty
            self._secure = import_object(ty)(repo, **kwargs)
            log.info('imported "%s" secure plugin', ty)

    def secure(self):
        return self._secure


class Context(object):
    def __init__(self, host, port, timeout, **kwargs):
        self._timeout = timeout
        self._options = kwargs

        self._configurator = Configurator()
        self._configurator.update()

        if host is None:
            host = self._configurator.config.get('locator', {}).get('host', DEFAULT_LOCATOR_HOST)

        if port is None:
            port = self._configurator.config.get('locator', {}).get('port', DEFAULT_LOCATOR_PORT)

        self._endpoints = [(host, int(port))]

        self._repo = PooledServiceFactory(endpoints=self._endpoints)

        self._loader = PluginLoader()
        self._loader.load(self._configurator.config, self._repo)

        self._repo.secure = self._loader.secure()

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        self._timeout = value

    @property
    def repo(self):
        return self._repo

    @property
    def locator(self):
        return self._repo.create_service('locator')

    def execute_action(self, __name, **kwargs):
        return Executor(timeout=self.timeout).execute_action(__name, **kwargs)


ALIASES = {
    'i': 'info',
}


class AliasedGroup(click.Group):
    def get_command(self, ctx, cmd_name):
        cmd = ALIASES.get(cmd_name)
        if cmd is None:
            return click.Group.get_command(self, ctx, cmd_name)
        else:
            return click.Group.get_command(self, ctx, cmd)


@click.group(cls=AliasedGroup)
def tools():
    pass


@tools.command()
def version():
    """
    Show version.
    """
    click.echo(__version__)


@tools.command()
@click.option('-n', '--name', metavar='', required=True, help='Service name.')
@with_options
def locate(name, **kwargs):
    """
    Show resolve information about specified service.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('locate', **{
        'name': name,
        'locator': ctx.locator,
    })


@tools.command()
@click.option('-n', '--name', metavar='', required=True, help='Routing group name.')
@with_options
def routing(name, **kwargs):
    """
    Show information about the requested routing group.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('routing', **{
        'name': name,
        'locator': ctx.locator,
    })


@tools.command()
@click.option('--resolve', metavar='', default=False, help='Show IPs instead of hostname.')
@with_options
def cluster(resolve, **kwargs):
    """
    Show cluster info.
    """
    # Actually we have IPs and we need not do anything to resolve them to IPs. So the default
    # behavior fits better to this option name.

    ctx = Context(**kwargs)
    ctx.execute_action('cluster', **{
        'locator': ctx.locator,
        'resolve': resolve,
    })


@tools.command()
@click.option('-n', '--name', metavar='', help='Application name.')
@click.option('-m', is_flag=True, default=False, help='Expand manifest.')
@click.option('-p', is_flag=True, default=False, help='Expand profile.')
@click.option('-b', is_flag=True, default=False, help='Show only brief info (disables -p and -m).')
@click.option('-w', is_flag=True, default=True, help='Do not use wildcard to match app name.')
@with_options
def info(name, m, p, b, w, **kwargs):
    """
    Show information about cocaine runtime.

    Return json-like string with information about cocaine-runtime.

    If the name option is not specified, shows information about all applications. Flags can be
    specified for fine-grained control of the output verbosity.
    """
    m = (m << 1) & 0b010
    p = (p << 2) & 0b100

    # Brief disables all further flags.
    if b:
        flags = 0b000
    else:
        flags = m | p | 0b001

    ctx = Context(**kwargs)
    ctx.execute_action('info', **{
        'node': ctx.repo.create_secure_service('node'),
        'locator': ctx.locator,
        'name': name,
        'flags': flags,
        'use_wildcard': w,
        'timeout': ctx.timeout,
    })


@tools.group(name='app')
def app_group():
    """
    Application commands.
    """
    pass


@tools.group(name='profile')
def profile_group():
    """
    Profile commands.
    """
    pass


@tools.group(name='runlist')
def runlist_group():
    """
    Runlist commands.
    """
    pass


@tools.group(name='crashlog')
def crashlog_group():
    """
    Crashlog commands.
    """
    pass


@tools.group(name='group')
def group_group():
    """
    Routing group commands.
    """
    pass


@tools.group(name='tracing')
def tracing_group():
    """
    Dynamic tracing support.
    """
    pass


@tools.group(name='logging')
def logging_group():
    """
    Dynamic logging filtering support.
    """
    pass


@tools.group(name='timeouts')
def timeouts_group():
    """
    Configurable timeout for applications support.
    """
    pass


@tools.group(name='auth')
def auth_group():
    """
    Authorization tokens management.
    """
    _print_experimental_warning()


@tools.group(name='access')
def access_group():
    """
    ACL management.
    """
    _print_experimental_warning()


@access_group.group(name='storage')
def access_storage_group():
    """
    ACL management for storage collections.
    """
    pass


@access_group.group(name='event')
def access_event_group():
    """
    ACL management for services.
    """
    pass


@tools.group(name='keyring')
def keyring_group():
    """
    Public keys management.
    """
    pass


@tools.command()
@click.option('--type', 'ty', default='plain', type=Choice(['plain', 'json']), help='Output type.')
@click.option('--query', help='Filtering query.')
@click.option('--query-type', default='mql', type=click.Choice(['mql', 'ast']), help='Query type.')
@with_options
def metrics(ty, query, query_type, **kwargs):
    """
    Outputs runtime metrics collected from cocaine-runtime and its services.

    This command shows runtime metrics collected from cocaine-runtime and its services during their
    lifetime.
    There are four kind of metrics available: gauges, counters, meters and timers.

    \b
    - Gauges   - an instantaneous measurement of a value.
    - Counters - just a gauge for an atomic integer instance.
    - Meters   - measures the rate of events over time (e.g., "requests per second"). In addition
      to the mean rate, meters also track 1-, 5-, and 15-minute moving averages.
    - Timers   - measures both the rate that a particular piece of code is called and the
      distribution of its duration.


    Every metric in has a unique name, which is just a dotted-name string like "connections.count"
    or "node.queue.size".

    An output type can be configured using --type option. The default one results in plain
    formatting where there is only one depth level.

    As an alternative you can expanded the JSON tree by specifying --type=json option. The depth of
    the result tree depends on metric name which is split by dot symbol.

    The result output will be probably too large without any customization. To reduce this output
    there are custom filters, which can be specified using --query option. Technically it's a
    special metrics query language (MQL) which supports the following operations and functions:

    \b
    - contains(<expr>, <expr>) - checks whether the result of second expression contains in the
      result of first expression. These expressions must resolve in strings. An output type of this
      function is bool.
    - name() - resolves in metric name.
    - type() - resolves in metric type (counter, meter, etc.).
    - tag(<expr>) - extracts custom metric tag and results in string.
    - && - combines several expressions in one, which applies when all of them apply.
    - || - combines several expressions in one, which applies when any of them apply.
    - == - compares two expressions for equality.
    - != - compares two expressions for an non-equality.
    - Also string literals (alphanumeric with dots) can be used as an expressions, for
      example "name() == locator.connections.accepted".

    Priorities can be specified using braces as in usual math expressions.

    The grammar for this query language is:

    \b
    expr    ::= term ((AND | OR) term)*
    term    ::= factor ((EQ | NE) factor)*
    factor  ::= func | literal | number | LPAREN expr RPAREN
    func    ::= literal LPAREN expr (,expr)* RPAREN
    literal ::= alphanum | .
    number  ::= <floating point number>

    An example of the query, which returns all meters (for all services) and the number of accepted
    connections for the Locator
    service: "contains(type(), meter) || name() == locator.connections.accepted".
    """
    ctx = Context(**kwargs)
    ctx.execute_action('metrics', **{
        'metrics': ctx.repo.create_secure_service('metrics'),
        'ty': ty,
        'query': query,
        'query_type': query_type,
    })


@app_group.command(name='list')
@with_options
def app_list(**kwargs):
    """
    Show uploaded applications.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:list', **{
        'storage': ctx.repo.create_secure_service('storage'),
    })


@app_group.command(name='view')
@click.option('-n', '--name', metavar='', help='Application name.')
@with_options
def app_view(name, **kwargs):
    """
    Show manifest content for an application.

    If application is not uploaded, an error will be displayed.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:view', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@app_group.command(name='upload')
@click.argument('path', required=False, type=click.Path(exists=True))
@click.option('-n', '--name', metavar='', help='Application name.')
@click.option('--manifest', metavar='', help='Manifest file name.')
@click.option('--package', metavar='', help='Path to the application archive.')
@click.option('--docker_address', metavar='', help='Docker address.')
@click.option('--registry', metavar='', help='Docker Registry address.')
@click.option('--manifest-only', is_flag=True, metavar='', default=False, help='Upload only manifest.')
@with_options
def app_upload(path, name, manifest, package, docker_address, registry, manifest_only, **kwargs):
    """
    Upload application with its environment (directory) into the storage.

    Application directory or its subdirectories must contain valid manifest file
    named `manifest.json` or `manifest` otherwise you must specify it explicitly by
    setting `--manifest` option.

    You can specify application name. By default, leaf directory name is treated as application
    name.

    If you have already prepared application archive (*.tar.gz), you can explicitly specify path to
    it by setting `--package` option.

    Additional output can be turned on by passing `-vvvv` option.
    """
    lower_limit = 120.0

    ctx = Context(**kwargs)
    if ctx.timeout < lower_limit:
        ctx.timeout = lower_limit
        log.info('shifted timeout to the %.2fs', ctx.timeout)

    mutex_record = collections.namedtuple('mutex_record', 'value, name')
    mutex = [
        (mutex_record(path, 'PATH'), mutex_record(package, '--package')),
        (mutex_record(package, '--package'), mutex_record(docker_address, '--docker')),
        (mutex_record(package, '--package'), mutex_record(registry, '--registry')),
    ]
    for (f, s) in mutex:
        if f.value and s.value:
            click.echo('Wrong usage: option {} and {} are mutual exclusive, you can only use one'.
                       format(f.name, s.name))
            exit(os.EX_USAGE)

    if manifest_only:
        ctx.execute_action('app:upload-manual', **{
            'storage': ctx.repo.create_secure_service('storage'),
            'name': name,
            'manifest': manifest,
            'package': None,
            'manifest_only': manifest_only,
        })
    elif package:
        ctx.execute_action('app:upload-manual', **{
            'storage': ctx.repo.create_secure_service('storage'),
            'name': name,
            'manifest': manifest,
            'package': package
        })
    elif docker_address:
        ctx.execute_action('app:upload-docker', **{
            'storage': ctx.repo.create_secure_service('storage'),
            'path': path,
            'name': name,
            'manifest': manifest,
            'address': docker_address,
            'registry': registry
        })
    else:
        ctx.execute_action('app:upload', **{
            'storage': ctx.repo.create_secure_service('storage'),
            'path': path,
            'name': name,
            'manifest': manifest
        })


@app_group.command(name='import')
@click.argument('path', type=click.Path(exists=True))
@click.option('-n', '--name', metavar='', help='Application name.')
@click.option('--manifest', metavar='', help='Manifest file name.')
@click.option('--container_url', metavar='', required=True, help='Docker container url.')
@click.option('--docker_address', metavar='', required=True, help='Docker address.')
@click.option('--registry', metavar='', help='Docker Registry address.')
@with_options
def app_import(path, name, manifest, container_url, docker_address, registry, **kwargs):
    """
    Import application Docker container.
    """
    lower_limit = 120.0

    ctx = Context(**kwargs)
    if ctx.timeout < lower_limit:
        ctx.timeout = lower_limit
        log.info('shifted timeout to the %.2fs', ctx.timeout)

    if container_url and docker_address:
        ctx.execute_action('app:import-docker', **{
            'storage': ctx.repo.create_secure_service('storage'),
            'path': path,
            'name': name,
            'manifest': manifest,
            'container': container_url,
            'address': docker_address,
            'registry': registry
        })
    else:
        raise ValueError("both `container_url` and `docker_address` options must not be empty")


@app_group.command(name='remove')
@click.option('-n', '--name', metavar='', help='Application name.')
@with_options
def app_remove(name, **kwargs):
    """
    Remove application from storage.

    No error messages will display if specified application is not uploaded.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:remove', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@app_group.command(name='start')
@click.option('-n', '--name', metavar='', help='Application name.')
@click.option('-r', '--profile', metavar='', help='Profile name.')
@with_options
def app_start(name, profile, **kwargs):
    """
    Start an application with specified profile.

    Does nothing if application is already running.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:start', **{
        'node': ctx.repo.create_secure_service('node'),
        'name': name,
        'profile': profile
    })


@app_group.command(name='pause')
@click.option('-n', '--name', metavar='', help='Application name.')
@with_options
def app_pause(name, **kwargs):
    """
    Stop application.

    This command is alias for ```cocaine-tool app stop```.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:stop', **{
        'node': ctx.repo.create_secure_service('node'),
        'name': name,
    })


@app_group.command(name='stop')
@click.option('-n', '--name', metavar='', help='Application name.')
@with_options
def app_stop(name, **kwargs):
    """
    Stop application.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:stop', **{
        'node': ctx.repo.create_secure_service('node'),
        'name': name,
    })


@app_group.command(name='restart')
@click.option('-n', '--name', metavar='', help='Application name.')
@click.option('-r', '--profile', metavar='', help='Profile name.')
@with_options
def app_restart(name, profile, **kwargs):
    """
    Restart application.

    Executes ```cocaine-tool app pause``` and ```cocaine-tool app start``` sequentially.

    It can be used to quickly change application profile.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:restart', **{
        'node': ctx.repo.create_secure_service('node'),
        'locator': ctx.locator,
        'name': name,
        'profile': profile,
    })


@app_group.command()
@click.option('-n', '--name', metavar='', help='Application name.')
@with_options
def check(name, **kwargs):
    """
    Check application status.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:check', **{
        'node': ctx.repo.create_secure_service('node'),
        'name': name,
    })


@profile_group.command(name='list')
@with_options
def profile_list(**kwargs):
    """
    Show uploaded profiles.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('profile:list', **{
        'storage': ctx.repo.create_secure_service('storage'),
    })


@profile_group.command(name='view')
@click.option('-n', '--name', metavar='', required=True, help='Profile name.')
@with_options
def profile_view(name, **kwargs):
    """
    Show profile configuration content.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('profile:view', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@profile_group.command(name='upload')
@click.option('-n', '--name', metavar='', required=True, help='Profile name.')
@click.option('-r', '--profile', metavar='', required=True, help='Path to profile.')
@with_options
def profile_upload(name, profile, **kwargs):
    """
    Upload profile into the storage.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('profile:upload', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'profile': profile,
    })


@profile_group.command(name='edit')
@click.option('-n', '--name', metavar='', required=True, help='Profile name.')
@with_options
def profile_edit(name, **kwargs):
    """
    Edit profile using interactive editor.
    """
    ctx = Context(**kwargs)
    ctx.timeout = None
    ctx.execute_action('profile:edit', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@profile_group.command(name='remove')
@click.option('-n', '--name', metavar='', required=True, help='Profile name.')
@with_options
def profile_remove(name, **kwargs):
    """
    Remove profile from the storage.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('profile:remove', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@profile_group.command(name='copy')
@click.option('-n', '--name', metavar='', required=True, help='Profile name.')
@click.option('--copyname', metavar='', required=True, help='Profile new name.')
@with_options
def profile_copy(name, copyname, **kwargs):
    """
    Copy a profile.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('profile:copy', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'copyname': copyname,
    })


@profile_group.command(name='rename')
@click.option('-n', '--name', metavar='', required=True, help='Profile name.')
@click.option('--copyname', metavar='', required=True, help='Profile new name.')
@with_options
def profile_rename(name, copyname, **kwargs):
    """
    Rename a profile.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('profile:rename', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'copyname': copyname,
    })


@runlist_group.command(name='list')
@with_options
def runlist_list(**kwargs):
    """
    Show uploaded runlists.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('runlist:list', **{
        'storage': ctx.repo.create_secure_service('storage'),
    })


@runlist_group.command(name='view')
@click.option('-n', '--name', metavar='', required=True, help='Runlist name.')
@with_options
def runlist_view(name, **kwargs):
    """
    Show configuration content for a specified runlist.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('runlist:view', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name
    })


@runlist_group.command(name='edit')
@click.option('-n', '--name', metavar='', required=True, help='Runlist name.')
@with_options
def runlist_edit(name, **kwargs):
    """
    Edit specified runlist interactively using editor.
    """
    ctx = Context(**kwargs)
    ctx.timeout = None
    ctx.execute_action('runlist:edit', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name
    })


@runlist_group.command(name='upload')
@click.option('-n', '--name', metavar='', required=True, help='Runlist name.')
@click.option('--runlist', metavar='', required=True,
              help='Either path to the runlist file or inline content')
@with_options
def runlist_upload(name, runlist, **kwargs):
    """
    Upload runlist with context into the storage.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('runlist:upload', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'runlist': runlist,
    })


@runlist_group.command(name='create')
@click.option('-n', '--name', metavar='', required=True, help='Runlist name.')
@with_options
def runlist_create(name, **kwargs):
    """
    Create runlist and upload it into the storage.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('runlist:create', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@runlist_group.command(name='remove')
@click.option('-n', '--name', metavar='', required=True, help='Runlist name.')
@with_options
def runlist_remove(name, **kwargs):
    """
    Remove runlist from the storage.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('runlist:remove', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@runlist_group.command(name='copy')
@click.option('-n', '--name', metavar='', required=True, help='Runlist name.')
@click.option('-c', '--copyname', metavar='', required=True, help='Cloned runlist name.')
@with_options
def runlist_copy(name, copyname, **kwargs):
    """
    Clone runlist.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('runlist:copy', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'copyname': copyname,
    })


@runlist_group.command(name='rename')
@click.option('-n', '--name', metavar='', required=True, help='Runlist name.')
@click.option('-c', '--copyname', metavar='', required=True, help='Runlist new name.')
@with_options
def runlist_rename(name, copyname, **kwargs):
    """
    Rename runlist.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('runlist:rename', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'copyname': copyname,
    })


@runlist_group.command(name='add-app')
@click.option('-n', '--name', metavar='', required=True, help='Runlist name.')
@click.option('--app', metavar='', required=True, help='Application name.')
@click.option('--profile', metavar='', required=True, help='Suggested profile name.')
@click.option('-f', '--force', is_flag=True, default=False, help='Create runlist if not exists.')
@with_options
def runlist_add_app(name, app, profile, force, **kwargs):
    """
    Add specified application with profile to the specified runlist.

    Existence of application or profile is not checked.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('runlist:add-app', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'app': app,
        'profile': profile,
        'force': force
    })


@runlist_group.command(name='remove-app')
@click.option('-n', '--name', metavar='', required=True, help='Runlist name.')
@click.option('--app', metavar='', required=True, help='Application name.')
@with_options
def runlist_remove_app(name, app, **kwargs):
    """
    Remove specified application from the runlist.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('runlist:remove-app', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'app': app,
    })


@crashlog_group.command(name='status')
@with_options
def crashlog_status(**kwargs):
    """
    Show crashlogs status.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('crashlog:status', **{
        'storage': ctx.repo.create_secure_service('storage'),
    })


@crashlog_group.command(name='list')
@click.option('-n', '--name', metavar='', required=True, help='Crashlog name.')
@click.option('-d', '--day', metavar='', help='Date filter.')
@with_options
def crashlog_list(name, day, **kwargs):
    """
    Show crashlogs list for application.

    Prints crashlog list in "Unix Timestamp - UUID" format.

    Filtering by day accepts the following variants: today, yesterday, %d, %d-%m or %d-%m-%Y.
    For example given today is a 06-12-2016 crashlogs for yesterday can be listed using:
    `--day=yesterday`, `--day=5`, `--day=05-12` or `--day=05-12-2016`.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('crashlog:list', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'day_string': day,
    })


@crashlog_group.command(name='view')
@click.option('-n', '--name', metavar='', required=True, help='Crashlog name.')
@click.option('-t', '--timestamp', metavar='', help='Timestamp.')
@with_options
def crashlog_view(name, timestamp, **kwargs):
    """
    Show crashlog for application with specified timestamp.

    Last crashlog for a given application will be displayed unless timestamp option is specified.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('crashlog:view', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'timestamp': timestamp,
    })


@crashlog_group.command(name='remove')
@click.option('-n', '--name', metavar='', required=True, help='Crashlog name.')
@click.option('-t', '--timestamp', metavar='', help='Timestamp.')
@with_options
def crashlog_remove(name, timestamp, **kwargs):
    """
    Remove crashlog for application with specified timestamp from the storage.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('crashlog:remove', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'timestamp': timestamp,
    })


@crashlog_group.command(name='removeall')
@click.option('-n', '--name', metavar='', required=True, help='Crashlog name.')
@with_options
def crashlog_removeall(name, **kwargs):
    """
    Remove all crashlogs for application from the storage.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('crashlog:removeall', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@crashlog_group.command(name='clean')
@click.option('-n', '--name', metavar='', required=True, help='Crashlog name.')
@click.option('-t', '--timestamp', metavar='', help='Timestamp.')
@click.option('-s', '--size', metavar='', default=1000, help='Number of crashlogs to leave.')
@with_options
def crashlog_clean(name, timestamp, size, **kwargs):
    """
    For application NAME leave SIZE crashlogs or remove all crashlogs with timestamp > TIMESTAMP.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('crashlog:clean', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'size': size,
        'timestamp': timestamp,
    })


@crashlog_group.command(name='cleanrange')
@click.option('--from_day', metavar='', required=True, help='From day.')
@click.option('--up_to_day', metavar='', default='yesterday', help='Up to day.')
@with_options
def crashlog_cleanrange(from_day, up_to_day, **kwargs):
    """
    Remove all crashlogs from one date up to another.

    The date can be specified as DAY-[MONTH-[YEAR]].

    Example:
        today, yesterday, 10, 10-09, 10-09-2015
    """
    ctx = Context(**kwargs)
    ctx.execute_action('crashlog:cleanwhen', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'from_day': from_day,
        'to_day': up_to_day,
    })


@group_group.command(name='list')
@with_options
def group_list(**kwargs):
    """
    Show available routing groups.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('group:list', **{
        'storage': ctx.repo.create_secure_service('storage'),
    })


@group_group.command(name='view')
@click.option('-n', '--name', metavar='', required=True, help='Routing group name.')
@with_options
def group_view(name, **kwargs):
    """
    Show specified routing group.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('group:view', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@group_group.command(name='create')
@click.option('-n', '--name', metavar='', required=True, help='Routing group name.')
@click.option('-c', '--content', metavar='', required=True, help='Routing group content.')
@with_options
def group_create(name, content, **kwargs):
    """
    Create routing group.

    You can optionally specify content for created routing group. It can be either direct JSON
    expression in single quotes, or path to the json file with settings. Settings itself must be
    key-value list, where `key` represents application name, and `value` represents its weight.

    For example:

    cocaine-tool group create -n new_group -c '{
        "app": 1,
        "another_app": 2
    }'.

    Warning: all application weights must be positive integers, total weight must be positive.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('group:create', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'content': content,
    })


@group_group.command(name='remove')
@click.option('-n', '--name', metavar='', required=True, help='Routing group name.')
@with_options
def group_remove(name, **kwargs):
    """
    Remove routing group from the storage.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('group:remove', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@group_group.command(name='copy')
@click.option('-n', '--name', metavar='', required=True, help='Routing group name.')
@click.option('-c', '--copyname', metavar='', required=True, help='Cloned routing group name.')
@with_options
def group_copy(name, copyname, **kwargs):
    """
    Copy routing group.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('group:copy', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'copyname': copyname,
    })


@group_group.command(name='rename')
@click.option('-n', '--name', metavar='', required=True, help='Routing group name.')
@click.option('-c', '--copyname', metavar='', required=True, help='New routing group name.')
@with_options
def group_rename(name, copyname, **kwargs):
    """
    Rename routing group.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('group:rename', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'copyname': copyname,
    })


@group_group.command(name='refresh')
@click.option('-n', '--name', metavar='', help='Routing group name.')
@with_options
def group_refresh(name, **kwargs):
    """
    Refresh routing group.

    If the name option is empty, this command will refresh all groups.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('group:refresh', **{
        'locator': ctx.locator,
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@group_group.command(name='push')
@click.option('-n', '--name', metavar='', required=True, help='Routing group name.')
@click.option('--app', metavar='', required=True, help='Application name.')
@click.option('-w', '--weight', metavar='', required=True, type=int, help='Application weight.')
@with_options
def group_push(name, app, weight, **kwargs):
    """
    Add application with its weight into the routing group.

    Warning: application weight must be positive integer.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('group:app:add', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'app': app,
        'weight': weight,
    })


@group_group.command(name='pop')
@click.option('-n', '--name', metavar='', required=True, help='Routing group name.')
@click.option('--app', metavar='', required=True, help='Application name.')
@with_options
def group_pop(name, app, **kwargs):
    """
    Remove application from the specified routing group.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('group:app:remove', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'app': app,
    })


@group_group.command(name='edit')
@click.option('-n', '--name', metavar='', required=True, help='Routing group name.')
@with_options
def group_edit(name, **kwargs):
    """
    Edit specified routing group in an interactive editor.
    """
    ctx = Context(**kwargs)
    ctx.timeout = None
    ctx.execute_action('group:edit', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@tracing_group.command(name='store')
@click.option('-n', '--name', metavar='', required=True, help='Unicorn node.')
@click.option('-v', '--value', metavar='', type=float, required=True, help='Value in percents.')
@with_options
def tracing_store(name, value, **kwargs):
    ctx = Context(**kwargs)
    ctx.execute_action('tracing:store', **{
        'configuration_service': ctx.repo.create_secure_service('unicorn'),
        'name': name,
        'value': value,
    })


@tracing_group.command(name='remove')
@click.option('-n', '--name', metavar='', required=True, help='Unicorn node.')
@with_options
def tracing_remove(name, **kwargs):
    ctx = Context(**kwargs)
    ctx.execute_action('tracing:remove', **{
        'configuration_service': ctx.repo.create_secure_service('unicorn'),
        'name': name,
    })


@tracing_group.command(name='view')
@click.option('-n', '--name', metavar='', help='Unicorn node.')
@with_options
def tracing_view(name, **kwargs):
    ctx = Context(**kwargs)
    ctx.execute_action('tracing:view', **{
        'configuration_service': ctx.repo.create_secure_service('unicorn'),
        'name': name,
    })


@timeouts_group.command(name='store')
@click.option('-n', '--name', metavar='', required=True, help='Unicorn node.')
@click.option('-e', '--event', metavar='', required=True, help='Event name.')
@click.option('-v', '--value', metavar='', required=True, default=30.0, help='Seconds.')
@with_options
def timeouts_store(name, event, value, **kwargs):
    ctx = Context(**kwargs)
    ctx.execute_action('timeouts:store', **{
        'configuration_service': ctx.repo.create_secure_service('unicorn'),
        'name': name,
        'value': value,
        'event': event,
    })


@timeouts_group.command(name='remove')
@click.option('-n', '--name', metavar='', required=True, help='Unicorn node.')
@click.option('-e', '--event', metavar='', required=True, help='Event name.')
@with_options
def timeouts_remove(name, event, **kwargs):
    ctx = Context(**kwargs)
    ctx.execute_action('timeouts:remove', **{
        'configuration_service': ctx.repo.create_secure_service('unicorn'),
        'name': name,
        'event': event,
    })


@timeouts_group.command(name='view')
@click.option('-n', '--name', metavar='', required=True, help='Unicorn node.')
@with_options
def timeouts_view(name, **kwargs):
    ctx = Context(**kwargs)
    ctx.execute_action('timeouts:view', **{
        'configuration_service': ctx.repo.create_secure_service('unicorn'),
        'name': name,
    })


@timeouts_group.command(name='drop')
@click.option('-n', '--name', metavar='', required=True, help='Unicorn node.')
@with_options
def timeouts_drop(name, **kwargs):
    ctx = Context(**kwargs)
    ctx.execute_action('timeouts:drop', **{
        'configuration_service': ctx.repo.create_secure_service('unicorn'),
        'name': name,
    })


@logging_group.command(name='list_loggers')
@with_options
def logging_list_loggers(**kwargs):
    """
    List all registered logger names.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('logging:list_loggers', **{
        'logging_service': ctx.repo.create_secure_service('logging'),
    })


@logging_group.command(name='set_filter')
@click.option('-n', '--name', metavar='', required=True, help='Logger name.')
@click.option('--filter_def', metavar='', required=True, help='Filter definition.')
@click.option('--ttl', metavar='', required=True, help='TTL.')
@with_options
def logging_set_filter(name, filter_def, ttl, **kwargs):
    """
    Set local filter.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('logging:set_filter', **{
        'logging_service': ctx.repo.create_secure_service('logging'),
        'logger_name': name,
        'filter_def': filter_def,
        'ttl': ttl,
    })


@logging_group.command(name='remove_filter')
@click.option('-i', '--filter-id', metavar='', required=True, help='Filter id.')
@with_options
def logging_remove_filter(filter_id, **kwargs):
    """
    Remove filter by filter id.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('logging:remove_filter', **{
        'logging_service': ctx.repo.create_secure_service('logging'),
        'filter_id': filter_id,
    })


@logging_group.command(name='list_filters')
@with_options
def logging_list_filters(**kwargs):
    """
    List all available filters.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('logging:list_filters', **{
        'logging_service': ctx.repo.create_secure_service('logging'),
    })


@logging_group.command(name='set_cluster_filter')
@click.option('-n', '--name', metavar='', required=True, help='Logger name.')
@click.option('--filter_def', metavar='', required=True, help='Filter definition.')
@click.option('--ttl', metavar='', required=True, help='TTL.')
@with_options
def logging_set_cluster_filter(name, filter_def, ttl, **kwargs):
    """
    Set cluster-wide filter.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('logging:set_cluster_filter', **{
        'logging_service': ctx.repo.create_secure_service('logging'),
        'logger_name': name,
        'filter_def': filter_def,
        'ttl': ttl,
    })


@auth_group.command(name='list')
@with_options
def auth_list(**kwargs):
    """
    Shows available authorization groups.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('auth:group:list', **{
        'storage': ctx.repo.create_secure_service('storage'),
    })


@auth_group.command(name='create')
@click.option('-n', '--name', metavar='', required=True, help='Group name.')
@click.option('--token', metavar='', required=True, help='Secure token.')
@click.option('--force', metavar='', is_flag=True, default=False, help='Override if exists.')
@with_options
def auth_create(name, token, force, **kwargs):
    """
    Creates an authorization group.

    The group sets a named association between an authorization token and the list of services. This
    is useful for group of applications that want to share a single token.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('auth:group:create', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'token': token,
        'force': force,
    })


@auth_group.command(name='edit')
@click.option('-n', '--name', metavar='', required=True, help='Group name.')
@with_options
def auth_edit(name, **kwargs):
    """
    Interactively edits an authorization group.
    """
    ctx = Context(**kwargs)
    ctx.timeout = None
    ctx.execute_action('auth:group:edit', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@auth_group.command(name='rm')
@click.option('-n', '--name', metavar='', required=True, help='Group name.')
@click.option('--drop/--no-drop', metavar='', default=False, help='Remove members.')
@with_options
def auth_remove(name, drop, **kwargs):
    """
    Removes an authorization group.

    Removes an authorization group with or without excluding associated members depending on --drop
    flag (disabled by default).
    """
    ctx = Context(**kwargs)
    ctx.execute_action('auth:group:remove', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'drop': drop,
    })


@auth_group.command(name='view')
@click.option('-n', '--name', metavar='', required=True, help='Group name.')
@with_options
def auth_view(name, **kwargs):
    """
    Shows an authorization group's content.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('auth:group:view', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@auth_group.command(name='add')
@click.option('-n', '--name', metavar='', required=True, help='Group name.')
@click.option('--service', metavar='', required=True, help='Service name.')
@with_options
def auth_add(name, service, **kwargs):
    """
    Adds a member of an authorization group.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('auth:group:members:add', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'service': service,
    })


@auth_group.command(name='exclude')
@click.option('-n', '--name', metavar='', required=True, help='Group name.')
@click.option('--service', metavar='', required=True, help='Service name.')
@with_options
def auth_exclude(name, service, **kwargs):
    """
    Excludes a member of an authorization group.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('auth:group:members:exclude', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'service': service,
    })


@access_storage_group.command(name='list')
@with_options
def access_storage_list(**kwargs):
    """
    Shows collections with ACL.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('access:storage:list', **{
        'storage': ctx.repo.create_secure_service('storage'),
    })


@access_storage_group.command(name='view')
@click.option('-n', '--name', metavar='', required=True, help='Collection name.')
@with_options
def access_storage_view(name, **kwargs):
    """
    Shows ACL for the specified collection.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('access:storage:view', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@access_storage_group.command(name='create')
@click.option('-n', '--name', metavar='', required=True, help='Collection name.')
@with_options
def access_storage_create(name, **kwargs):
    """
    Creates new ACL for the specified collection.

    Does nothing if ACL already exists.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('access:storage:create', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@access_storage_group.command(name='edit')
@click.option('-n', '--name', metavar='', required=True, help='Collection name.')
@click.option('-c', '--cid', metavar='', multiple=True, help='Client ID.')
@click.option('-u', '--uid', metavar='', multiple=True, help='User ID.')
@click.option('--perm', metavar='', required=True, type=click.Choice(['R', 'W', 'RW', '0']),
              help='Permissions.')
@with_options
def access_storage_edit(name, cid, uid, perm, **kwargs):
    """
    Edits ACL for the specified collection.

    Creates if necessary.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('access:storage:edit', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
        'cids': cid,
        'uids': uid,
        'perm': perm,
    })


@access_storage_group.command(name='rm')
@click.option('-n', '--name', metavar='', help='Collection name.')
@click.option('--yes', is_flag=True, default=False, help='Do not prompt.')
@with_options
def access_storage_rm(name, yes, **kwargs):
    """
    Remove ACL for the specified collection.

    If none is specified - removes ACL for all collections.
    """
    if name is None:
        if not yes:
            click.confirm('Are you sure you want to remove all ACL?', abort=True)

    ctx = Context(**kwargs)
    ctx.execute_action('access:storage:rm', **{
        'storage': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@access_event_group.command(name='list')
@with_options
def access_list(**kwargs):
    """
    Shows services for which there are ACL specified.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('access:list', **{
        'unicorn': ctx.repo.create_secure_service('unicorn'),
    })


@access_event_group.command(name='view')
@click.option('--name', metavar='', required=True, help='Service name.')
@with_options
def access_view(name, **kwargs):
    """
    Shows ACL for the specified service.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('access:view', **{
        'unicorn': ctx.repo.create_secure_service('unicorn'),
        'service': name,
    })


@access_event_group.command(name='add')
@click.option('--name', metavar='', required=True, help='Service name.')
@click.option('--event', metavar='', required=True, help='Event name.')
@click.option('-c', '--cid', multiple=True, help='Client identifier.')
@click.option('-u', '--uid', multiple=True, help='User identifier.')
@with_options
def access_add(name, event, cid, uid, **kwargs):
    """
    Creates a new record with specified cid/uid in the event authorization.

    Requests with token that contains such cid/uid will have access to the specified event of a
    service.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('access:add', **{
        'unicorn': ctx.repo.create_secure_service('unicorn'),
        'service': name,
        'event': event,
        'cids': cid,
        'uids': uid,
    })


@access_event_group.command(name='edit')
@click.option('--name', metavar='', required=True, help='Service name.')
@with_options
def access_edit(name, **kwargs):
    """
    Edits interactively an access list for a service with further validation.
    """
    ctx = Context(**kwargs)
    ctx.timeout = None
    ctx.execute_action('access:edit', **{
        'unicorn': ctx.repo.create_secure_service('unicorn'),
        'service': name,
    })


@keyring_group.command(name='update')
@click.option('--cid', metavar='', type=int, required=True, help='Client id.')
@with_options
def keyring_update(cid, **kwargs):
    """
    Downloads and cache public key(s).

    Downloads and caches in the TVM component public key(s) for a given client id.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('keyring:update', **{
        'tvm': ctx.repo.create_secure_service('tvm'),
        'cid': cid,
    })


cli = click.CommandCollection(sources=[tools])
