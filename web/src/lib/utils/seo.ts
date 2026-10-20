/** SEO helpers: build og/twitter/JSON-LD metadata for entry pages.
 *
 * The surface the former static renderer (`/site`, removed in #953)
 * emitted, now built by the live app. See
 * the kb/backlog/pyrite-entry-page-seo-meta.md ticket for scope.
 */

import type { EntryResponse } from '$lib/api/types';

/** schema.org @type mapping by entry_type. */
const SCHEMA_TYPES: Record<string, string> = {
	note: 'Article',
	person: 'Person',
	organization: 'Organization',
	event: 'Event',
	source: 'ScholarlyArticle',
	concept: 'Article',
	writing: 'Article',
	era: 'Article',
	component: 'SoftwareSourceCode'
};

export function schemaTypeFor(entryType: string): string {
	return SCHEMA_TYPES[entryType] ?? 'Article';
}

/** Description fallback: summary if present, else trimmed body head. */
export function entryDescription(entry: EntryResponse, max = 160): string {
	const src = entry.summary?.trim() || entry.body?.trim() || '';
	if (src.length <= max) return src;
	const cut = src.slice(0, max);
	const ws = cut.lastIndexOf(' ');
	return (ws > max * 0.5 ? cut.slice(0, ws) : cut) + '…';
}

export interface SeoInput {
	entry: EntryResponse;
	brand: { name: string; og_image_url: string | null; site_url: string };
	/** Optional explicit canonical URL (absolute). Falls back to path-only. */
	canonicalUrl?: string;
}

export interface EntrySeo {
	title: string;
	description: string;
	canonical: string;
	ogTitle: string;
	ogDescription: string;
	ogType: 'article';
	ogUrl: string;
	ogImage: string | null;
	twitterCard: 'summary';
	jsonLd: Record<string, unknown>;
}

export function buildEntrySeo({ entry, brand, canonicalUrl }: SeoInput): EntrySeo {
	const base = (brand.site_url ?? '').replace(/\/+$/, '');
	const path = `/entries/${encodeURIComponent(entry.id)}?kb=${encodeURIComponent(entry.kb_name)}`;
	const url = canonicalUrl ?? (base ? `${base}${path}` : path);

	const titleMain = `${entry.title} — ${entry.kb_name}`;
	const fullTitle = `${titleMain} | ${brand.name}`;
	const description = entryDescription(entry);

	const jsonLd: Record<string, unknown> = {
		'@context': 'https://schema.org',
		'@type': schemaTypeFor(entry.entry_type),
		name: entry.title,
		description,
		url,
		publisher: { '@type': 'Organization', name: brand.name }
	};
	if (entry.updated_at) jsonLd.dateModified = entry.updated_at;
	if (entry.created_at) jsonLd.datePublished = entry.created_at;

	return {
		title: fullTitle,
		description,
		canonical: url,
		ogTitle: titleMain,
		ogDescription: description,
		ogType: 'article',
		ogUrl: url,
		ogImage: brand.og_image_url,
		twitterCard: 'summary',
		jsonLd
	};
}
