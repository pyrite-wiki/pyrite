/**
 * Graph tooltip content, as plain-text lines rather than an HTML string.
 *
 * Every value here (title, entry type, KB name, edge relation) is entry
 * content -- written by whoever can edit that KB -- so it must never be
 * concatenated into markup and assigned to `innerHTML` (P-B1). The caller
 * renders each line as a text node in its own element instead.
 */

export interface TooltipLine {
	text: string;
	className?: string;
}

export interface NodeTooltipData {
	fullTitle: string;
	entryType: string;
	kbName: string;
	linkCount: number;
	centrality?: number;
}

export function buildNodeTooltipLines(data: NodeTooltipData): TooltipLine[] {
	const lines: TooltipLine[] = [
		{ text: data.fullTitle, className: 'font-semibold' },
		{ text: `${data.entryType} · ${data.kbName}`, className: 'text-zinc-400' },
		{
			text: `${data.linkCount} link${data.linkCount !== 1 ? 's' : ''}`,
			className: 'text-zinc-400'
		}
	];
	if (data.centrality && data.centrality > 0) {
		lines.push({ text: `centrality: ${data.centrality.toFixed(3)}`, className: 'text-zinc-400' });
	}
	return lines;
}

export function buildEdgeTooltipLines(relation: string): TooltipLine[] {
	return [{ text: relation, className: 'text-zinc-300' }];
}
