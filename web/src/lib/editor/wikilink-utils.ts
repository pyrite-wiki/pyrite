import { entryHref } from '$lib/utils/entry-href';

/**
 * Wikilink parsing and rendering utilities.
 *
 * Supports [[id]], [[id|display text]], [[kb:id]], [[id#heading]], and [[id^block-id]].
 */

/** Regex matching wikilinks with optional heading/block-id fragments. */
export const WIKILINK_REGEX = /\[\[(?:([a-z0-9-]+):)?([^\]|#^]+?)(?:#([^\]|^]+?))?(?:\^([^\]|]+?))?(?:\|([^\]]+?))?\]\]/g;

/** Regex matching transclusions: ![[target]], ![[target#heading]], ![[target^block-id]] */
export const TRANSCLUSION_REGEX = /!\[\[(?:([a-z0-9-]+):)?([^\]|#^]+?)(?:#([^\]|^]+?))?(?:\^([^\]|]+?))?(?:\|([^\]]+?))?\]\]/g;

/** Regex matching transclusions with optional view options: ![[target]]{ view: "table", limit: 5 } */
export const TRANSCLUSION_OPTIONS_REGEX = /!\[\[(?:([a-z0-9-]+):)?([^\]|#^]+?)(?:#([^\]|^]+?))?(?:\^([^\]|]+?))?(?:\|([^\]]+?))?\]\](?:\{([^}]*)\})?/g;

/** Single wikilink match result */
export interface WikilinkMatch {
	/** Full match including brackets */
	full: string;
	/** Target entry ID or title */
	target: string;
	/** Optional display text */
	display?: string;
	/** Cross-KB prefix */
	kb?: string;
	/** Heading fragment (after #) */
	heading?: string;
	/** Block ID fragment (after ^) */
	blockId?: string;
	/** View options from trailing { ... } block */
	options?: Record<string, string | number | boolean>;
	/** Start index in source string */
	start: number;
	/** End index in source string */
	end: number;
}

/**
 * Extract all wikilinks from a string.
 */
export function parseWikilinks(text: string): WikilinkMatch[] {
	const matches: WikilinkMatch[] = [];
	const regex = new RegExp(WIKILINK_REGEX.source, 'g');
	let match: RegExpExecArray | null;
	while ((match = regex.exec(text)) !== null) {
		matches.push({
			full: match[0],
			kb: match[1]?.trim(),
			target: match[2].trim(),
			heading: match[3]?.trim(),
			blockId: match[4]?.trim(),
			display: match[5]?.trim(),
			start: match.index,
			end: match.index + match[0].length
		});
	}
	return matches;
}

/**
 * Parse a transclusion options string (content inside { ... }) into a record.
 * Accepts JSON-like syntax with unquoted keys: `view: "table", limit: 5`.
 * Returns undefined if parsing fails.
 */
function parseOptionsString(optionsStr: string | undefined): Record<string, string | number | boolean> | undefined {
	if (!optionsStr) return undefined;
	try {
		// Quote bare keys: `view: "table"` -> `"view": "table"`
		const jsonStr = optionsStr.replace(/([a-zA-Z_]\w*)\s*:/g, '"$1":');
		const parsed = JSON.parse(`{${jsonStr}}`);
		if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
			return parsed as Record<string, string | number | boolean>;
		}
		return undefined;
	} catch {
		return undefined;
	}
}

/**
 * Extract all transclusions from a string.
 */
export function parseTransclusions(text: string): WikilinkMatch[] {
	const matches: WikilinkMatch[] = [];
	const regex = new RegExp(TRANSCLUSION_OPTIONS_REGEX.source, 'g');
	let match: RegExpExecArray | null;
	while ((match = regex.exec(text)) !== null) {
		const options = parseOptionsString(match[6]);
		const result: WikilinkMatch = {
			full: match[0],
			kb: match[1]?.trim(),
			target: match[2].trim(),
			heading: match[3]?.trim(),
			blockId: match[4]?.trim(),
			display: match[5]?.trim(),
			start: match.index,
			end: match.index + match[0].length
		};
		if (options) {
			result.options = options;
		}
		matches.push(result);
	}
	return matches;
}

/**
 * Replace wikilinks in HTML with clickable links.
 * Used in the rendered markdown view.
 *
 * @param html - HTML string with [[wikilink]] syntax
 * @param existingIds - Optional set of entry IDs that exist. If provided,
 *   links to non-existent entries get a "wikilink-missing" class (red links).
 */
export function renderWikilinks(html: string, existingIds?: Set<string>): string {
	// First, handle transclusions: ![[...]] -> blockquote embeds
	let result = html.replace(
		TRANSCLUSION_REGEX,
		(_match, transclusionKb: string | undefined, target: string, heading?: string, blockId?: string) => {
			const trimmed = target.trim();
			const headingTrimmed = heading?.trim() || '';
			const blockIdTrimmed = blockId?.trim() || '';
			return `<blockquote class="transclusion" data-transclusion-target="${trimmed}" data-transclusion-kb="${transclusionKb?.trim() ?? ''}" data-transclusion-heading="${headingTrimmed}" data-transclusion-block="${blockIdTrimmed}">
  <p class="transclusion-loading">Loading transcluded content...</p>
  <cite><a href="${entryHref(trimmed, transclusionKb?.trim())}">${trimmed}</a></cite>
</blockquote>`;
		}
	);

	// Then handle regular wikilinks: [[...]] -> clickable links
	return result.replace(
		WIKILINK_REGEX,
		(_match, kb: string | undefined, target: string, heading?: string, blockId?: string, display?: string) => {
			const trimmed = target.trim();
			const kbPrefix = kb?.trim();
			const headingTrimmed = heading?.trim();
			const blockIdTrimmed = blockId?.trim();

			// Build display label
			let label: string;
			if (display?.trim()) {
				label = display.trim();
			} else if (headingTrimmed) {
				const base = kbPrefix ? `${kbPrefix}:${trimmed}` : trimmed;
				label = `${base} \u00A7 ${headingTrimmed}`;
			} else if (blockIdTrimmed) {
				const base = kbPrefix ? `${kbPrefix}:${trimmed}` : trimmed;
				label = `${base} ^${blockIdTrimmed}`;
			} else {
				label = kbPrefix ? `${kbPrefix}:${trimmed}` : trimmed;
			}

			// Build href with fragment
			let fragment = '';
			if (headingTrimmed) {
				fragment = `#${encodeURIComponent(headingTrimmed)}`;
			} else if (blockIdTrimmed) {
				fragment = `#block-${encodeURIComponent(blockIdTrimmed)}`;
			}
			const href = `${entryHref(trimmed, kbPrefix)}${fragment}`;

			const missing = existingIds && !existingIds.has(trimmed);
			const cls = missing ? 'wikilink wikilink-missing' : 'wikilink';
			const kbBadge = kbPrefix
				? `<span class="wikilink-kb-badge">${kbPrefix}</span>`
				: '';
			return `<a href="${href}" class="${cls}" data-wikilink="${trimmed}" data-wikilink-kb="${kbPrefix || ''}">${kbBadge}${label}</a>`;
		}
	);
}
