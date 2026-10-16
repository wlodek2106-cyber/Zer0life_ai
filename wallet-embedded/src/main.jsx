import React, { Component, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { PrivyProvider, usePrivy } from '@privy-io/react-auth';
import { useWallets, useCreateWallet, useSignMessage, useExportWallet } from '@privy-io/react-auth/solana';
import { createWalletController } from './controller.js';

// Public application identifier, never an App Secret or a shared player wallet.
const PRIVY_APP_ID = 'cmvb77y4v01b10ejtwhm44mkw';
const notify = () => window.dispatchEvent(new Event('zerolife:embedded-wallet'));
const controller = createWalletController({ onChange: notify });

function WalletBridge() {
    const { ready, authenticated, user, logout } = usePrivy();
    const { ready: walletsReady, wallets } = useWallets();
    const { createWallet } = useCreateWallet();
    const { signMessage } = useSignMessage();
    const { exportWallet } = useExportWallet();
    useEffect(() => {
        controller.update({
            ready, authenticated, user, logout, walletsReady, wallets,
            createWallet, signMessage, exportWallet,
        });
    }, [ready, authenticated, user, logout, walletsReady, wallets, createWallet, signMessage, exportWallet]);
    return null;
}

class WalletBoundary extends Component {
    state = { failed: false };
    static getDerivedStateFromError() { return { failed: true }; }
    componentDidCatch() { window.dispatchEvent(new Event('zerolife:wallet-sdk-error')); }
    render() { return this.state.failed ? null : this.props.children; }
}

const root = document.getElementById('embedded-wallet-root');
if (root && window.Telegram?.WebApp?.initData) {
    window.embeddedWallet = controller;
    createRoot(root).render(
        <WalletBoundary>
            <PrivyProvider appId={PRIVY_APP_ID} config={{
                loginMethods: ['telegram'],
                appearance: { theme: 'dark', accentColor: '#b5f33f', walletChainType: 'solana-only' },
                embeddedWallets: {
                    ethereum: { createOnLogin: 'off' },
                    solana: { createOnLogin: 'off' },
                    showWalletUIs: true,
                },
            }}>
                <WalletBridge />
            </PrivyProvider>
        </WalletBoundary>
    );
    notify();
}
