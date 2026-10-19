import esbuild from 'esbuild';
import { builtinModules } from 'node:module';

const options = {
	entryPoints: ['src/main.ts'],
	bundle: true,
	format: 'cjs',
	target: 'es2022',
	platform: 'browser',
	// Obsidian provides these at runtime. Node built-ins are there because the plugin is desktop only.
	external: ['obsidian', 'electron', ...builtinModules, ...builtinModules.map((m) => `node:${m}`)],
	outfile: 'main.js',
	sourcemap: process.argv.includes('--watch') ? 'inline' : false,
	minify: !process.argv.includes('--watch'),
	logLevel: 'info'
};

if (process.argv.includes('--watch')) await (await esbuild.context(options)).watch();
else await esbuild.build(options);
