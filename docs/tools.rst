Cocaine Tools Command Line Interface
====================================
This part describes cocaine command line tools.
It is useful for management your cocaine cloud, uploading applications, profiles and other stuff.


Common tools
------------------------------------
This part describes common tools.


cocaine-tool info
''''''''''''''''''''''''''''''''''''
Show information about cocaine runtime

    Return json-like string with information about cocaine-runtime.

    >>> cocaine-tool info
    {
        "uptime": 738,
        "identity": "dhcp-666-66-wifi.yandex.net"
    }

    If some applications is running, its information will be displayed too.

    >>> cocaine-tool info
    {
        "uptime": 738,
        "apps": {
            "Echo": {
                "load-median": 0,
                "profile": "EchoProfile",
                "sessions": {
                    "pending": 0
                },
                "queue": {
                    "depth": 0,
                    "capacity": 100
                },
                "state": "running",
                "slaves": {
                    "active": 0,
                    "idle": 0,
                    "capacity": 4
                }
            }
        },
        "identity": "dhcp-666-66-wifi.yandex.net"
    }


cocaine-tool call
''''''''''''''''''''''''''''''''''''
Invoke specified method from service.

    Performs method invocation from specified service. Service name should be correct string and must be correctly
    located through locator. By default, locator endpoint is ```localhost, 10053```, but it can be changed by passing
    global `--host` and `--port` arguments.

    Method arguments should be passed in double quotes as they would be written in Python.
    If no method provided, service API will be printed.

    *Request service API*:

    >>> cocaine-tool call node
    API of service "node": [
        "start_app",
        "pause_app",
        "info"
    ]

    *Invoke `info` method from service `node`*:

    >>> cocaine-tool call node info
    {'uptime': 1855, 'identity': 'dhcp-666-66-wifi.yandex.net'}

    *Specifying locator endpoint*

    >>> cocaine-tool call node info --host localhost --port 10052
    LocatorResolveError: Unable to resolve API for service node at localhost:10052, because [Errno 61] Connection
    refused

    *Passing complex method arguments*

    >>> cocaine-tool call storage read "'apps', 'Echo'"
    [Lot of binary data]


Application specific tools
------------------------------------
This part describes application specific tools.

cocaine-tool app list
''''''''''''''''''''''''''''''''''''
Show installed applications list.

    Returns list of installed applications.

    >>> cocaine-tools app list
    [
        "app1",
        "app2"
    ]

cocaine-tool app view
''''''''''''''''''''''''''''''''''''
Show manifest context for application.

    If application is not uploaded, an error will be displayed.

    :name: application name.

    >>> cocaine-tool app view --name Echo
    {
        "slave": "/home/satan/echo/echo.py"
    }

cocaine-tool app upload
''''''''''''''''''''''''''''''''''''
Upload application with its environment (directory) into the storage.

    Application directory or its subdirectories must contain valid manifest file named `manifest.json` or `manifest`,
    which represents application settings. More you can read
    `here <https://github.com/cocaine/cocaine-core/wiki/manifest>`_. Manifest is located automatically, otherwise you
    must specify it explicitly by setting `--manifest` option.

    By default, leaf directory name is treated as application name. But you can specify application name by setting
    `--name` option.

    If you have already prepared application archive (\*.tar.gz), you can explicitly specify path to it by setting
    `--package` option. Note, that PATH and --package options are mutual exclusive.

    There is possible to control process of creating and uploading application by specifying `--debug=tools` option,
    which is helpful when some errors occurred. If you want full debugging output, specify `--debug=all` option.

    We are now supporting `Docker <http://docker.io>`_ containerization technology!

    There is possible to create Docker container from your application and push it to the Docker Registry. To do this,
    application root directory must contain valid `Dockerfile` from which the container will be built. Then, specify
    `--docker-address` option and watch container build progress.
    Just created container needs its place to store itself. By specifying `--registry` option, you notifying build
    system a place where Docker Registry is located, and the container will be uploaded there.

    Note, that Docker-specific options and `--package` option are mutual exclusive.

    :path: path to the application root.
    :name: application name. If it is not specified, application will be named as its directory name.
    :manifest: path to application manifest json file.
    :package: path to application archive.
    :docker-address: address of docker build farm with explicit protocol specifying. For example:
                     `http://your-farm.com:4321` or `unix:///var/run/docker.sock`. Note, that application directory
                     must contain valid `Dockerfile` to create container.
    :registry: registry address, where just created container will be pushed. For example: `your-registry.com:5000`.

    *The simplest usage*

    >>> cd /home/user/your_app
    >>> cocaine-tool app upload
    Application your_app has been successfully uploaded

    *But you can specify path directly as first positional argument like this*

    >>> cocaine-tool app upload ~/echo
    Application echo has been successfully uploaded

    *Explicitly set application name*

    >>> cocaine-tool app upload ~/echo --name TheEchoApp
    Application TheEchoApp has been successfully uploaded

    *If you want to explicitly specify application archive*

    >>> cocaine-tool app upload --name echo --manifest ~/echo/manifest.json --package ~/echo/echo.tar.gz
    Application echo has been successfully uploaded

    *Let's upload application, that contains `Dockerfile` to the Docker*

    >>> cocaine-tool app upload ~/echo --docker-address=http://docker-farm.net:4321 --registry=docker-registry.net:5000
    Local path detected. Creating archive "~/echo"... OK
    Building "http://docker-farm.net:4321/v1.4/build?q=False&t=docker-registry.net%3A5000%2Fecho"... Step 1 : FROM ubuntu
     ---> 8dbd9e392a96
    Step 2 : MAINTAINER Evgeny Safronov "division494@gmail.com"
     ---> Using cache
     ---> 41fe6b0d44a8
    Step 3 : RUN echo "deb http://archive.ubuntu.com/ubuntu precise main universe" > /etc/apt/sources.list
     ---> Using cache
     ---> 1a45facf1e13
    Step 4 : RUN apt-get update
     ---> Using cache
     ---> 1d8ffd3385ef
    Step 5 : RUN apt-get install -y git
     ---> Using cache
     ---> 1b5ad01e42f3
    Step 6 : RUN apt-get install -y nano
     ---> Using cache
     ---> 58d5b0c42376
    Successfully built 58d5b0c42376
    OK
    Pushing "echo" into "docker-registry.net:5000/v1/"... The push refers to a repository [docker-registry.net:5000/echo] (len: 1)
    Sending image list
    Pushing repository docker-registry.net:5000/echo (1 tags)
    Image 8dbd9e392a964056420e5d58ca5cc376ef18e2de93b5cc90e868a1bbc8318c1c already pushed, skipping
    Image 41fe6b0d44a84cebdd88a75c1e6dfca114edc4ce7b65e7748a54e614443c1625 already pushed, skipping
    Image 1a45facf1e139f32c03af3c006e78bb6a6e6134e823e64b714022dce25a0fac1 already pushed, skipping
    Image 1d8ffd3385ef3b9b3614ffc0ddf319dc35c6cbe36375a45a182e5981b50311dc already pushed, skipping
    Image 1b5ad01e42f37a54c569297330ca7cb188d0459e8575df1132779e0d695f916d already pushed, skipping
    Image 58d5b0c4237612c136c3802de6230d03c1b4b1c55d04710bd1bc8ed9befcbb8a already pushed, skipping
    OK

cocaine-tool app remove
''''''''''''''''''''''''''''''''''''
Remove application from storage.

    No error messages will display if specified application is not uploaded.

    :name: application name.

    >>> cocaine-tool app remove --name echo
    The app "echo" has been successfully removed

cocaine-tool app start
''''''''''''''''''''''''''''''''''''
Start application with specified profile.

    Does nothing if application is already running.

    :name: application name.
    :profile: desired profile.

    >>> cocaine-tool app start --name Echo --profile EchoDefault
    {
        "Echo": "the app has been started"
    }

    *If application is already running*

    >>> cocaine-tool app start --name Echo --profile EchoDefault
    {
        "Echo": "the app is already running"
    }

cocaine-tool app pause/stop
''''''''''''''''''''''''''''''''''''
Stop application.

    This command is alias for ```cocaine-tool app stop```.

    :name: application name.

    >>> cocaine-tool app pause --name Echo
    {
        "Echo": "the app has been stopped"
    }

    *For non running application*

    >>> cocaine-tool app pause --name Echo
    {
        "Echo": "the app is not running"
    }

cocaine-tool app restart
''''''''''''''''''''''''''''''''''''
Restart application.

    Executes ```cocaine-tool app pause``` and ```cocaine-tool app start``` sequentially.

    It can be used to quickly change application profile.

    :name: application name.
    :profile: desired profile. If no profile specified, application will be restarted with the current profile.

    *Usual case*

    >>> cocaine-tool app restart --name Echo
    [
        {
            "Echo": "the app has been stopped"
        },
        {
            "Echo": "the app has been started"
        }
    ]

    *If application was not run and no profile name provided*

    >>> cocaine-tool app restart --name Echo
    Error occurred: Application "Echo" is not running and profile not specified

    *But if we specify profile name*

    >>> cocaine-tool app restart --name Echo --profile EchoProfile
    [
        {
            "Echo": "the app is not running"
        },
        {
            "Echo": "the app has been started"
        }
    ]

    *In case wrong profile just stops application*

    >>> cocaine-tool app restart --name Echo --profile EchoProf
    [
        {
            "Echo": "the app has been stopped"
        },
        {
            "Echo": "object 'EchoProf' has not been found in 'profiles'"
        }
    ]

cocaine-tool app check
''''''''''''''''''''''''''''''''''''
Checks application status.

    :name: application name.

    >>> cocaine-tool app check --name Echo
    {
        "Echo": "stopped or missing"
    }


Profile specific tools
------------------------------------
This part describes profile specific tools.

cocaine-tool profile list
''''''''''''''''''''''''''''''''''''
Show installed profiles.

    Returns list of installed profiles.

    >>> cocaine-tool profile list
    [
        "EchoProfile"
    ]

cocaine-tool profile view
''''''''''''''''''''''''''''''''''''
Show profile configuration context.

    :name: profile name

    >>> cocaine-tool profile view --name EchoProfile
    {
        "pool-limit": 4
    }

cocaine-tool profile upload
''''''''''''''''''''''''''''''''''''
Upload profile into the storage.

    :name: profile name.
    :profile: path to the profile json file.

    >>> cocaine-tool profile upload --name EchoProfile --profile ../examples/echo/profile.json
    The profile "EchoProfile" has been successfully uploaded

cocaine-tool profile remove
''''''''''''''''''''''''''''''''''''
Remove profile from the storage.

    :name: profile name.

    >>> cocaine-tool profile remove --name EchoProfile
    The profile "EchoProfile" has been successfully removed


Profile specific tools
------------------------------------
This part describes runlist specific tools.

cocaine-tool runlist list
''''''''''''''''''''''''''''''''''''
Show uploaded runlists.

    Returns list of installed runlists.

    >>> cocaine-tool runlist list
    [
        "default"
    ]

cocaine-tool runlist view
''''''''''''''''''''''''''''''''''''
Show configuration context for runlist.

    :name: runlist name.

    >>> cocaine-tool runlist view --name default
    {
        "Echo": "EchoProfile"
    }

cocaine-tool runlist upload
''''''''''''''''''''''''''''''''''''
Upload runlist with context into the storage.

    :name: runlist name.
    :runlist: path to the runlist configuration json file.

    >>> cocaine-tool runlist upload --name default --runlist ../examples/echo/runlsit.json
    The runlist "default" has been successfully uploaded

cocaine-tool runlist create
''''''''''''''''''''''''''''''''''''
Create runlist and upload it into the storage.

    :name: runlist name.

    >>> cocaine-tool runlist create --name default
    The runlist "default" has been successfully created

cocaine-tool runlist remove
''''''''''''''''''''''''''''''''''''
Remove runlist from the storage.

    :name: runlist name.

    >>> cocaine-tool runlist remove --name default
    The runlist "default" has been successfully removed

cocaine-tool runlist add-app
''''''''''''''''''''''''''''''''''''
Add specified application with profile to the runlist.

    Existence of application or profile is not checked.

    :name: runlist name.
    :app: application name.
    :profile: suggested profile name.

    >>> cocaine-tool runlist add-app --name default --app Echo --profile EchoProfile
    {
        "status": "Success",
        "added": {
            "profile": "EchoProfile",
            "app": "Echo"
        },
        "runlist": "default"
    }


Crashlog specific tools
------------------------------------
This part describes crashlog specific tools.

cocaine-tool crashlog list
''''''''''''''''''''''''''''''''''''
Show crashlogs list for application.

    Prints crashlog list in timestamp - uuid format.

    :name: application name.

    >>> cocaine-tool crashlog list --name Echo
    Currently available crashlogs for application 'Echo'
    1372165800114964 Tue Jun 25 17:10:00 2013 2d92aa19-535d-4aa3-9c68-7aa32f9967df
    1372166090866950 Tue Jun 25 17:14:50 2013 e27b2ccc-64a6-4958-a9b4-f2abac974e4a
    1372166371522675 Tue Jun 25 17:19:31 2013 762f2fb8-8d8c-4b1d-ab79-14cdb6332ecb
    1372166822795587 Tue Jun 25 17:27:02 2013 1fd3ca03-3402-4279-8b2b-1e40ff92f4a7

cocaine-tool crashlog view
''''''''''''''''''''''''''''''''''''
Show crashlog for application with specified timestamp.

    :name: application name.
    :timestamp: desired timestamp - time_t format.

    >>> cocaine-tool crashlog view --name Echo --timestamp 1372165800114964
    Crashlog:
      File "/Library/Python/2.7/site-packages/tornado-3.1-py2.7.egg/tornado/ioloop.py", line 672, in start
        self._handlers[fd](fd, events)
      File "/Library/Python/2.7/site-packages/tornado-3.1-py2.7.egg/tornado/stack_context.py", line 331, in wrapped
        raise_exc_info(exc)
      File "/Library/Python/2.7/site-packages/tornado-3.1-py2.7.egg/tornado/stack_context.py", line 302, in wrapped
        ret = fn(*args, **kwargs)
      File "build/bdist.macosx-10.8-intel/egg/cocaine/asio/ev.py", line 93, in proxy
        self._callbacks[(fd, self.WRITE)]()
      File "build/bdist.macosx-10.8-intel/egg/cocaine/asio/stream.py", line 128, in _on_event
        sent = self.pipe.write(buffer(current, self.tx_offset))
    TypeError: an integer is required
    ERROR:tornado.application:Exception in I/O handler for fd 11

cocaine-tool crashlog remove
''''''''''''''''''''''''''''''''''''
Remove crashlog for application with specified timestamp from the storage.

    :name: application name.
    :timestamp: desired timestamp - time_t format.

    >>> cocaine-tool crashlog remove --name Echo --timestamp 1372165800114964
    Crashlog for app "Echo" has been removed

cocaine-tool crashlog removeall
''''''''''''''''''''''''''''''''''''
Remove all crashlogs for application from the storage.

    :name: application name.

    >>> cocaine-tool crashlog removeall --name Echo
    Crashlogs for app "Echo" have been removed


Routing group specific tools
------------------------------------
This part describes routing group specific tools.

cocaine-tool group list
''''''''''''''''''''''''''''''''''''
Show currently uploaded routing groups.

    Routing groups are located in the storage.

    >>> cocaine-tool group list
    [
        "new_group"
    ]

cocaine-tool group view
''''''''''''''''''''''''''''''''''''
Show content of specified routing group.

    :name: routing group name.

    >>> cocaine-tool group view new_group
    {
        "app": 2
    }

cocaine-tool group create
''''''''''''''''''''''''''''''''''''
Create new routing group and (optionally) specify its content.

    Specified content can be both direct json expression in single quotes, or path to the json file with settings.
    The settings itself must be key-value list, where `key` represents application name, and `value` represents its
    weight. For example:

    >>> cocaine-tool group create new_group '{
        "app": 1,
        "another_app": 2
    }'

    Let's create it from file:

    >>> cocaine-tool group create new_group ../group.json

    :name: routing group name.
    :content: routing group content. It can be both path to the json file, or typed direct expression in single quotes.

    .. warning:: All application weights must be positive integers.

cocaine-tool group remove
''''''''''''''''''''''''''''''''''''
Remove existing routing group.

    :name: routing group name.

    >>> cocaine-tool group remove new_group

cocaine-tool group refresh
''''''''''''''''''''''''''''''''''''
Refresh routing group or groups, forcing locator to reread them from storage.

    :name: routing group name.

    .. note:: If group name is empty this command will refresh all groups.

    Let's refresh all groups:

    >>> cocaine-tool group refresh

    Or maybe only one:

    >>> cocaine-tool group refresh new_group

cocaine-tool group push
''''''''''''''''''''''''''''''''''''
Add application with its weight into the routing group.

    :name: routing group name.
    :app: application name.
    :weight: positive integer meaning application weight.

    .. warning:: application weight must be positive integer.

    Let's push application `echo` to the routing group `new_group` with weight `42`:

    >>> cocaine-tool group push new_group echo 42

cocaine-tool group pop
''''''''''''''''''''''''''''''''''''
Remove application from routing group.

    :name: routing group name.
    :app: application name.

    Here we are removing `echo` application from routing group `new_group`:

    >>> cocaine-tool group pop new_group echo