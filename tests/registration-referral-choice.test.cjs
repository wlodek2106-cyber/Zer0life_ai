'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const project = path.join(__dirname, '..');
const dashboard = fs.readFileSync(path.join(project, 'dashboard.html'), 'utf8');
const walletService = fs.readFileSync(path.join(project, 'wallet_service.py'), 'utf8');

test('registration offers an explicit choice to use or skip a referral', () => {
    assert.match(dashboard, /name="registration-referral-choice" value="with_referral"/);
    assert.match(dashboard, /name="registration-referral-choice" value="without_referral"/);
    assert.match(dashboard, /Your 1,000 ZRL registration bonus is the same either way\./);
});

test('a referral invite is preselected, but the player choice controls the registration request', () => {
    assert.match(dashboard, /useReferralChoice\.checked = Boolean\(incomingReferralMatch\)/);
    assert.match(dashboard, /referralChoice === 'with_referral'[\s\S]*?referralCodeInput\.value/);
    assert.match(dashboard, /referralChoice,\s*\n\s*\}\)/);
    assert.match(dashboard, /Enter an invite code or choose to register without a referral\./);
});

test('without-referral selection hides the code field and keeps profile creation visibly active', () => {
    assert.match(dashboard, /referralCodeGroup\.hidden = !useReferral/);
    assert.match(dashboard, /#register-modal #reg-referral-group\[hidden\]\s*\{\s*display:\s*none !important;\s*\}/);
    assert.match(dashboard, /#register-modal #reg-submit-btn:not\(:disabled\)\s*\{[^}]*background:[^;]+!important;[^}]*color:[^;]+!important;/);
    const submitButton = dashboard.match(/<button\b[^>]*id="reg-submit-btn"[^>]*>/);
    assert.ok(submitButton, 'registration submit button exists');
    assert.doesNotMatch(submitButton[0], /\bdisabled\b/);
});

test('server honors an explicit no-referral choice and still grants the registration bonus', () => {
    assert.match(walletService, /referral_choice == "without_referral":[\s\S]*?referral_code = ""/);
    assert.match(walletService, /UPDATE wallet_accounts SET referred_by = NULL WHERE telegram_id = \?/);
    assert.match(walletService, /_ledger_entry\(db, user\["id"\], "registration", 1000 \* ZRL_SCALE/);
});
