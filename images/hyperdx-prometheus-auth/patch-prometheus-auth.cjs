'use strict';
const fs = require('node:fs');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const target = process.argv[2] || '/app/packages/api/build/routers/api/prometheus.js';
const source = fs.readFileSync(target, 'utf8');
if (crypto.createHash('sha256').update(source).digest('hex') !== 'ed851430a07743b601b7993644a30243d39652aa4792408f719b331c96dc738f') throw new Error('Unrecognized upstream API file');
if (source.includes('charliePrometheusAuthHeaders')) throw new Error('Patch already applied');
// Split call arguments without relying on compiler whitespace. Nested objects,
// strings, template literals and parentheses remain part of their argument.
function argumentsAt(s, start) {
  let depth = 0, quote = '', escaped = false, args = [], from = start;
  for (let i = start; i < s.length; i++) {
    const c = s[i];
    if (quote) { if (escaped) escaped = false; else if (c === '\\') escaped = true; else if (c === quote) quote = ''; continue; }
    if (c === '/' && s[i+1] === '/') { i=s.indexOf('\n',i); if(i<0) throw new Error('Unclosed comment'); continue; }
    if (c === '/' && s[i+1] === '*') { i=s.indexOf('*/',i+2); if(i<0) throw new Error('Unclosed comment'); i++; continue; }
    if ('"\'`'.includes(c)) { quote = c; continue; }
    if ('([{'.includes(c)) depth++;
    else if (')]}'.includes(c)) {
      if (c === ')' && depth === 0) { if (s.slice(from, i).trim()) args.push(s.slice(from, i).trim()); return {args, end:i}; }
      depth--;
    } else if (c === ',' && depth === 0) { args.push(s.slice(from, i).trim()); from = i + 1; }
  }
  throw new Error('Unclosed call');
}
const edits = [];
for (const match of source.matchAll(/\bproxyToPrometheus\s*\(/g)) {
  if (!/^\s*connection\.host\s*,/.test(source.slice(match.index + match[0].length))) continue;
  const call = argumentsAt(source, match.index + match[0].length);
  if (call.args[0] !== 'connection.host') continue;
  if (call.args.length !== 4 || call.args[3] !== 'res') throw new Error('Unexpected external Prometheus call');
  edits.push({start:match.index, end:call.end+1, text:'proxyToPrometheus('+call.args.concat('charliePrometheusAuthHeaders(connection)').join(', ')+')'});
}
if (edits.length !== 4) throw new Error('Expected exactly four external Prometheus calls');
let output = source;
for (const edit of edits.reverse()) output = output.slice(0, edit.start)+edit.text+output.slice(edit.end);
const lookup = /\(0,\s*([\w$.]+\.getConnectionById)\)\(teamId\.toString\(\),\s*connectionId\)/g;
const lookups = [...output.matchAll(lookup)];
if (lookups.length !== 1) throw new Error('Expected one exemplar lookup without password');
output = output.replace(lookup, '(0, $1)(teamId.toString(), connectionId, true)');
const helper = `
// Charlie compatibility patch: credentials remain in request headers, never URLs.
function charliePrometheusAuthHeaders(connection) {
  const username = connection.username ?? '';
  const password = connection.password ?? '';
  if (typeof username !== 'string' || typeof password !== 'string') throw new TypeError('Invalid Prometheus credentials');
  if (!username && !password) return {};
  return { Authorization: 'Basic ' + Buffer.from(username + ':' + password, 'utf8').toString('base64') };
}
`;
if ((output.match(/async function proxyToPrometheus\(/g)||[]).length !== 1) throw new Error('Unexpected proxy function layout');
output = output.replace('async function proxyToPrometheus(', helper+'\nasync function proxyToPrometheus(');
// Validate syntax before replacing the original. This is JavaScript, not TS.
new (require('node:vm').Script)(output, {filename:target});
fs.writeFileSync(target, output);
cp.execFileSync(process.execPath, ['--check', target]);
console.log(JSON.stringify({externalCalls:4,exemplarPasswordSelection:true,originalSha256:crypto.createHash('sha256').update(source).digest('hex'),patchedSha256:crypto.createHash('sha256').update(output).digest('hex')}));
