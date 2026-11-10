// Restrict HTTP requests made by the trusted native scoped audit CLI.
'use strict';

const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');
const { urlToHttpOptions } = require('node:url');

const registry = new URL(process.env.DEPLOOM_AUDIT_GUARD_REGISTRY);
const statusFile = process.env.DEPLOOM_AUDIT_GUARD_STATUS;
const base = registry.pathname.replace(/\/+$/, '');
const requireCoverage = process.env.DEPLOOM_AUDIT_GUARD_COVERAGE === '1';
const bulkPath = base + '/-/npm/v1/security/advisories/bulk';
const allowedPaths = new Set([
    bulkPath,
    base + '/-/npm/v1/security/audits/quick',
]);
const port = protocol => protocol === 'https:' ? '443' : '80';
const hostname = value => String(value || '').toLowerCase().replace(/^\[|\]$/g, '');
let denied = false;
let coverageMissing = false;
let bulkResponseSeen = false;
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
        const method = String(options.method || 'GET').toUpperCase();
        const path = String(options.path || '');
        // npm may fetch package metadata to calculate the impact of a finding.
        const metadata = bulkResponseSeen && method === 'GET' && path.startsWith(base + '/')
            && /^(?:(?:@|%40)[A-Za-z0-9_.~-]+(?:\/|%2f))?[A-Za-z0-9_~-][A-Za-z0-9_.~-]*$/i.test(path.slice(base.length + 1));
        if (denied || protocol !== registry.protocol || (options.protocol && options.protocol !== protocol)
            || hostname(options.hostname || options.host) !== hostname(registry.hostname)
            || String(options.port || port(protocol)) !== (registry.port || port(protocol))
            || options.socketPath || (!metadata && (method !== 'POST' || !allowedPaths.has(path)))) {
            deny();
            throw Object.assign(new Error('Scoped audit request denied'), { code: 'DEPLOOM_AUDIT_EGRESS_DENIED' });
        }
        const request = original.call(this, input, ...args);
        request.prependListener('response', response => {
            if (response.statusCode >= 300 && response.statusCode < 400) {
                deny();
            } else if (!denied && !metadata) {
                if (requireCoverage) {
                    const successfulBulk = path === bulkPath && response.statusCode >= 200 && response.statusCode < 300;
                    bulkResponseSeen ||= successfulBulk;
                    coverageMissing ||= !successfulBulk || response.headers['x-deploom-advisory-coverage'] !== 'complete-v1';
                }
                fs.writeFileSync(statusFile, coverageMissing ? 'coverage-missing' : 'observed');
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
