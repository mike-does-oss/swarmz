// Prints the value for ADMIN_PASS_HASH. The password is read from standard input so it stays out of the process list.
import { hashPassword } from '../lib/auth.js';
let pw = ''; for await (const c of process.stdin) pw += c;
pw = pw.replace(/\r?\n$/, '');
if (pw.length < 8) { console.error('password too short'); process.exit(1); }
process.stdout.write(hashPassword(pw) + '\n');
