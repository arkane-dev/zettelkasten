import esbuild from 'esbuild';
await esbuild.build({
  entryPoints: ['main.ts'], bundle: true, format: 'cjs', target: 'es2022', platform: 'browser',
  external: ['obsidian', 'electron'], outfile: 'main.js', logLevel: 'info'
});
