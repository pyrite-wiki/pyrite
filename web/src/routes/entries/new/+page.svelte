<script lang="ts">
	import { entryHref } from '$lib/utils/entry-href';
	import Topbar from '$lib/components/layout/Topbar.svelte';
	import TemplatePicker from '$lib/components/entry/TemplatePicker.svelte';
	import TypeChoice from '$lib/components/entry/TypeChoice.svelte';
	import Editor from '$lib/editor/Editor.svelte';
	import { api, ApiError } from '$lib/api/client';
	import { kbStore } from '$lib/stores/kbs.svelte';
	import { uiStore } from '$lib/stores/ui.svelte';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import type { TemplateSummary } from '$lib/api/types';
	import type { TypeSchemaInfo } from '$lib/api/types';
	import { buildMetadata } from '$lib/utils/entry-fields';

	let step = $state<'pick' | 'edit'>('pick');
	let templates = $state<TemplateSummary[]>([]);
	let loadingTemplates = $state(false);
	let typeSchemas = $state<Record<string, TypeSchemaInfo>>({});
	let declaredTypes = $state<string[]>([]);
	let schemasLoading = $state(true);
	let schemasError = $state(false);
	let title = $state('');
	// '' means "not chosen yet" -- TypeChoice starts here and reports a real
	// type only when the user picks one (or the KB declares exactly one).
	// Never default to an arbitrary declared type (round-1 cold read).
	let entryType = $state('');
	let allowUndeclared = $state(false);
	// A template named a type the KB does not declare: show TypeChoice with
	// that type selected and the override toggle already visible/engaged, so
	// the user SEES and can act on it -- but allowUndeclared stays false
	// until the user's own interaction confirms it (round-1 blocker 1).
	let templateForcesOverride = $state(false);
	let body = $state('');
	let tags = $state('');
	let date = $state('');
	let importance = $state(5);
	let status = $state('');
	let customFields = $state<Record<string, string>>({});
	let saving = $state(false);

	const kb = $derived(kbStore.activeKB ?? '');
	const selectedTypeSchema = $derived(typeSchemas[entryType]);
	const typeDescriptions = $derived(
		Object.fromEntries(
			Object.entries(typeSchemas).map(([name, schema]) => [name, schema.description])
		)
	);
	const typeFieldEntries = $derived(
		selectedTypeSchema
			? Object.entries(selectedTypeSchema.fields).filter(
					([name]) => !['date', 'importance', 'status', 'tags', 'links'].includes(name)
				)
			: []
	);
	// The user has not yet made a type choice: block Create so a KB with more
	// than one declared type can never fall through to an unintended default.
	const typeChoicePending = $derived(
		Object.keys(typeSchemas).length > 0 && !entryType
	);

	// Sort types: common ones first, then alphabetical
	function sortTypeNames(names: string[]): string[] {
		const priority = ['note', 'event', 'person', 'organization', 'document', 'topic'];
		const top = priority.filter((n) => names.includes(n));
		const rest = names.filter((n) => !priority.includes(n)).sort();
		return [...top, ...rest];
	}
	const sortedTypeNames = $derived(() => sortTypeNames(Object.keys(typeSchemas)));

	onMount(async () => {
		if (!kb) await kbStore.load();
		loadTemplates();
		loadTypeSchemas();
	});

	async function loadTemplates() {
		if (!kb) return;
		loadingTemplates = true;
		try {
			const res = await api.getTemplates(kb);
			templates = res.templates;
		} catch {
			templates = [];
		} finally {
			loadingTemplates = false;
		}
	}

	async function loadTypeSchemas() {
		if (!kb) return;
		schemasLoading = true;
		schemasError = false;
		try {
			const res = await api.getTypeSchemas(kb);
			typeSchemas = res.types;
			declaredTypes = res.declared ?? [];
		} catch {
			typeSchemas = {};
			declaredTypes = [];
			schemasError = true;
		} finally {
			schemasLoading = false;
		}
	}

	function onTypeChoice(choice: { entryType: string; allowUndeclared: boolean }) {
		entryType = choice.entryType;
		allowUndeclared = choice.allowUndeclared;
		customFields = {};
	}

	async function onTemplateSelect(templateName: string | null) {
		if (templateName === null) {
			// Keep the user's type selection from the picker step
			body = '';
			customFields = {};
			step = 'edit';
			return;
		}

		try {
			const rendered = await api.renderTemplate(kb, templateName, { title });
			body = rendered.body;
			entryType = rendered.entry_type;
			// A template names its own type (template_service.py defaults an
			// untyped one to `note`). It must never silently set
			// allow_undeclared on the user's behalf -- that is exactly the
			// #197 hazard TypeChoice exists to close. Show the picker with the
			// undeclared type visible and the override control already
			// engaged so the user can see and confirm or change it; if they
			// submit without touching it, the server's own refusal surfaces.
			templateForcesOverride = declaredTypes.length > 0 && !declaredTypes.includes(entryType);
			allowUndeclared = false;
			customFields = {};
			step = 'edit';
		} catch {
			uiStore.toast('Failed to load template', 'error');
		}
	}

	function onEditorChange(content: string) {
		body = content;
	}

	async function save() {
		if (!title.trim()) {
			uiStore.toast('Title is required', 'error');
			return;
		}
		if (typeChoicePending) {
			uiStore.toast('Choose an entry type', 'error');
			return;
		}
		saving = true;
		try {
			const req: Record<string, unknown> = {
				kb,
				entry_type: entryType,
				title: title.trim(),
				body
			};
			if (tags.trim())
				req.tags = tags
					.split(',')
					.map((t: string) => t.trim())
					.filter(Boolean);
			if (date) req.date = date;
			if (importance !== 5) req.importance = importance;
			if (status.trim()) req.status = status.trim();
			// Only the explicit "use a type this KB does not declare" choice
			// (TypeChoice's own toggle) sends this; the client itself never
			// opts in on the caller's behalf, and a template naming an
			// undeclared type does not either (#392, round-1 blocker 1).
			if (allowUndeclared) req.allow_undeclared = true;

			// Merge non-empty custom fields into metadata, coerced by schema type.
			const meta = buildMetadata(customFields, selectedTypeSchema?.fields);
			if (Object.keys(meta).length > 0) req.metadata = meta;

			const res = await api.createEntry(req as any);
			uiStore.toast('Entry created', 'success');
			goto(entryHref(res.id, res.kb_name));
		} catch (e) {
			// Surface the server's own refusal message (e.g. #378's
			// undeclared-type refusal) instead of a generic failure -- the
			// template path can reach this when the user submits without
			// confirming an undeclared type (round-1 blocker 1).
			const message = e instanceof ApiError ? e.detail : 'Failed to create entry';
			uiStore.toast(message, 'error');
		} finally {
			saving = false;
		}
	}

	function fieldTypeToInputType(fieldType: string): string {
		switch (fieldType) {
			case 'number':
				return 'number';
			case 'date':
			case 'datetime':
				return 'date';
			case 'checkbox':
				return 'checkbox';
			default:
				return 'text';
		}
	}

	const breadcrumbs = [{ label: 'Entries', href: '/entries' }, { label: 'New Entry' }];
</script>

<svelte:head><title>New Entry — Pyrite</title></svelte:head>

<Topbar {breadcrumbs} />

<div class="flex-1 overflow-y-auto p-6">
	{#if step === 'pick'}
		<div class="mx-auto max-w-lg">
			<h1 class="mb-2 text-2xl font-bold">New Entry</h1>

			<!-- Title input -->
			<div class="mb-4">
				<label for="entry-title" class="mb-1 block text-sm font-medium">Title</label>
				<input
					id="entry-title"
					type="text"
					bind:value={title}
					placeholder="Entry title..."
					class="w-full rounded-md border border-zinc-300 px-3 py-2 dark:border-zinc-700 dark:bg-zinc-800"
				/>
			</div>

			<!-- Type selector -->
			{#if schemasLoading}
				<p class="mb-4 text-sm text-zinc-400" data-testid="type-schemas-loading">
					Loading entry types…
				</p>
			{:else if schemasError}
				<div class="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-600 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
					<p data-testid="type-schemas-error">Could not load this KB's entry types.</p>
					<button
						type="button"
						onclick={loadTypeSchemas}
						class="mt-1 text-xs font-medium underline"
						data-testid="type-schemas-retry"
					>
						Retry
					</button>
				</div>
			{:else if Object.keys(typeSchemas).length > 0}
				<div class="mb-4">
					<TypeChoice
						id="entry-type-select"
						types={sortedTypeNames()}
						declared={declaredTypes}
						value={entryType}
						forceOverride={templateForcesOverride}
						{typeDescriptions}
						onchange={onTypeChoice}
					/>
					{#if selectedTypeSchema?.description}
						<p class="mt-1 text-xs text-zinc-500">{selectedTypeSchema.description}</p>
					{/if}
				</div>
			{/if}

			<!-- Template picker -->
			<h2 class="mb-2 text-lg font-medium">Choose a template</h2>
			<TemplatePicker {templates} loading={loadingTemplates} onselect={onTemplateSelect} />
		</div>
	{:else}
		<!-- Editor step -->
		<div class="mx-auto max-w-4xl">
			<div class="mb-4 flex items-center justify-between">
				<div>
					<button
						onclick={() => (step = 'pick')}
						class="text-sm text-blue-600 hover:underline"
					>
						&larr; Back
					</button>
					<h1 class="text-xl font-bold">{title || 'Untitled'}</h1>
					<span class="text-sm text-zinc-500">Type: {entryType || '(none chosen)'}</span>
				</div>
				<div class="flex items-center gap-2">
					<input
						type="text"
						bind:value={title}
						placeholder="Title..."
						class="rounded-md border border-zinc-300 px-3 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-800"
					/>
					<button
						onclick={save}
						disabled={saving || !kb || schemasLoading || schemasError || typeChoicePending}
						class="rounded-md bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
					>
						{saving ? 'Saving...' : 'Create'}
					</button>
				</div>
			</div>

			{#if schemasError}
				<div class="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-600 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
					<p data-testid="type-schemas-error">Could not load this KB's entry types.</p>
					<button
						type="button"
						onclick={loadTypeSchemas}
						class="mt-1 text-xs font-medium underline"
						data-testid="type-schemas-retry"
					>
						Retry
					</button>
				</div>
			{:else if Object.keys(typeSchemas).length > 0}
				<div class="mb-4">
					<TypeChoice
						id="entry-type-select-edit"
						types={sortedTypeNames()}
						declared={declaredTypes}
						value={entryType}
						forceOverride={templateForcesOverride}
						{typeDescriptions}
						onchange={onTypeChoice}
					/>
				</div>
			{/if}

			<!-- Type-specific fields + standard fields -->
			<div class="mb-4 space-y-3">
				<!-- Standard fields row -->
				<div class="grid grid-cols-2 gap-3 rounded-lg border border-zinc-200 p-3 dark:border-zinc-700 sm:grid-cols-4">
					<div>
						<label
							for="entry-tags"
							class="mb-1 block text-xs font-medium text-zinc-500">Tags</label
						>
						<input
							id="entry-tags"
							type="text"
							bind:value={tags}
							placeholder="tag1, tag2"
							class="w-full rounded border border-zinc-300 px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-800"
						/>
					</div>
					<div>
						<label
							for="entry-date"
							class="mb-1 block text-xs font-medium text-zinc-500">Date</label
						>
						<input
							id="entry-date"
							type="date"
							bind:value={date}
							class="w-full rounded border border-zinc-300 px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-800"
						/>
					</div>
					<div>
						<label
							for="entry-importance"
							class="mb-1 block text-xs font-medium text-zinc-500"
							>Importance: {importance}</label
						>
						<input
							id="entry-importance"
							type="range"
							min="1"
							max="10"
							bind:value={importance}
							class="w-full"
						/>
					</div>
					<div>
						<label
							for="entry-status"
							class="mb-1 block text-xs font-medium text-zinc-500">Status</label
						>
						<input
							id="entry-status"
							type="text"
							bind:value={status}
							placeholder="draft"
							class="w-full rounded border border-zinc-300 px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-800"
						/>
					</div>
				</div>

				<!-- Type-specific fields -->
				{#if typeFieldEntries.length > 0}
					<div
						class="rounded-lg border border-purple-200 bg-purple-50/50 p-3 dark:border-purple-800 dark:bg-purple-900/10"
					>
						<p
							class="mb-2 text-xs font-semibold uppercase text-purple-600 dark:text-purple-400"
						>
							{entryType} fields
						</p>
						<div class="grid grid-cols-2 gap-3">
							{#each typeFieldEntries as [fieldName, fieldSchema]}
								<div>
									<label
										for="field-{fieldName}"
										class="mb-1 block text-xs font-medium text-zinc-600 dark:text-zinc-400"
									>
										{fieldName.replace(/_/g, ' ')}
										{#if fieldSchema.required}<span class="text-red-500">*</span
											>{/if}
									</label>
									{#if fieldSchema.options && fieldSchema.options.length > 0}
										<select
											id="field-{fieldName}"
											value={customFields[fieldName] ?? ''}
											onchange={(e) =>
												(customFields[fieldName] = e.currentTarget.value)}
											class="w-full rounded border border-zinc-300 px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-800"
										>
											<option value="">Select...</option>
											{#each fieldSchema.options as opt}
												<option value={opt}>{opt}</option>
											{/each}
										</select>
									{:else if fieldSchema.type === 'list'}
										<input
											id="field-{fieldName}"
											type="text"
											value={customFields[fieldName] ?? ''}
											onchange={(e) =>
												(customFields[fieldName] = e.currentTarget.value)}
											placeholder="Comma-separated values"
											class="w-full rounded border border-zinc-300 px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-800"
										/>
									{:else if fieldSchema.type === 'checkbox' || fieldSchema.type === 'boolean'}
										<input
											id="field-{fieldName}"
											type="checkbox"
											checked={customFields[fieldName] === 'true'}
											onchange={(e) =>
												(customFields[fieldName] = e.currentTarget.checked
													? 'true'
													: 'false')}
											class="h-4 w-4 rounded border-zinc-300 dark:border-zinc-700 dark:bg-zinc-800"
										/>
									{:else}
										<input
											id="field-{fieldName}"
											type={fieldTypeToInputType(fieldSchema.type)}
											value={customFields[fieldName] ?? ''}
											onchange={(e) =>
												(customFields[fieldName] = e.currentTarget.value)}
											placeholder={fieldSchema.description || ''}
											class="w-full rounded border border-zinc-300 px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-800"
										/>
									{/if}
									{#if fieldSchema.description}
										<p class="mt-0.5 text-[10px] text-zinc-400">
											{fieldSchema.description}
										</p>
									{/if}
								</div>
							{/each}
						</div>
					</div>
				{/if}
			</div>

			<div class="h-[calc(100vh-20rem)]">
				<Editor content={body} onchange={onEditorChange} onsave={save} />
			</div>
		</div>
	{/if}
</div>
