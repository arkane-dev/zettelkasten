// Plugin settings, stored per vault in .obsidian/plugins/zettelkasten/data.json.

export interface ZkSettings {
	/** Path to the zk-sidecar executable. */
	sidecarCommand: string;
	/** Any OpenAI-compatible API. Ollama by default. */
	llmBaseUrl: string;
	llmModel: string;
	llmApiKey: string;
	/** The model's context window in tokens, as text. */
	llmContext: string;
	embedModel: string;
	/** Chromium for SingleFile. Empty means the sidecar finds one. */
	browserPath: string;
	/** A Python with ipykernel for code. Empty means the vault's own, in .zk/python. */
	pythonPath: string;
	/** Rebuild the communities daily at HH:MM, if the vault changed. Empty turns it off. */
	graphSchedule: string;
}

export const DEFAULT_SETTINGS: ZkSettings = {
	sidecarCommand: '',
	llmBaseUrl: 'http://127.0.0.1:11434/v1',
	llmModel: 'gemma3:27b',
	llmApiKey: '',
	llmContext: '8192',
	embedModel: 'nomic-embed-text',
	browserPath: '',
	pythonPath: '',
	graphSchedule: '03:00'
};

/** Fill gaps from the defaults. Unknown keys from older versions are dropped. */
export function mergeSettings(saved: unknown): ZkSettings {
	const s = (saved && typeof saved === 'object' ? saved : {}) as Record<string, unknown>;
	const out = { ...DEFAULT_SETTINGS };
	for (const key of Object.keys(DEFAULT_SETTINGS) as (keyof ZkSettings)[]) {
		if (typeof s[key] === 'string') out[key] = s[key] as string;
	}
	return out;
}

/** The environment the sidecar reads its settings from. Secrets stay off the command line. */
export function sidecarEnv(s: ZkSettings): Record<string, string> {
	return {
		ZK_LLM_BASE_URL: s.llmBaseUrl,
		ZK_LLM_MODEL: s.llmModel,
		ZK_LLM_API_KEY: s.llmApiKey,
		ZK_LLM_CONTEXT: s.llmContext,
		ZK_EMBED_MODEL: s.embedModel,
		ZK_BROWSER: s.browserPath,
		ZK_PYTHON: s.pythonPath,
		ZK_GRAPH_SCHEDULE: s.graphSchedule
	};
}
