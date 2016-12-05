#!/usr/bin/env bash

curl localhost:8080 -H "X-Cocaine-JSON-RPC: 1" -d \
    '{"jsonrpc": "2.0", "method": "storage.read", "params": ["collection", "key"], "id": 1}'
curl localhost:8080 -H "X-Cocaine-JSON-RPC: 1" -d \
    '{"jsonrpc": "2.0", "method": "logging.emit", "params": [3, "app/proxy", "le value"], "id": 1}'
curl localhost:8080 -H "X-Cocaine-JSON-RPC: 1" -d \
    '{"jsonrpc": "2.0", "method": "echo-cpp.enqueue", "params": ["ping"], "chunks": [["write", ["hui"]], ["close", []]], "id": 1}'
curl localhost:8080 -H "X-Cocaine-JSON-RPC: 1" -d \
'{"jsonrpc": "2.0", "method": "echo-cpp.enqueue", "params": ["__bang"], "chunks": [["close", []]], "id": 1}'