'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const productionCopyFiles = [
    'dashboard.html',
    'wallet.js',
    'legacy-farms.js',
    'legacy-recovery.js',
    'recovery-actions.js',
    'phantom-mobile.js',
    'wallet-embedded/src/controller.js',
    'bot.py',
    'payout_server.py',
    'wallet_service.py',
    'wallet_recovery.py',
    'recovery_release.py',
    'wallet_withdrawal_otp.py',
    'withdrawal_errors.py',
    'merchant_payments.py',
];

test('user-facing app and bot copy stays in English', () => {
    for (const file of productionCopyFiles) {
        const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
        assert.doesNotMatch(source, /[\u0400-\u04FF]/, `${file} still contains Cyrillic text`);
    }
});
