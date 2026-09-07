// node scripts/qa-kill-script.js
// Real-process proof for kill.bat / scripts/kill-app.ps1 (Windows):
//  1. an isolated instance launched from THIS checkout writes boardclip.pid
//  2. kill-app.ps1 with the WMI sweep disabled (what a wedged winmgmt looks like)
//     stops it through the pid file alone, exit 0, and clears the pid file
//  3. a second instance is stopped by the normal kill.bat (pid file + sweep)
//  4. with nothing running the script still exits 0
//  5. electron processes of OTHER checkouts (the live app, MCP helpers) are
//     untouched: their count before and after is identical
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const ROOT = path.join(__dirname, '..');
const ELECTRON = path.join(ROOT, 'node_modules', 'electron', 'dist', 'electron.exe');
const PID_FILE = path.join(ROOT, 'boardclip.pid');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let providerPaths = [];
try { const accs = require(path.join(ROOT, 'lib', 'cloud-accounts'))(); providerPaths = (Array.isArray(accs) ? accs : []).map((a) => a && a.path).filter(Boolean); } catch {}

function ps(cmd) {
  const r = spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', cmd], { encoding: 'utf8', timeout: 60000 });
  return { code: r.status, out: (r.stdout || '') + (r.stderr || '') };
}
function otherElectrons() {
  // Get-Process only (no WMI): electron.exe processes NOT from this checkout.
  const r = ps(`@(Get-Process electron -ErrorAction SilentlyContinue | Where-Object { $_.Path -and ([IO.Path]::GetFullPath($_.Path) -ine '${ELECTRON.replace(/'/g, "''")}') }).Count`);
  return Number(String(r.out).trim().split(/\r?\n/).pop());
}
function alive(pid) { const r = ps(`[bool](Get-Process -Id ${pid} -ErrorAction SilentlyContinue)`); return /True/.test(r.out); }
function launch(tag) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bc-kill-' + tag + '-'));
  const dataDir = path.join(tmp, 'data'); fs.mkdirSync(dataDir, { recursive: true });
  fs.writeFileSync(path.join(dataDir, 'clipboard-settings.json'), JSON.stringify({ p2p_enabled: false, ai_access_enabled: false, sync_disabled_paths: providerPaths, diagnostics_enabled: false }));
  fs.writeFileSync(path.join(dataDir, 'clipboard-history.json'), '[]');
  const child = spawn(ELECTRON, ['.', `--user-data-dir=${path.join(tmp, 'udd')}`], {
    cwd: ROOT, env: { ...process.env, BOARDCLIP_DATA_DIR: dataDir, BOARDCLIP_ISOLATED: '1' }, stdio: 'ignore', windowsHide: true, detached: false,
  });
  return { child, tmp };
}
async function waitPidFile(pid) {
  for (let i = 0; i < 60; i++) {
    try { const rec = JSON.parse(fs.readFileSync(PID_FILE, 'utf8')); if (rec.pid === pid) return rec; } catch {}
    await sleep(500);
  }
  return null;
}
(async () => {
  const out = { ok: false };
  const started = [];
  try {
    if (process.platform !== 'win32') throw new Error('windows only');
    try { fs.unlinkSync(PID_FILE); } catch {}
    out.othersBefore = otherElectrons();

    // ---- 1 + 2: pid-file-only kill (WMI sweep disabled) ----
    const a = launch('a'); started.push(a);
    const recA = await waitPidFile(a.child.pid);
    out.pidFileWritten = !!recA;
    if (!recA) throw new Error('instance A never wrote boardclip.pid');
    const t0 = Date.now();
    const killA = ps(`& '${path.join(ROOT, 'scripts', 'kill-app.ps1')}' -AppDir '${ROOT}' -SweepTimeoutSec 0; exit $LASTEXITCODE`);
    out.killA = { code: killA.code, out: killA.out.trim(), ms: Date.now() - t0 };
    await sleep(500);
    out.aDeadAfterPidKill = !alive(a.child.pid);
    out.pidFileClearedByKill = !fs.existsSync(PID_FILE);

    // ---- 3: normal kill.bat (pid file + capped sweep) ----
    const b = launch('b'); started.push(b);
    const recB = await waitPidFile(b.child.pid);
    if (!recB) throw new Error('instance B never wrote boardclip.pid');
    const t1 = Date.now();
    const killB = spawnSync('cmd.exe', ['/c', path.join(ROOT, 'kill.bat')], { encoding: 'utf8', timeout: 60000, cwd: ROOT });
    out.killB = { code: killB.status, out: ((killB.stdout || '') + (killB.stderr || '')).trim(), ms: Date.now() - t1 };
    await sleep(500);
    out.bDeadAfterKillBat = !alive(b.child.pid);

    // ---- 4: nothing running ----
    const t2 = Date.now();
    const killNone = ps(`& '${path.join(ROOT, 'scripts', 'kill-app.ps1')}' -AppDir '${ROOT}'; exit $LASTEXITCODE`);
    out.killNone = { code: killNone.code, out: killNone.out.trim(), ms: Date.now() - t2 };

    // ---- 5: other checkouts untouched ----
    out.othersAfter = otherElectrons();
    out.ok = out.pidFileWritten && out.killA.code === 0 && out.aDeadAfterPidKill && out.pidFileClearedByKill && /sweep skipped|sweep disabled/i.test(out.killA.out)
      && out.killB.code === 0 && out.bDeadAfterKillBat && out.killNone.code === 0 && /not running/i.test(out.killNone.out)
      && out.othersAfter === out.othersBefore;
  } catch (e) { out.error = e.message; }
  finally {
    for (const s of started) { try { s.child.kill(); } catch {} }
    await sleep(800);
    for (const s of started) { try { fs.rmSync(s.tmp, { recursive: true, force: true }); } catch {} }
    try { fs.unlinkSync(PID_FILE); } catch {}
    console.log(JSON.stringify(out, null, 2));
    process.exit(out.ok ? 0 : 1);
  }
})();
