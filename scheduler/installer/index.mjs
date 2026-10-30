#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { access, chmod, copyFile, mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { homedir, platform, arch } from 'node:os';
import path from 'node:path';
import { createInterface } from 'node:readline/promises';
import { stdin, stdout } from 'node:process';
import { spawnSync } from 'node:child_process';

const repo = 'chialuka/Auto-Continue-Plugin';
const serverName = 'agent-scheduler';
const home = homedir();
const codexHome = process.env.CODEX_HOME || path.join(home, '.codex');
const skillSource = new URL('../skills/agent-scheduler/SKILL.md', import.meta.url);
const rl = createInterface({ input: stdin, output: stdout });

function commandExists(name) {
  const searchPath = process.env.PATH || '';
  const extensions = platform() === 'win32' ? (process.env.PATHEXT || '.EXE;.CMD;.BAT').split(';') : [''];
  for (const dir of searchPath.split(path.delimiter)) {
    for (const ext of extensions) {
      if (existsSync(path.join(dir, name + ext))) return true;
    }
  }
  return false;
}

function tomlString(value) {
  return `"${value.replaceAll('\\', '\\\\').replaceAll('"', '\\"').replaceAll('\n', '\\n').replaceAll('\r', '\\r')}"`;
}

async function updateCodexMcp(executable) {
  const configPath = path.join(codexHome, 'config.toml');
  await mkdir(path.dirname(configPath), { recursive: true });
  let source = '';
  try { source = await readFile(configPath, 'utf8'); } catch (error) { if (error.code !== 'ENOENT') throw error; }

  const lines = source.split(/\r?\n/);
  const kept = [];
  let skipping = false;
  for (const line of lines) {
    const header = line.match(/^\s*\[([^\]]+)\]\s*(?:#.*)?$/);
    if (header) {
      const sectionName = header[1].trim();
      skipping = sectionName === `mcp_servers.${serverName}` || sectionName.startsWith(`mcp_servers.${serverName}.`);
    }
    if (!skipping) kept.push(line);
  }
  while (kept.length && kept.at(-1) === '') kept.pop();
  const section = [
    '',
    `[mcp_servers.${serverName}]`,
    `command = ${tomlString(executable)}`,
    'args = []',
  ];
  await atomicWrite(configPath, `${kept.join('\n')}${section.join('\n')}\n`);
}

async function atomicWrite(file, contents) {
  const temporary = `${file}.agent-scheduler.tmp`;
  await writeFile(temporary, contents, { mode: 0o600 });
  await chmod(temporary, 0o600);
  await rename(temporary, file);
}

async function installSkill(agent) {
  const destinationRoot = agent === 'codex'
    ? path.join(process.env.CODEX_HOME ? codexHome : path.join(home, '.agents'), 'skills', serverName)
    : path.join(home, '.claude', 'skills', serverName);
  await mkdir(destinationRoot, { recursive: true });
  await copyFile(skillSource, path.join(destinationRoot, 'SKILL.md'));
}

async function updateClaudeMcp(executable) {
  const configPath = path.join(home, '.claude.json');
  let config = {};
  try {
    config = JSON.parse(await readFile(configPath, 'utf8'));
  } catch (error) {
    if (error.code !== 'ENOENT') throw new Error(`Could not read Claude Code configuration: ${error.message}`);
  }
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    throw new Error('Claude Code configuration must contain a JSON object.');
  }
  if (!config.mcpServers || typeof config.mcpServers !== 'object' || Array.isArray(config.mcpServers)) {
    config.mcpServers = {};
  }
  config.mcpServers[serverName] = { type: 'stdio', command: executable, args: [] };
  await atomicWrite(configPath, `${JSON.stringify(config, null, 2)}\n`);
}

function releaseTarget() {
  const os = ({ darwin: 'darwin', linux: 'linux', win32: 'windows' })[platform()];
  const cpu = ({ arm64: 'arm64', x64: 'amd64' })[arch()];
  if (!os || !cpu) throw new Error(`Unsupported platform: ${platform()} ${arch()}`);
  return `agent-scheduler-${os}-${cpu}${os === 'windows' ? '.exe' : ''}`;
}

async function downloadBinary() {
  const asset = releaseTarget();
  const packageInfo = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
  const tag = `scheduler-v${packageInfo.version}`;
  const base = `https://github.com/${repo}/releases/download/${tag}`;
  const [binaryResponse, sumsResponse] = await Promise.all([
    fetch(`${base}/${asset}`),
    fetch(`${base}/SHASUMS256.txt`),
  ]);
  if (!binaryResponse.ok) throw new Error(`Could not download ${asset} for ${tag} (${binaryResponse.status}); publish the matching GitHub release first.`);
  if (!sumsResponse.ok) throw new Error(`Could not download release checksums (${sumsResponse.status}).`);
  const sums = await sumsResponse.text();
  const expected = sums.split(/\r?\n/).map(line => line.trim().split(/\s+/)).find(([, name]) => name === asset)?.[0];
  if (!expected) throw new Error(`No checksum found for ${asset}.`);
  const bytes = Buffer.from(await binaryResponse.arrayBuffer());
  const actual = createHash('sha256').update(bytes).digest('hex');
  if (actual.toLowerCase() !== expected.toLowerCase()) throw new Error('Downloaded connector checksum does not match the release checksum.');
  const binDir = path.join(home, '.local', 'bin');
  await mkdir(binDir, { recursive: true });
  const destination = path.join(binDir, platform() === 'win32' ? 'agent-scheduler.exe' : 'agent-scheduler');
  await writeFile(destination, bytes, { mode: 0o700 });
  await chmod(destination, 0o700);
  return destination;
}

async function chooseAgents() {
  const detected = [];
  if (commandExists('codex') || await exists(path.join(codexHome, 'config.toml'))) detected.push('codex');
  if (commandExists('claude') || await exists(path.join(home, '.claude.json')) || await exists(path.join(home, '.claude'))) detected.push('claude');
  const defaultValue = detected.length ? detected.map(x => x === 'codex' ? '1' : '2').join(',') : '1,2';
  stdout.write('Choose where to install Agent Scheduler (comma-separated numbers; Enter selects detected agents):\n');
  stdout.write(`  1) Codex${detected.includes('codex') ? ' (detected)' : ''}\n`);
  stdout.write(`  2) Claude Code${detected.includes('claude') ? ' (detected)' : ''}\n`);
  const answer = (await rl.question(`Agents [${defaultValue}]: `)).trim() || defaultValue;
  const map = { '1': 'codex', '2': 'claude' };
  const selected = [...new Set(answer.split(',').map(item => map[item.trim()]).filter(Boolean))];
  if (!selected.length) throw new Error('Select at least one supported agent.');
  return selected;
}

async function exists(file) {
  try { await access(file); return true; } catch { return false; }
}

async function main() {
  const selected = await chooseAgents();
  let serverUrl = process.env.AGENT_SCHEDULER_URL || '';
  if (!serverUrl) serverUrl = (await rl.question('Optional scheduler server URL (press Enter for local-only): ')).trim();
  if (serverUrl && !/^https:\/\//i.test(serverUrl)) throw new Error('Enter an HTTPS scheduler server URL, or leave it empty for local-only use.');

  const executable = await downloadBinary();
  if (selected.includes('codex')) {
    await installSkill('codex');
    await updateCodexMcp(executable);
    stdout.write('Installed the skill and updated Codex MCP configuration.\n');
  }
  if (selected.includes('claude')) {
    await installSkill('claude');
    await updateClaudeMcp(executable);
    stdout.write('Installed the skill and updated Claude Code MCP configuration.\n');
  }

  const setup = spawnSync(executable, ['setup', ...(serverUrl ? ['--url', serverUrl] : []), '--skip-agent-config'], { stdio: 'inherit' });
  if (setup.error || setup.status !== 0) throw new Error(`Connector setup failed: ${setup.error?.message || `exit ${setup.status}`}`);
  stdout.write('Restart the selected agent app or start a new session to load the MCP server.\n');
}

main().catch(error => {
  console.error(`Agent Scheduler setup failed: ${error.message}`);
  process.exitCode = 1;
}).finally(() => rl.close());
