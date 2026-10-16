import { cp, mkdir, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const repo = fileURLToPath(new URL('../../', import.meta.url));
const site = path.join(repo, 'generated-artifacts/site');
await rm(site, { recursive: true, force: true });
await mkdir(site, { recursive: true });
// Explicit public allow-list: never publish Python, DBs, secrets, tests or node_modules.
for (const item of await readdir(repo, { withFileTypes: true })) {
    if (item.isFile() && /\.(html|js|css|ico)$/.test(item.name)) {
        await cp(path.join(repo, item.name), path.join(site, item.name));
    }
}
await cp(path.join(repo, 'assets'), path.join(site, 'assets'), { recursive: true });
await cp(path.join(repo, 'generated-artifacts/wallet-sdk'), path.join(site, 'wallet-embedded'), { recursive: true });
await writeFile(path.join(site, '.nojekyll'), '');
const html = await readFile(path.join(site, 'dashboard.html'), 'utf8');
if (!html.includes('./wallet-embedded/embedded-wallet.js?')) {
    throw new Error('Dashboard is not wired to the built SDK.');
}
console.log('Static Pages site built; only public frontend assets included.');
