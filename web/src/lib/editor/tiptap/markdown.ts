/**
 * Markdown <-> HTML conversion for Tiptap editor.
 *
 * Uses `marked` (already a dep) for md->html.
 * Uses a lightweight recursive converter for html->md.
 * Markdown is always the source of truth.
 */

import { marked } from 'marked';
import { sanitizeHtml } from '$lib/utils/sanitize';

/**
 * Convert Markdown to HTML for Tiptap consumption.
 * Handles wikilinks as inline spans so Tiptap can render them.
 */
export function markdownToHtml(md: string): string {
	// Pre-process transclusions: ![[target#heading]] -> <div data-transclusion="target" ...>
	let processed = md.replace(
		/!\[\[(?:([a-z0-9-]+):)?([^\]|#^]+?)(?:#([^\]|^]+?))?(?:\^([^\]|]+?))?(?:\|([^\]]+?))?\]\]/g,
		(_match, kb: string | undefined, target: string, heading?: string, blockId?: string) => {
			const attrs = [
				`data-transclusion="${target.trim()}"`,
				heading ? `data-transclusion-heading="${heading.trim()}"` : '',
				blockId ? `data-transclusion-block="${blockId.trim()}"` : '',
				kb ? `data-transclusion-kb="${kb.trim()}"` : ''
			]
				.filter(Boolean)
				.join(' ');
			return `<div ${attrs} class="transclusion-embed"><p class="transclusion-content">Loading...</p></div>`;
		}
	);

	// Pre-process wikilinks: [[kb:target#heading^block-id|display]] -> <span data-wikilink="target" ...>display</span>
	processed = processed.replace(
		/\[\[(?:([a-z0-9-]+):)?([^\]|#^]+?)(?:#([^\]|^]+?))?(?:\^([^\]|]+?))?(?:\|([^\]]+?))?\]\]/g,
		(_match, kb: string | undefined, target: string, heading?: string, blockId?: string, display?: string) => {
			const label = display || (heading ? `${target} \u00A7 ${heading}` : blockId ? `${target} ^${blockId}` : target);
			const attrs = [
				`data-wikilink="${target.trim()}"`,
				heading ? `data-wikilink-heading="${heading.trim()}"` : '',
				blockId ? `data-wikilink-block="${blockId.trim()}"` : '',
				kb ? `data-wikilink-kb="${kb.trim()}"` : ''
			].filter(Boolean).join(' ');
			return `<span ${attrs} class="wikilink">${label.trim()}</span>`;
		}
	);

	const html = marked.parse(processed, { async: false }) as string;
	// This output reaches `innerHTML` at every call site (the transclusion
	// extension, the entry-detail transclusion preview) -- markdown bodies
	// are written by other users and by agents ingesting untrusted web
	// content, same as the read-only renderer's `sanitizeHtml` call (P-B1).
	return sanitizeHtml(html);
}

/**
 * Convert HTML from Tiptap back to Markdown.
 * Lightweight recursive converter.
 */
export function htmlToMarkdown(html: string): string {
	const parser = new DOMParser();
	const doc = parser.parseFromString(html, 'text/html');
	return nodeToMarkdown(doc.body).trim() + '\n';
}

function nodeToMarkdown(node: Node): string {
	if (node.nodeType === Node.TEXT_NODE) {
		return node.textContent || '';
	}

	if (node.nodeType !== Node.ELEMENT_NODE) return '';

	const el = node as HTMLElement;
	const tag = el.tagName.toLowerCase();
	const children = () => Array.from(el.childNodes).map(nodeToMarkdown).join('');

	// Transclusion divs
	if (tag === 'div' && el.dataset.transclusion) {
		const target = el.dataset.transclusion;
		const heading = el.dataset.transclusionHeading;
		const blockId = el.dataset.transclusionBlock;
		const kb = el.dataset.transclusionKb;
		let ref = kb ? `${kb}:${target}` : target;
		if (heading) ref += `#${heading}`;
		else if (blockId) ref += `^${blockId}`;
		return `![[${ref}]]\n\n`;
	}

	// Wikilink spans
	if (tag === 'span' && el.dataset.wikilink) {
		const target = el.dataset.wikilink;
		const heading = el.dataset.wikilinkHeading;
		const blockId = el.dataset.wikilinkBlock;
		const kb = el.dataset.wikilinkKb;
		const display = el.textContent || target;

		// Reconstruct full wikilink syntax
		let ref = kb ? `${kb}:${target}` : target;
		if (heading) ref += `#${heading}`;
		else if (blockId) ref += `^${blockId}`;

		// If display is the auto-generated label, don't include it
		const autoLabel = heading ? `${target} \u00A7 ${heading}` : blockId ? `${target} ^${blockId}` : target;
		return ref === display || autoLabel === display ? `[[${ref}]]` : `[[${ref}|${display}]]`;
	}

	switch (tag) {
		case 'h1':
			return `# ${children()}\n\n`;
		case 'h2':
			return `## ${children()}\n\n`;
		case 'h3':
			return `### ${children()}\n\n`;
		case 'h4':
			return `#### ${children()}\n\n`;
		case 'h5':
			return `##### ${children()}\n\n`;
		case 'h6':
			return `###### ${children()}\n\n`;
		case 'p':
			return `${children()}\n\n`;
		case 'br':
			return '\n';
		case 'strong':
		case 'b':
			return `**${children()}**`;
		case 'em':
		case 'i':
			return `*${children()}*`;
		case 'code': {
			if (el.parentElement?.tagName.toLowerCase() === 'pre') {
				return children();
			}
			return `\`${children()}\``;
		}
		case 'pre': {
			const codeEl = el.querySelector('code');
			const lang = codeEl?.className?.match(/language-(\w+)/)?.[1] || '';
			const code = codeEl?.textContent || children();
			return `\`\`\`${lang}\n${code}\n\`\`\`\n\n`;
		}
		case 'a': {
			const href = el.getAttribute('href') || '';
			return `[${children()}](${href})`;
		}
		case 'ul':
			return listToMarkdown(el, false);
		case 'ol':
			return listToMarkdown(el, true);
		case 'li': {
			// Handle task list items
			const checkbox = el.querySelector('input[type="checkbox"]');
			if (checkbox) {
				const checked = (checkbox as HTMLInputElement).checked;
				const text = Array.from(el.childNodes)
					.filter((n) => n !== checkbox && !(n instanceof HTMLElement && n.tagName === 'LABEL'))
					.map(nodeToMarkdown)
					.join('')
					.trim();
				return `[${checked ? 'x' : ' '}] ${text}`;
			}
			return children().trim();
		}
		case 'blockquote':
			return (
				children()
					.trim()
					.split('\n')
					.map((line: string) => `> ${line}`)
					.join('\n') + '\n\n'
			);
		case 'hr':
			return '---\n\n';
		case 'img': {
			const src = el.getAttribute('src') || '';
			const alt = el.getAttribute('alt') || '';
			return `![${alt}](${src})`;
		}
		default:
			return children();
	}
}

function listToMarkdown(el: HTMLElement, ordered: boolean): string {
	const items = Array.from(el.children);
	const lines = items.map((item, i) => {
		const prefix = ordered ? `${i + 1}. ` : '- ';
		const content = nodeToMarkdown(item);
		return `${prefix}${content}`;
	});
	return lines.join('\n') + '\n\n';
}
