/** Shared constants for Pyrite frontend. */

/** Color mapping for entry types, used in search results and graph views. */
export const typeColors: Record<string, string> = {
	event: '#3b82f6',
	person: '#8b5cf6',
	organization: '#f59e0b',
	topic: '#10b981',
	note: '#6b7280',
	place: '#ef4444',
	source: '#06b6d4',
	document: '#84cc16',
	standard: '#f472b6',
	component: '#22d3ee',
	adr: '#a78bfa',
	backlog_item: '#fb923c',
	qa_assessment: '#34d399',
	task: '#60a5fa',
	generic: '#94a3b8',
	collection: '#c084fc',
	daily_note: '#fbbf24',
	meeting: '#3b82f6',
	article: '#6b7280',
	actor: '#6b7280',
};

/** Default color for unknown entry types. */
export const defaultTypeColor = '#71717a';

/** Get the color for an entry type, falling back to default. */
export function typeColor(entryType: string): string {
	return typeColors[entryType] ?? defaultTypeColor;
}

/**
 * Tailwind background+text class mapping for entry type badges.
 * Used in collection views (Gallery, Kanban, Table) and AI search results.
 */
export const typeBgColors: Record<string, string> = {
	event: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
	person: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300',
	organization: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
	topic: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
	note: 'bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-300',
	place: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
	source: 'bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-300',
	document: 'bg-lime-100 text-lime-800 dark:bg-lime-900/30 dark:text-lime-300',
	standard: 'bg-pink-100 text-pink-800 dark:bg-pink-900/30 dark:text-pink-300',
	component: 'bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-300',
	adr: 'bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300',
	backlog_item: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300',
	qa_assessment: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
	task: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
	generic: 'bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-300',
	collection: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300',
	daily_note: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
	meeting: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
	article: 'bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-300',
	actor: 'bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-300',
	concept: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300',
	project: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
};

/** Default Tailwind bg+text classes for unknown entry types. */
export const defaultTypeBgColor = 'bg-zinc-100 text-zinc-600 dark:bg-zinc-700 dark:text-zinc-400';

/** Get the Tailwind bg+text classes for an entry type badge, falling back to default. */
export function typeBgColor(entryType: string): string {
	return typeBgColors[entryType] ?? defaultTypeBgColor;
}
