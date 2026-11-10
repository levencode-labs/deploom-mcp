// Restrict HTTP requests made by the trusted native scoped audit CLI.
'use strict';

const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');
const { urlToHttpOptions } = require('node:url');

const registry = new URL(process.env.DEPLOOM_AUDIT_GUARD_REGISTRY);
const statusFile = process.env.DEPLOOM_AUDIT_GUARD_STATUS;
const base = registry.pathname.replace(/\/+$/, '');
const allowedPaths = new Set([
    base + '/-/npm/v1/security/advisories/bulk',
    base + '/-/npm/v1/security/audits/quick',
]);
const port = protocol => protocol === 'https:' ? '443' : '80';
const hostname = value => String(value || '').toLowerCase().replace(/^\[|\]$/g, '');
let denied = false;
fs.writeFileSync(statusFile, 'ready');

function deny() {
    denied = true;
    fs.writeFileSync(statusFile, 'denied');
}

function restrict(transport, protocol) {
    const original = transport.request;
    transport.request = function (input, ...args) {
        const options = typeof input === 'string' || input instanceof URL
            ? { ...urlToHttpOptions(new URL(input)), ...(typeof args[0] === 'object' ? args[0] : {}) }
            : input || {};
        if (denied || protocol !== registry.protocol || (options.protocol && options.protocol !== protocol)
            || hostname(options.hostname || options.host) !== hostname(registry.hostname)
            || String(options.port || port(protocol)) !== (registry.port || port(protocol))
            || options.socketPath || String(options.method).toUpperCase() !== 'POST'
            || !allowedPaths.has(options.path)) {
            deny();
            throw Object.assign(new Error('Scoped audit request denied'), { code: 'DEPLOOM_AUDIT_EGRESS_DENIED' });
        }
        const request = original.call(this, input, ...args);
        request.prependListener('response', response => {
            if (response.statusCode >= 300 && response.statusCode < 400) {
                deny();
            } else if (!denied) {
                fs.writeFileSync(statusFile, 'observed');
            }
        });
        return request;
    };
    transport.get = function (...args) {
        const request = transport.request(...args);
        request.end();
        return request;
    };
}

restrict(http, 'http:');
restrict(https, 'https:');
