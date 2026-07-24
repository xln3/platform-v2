export type PubId = string & { readonly __brand: 'PubId' };
export type UtcIsoDateTime = string & { readonly __brand: 'UtcIsoDateTime' };
export type CursorPage<T> = { data: T[]; page: { next_cursor: string | null; has_more: boolean } };
