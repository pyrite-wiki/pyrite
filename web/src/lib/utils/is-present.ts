/** True when an optional scalar has a value worth showing in the UI. */
export function isPresent(value: unknown): value is string | number | boolean {
	return value != null && value !== '';
}
