'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const dashboard = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');
const wallet = fs.readFileSync(path.join(__dirname, '..', 'wallet.js'), 'utf8');

test('run-claim conflicts refresh the displayed authoritative balance', () => {
    assert.match(wallet, /error\.data\s*=\s*result/);
    assert.match(dashboard, /error\.status === 409 && error\.message === 'No run rewards to claim'/);
    assert.match(dashboard, /window\.legacyRecovery\.preserve\(STORAGE_PREFIX\)/);
    assert.match(dashboard, /window\.secureWallet\.applyResult\(current\)/);
    assert.match(dashboard, /Current game balance: \$\{balance\.toFixed\(2\)\} ZRL/);
    assert.match(dashboard, /wallet\.js\?v=boots-solana-pay-reference-1/);
});
